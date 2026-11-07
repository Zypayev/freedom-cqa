"""Оценка одного диалога: факты → промпт → LLM (N прогонов) → проверка → голосование → баллы."""
from __future__ import annotations

import asyncio
import copy
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from . import prompt as P
from .facts import compute_facts, normalize_turns
from .llm import LLMError, LLMResult, Provider
from .scoring import NA, allowed_answers, compute

MAX_ATTEMPTS = 2  # исходный вызов + одна попытка исправления
CONF_RANK = {"high": 3, "medium": 2, "low": 1}


def validate_run(parsed: dict[str, Any], criteria: list[dict[str, Any]], turns: list[dict[str, Any]]) -> list[str]:
    """Проверки, которые не покрывает JSON-схема. Возвращает список проблем."""
    speakers = {t["id"]: t["speaker"] for t in turns}
    issues = []
    for c in criteria:
        r = parsed.get(c["id"])
        if not isinstance(r, dict):
            issues.append(f"{c['id']}: нет результата")
            continue
        ans = r.get("answer")
        if ans not in allowed_answers(c):
            issues.append(f"{c['id']}: недопустимый ответ «{ans}», допустимы {allowed_answers(c)}")
            continue
        ev = [e for e in r.get("evidence_turn_ids", []) if e in speakers]
        if ans == NA:
            if not (r.get("na_reason") or "").strip():
                issues.append(f"{c['id']}: N/A без na_reason")
            continue
        if c["type"] == "flag" and ans == "no":
            continue  # отсутствие нарушения доказывается проверенным участком
        if not ev and not r.get("expected_at_turn_id"):
            issues.append(f"{c['id']}: нет ни доказательства, ни места, где действие ожидалось")
        if ans == "yes" and c["type"] == "binary" and ev and all(speakers[e] in ("client", "bot") for e in ev):
            issues.append(f"{c['id']}: «Да» подтверждено только репликами клиента/бота — укажите реплику оператора или системы")
    return issues


async def run_once(provider: Provider, system: str, dialog: dict[str, Any], turns, facts, schema,
                   criteria) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Один прогон с одной попыткой исправления. Возвращает (parsed, лог вызовов)."""
    log = []
    correction = None
    parsed = None
    for attempt in range(MAX_ATTEMPTS):
        user = P.build_user_prompt(dialog, turns, facts, correction)
        entry: dict[str, Any] = {"attempt": attempt + 1, "started_at": now_iso()}
        try:
            res: LLMResult = await provider.complete(system, user, schema)
            entry.update(model=res.model, provider=res.provider, usage=res.usage, cost_usd=res.cost_usd,
                         duration_sec=res.duration_sec, stop_reason=res.stop_reason, raw=res.text)
            parsed = res.parsed()
        except LLMError as e:
            entry["error"] = str(e)
            log.append(entry)
            if attempt + 1 >= MAX_ATTEMPTS:
                return None, log
            correction = f"Ошибка: {e}"
            continue
        issues = validate_run(parsed, criteria, turns)
        entry["issues"] = issues
        log.append(entry)
        if not issues:
            return parsed, log
        correction = "\n".join(f"- {i}" for i in issues)
    if parsed is not None and not is_usable(parsed, criteria):
        return None, log
    return parsed, log  # последняя попытка, даже с мягкими замечаниями


def is_usable(parsed: dict[str, Any], criteria: list[dict[str, Any]]) -> bool:
    return all(isinstance(parsed.get(c["id"]), dict) and parsed[c["id"]].get("answer") in allowed_answers(c)
               for c in criteria)


def aggregate(runs: list[dict[str, Any]], criteria: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Голосование большинством по каждому критерию."""
    out = {}
    n = len(runs)
    for c in criteria:
        results = [r[c["id"]] for r in runs if c["id"] in r]
        votes = Counter(r["answer"] for r in results)
        top = votes.most_common()
        best_count = top[0][1]
        tied = [a for a, cnt in top if cnt == best_count]
        if len(tied) > 1:
            if c["type"] == "scale" and NA not in tied:
                vals = sorted(int(a) for a in tied)
                winner = str(vals[(len(vals) - 1) // 2])  # нижняя медиана
            else:
                # при ничьей — ответ прогона с наибольшей уверенностью
                winner = max((r for r in results if r["answer"] in tied),
                             key=lambda r: CONF_RANK.get(r.get("confidence"), 0))["answer"]
        else:
            winner = tied[0]
        chosen = max((r for r in results if r["answer"] == winner), key=lambda r: CONF_RANK.get(r.get("confidence"), 0))
        agreement = votes[winner] / n if n else 1.0
        confidence = chosen.get("confidence", "medium")
        if n > 1 and agreement < 1:
            confidence = "low" if agreement <= 0.5 else "medium"
        out[c["id"]] = {**chosen, "answer": winner, "votes": dict(votes), "agreement": round(agreement, 2),
                        "confidence": confidence}
    return out


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def scoring_checklist(checklist: dict[str, Any], channel: str | None) -> dict[str, Any]:
    cl = copy.deepcopy(checklist)
    cl["criteria"] = P.applicable_criteria(checklist, channel)
    return cl


async def evaluate_dialog(dialog: dict[str, Any], checklist: dict[str, Any], provider: Provider,
                          votes: int = 1) -> dict[str, Any]:
    channel = dialog.get("channel")
    turns = normalize_turns(dialog["transcript"])
    facts = compute_facts(dialog, turns, checklist)
    criteria = P.applicable_criteria(checklist, channel)
    system = P.build_system_prompt(checklist, channel)
    schema = P.build_schema(checklist, channel, turns)

    started = now_iso()
    outcomes = await asyncio.gather(*[
        run_once(provider, system, dialog, turns, facts, schema, criteria) for _ in range(max(1, votes))
    ])
    runs = [p for p, _ in outcomes if p is not None]
    calls = [entry for _, log in outcomes for entry in log]
    usage_total: dict[str, int] = {}
    cost = 0.0
    for e in calls:
        for k, v in (e.get("usage") or {}).items():
            usage_total[k] = usage_total.get(k, 0) + int(v or 0)
        cost += e.get("cost_usd") or 0.0
    meta = {
        "provider": provider.name, "model": provider.model, "votes": votes, "started_at": started,
        "finished_at": now_iso(), "calls": len(calls), "usage": usage_total, "cost_usd": round(cost, 5),
    }
    if not runs:
        return {"status": "error", "error": calls[-1].get("error") if calls else "нет ответа", "facts": facts,
                "llm_log": calls, "meta": meta}

    agg = aggregate(runs, criteria)
    answers = {cid: r["answer"] for cid, r in agg.items()}
    score = compute(scoring_checklist(checklist, channel), answers)
    criteria_results = []
    for c in criteria:
        r = agg[c["id"]]
        criteria_results.append({
            "criterion_id": c["id"],
            "answer": r["answer"],
            "explanation": r.get("explanation", ""),
            "analysis": r.get("analysis", ""),
            "evidence_turn_ids": [e for e in r.get("evidence_turn_ids", []) if any(t["id"] == e for t in turns)],
            "expected_at_turn_id": r.get("expected_at_turn_id") or None,
            "checked_range": [r.get("checked_from") or None, r.get("checked_to") or None],
            "na_reason": r.get("na_reason") or None,
            "confidence": r["confidence"],
            "agreement": r["agreement"],
            "votes": r["votes"],
        })
    needs_review = [c["criterion_id"] for c in criteria_results if c["confidence"] == "low"]
    summary = next((r.get("summary") for r in runs if r.get("summary")), "")
    return {
        "status": "done",
        "summary": summary,
        "criteria": criteria_results,
        "score": score,
        "needs_review": needs_review,
        "facts": facts,
        "turn_ids": [t["id"] for t in turns],
        "llm_log": calls,
        "meta": meta,
    }
