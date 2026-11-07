"""Бизнес-логика поверх БД: валидация чек-листа, итог с учётом апелляций, точность относительно эталона."""
from __future__ import annotations

import json
import re
from typing import Any

from . import db
from .evaluator import scoring_checklist
from .scoring import NA, allowed_answers, compute

TYPES = {"binary", "scale", "flag"}


def validate_checklist(data: dict[str, Any]) -> list[str]:
    errors = []
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,63}", str(data.get("slug", ""))):
        errors.append("slug: латиница, цифры, - и _ (2–64 символа)")
    if not str(data.get("name", "")).strip():
        errors.append("name: укажите название")
    section_ids = {s.get("id") for s in data.get("sections", [])}
    crits = data.get("criteria") or []
    if not crits:
        errors.append("criteria: нужен хотя бы один критерий")
    seen = set()
    for i, c in enumerate(crits, 1):
        p = f"Критерий {i}"
        cid = c.get("id", "")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", str(cid)):
            errors.append(f"{p}: id — латиница в нижнем регистре, цифры, _")
        if cid in seen:
            errors.append(f"{p}: id «{cid}» повторяется")
        seen.add(cid)
        if not str(c.get("title", "")).strip():
            errors.append(f"{p}: нет названия")
        if c.get("type") not in TYPES:
            errors.append(f"{p}: тип должен быть binary, scale или flag")
        w = c.get("weight", 0)
        if not isinstance(w, (int, float)) or w < 0:
            errors.append(f"{p}: вес должен быть числом ≥ 0")
        if c.get("type") == "scale":
            steps = c.get("scale") or []
            vals = [s.get("value") for s in steps]
            if len(steps) < 2 or not all(isinstance(v, (int, float)) for v in vals):
                errors.append(f"{p}: у шкалы нужно минимум 2 числовые ступени")
            elif len(set(vals)) != len(vals) or max(vals) <= 0:
                errors.append(f"{p}: ступени шкалы уникальны, верхняя > 0")
        if c.get("na_allowed") and not str(c.get("na_condition", "")).strip():
            errors.append(f"{p}: при разрешённом N/A опишите условие")
        if c.get("section") and section_ids and c["section"] not in section_ids:
            errors.append(f"{p}: секция «{c['section']}» не найдена")
        instr = c.get("instructions")
        text = instr if isinstance(instr, str) else (instr or {}).get("default", "")
        if not str(text).strip():
            errors.append(f"{p}: нужна формулировка для оценщика (instructions.default)")
    if sum(c.get("weight", 0) for c in crits if c.get("type") != "flag") <= 0:
        errors.append("Сумма весов критериев должна быть > 0")
    return errors


def load_evaluation(eid: int) -> dict[str, Any] | None:
    row = db.one("SELECT * FROM evaluations WHERE id=?", (eid,))
    if not row:
        return None
    row["result"] = json.loads(row["result"])
    return row


def appeals_for(eid: int) -> list[dict[str, Any]]:
    rows = db.q("SELECT * FROM appeals WHERE evaluation_id=? ORDER BY created_at", (eid,))
    for r in rows:
        r["original"] = json.loads(r["original"])
        r["decision"] = json.loads(r["decision"]) if r["decision"] else None
    return rows


def final_view(evaluation: dict[str, Any]) -> dict[str, Any]:
    """Исходная оценка + решения по апелляциям → финальный результат (пересчитан кодом)."""
    result = evaluation["result"]
    checklist = db.get_checklist(evaluation["checklist_id"])["data"]
    dialog = db.get_dialog(evaluation["dialog_id"])
    appeals = appeals_for(evaluation["id"])
    if result.get("status") != "done":
        return {"original": result, "appeals": appeals, "final": None}
    answers = {c["criterion_id"]: c["answer"] for c in result["criteria"]}
    overrides = {}
    for a in appeals:
        if a["status"] == "resolved" and a["decision"] and a["decision"].get("action") == "change":
            overrides[a["criterion_id"]] = a["decision"]["new_answer"]
    final_answers = {**answers, **overrides}
    final_score = compute(scoring_checklist(checklist, dialog.get("channel")), final_answers)
    return {
        "original": result,
        "appeals": appeals,
        "final": {"answers": final_answers, "overrides": overrides, "score": final_score},
    }


def refresh_final_total(eid: int) -> None:
    ev = load_evaluation(eid)
    view = final_view(ev)
    if view["final"]:
        db.ex("UPDATE evaluations SET final_total=?, gross_error=? WHERE id=?",
              (view["final"]["score"]["total"], int(view["final"]["score"]["gross_error"]), eid))


def check_decision(checklist: dict[str, Any], criterion_id: str, new_answer: str) -> str | None:
    c = next((c for c in checklist["criteria"] if c["id"] == criterion_id), None)
    if not c:
        return "Критерий не найден в чек-листе оценки"
    if new_answer not in allowed_answers(c):
        return f"Недопустимый ответ. Допустимы: {', '.join(allowed_answers(c))}"
    return None


def accuracy(checklist_id: int) -> dict[str, Any]:
    """Сравнение последних оценок данной версии чек-листа с эталонной разметкой."""
    cl = db.get_checklist(checklist_id)
    if not cl:
        return {"error": "Чек-лист не найден"}
    data = cl["data"]
    golds = db.q("SELECT * FROM gold_labels WHERE checklist_slug=?", (data["slug"],))
    per_crit: dict[str, dict[str, Any]] = {c["id"]: {"criterion_id": c["id"], "title": c["title"], "n": 0, "match": 0,
                                                      "na_errors": 0, "abs_err": 0.0, "errors": []}
                                           for c in data["criteria"]}
    dialogs_out = []
    total_abs = []
    for g in golds:
        labels = json.loads(g["labels"])
        ev = db.one("SELECT * FROM evaluations WHERE dialog_id=? AND checklist_id=? AND status='done' "
                    "ORDER BY created_at DESC LIMIT 1", (g["dialog_id"], checklist_id))
        if not ev:
            continue
        res = json.loads(ev["result"])
        pred = {c["criterion_id"]: c["answer"] for c in res["criteria"]}
        dialog = db.get_dialog(g["dialog_id"])
        crits = scoring_checklist(data, dialog.get("channel") if dialog else None)
        gold_complete = all(c["id"] in labels for c in crits["criteria"])
        gold_total = None
        if gold_complete:
            try:
                gold_total = compute(crits, {c["id"]: labels[c["id"]] for c in crits["criteria"]})["total"]
            except Exception:
                gold_total = None
        for c in data["criteria"]:
            if c["id"] not in labels or c["id"] not in pred:
                continue
            pc = per_crit[c["id"]]
            pc["n"] += 1
            gl, pr = labels[c["id"]], pred[c["id"]]
            if gl == pr:
                pc["match"] += 1
            else:
                pc["errors"].append({"dialog_id": g["dialog_id"], "gold": gl, "pred": pr})
                if NA in (gl, pr):
                    pc["na_errors"] += 1
            if c["type"] == "scale" and NA not in (gl, pr):
                pc["abs_err"] += abs(float(gl) - float(pr))
        if gold_total is not None and ev["total"] is not None:
            total_abs.append(abs(gold_total - ev["total"]))
        dialogs_out.append({"dialog_id": g["dialog_id"], "gold_total": gold_total, "pred_total": ev["total"]})
    crit_list = []
    for pc in per_crit.values():
        pc["accuracy"] = round(pc["match"] / pc["n"], 3) if pc["n"] else None
        crit_list.append(pc)
    n_all = sum(p["n"] for p in crit_list)
    return {
        "checklist_id": checklist_id,
        "dialogs": dialogs_out,
        "criteria": crit_list,
        "overall_accuracy": round(sum(p["match"] for p in crit_list) / n_all, 3) if n_all else None,
        "total_mae": round(sum(total_abs) / len(total_abs), 2) if total_abs else None,
        "labeled_dialogs": len(dialogs_out),
    }
