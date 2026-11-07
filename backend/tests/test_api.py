"""Сквозной тест API: импорт → оценка → апелляция → решение → новый итог → новая версия чек-листа."""
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import db, main
from tests.test_pipeline import CHECKLIST, FakeProvider, make_answer

ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db.reset_for_tests(str(tmp_path / "t.sqlite3"))
    main.JOBS.clear()
    answer = make_answer({"needs": "na", "consultation": "15", "dialog_management": "7", "identification": "na",
                          "closing": "no"})
    monkeypatch.setattr(main, "get_provider", lambda *a, **k: FakeProvider([answer]))
    with TestClient(main.app) as c:
        yield c


def wait_job(client, job_id):
    for _ in range(100):
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job timeout")


def test_end_to_end(client):
    payload = json.loads((ROOT / "data/sample_dataset.json").read_text(encoding="utf-8"))
    r = client.post("/api/datasets/upload", json=payload)
    assert r.json()["imported"] == 3

    cl_id = client.get("/api/checklists").json()["checklists"][0]["id"]
    job = client.post("/api/jobs", json={"dialog_ids": ["sample-ru-001"], "checklist_id": cl_id}).json()
    job = wait_job(client, job["id"])
    assert job["status"] == "finished" and len(job["done"]) == 1, job
    eid = job["done"][0]["evaluation_id"]

    ev = client.get(f"/api/evaluations/{eid}").json()
    assert ev["view"]["final"]["score"]["total"] == 77.69

    # Апелляция по «Завершению»: было Нет → проверяющий меняет на Да
    ap = client.post(f"/api/evaluations/{eid}/appeals", json={"criterion_id": "closing", "comment": "Оператор попрощался в t11"}).json()
    assert client.get("/api/appeals?status=open").json()["appeals"][0]["id"] == ap["id"]
    bad = client.post(f"/api/appeals/{ap['id']}/decision", json={"action": "change", "new_answer": "maybe", "reason": "x"})
    assert bad.status_code == 400  # недопустимое значение ответа отклоняется
    r = client.post(f"/api/appeals/{ap['id']}/decision", json={"action": "change", "new_answer": "yes", "reason": "Прощание есть в t11"}).json()
    assert r["total_before"] == 77.69
    assert r["total_after"] == 85.38  # (50.5 + 5) / 65 × 100
    ev = client.get(f"/api/evaluations/{eid}").json()
    assert ev["view"]["original"]["score"]["total"] == 77.69   # исходная оценка сохранена
    assert ev["view"]["appeals"][0]["decision"]["old_answer"] == "no"

    lst = client.get("/api/dialogs").json()["dialogs"]
    row = next(d for d in lst if d["id"] == "sample-ru-001")
    assert row["total"] == 77.69 and row["final_total"] == 85.38

    # Замена критерия → новая версия, старая остаётся
    data = client.get(f"/api/checklists/{cl_id}").json()["data"]
    data["criteria"][6] = {**data["criteria"][6], "id": "closing_v2", "title": "Завершение (новое)"}
    r = client.post("/api/checklists", json={"data": data, "comment": "замена критерия 7"})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 2
    assert len(client.get("/api/checklists").json()["checklists"]) == 2


def test_invalid_checklist_rejected(client):
    r = client.post("/api/checklists", json={"data": {"slug": "x y", "name": "", "criteria": []}})
    assert r.status_code == 422
    assert r.json()["detail"]["errors"]


def test_gold_and_accuracy(client):
    payload = json.loads((ROOT / "data/sample_dataset.json").read_text(encoding="utf-8"))
    client.post("/api/datasets/upload", json=payload)
    cl_id = client.get("/api/checklists").json()["checklists"][0]["id"]
    job = client.post("/api/jobs", json={"dataset": "SAMPLE", "checklist_id": cl_id}).json()
    wait_job(client, job["id"])
    gold = {c["id"]: "yes" if c["type"] == "binary" else ("no" if c["type"] == "flag" else str(c["scale"][0]["value"]))
            for c in CHECKLIST["criteria"]}
    client.put("/api/gold/sample-ru-001", json={"slug": "chat-quality", "labels": gold})
    acc = client.get(f"/api/accuracy?checklist_id={cl_id}").json()
    assert acc["labeled_dialogs"] == 1
    greeting = next(c for c in acc["criteria"] if c["criterion_id"] == "greeting")
    assert greeting["accuracy"] == 1.0
    needs = next(c for c in acc["criteria"] if c["criterion_id"] == "needs")
    assert needs["accuracy"] == 0.0 and needs["na_errors"] == 1
    assert acc["total_mae"] == round(100 - 77.69, 2)
