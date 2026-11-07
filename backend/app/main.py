"""HTTP API сервиса CQA + раздача фронтенда."""
from __future__ import annotations

import asyncio
import csv
import io
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from . import db, services
from .config import ROOT, settings
from .evaluator import evaluate_dialog
from .llm import LLMError, get_provider

DEFAULT_CHECKLIST = Path(__file__).parent / "checklists" / "chat_quality_v1.json"
JOBS: dict[str, dict[str, Any]] = {}


@asynccontextmanager
async def lifespan(_app):
    db.conn()
    if not db.list_checklists():
        db.save_checklist(json.loads(DEFAULT_CHECKLIST.read_text(encoding="utf-8")), comment="Начальная конфигурация из PDF")
    yield


app = FastAPI(title="CQA — Conversation Quality Analytics", lifespan=lifespan)
# Фронтенд может жить в Dynamics 365 web resource на другом домене.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])



# ---------------- служебное ----------------

@app.get("/api/health")
def health():
    return {"ok": True, "provider": settings.provider,
            "model": settings.model if settings.provider == "anthropic" else settings.ollama_model,
            "effort": settings.effort, "votes": settings.votes, "dataset_url_configured": bool(settings.dataset_url)}


# ---------------- данные ----------------

def import_payload(payload: dict[str, Any], dataset: str | None = None) -> dict[str, Any]:
    if not payload.get("isSuccess", True):
        raise HTTPException(502, f"API организаторов вернуло ошибку: {payload.get('error')}")
    data = payload.get("data", payload)
    records = data.get("records") or []
    name = dataset or data.get("dataset_group_id") or "UPLOAD"
    for rec in records:
        if not rec.get("conversation_id") or not isinstance(rec.get("transcript"), list):
            raise HTTPException(400, f"Неверная запись: {str(rec)[:200]}")
        db.upsert_dialog(name, rec)
    return {"dataset": name, "imported": len(records), "total_records": data.get("total_records")}


@app.post("/api/datasets/fetch")
async def fetch_dataset(body: dict = Body(...)):
    group_id = str(body.get("group_id") or "").strip()
    url = (body.get("url") or settings.dataset_url or "").strip()
    if not group_id:
        raise HTTPException(400, "Укажите group_id")
    if not url:
        raise HTTPException(400, "Не задан адрес API: укажите url или CQA_DATASET_URL в .env")
    if "{group_id}" in url:
        url, params = url.replace("{group_id}", group_id), None
    else:
        params = {"group_id": group_id}
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
            r = await c.get(url, params=params)
            r.raise_for_status()
            payload = r.json()
    except (httpx.HTTPError, ValueError) as e:
        raise HTTPException(502, f"Не удалось получить набор: {e}")
    return import_payload(payload, group_id)


@app.post("/api/datasets/upload")
def upload_dataset(body: dict = Body(...)):
    return import_payload(body.get("payload", body), body.get("dataset"))


@app.get("/api/dialogs")
def list_dialogs(dataset: str | None = None):
    sql = """
    SELECT d.id, d.dataset, d.channel, d.language, d.audio_url, d.imported_at,
           json_array_length(d.transcript) AS turns,
           e.id AS evaluation_id, e.status, e.total, e.final_total, e.gross_error, e.needs_review,
           e.checklist_id, e.created_at AS evaluated_at, e.cost_usd,
           (SELECT COUNT(*) FROM appeals a WHERE a.evaluation_id = e.id AND a.status='open') AS open_appeals
    FROM dialogs d
    LEFT JOIN evaluations e ON e.id = (SELECT id FROM evaluations WHERE dialog_id=d.id ORDER BY created_at DESC, id DESC LIMIT 1)
    """
    args: tuple = ()
    if dataset:
        sql += " WHERE d.dataset=?"
        args = (dataset,)
    sql += " ORDER BY d.dataset, d.id"
    rows = db.q(sql, args)
    for r in rows:
        if r["checklist_id"]:
            cl = db.one("SELECT version, name FROM checklists WHERE id=?", (r["checklist_id"],))
            r["checklist_version"] = cl["version"] if cl else None
    datasets = [r["dataset"] for r in db.q("SELECT DISTINCT dataset FROM dialogs ORDER BY dataset")]
    return {"dialogs": rows, "datasets": datasets}


@app.get("/api/dialogs/{dialog_id}")
def get_dialog(dialog_id: str):
    d = db.get_dialog(dialog_id)
    if not d:
        raise HTTPException(404, "Диалог не найден")
    from .facts import normalize_turns
    d["turns"] = normalize_turns(d["transcript"])
    d["evaluations"] = db.q(
        """SELECT e.id, e.status, e.total, e.final_total, e.gross_error, e.model, e.cost_usd, e.created_at,
                  e.checklist_id, c.version AS checklist_version, c.name AS checklist_name
           FROM evaluations e JOIN checklists c ON c.id=e.checklist_id
           WHERE e.dialog_id=? ORDER BY e.created_at DESC, e.id DESC""", (dialog_id,))
    return d


# ---------------- чек-листы ----------------

@app.get("/api/checklists")
def checklists():
    return {"checklists": db.list_checklists()}


@app.get("/api/checklists/{cid}")
def checklist(cid: int):
    c = db.get_checklist(cid)
    if not c:
        raise HTTPException(404, "Чек-лист не найден")
    return c


@app.post("/api/checklists")
def save_checklist(body: dict = Body(...)):
    data = body.get("data") or {}
    errors = services.validate_checklist(data)
    if errors:
        raise HTTPException(422, {"errors": errors})
    cid = db.save_checklist(data, comment=body.get("comment", ""), activate=body.get("activate", True))
    return db.get_checklist(cid)


@app.post("/api/checklists/{cid}/activate")
def activate(cid: int):
    db.activate_checklist(cid)
    return {"ok": True}


# ---------------- оценка (задачи) ----------------

async def _run_job(job_id: str, dialog_ids: list[str], checklist_id: int, votes: int) -> None:
    job = JOBS[job_id]
    cl = db.get_checklist(checklist_id)
    try:
        provider = get_provider()
    except Exception as e:  # ключ не задан и т.п.
        job.update(status="error", error=str(e))
        return
    sem = asyncio.Semaphore(settings.concurrency)

    async def one(did: str):
        async with sem:
            dialog = db.get_dialog(did)
            if not dialog:
                job["failed"].append({"dialog_id": did, "error": "не найден"})
                return
            job["running"].append(did)
            try:
                result = await evaluate_dialog(dialog, cl["data"], provider, votes=votes)
            except (LLMError, Exception) as e:  # noqa: BLE001 — задача не должна падать целиком
                result = {"status": "error", "error": f"{type(e).__name__}: {e}", "meta": {}}
            finally:
                job["running"].remove(did)
            score = result.get("score") or {}
            meta = result.get("meta") or {}
            eid = db.ex(
                """INSERT INTO evaluations(dialog_id, checklist_id, status, result, total, final_total, gross_error,
                       needs_review, model, cost_usd, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (did, checklist_id, result["status"], db.dumps(result), score.get("total"), score.get("total"),
                 int(bool(score.get("gross_error"))), len(result.get("needs_review") or []),
                 meta.get("model"), meta.get("cost_usd"), db.now()))
            job["cost_usd"] = round(job["cost_usd"] + (meta.get("cost_usd") or 0), 5)
            if result["status"] == "done":
                job["done"].append({"dialog_id": did, "evaluation_id": eid, "total": score.get("total")})
            else:
                job["failed"].append({"dialog_id": did, "evaluation_id": eid, "error": result.get("error")})

    await asyncio.gather(*[one(d) for d in dialog_ids])
    job["status"] = "finished"
    job["finished_at"] = db.now()


@app.post("/api/jobs")
async def create_job(body: dict = Body(...)):
    checklist_id = body.get("checklist_id") or (db.active_checklist() or {}).get("id")
    if not checklist_id or not db.get_checklist(int(checklist_id)):
        raise HTTPException(400, "Чек-лист не найден")
    dialog_ids = body.get("dialog_ids") or []
    if body.get("dataset"):
        dialog_ids = [r["id"] for r in db.q("SELECT id FROM dialogs WHERE dataset=? ORDER BY id", (body["dataset"],))]
        if body.get("only_missing"):
            done = {r["dialog_id"] for r in db.q(
                "SELECT DISTINCT dialog_id FROM evaluations WHERE checklist_id=? AND status='done'", (checklist_id,))}
            dialog_ids = [d for d in dialog_ids if d not in done]
    if not dialog_ids:
        raise HTTPException(400, "Нет диалогов для оценки")
    votes = int(body.get("votes") or settings.votes)
    job_id = uuid.uuid4().hex[:10]
    JOBS[job_id] = {"id": job_id, "status": "running", "total": len(dialog_ids), "done": [], "failed": [],
                    "running": [], "checklist_id": int(checklist_id), "votes": votes, "cost_usd": 0.0,
                    "started_at": db.now()}
    asyncio.create_task(_run_job(job_id, dialog_ids, int(checklist_id), votes))
    return JOBS[job_id]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(404, "Задача не найдена")
    return JOBS[job_id]


@app.get("/api/jobs")
def list_jobs():
    return {"jobs": sorted(JOBS.values(), key=lambda j: j["started_at"], reverse=True)[:20]}


@app.get("/api/evaluations/{eid}")
def get_evaluation(eid: int):
    ev = services.load_evaluation(eid)
    if not ev:
        raise HTTPException(404, "Оценка не найдена")
    ev["checklist"] = db.get_checklist(ev["checklist_id"])
    ev["view"] = services.final_view(ev)
    return ev


@app.get("/api/export", response_class=PlainTextResponse)
def export_csv(dataset: str | None = None, checklist_id: int | None = None):
    """CSV последних оценок: по строке на диалог, ответ по каждому критерию."""
    cl = db.get_checklist(checklist_id) if checklist_id else db.active_checklist()
    if not cl:
        raise HTTPException(400, "Чек-лист не найден")
    crit_ids = [c["id"] for c in cl["data"]["criteria"]]
    rows = db.q("SELECT id FROM dialogs" + (" WHERE dataset=?" if dataset else "") + " ORDER BY id",
                (dataset,) if dataset else ())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["conversation_id", "total", "final_total", "gross_error"] + crit_ids)
    for r in rows:
        ev = db.one("SELECT * FROM evaluations WHERE dialog_id=? AND checklist_id=? AND status='done' "
                    "ORDER BY created_at DESC, id DESC LIMIT 1", (r["id"], cl["id"]))
        if not ev:
            continue
        view = services.final_view(services.load_evaluation(ev["id"]))
        ans = view["final"]["answers"]
        w.writerow([r["id"], ev["total"], view["final"]["score"]["total"], int(view["final"]["score"]["gross_error"])]
                   + [ans.get(c, "") for c in crit_ids])
    return buf.getvalue()


# ---------------- апелляции ----------------

@app.post("/api/evaluations/{eid}/appeals")
def create_appeal(eid: int, body: dict = Body(...)):
    ev = services.load_evaluation(eid)
    if not ev or ev["result"].get("status") != "done":
        raise HTTPException(404, "Оценка не найдена")
    cid = body.get("criterion_id")
    comment = (body.get("comment") or "").strip()
    crit = next((c for c in ev["result"]["criteria"] if c["criterion_id"] == cid), None)
    if not crit:
        raise HTTPException(400, "Критерий не найден в оценке")
    if not comment:
        raise HTTPException(400, "Добавьте комментарий")
    if db.one("SELECT id FROM appeals WHERE evaluation_id=? AND criterion_id=? AND status='open'", (eid, cid)):
        raise HTTPException(409, "По этому критерию уже есть открытая апелляция")
    aid = db.ex("INSERT INTO appeals(evaluation_id, criterion_id, comment, author, status, original, created_at) "
                "VALUES(?,?,?,?,?,?,?)", (eid, cid, comment, body.get("author") or "Оператор", "open",
                                          db.dumps(crit), db.now()))
    return db.one("SELECT * FROM appeals WHERE id=?", (aid,))


@app.get("/api/appeals")
def list_appeals(status: str | None = None):
    sql = """SELECT a.*, e.dialog_id, e.checklist_id, e.total FROM appeals a JOIN evaluations e ON e.id=a.evaluation_id"""
    args: tuple = ()
    if status:
        sql += " WHERE a.status=?"
        args = (status,)
    sql += " ORDER BY a.status='open' DESC, a.created_at DESC"
    rows = db.q(sql, args)
    for r in rows:
        r["original"] = json.loads(r["original"])
        r["decision"] = json.loads(r["decision"]) if r["decision"] else None
    return {"appeals": rows}


@app.post("/api/appeals/{aid}/decision")
def decide(aid: int, body: dict = Body(...)):
    ap = db.one("SELECT * FROM appeals WHERE id=?", (aid,))
    if not ap:
        raise HTTPException(404, "Апелляция не найдена")
    if ap["status"] != "open":
        raise HTTPException(409, "Апелляция уже рассмотрена")
    action = body.get("action")
    reason = (body.get("reason") or "").strip()
    if action not in ("confirm", "change"):
        raise HTTPException(400, "action: confirm или change")
    if not reason:
        raise HTTPException(400, "Укажите причину решения")
    ev = services.load_evaluation(ap["evaluation_id"])
    before = services.final_view(ev)["final"]["score"]["total"]
    decision: dict[str, Any] = {"action": action, "reason": reason, "reviewer": body.get("reviewer") or "Проверяющий"}
    if action == "change":
        new_answer = str(body.get("new_answer"))
        err = services.check_decision(db.get_checklist(ev["checklist_id"])["data"], ap["criterion_id"], new_answer)
        if err:
            raise HTTPException(400, err)
        decision["new_answer"] = new_answer
    decision["old_answer"] = json.loads(ap["original"])["answer"]
    db.ex("UPDATE appeals SET status='resolved', decision=?, decided_at=? WHERE id=?", (db.dumps(decision), db.now(), aid))
    services.refresh_final_total(ev["id"])
    after = services.final_view(services.load_evaluation(ev["id"]))["final"]["score"]["total"]
    return {"ok": True, "total_before": before, "total_after": after}


# ---------------- эталон и точность ----------------

@app.get("/api/gold/{dialog_id}")
def get_gold(dialog_id: str, slug: str):
    r = db.one("SELECT * FROM gold_labels WHERE dialog_id=? AND checklist_slug=?", (dialog_id, slug))
    return {"labels": json.loads(r["labels"]) if r else {}}


@app.put("/api/gold/{dialog_id}")
def put_gold(dialog_id: str, body: dict = Body(...)):
    slug = body.get("slug")
    labels = {k: str(v) for k, v in (body.get("labels") or {}).items() if v not in (None, "")}
    db.ex("""INSERT INTO gold_labels(dialog_id, checklist_slug, labels, updated_at) VALUES(?,?,?,?)
             ON CONFLICT(dialog_id, checklist_slug) DO UPDATE SET labels=excluded.labels, updated_at=excluded.updated_at""",
          (dialog_id, slug, db.dumps(labels), db.now()))
    return {"ok": True, "labels": labels}


@app.get("/api/accuracy")
def get_accuracy(checklist_id: int | None = None):
    cid = checklist_id or (db.active_checklist() or {}).get("id")
    return services.accuracy(int(cid)) if cid else {"error": "Нет чек-листа"}


# ---------------- фронтенд ----------------

app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
