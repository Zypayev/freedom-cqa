"""Выгрузка результатов в формат организаторов (CQA Student Guide Appendix, JSON 1.0).

    python -m app.export_results --dataset DEV --out ../results/dev/results.json
    python -m app.export_results --dataset <group_id> --out ../results/prod/results.json

Коды критериев берутся из поля export_id чек-листа (в коде критериев нет).
Номер реплики = порядковый номер в исходном массиве транскрипта, с 1 (t05 → 5).
Берётся исходная оценка системы (без решений по апелляциям).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from . import db
from .config import settings
from .prompt import applicable_criteria
from .scoring import NA

FILE_CHECKLIST = Path(__file__).parent / "checklists" / "chat_quality_v1.json"


def _export_ids(checklist: dict[str, Any]) -> dict[str, str]:
    ids = {c["id"]: c.get("export_id") for c in checklist["criteria"]}
    try:  # запасной источник кодов — исходный файл чек-листа
        base = json.loads(FILE_CHECKLIST.read_text(encoding="utf-8"))
        for c in base["criteria"]:
            if not ids.get(c["id"]) and c.get("export_id"):
                ids[c["id"]] = c["export_id"]
    except (OSError, ValueError):
        pass
    return {k: v or k for k, v in ids.items()}


def _value(answer: str, ctype: str) -> Any:
    if answer == NA:
        return None
    if ctype == "scale":
        return int(answer)
    return answer == "yes"


def _turn_no(tid: str) -> int | None:
    try:
        return int(str(tid).lstrip("t"))
    except ValueError:
        return None


def _pick_evaluation(dialog_id: str, checklist_id: int | None) -> dict[str, Any] | None:
    sql = "SELECT * FROM evaluations WHERE dialog_id=? {} ORDER BY (status='done') DESC, id DESC LIMIT 1"
    if checklist_id:
        r = db.one(sql.format("AND checklist_id=?"), (dialog_id, checklist_id))
        if r:
            return r
    return db.one(sql.format(""), (dialog_id,))


def build(dataset: str, checklist_id: int | None = None, team: str | None = None) -> dict[str, Any]:
    checklist_id = checklist_id or (db.active_checklist() or {}).get("id")
    dialogs = db.q("SELECT id, channel FROM dialogs WHERE dataset=? ORDER BY id", (dataset,))
    out = []
    models = set()
    for d in dialogs:
        ev = _pick_evaluation(d["id"], checklist_id)
        if not ev:
            out.append({"conversation_id": d["id"], "status": "error", "criteria": [], "score": None,
                        "error": "Диалог не был оценён системой."})
            continue
        res = json.loads(ev["result"])
        if ev["status"] != "done" or res.get("status") != "done":
            out.append({"conversation_id": d["id"], "status": "error", "criteria": [], "score": None,
                        "error": res.get("error") or "Не удалось получить ответ модели."})
            continue
        if ev.get("model"):
            models.add(ev["model"])
        cl = db.get_checklist(ev["checklist_id"])["data"]
        codes = _export_ids(cl)
        by_id = {c["criterion_id"]: c for c in res["criteria"]}
        transcript = (db.get_dialog(d["id"]) or {}).get("transcript") or []
        op_turns = [i + 1 for i, t in enumerate(transcript) if (t.get("speaker") or "").lower() == "operator"]
        crit = []
        for c in applicable_criteria(cl, d["channel"]):
            r = by_id[c["id"]]
            turns = [n for n in (_turn_no(t) for t in r.get("evidence_turn_ids") or []) if n]
            if not turns and r.get("expected_at_turn_id"):
                turns = [n for n in [_turn_no(r["expected_at_turn_id"])] if n]
            if not turns:  # проверенный участок checked_from..checked_to
                lo, hi = [_turn_no(x) if x else None for x in (r.get("checked_range") or [None, None])]
                if lo and hi and hi >= lo:
                    turns = list(range(lo, hi + 1))
                elif lo or hi:
                    turns = [lo or hi]
            if not turns:  # последний запасной вариант: все реплики оператора (проверенный участок)
                turns = op_turns
            rationale = r.get("explanation") or ""
            if r["answer"] == NA and r.get("na_reason") and r["na_reason"] not in rationale:
                rationale = f"{rationale} N/A: {r['na_reason']}".strip()
            crit.append({"criterion_id": codes[c["id"]], "value": _value(r["answer"], c["type"]),
                         "rationale": rationale, "evidence_turns": turns})
        score = res.get("score") or {}
        out.append({"conversation_id": d["id"], "status": "ok", "criteria": crit,
                    "score": {"final_score": score.get("total"), "critical_flag": bool(score.get("gross_error"))}})
    return {
        "schema_version": "1.0",
        "team_name": team or os.getenv("CQA_TEAM_NAME") or "freedom-cqa",
        "model": {"mode": "local" if settings.provider == "ollama" else "external_api",
                  "name": ", ".join(sorted(models)) or settings.ollama_model},
        "results": out,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True, help="набор (group_id), например DEV")
    ap.add_argument("--out", required=True, help="куда записать results.json")
    ap.add_argument("--checklist-id", type=int, help="версия чек-листа (по умолчанию активная)")
    ap.add_argument("--team", help="название команды (или CQA_TEAM_NAME в .env)")
    a = ap.parse_args()
    data = build(a.dataset, a.checklist_id, a.team)
    path = Path(a.out)
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    rs = data["results"]
    ok = sum(r["status"] == "ok" for r in rs)
    msg = f"{path}: {len(rs)} records, ok={ok}, error={len(rs) - ok}, team={data['team_name']}, model={data['model']}"
    print(msg.encode("ascii", "replace").decode())


if __name__ == "__main__":
    main()
