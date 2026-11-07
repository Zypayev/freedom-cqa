import json
from pathlib import Path

import pytest

from app.scoring import ScoringError, allowed_answers, compute

CHECKLIST = json.loads(
    (Path(__file__).resolve().parent.parent / "app" / "checklists" / "chat_quality_v1.json").read_text(encoding="utf-8")
)


def answers(**over):
    base = {
        "greeting": "yes", "needs": "yes", "identification": "yes", "consultation": "20",
        "empathy": "yes", "dialog_management": "10", "closing": "yes", "gross_errors": "no",
    }
    base.update(over)
    return base


def test_perfect_dialog_is_100():
    r = compute(CHECKLIST, answers())
    assert r["total"] == 100.0
    assert r["gross_error"] is False


def test_na_redistributes_weight_example_from_readme():
    # Пример: 2 и 3 N/A, 4 = 15/20, 6 = 7/10, 7 = Нет → 50.5 / 65 × 100 = 77.69
    r = compute(CHECKLIST, answers(needs="na", identification="na", consultation="15",
                                   dialog_management="7", closing="no"))
    assert r["applicable_weight"] == 65
    assert r["total"] == 77.69
    # Перераспределённый вес: 10 / 65 × 100 = 15.38
    assert r["criteria"]["greeting"]["effective_weight"] == 15.38
    assert r["criteria"]["needs"]["applicable"] is False


def test_scale_top_step_gives_full_weight():
    r = compute(CHECKLIST, answers(consultation="20"))
    assert r["criteria"]["consultation"]["points"] == 20.0
    r = compute(CHECKLIST, answers(dialog_management="4"))
    assert r["criteria"]["dialog_management"]["points"] == 6.0


def test_gross_error_does_not_change_total():
    r = compute(CHECKLIST, answers(gross_errors="yes"))
    assert r["total"] == 100.0
    assert r["gross_error"] is True
    assert r["gross_error_criteria"] == ["gross_errors"]


def test_na_not_allowed_is_rejected():
    with pytest.raises(ScoringError):
        compute(CHECKLIST, answers(greeting="na"))


def test_invalid_scale_step_rejected():
    with pytest.raises(ScoringError):
        compute(CHECKLIST, answers(consultation="12"))


def test_no_applicable_criteria_gives_no_total():
    cl = {"criteria": [
        {"id": "a", "type": "binary", "weight": 50, "na_allowed": True},
        {"id": "b", "type": "binary", "weight": 50, "na_allowed": True},
    ], "sections": []}
    r = compute(cl, {"a": "na", "b": "na"})
    assert r["total"] is None


def test_sections():
    r = compute(CHECKLIST, answers(needs="no"))
    start = next(s for s in r["sections"] if s["id"] == "start")
    assert start["points"] == 10.0 and start["max_points"] == 25.0
    gross = next(s for s in r["sections"] if s["id"] == "gross")
    assert gross["flagged"] is False


def test_allowed_answers():
    assert allowed_answers(CHECKLIST["criteria"][3]) == ["20", "15", "10", "5", "0", "na"]
    assert allowed_answers(CHECKLIST["criteria"][0]) == ["yes", "no"]


def test_half_up_rounding():
    cl = {"rounding": 2, "sections": [], "criteria": [
        {"id": "a", "type": "binary", "weight": 1, "na_allowed": False},
        {"id": "b", "type": "binary", "weight": 2, "na_allowed": False},
    ]}
    # 1/3*100 = 33.333.. → 33.33
    assert compute(cl, {"a": "yes", "b": "no"})["total"] == 33.33
