"""Тесты фактов, промпта и оркестрации оценщика с подставным провайдером (без сети)."""
import asyncio
import json
from pathlib import Path

from app import prompt as P
from app.evaluator import aggregate, evaluate_dialog, validate_run
from app.facts import compute_facts, detect_language, normalize_turns
from app.llm import LLMResult

ROOT = Path(__file__).resolve().parent.parent.parent
CHECKLIST = json.loads((ROOT / "backend/app/checklists/chat_quality_v1.json").read_text(encoding="utf-8"))
RECORDS = json.loads((ROOT / "data/sample_dataset.json").read_text(encoding="utf-8"))["data"]["records"]


def rec(cid):
    return next(r for r in RECORDS if r["conversation_id"] == cid)


def test_language_detection():
    assert detect_language("Сәлеметсіз бе! Қандай құжаттар керек?") == "KZ"
    assert detect_language("Здравствуйте, меня зовут Айгерим") == "RU"
    assert detect_language("Good morning, my name is Daniel") == "EN"


def test_turn_ids_and_offsets_chat():
    turns = normalize_turns(rec("sample-ru-001")["transcript"])
    assert turns[0]["id"] == "t01" and turns[-1]["id"] == "t12"
    assert turns[3]["offset_sec"] == 65
    assert turns[3]["timestamp"] == "01:05"  # исходная метка сохраняется


def test_offsets_call_iso():
    turns = normalize_turns(rec("sample-en-call-001")["transcript"])
    assert turns[0]["offset_sec"] == 0
    assert turns[4]["offset_sec"] == 25


def test_facts_first_response_and_waits():
    d = rec("sample-kz-001")
    turns = normalize_turns(d["transcript"])
    f = compute_facts(d, turns, CHECKLIST)
    assert f["client_language_detected"] == "KZ"
    assert f["first_operator_language_detected"] == "RU"
    assert f["first_response_sec"] == 270 and f["first_response_exceeded"] is True

    d = rec("sample-ru-001")
    turns = normalize_turns(d["transcript"])
    f = compute_facts(d, turns, CHECKLIST)
    wait = next(w for w in f["long_waits"] if w["reply_turn"] == "t09")
    assert wait["seconds"] == 150 and wait["warned_hint"] is True


def test_schema_has_all_criteria_and_turn_enum():
    d = rec("sample-ru-001")
    turns = normalize_turns(d["transcript"])
    schema = P.build_schema(CHECKLIST, d["channel"], turns)
    assert schema["required"][0] == "summary"
    assert set(schema["required"][1:]) == {c["id"] for c in CHECKLIST["criteria"]}
    assert schema["properties"]["consultation"]["properties"]["answer"]["enum"] == ["20", "15", "10", "5", "0", "na"]
    assert "t12" in schema["properties"]["greeting"]["properties"]["evidence_turn_ids"]["items"]["enum"]


def test_call_uses_call_instruction():
    sp = P.build_system_prompt(CHECKLIST, "voice call")
    assert "управление паузами" in sp
    sp_chat = P.build_system_prompt(CHECKLIST, "agent chat")
    assert "управление паузами" not in sp_chat


def make_answer(answers, turns_ev="t04"):
    out = {"summary": "Тест"}
    for c in CHECKLIST["criteria"]:
        a = answers.get(c["id"], "no" if c["type"] == "flag" else ("yes" if c["type"] == "binary" else str(c["scale"][0]["value"])))
        out[c["id"]] = {
            "analysis": "", "evidence_turn_ids": [turns_ev], "expected_at_turn_id": "", "checked_from": "t01",
            "checked_to": "t12", "answer": a, "na_reason": "условие выполнено" if a == "na" else "",
            "explanation": f"{c['title']} — {a}", "confidence": "high",
        }
    return out


class FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, system, user, schema):
        self.calls += 1
        body = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return LLMResult(text=json.dumps(body, ensure_ascii=False), model=self.model, provider=self.name,
                         usage={"input_tokens": 1000, "output_tokens": 200}, cost_usd=0.001)


def test_validation_flags_na_without_reason_and_retries():
    d = rec("sample-ru-001")
    bad = make_answer({"needs": "na"})
    bad["needs"]["na_reason"] = ""
    good = make_answer({"needs": "na"})
    prov = FakeProvider([bad, good])
    result = asyncio.run(evaluate_dialog(d, CHECKLIST, prov))
    assert prov.calls == 2
    assert result["status"] == "done"
    assert result["llm_log"][0]["issues"]


def test_full_evaluation_scoring():
    d = rec("sample-ru-001")
    prov = FakeProvider([make_answer({"needs": "na", "consultation": "15", "dialog_management": "7", "identification": "na", "closing": "no"})])
    result = asyncio.run(evaluate_dialog(d, CHECKLIST, prov))
    assert result["score"]["total"] == 77.69
    assert result["meta"]["cost_usd"] == 0.001


def test_voting_majority_and_agreement():
    crit = CHECKLIST["criteria"]
    runs = [make_answer({"consultation": "15"}), make_answer({"consultation": "15"}), make_answer({"consultation": "10"})]
    agg = aggregate(runs, crit)
    assert agg["consultation"]["answer"] == "15"
    assert agg["consultation"]["agreement"] == 0.67
    assert agg["consultation"]["confidence"] == "medium"
    assert agg["greeting"]["agreement"] == 1.0


def test_yes_backed_only_by_client_is_flagged():
    d = rec("sample-ru-001")
    turns = normalize_turns(d["transcript"])
    parsed = make_answer({}, turns_ev="t02")  # t02 — реплика клиента
    issues = validate_run(parsed, CHECKLIST["criteria"], turns)
    assert any("greeting" in i for i in issues)
