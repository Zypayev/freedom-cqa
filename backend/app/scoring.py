"""Детерминированный расчёт баллов по правилам чек-листа.

Модель возвращает только ответы по критериям; всё остальное считает этот модуль:
1. применимость (N/A только там, где разрешён);
2. «Да» = полный вес, «Нет» = 0, шкала = ступень / верхняя ступень × вес;
3. N/A исключается, вес пропорционально перераспределяется (максимум остаётся 100);
4. итог округляется до `rounding` знаков (половина вверх); нет применимых — итога нет;
5. грубые ошибки (type=flag) отмечаются отдельно и в баллы не входят.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

NA = "na"


class ScoringError(ValueError):
    pass


def round_half_up(value: float, digits: int) -> float:
    q = Decimal(1).scaleb(-digits)
    return float(Decimal(str(value)).quantize(q, rounding=ROUND_HALF_UP))


def allowed_answers(criterion: dict[str, Any]) -> list[str]:
    """Допустимые значения ответа для критерия (строки)."""
    ctype = criterion["type"]
    if ctype in ("binary", "flag"):
        values = ["yes", "no"]
    elif ctype == "scale":
        values = [str(step["value"]) for step in sorted(criterion["scale"], key=lambda s: -s["value"])]
    else:
        raise ScoringError(f"Неизвестный тип критерия: {ctype}")
    if criterion.get("na_allowed"):
        values.append(NA)
    return values


def criterion_ratio(criterion: dict[str, Any], answer: str) -> float:
    """Доля от веса критерия, которую даёт ответ (0..1)."""
    ctype = criterion["type"]
    if ctype == "binary":
        return 1.0 if answer == "yes" else 0.0
    if ctype == "scale":
        top = max(step["value"] for step in criterion["scale"])
        if top <= 0:
            raise ScoringError(f"У шкалы критерия {criterion['id']} верхняя ступень должна быть > 0")
        return float(answer) / top
    raise ScoringError(f"Критерий {criterion['id']} не даёт баллов")


def compute(checklist: dict[str, Any], answers: dict[str, str]) -> dict[str, Any]:
    """Считает итог.

    answers: {criterion_id: "yes" | "no" | "na" | "<ступень шкалы>"}.
    Возвращает разбивку по критериям, секциям, итог и флаг грубых ошибок.
    """
    rounding = int(checklist.get("rounding", 2))
    criteria = checklist["criteria"]

    for c in criteria:
        a = answers.get(c["id"])
        if a is None:
            raise ScoringError(f"Нет ответа по критерию {c['id']}")
        if a not in allowed_answers(c):
            raise ScoringError(f"Недопустимый ответ «{a}» для критерия {c['id']}")

    scored = [c for c in criteria if c["type"] != "flag" and c.get("weight", 0) > 0]
    applicable = [c for c in scored if answers[c["id"]] != NA]
    applicable_weight = sum(c["weight"] for c in applicable)
    total_weight = sum(c["weight"] for c in scored)
    factor = (total_weight / applicable_weight) if applicable_weight else 0.0
    # Нормируем на 100, если веса в чек-листе в сумме дают не 100.
    scale_to_100 = 100.0 / total_weight if total_weight else 0.0

    per_criterion: dict[str, dict[str, Any]] = {}
    raw_sum = 0.0
    for c in criteria:
        a = answers[c["id"]]
        entry: dict[str, Any] = {"criterion_id": c["id"], "answer": a, "weight": c.get("weight", 0)}
        if c["type"] == "flag":
            entry.update(points=None, max_points=None, effective_weight=None, applicable=None)
        elif a == NA or c.get("weight", 0) <= 0:
            entry.update(points=None, max_points=0.0, effective_weight=0.0, applicable=False)
        else:
            ratio = criterion_ratio(c, a)
            eff_w = c["weight"] * factor * scale_to_100
            pts = ratio * eff_w
            raw_sum += ratio * c["weight"]
            entry.update(
                points=round_half_up(pts, rounding),
                max_points=round_half_up(eff_w, rounding),
                effective_weight=round_half_up(eff_w, rounding),
                applicable=True,
                base_points=ratio * c["weight"],
            )
        per_criterion[c["id"]] = entry

    total = round_half_up(raw_sum / applicable_weight * 100, rounding) if applicable_weight else None

    sections = []
    for s in checklist.get("sections", []):
        members = [c for c in criteria if c.get("section") == s["id"]]
        pts_members = [per_criterion[c["id"]] for c in members if per_criterion[c["id"]]["applicable"]]
        flags = [c for c in members if c["type"] == "flag"]
        sec: dict[str, Any] = {"id": s["id"], "title": s["title"]}
        if pts_members:
            earned = sum(e["base_points"] for e in pts_members) * factor * scale_to_100
            maximum = sum(e["weight"] for e in pts_members) * factor * scale_to_100
            sec.update(points=round_half_up(earned, rounding), max_points=round_half_up(maximum, rounding),
                       percent=round_half_up(earned / maximum * 100, rounding) if maximum else None)
        elif flags:
            sec.update(points=None, max_points=None, percent=None,
                       flagged=any(answers[c["id"]] == "yes" for c in flags))
        else:
            sec.update(points=None, max_points=None, percent=None)
        sections.append(sec)

    for e in per_criterion.values():
        e.pop("base_points", None)

    gross = [c["id"] for c in criteria if c["type"] == "flag" and answers[c["id"]] == "yes"]
    return {
        "total": total,
        "max_total": 100.0 if applicable_weight else None,
        "applicable_weight": applicable_weight,
        "criteria": per_criterion,
        "sections": sections,
        "gross_error": bool(gross),
        "gross_error_criteria": gross,
    }
