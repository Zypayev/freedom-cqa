"""Факты о диалоге, которые считает код, а не модель.

ID реплик, время, язык, задержки ответов, паузы, системные события.
Эти факты передаются в промпт и показываются в интерфейсе.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

KZ_LETTERS = set("әғқңөұүһіӘҒҚҢӨҰҮҺІ")
KZ_WORDS = {
    "сәлем", "сәлеметсіз", "рахмет", "иә", "жоқ", "қалай", "керек", "сіз", "сізге", "мен", "бар",
    "көмектесе", "аламын", "өтінемін", "жақсы", "кешіріңіз", "қайырлы", "күн", "болды", "шот",
}
WAIT_PATTERNS = re.compile(
    r"(минут|секунд|ожидан|подожд|уточню|проверю|посмотрю|сейчас узна|момент|"
    r"күте|күтіңіз|тексерем|қараймын|нақтылап|"
    r"\bwait\b|moment|minute|\bhold\b|let me check|i'?ll check|checking)",
    re.IGNORECASE,
)


def turn_id(index: int, total: int) -> str:
    width = max(2, len(str(total)))
    return f"t{index + 1:0{width}d}"


def parse_time(ts: str | None) -> tuple[str, float | datetime | None]:
    """Возвращает ('rel', секунды) для MM:SS/HH:MM:SS, ('abs', datetime) для ISO 8601."""
    if not ts:
        return ("none", None)
    ts = str(ts).strip()
    if re.fullmatch(r"\d{1,3}:\d{2}(:\d{2})?(\.\d+)?", ts):
        parts = [float(p) for p in ts.split(":")]
        secs = 0.0
        for p in parts:
            secs = secs * 60 + p
        return ("rel", secs)
    try:
        return ("abs", datetime.fromisoformat(ts.replace("Z", "+00:00")))
    except ValueError:
        return ("none", None)


def detect_language(text: str) -> str:
    """Грубый детектор RU / KZ / EN по алфавиту и характерным словам."""
    if not text:
        return "unknown"
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return "unknown"
    words = set(re.findall(r"[^\W\d_]+", text.lower()))
    if any(ch in KZ_LETTERS for ch in letters) or len(words & KZ_WORDS) >= 2:
        return "KZ"
    cyr = sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ")
    # Названия продуктов на латинице внутри русской фразы не делают её английской.
    if cyr >= 0.3 * len(letters):
        return "RU"
    return "EN"


def normalize_turns(transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Присваивает ID и относительное время (сек от начала). Исходный timestamp сохраняется."""
    n = len(transcript)
    parsed = [parse_time(t.get("timestamp")) for t in transcript]
    first_abs = next((v for kind, v in parsed if kind == "abs"), None)
    turns = []
    for i, (t, (kind, val)) in enumerate(zip(transcript, parsed)):
        if kind == "rel":
            offset = float(val)
        elif kind == "abs" and first_abs is not None:
            offset = (val - first_abs).total_seconds()
        else:
            offset = None
        turns.append({
            "id": turn_id(i, n),
            "index": i,
            "speaker": (t.get("speaker") or "unknown").lower(),
            "text": t.get("text") or "",
            "timestamp": t.get("timestamp"),
            "offset_sec": offset,
        })
    return turns


def fmt_dur(sec: float | None) -> str:
    if sec is None:
        return "—"
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def compute_facts(dialog: dict[str, Any], turns: list[dict[str, Any]], checklist: dict[str, Any]) -> dict[str, Any]:
    channel = dialog.get("channel") or "agent chat"
    is_call = "call" in channel
    first_threshold = float(checklist.get("first_response_threshold_sec", 180))
    wait_threshold = float(checklist.get("wait_threshold_sec", 30 if is_call else 120))

    by_speaker: dict[str, int] = {}
    for t in turns:
        by_speaker[t["speaker"]] = by_speaker.get(t["speaker"], 0) + 1

    client_text = " ".join(t["text"] for t in turns if t["speaker"] == "client")
    client_lang = detect_language(client_text)
    first_client = next((t for t in turns if t["speaker"] == "client"), None)
    first_op = next((t for t in turns if t["speaker"] == "operator"), None)

    # Первая реакция считается от передачи чата оператору (системное событие перед первой
    # репликой оператора, например «Эскалация на оператора»), а если его нет — от первой реплики клиента.
    first_response = None
    first_response_from = None
    if first_op:
        before = turns[: first_op["index"]]
        handoff = next((t for t in reversed(before) if t["speaker"] == "system"), None)
        anchor = handoff or first_client
        if anchor and anchor["index"] < first_op["index"] and anchor["offset_sec"] is not None and first_op["offset_sec"] is not None:
            first_response = max(0.0, first_op["offset_sec"] - anchor["offset_sec"])
            first_response_from = anchor["id"]

    # Задержки ответа оператора: от последней реплики не-оператора до следующей реплики оператора.
    long_waits = []
    last_other = None   # последняя реплика клиента, на которую ещё не ответили
    last_op_before = None
    # Ожидания считаем только после подключения оператора: первая реакция учитывается отдельно.
    start_idx = first_op["index"] if first_op else len(turns)
    for t in turns[start_idx:]:
        if t["speaker"] == "client":
            if last_other is None:
                last_other = t
                last_op_before = None
        elif t["speaker"] == "operator":
            if last_other is not None and t["offset_sec"] is not None and last_other["offset_sec"] is not None:
                gap = t["offset_sec"] - last_other["offset_sec"]
                if gap >= wait_threshold:
                    long_waits.append({
                        "from_turn": last_other["id"], "reply_turn": t["id"], "seconds": round(gap, 1),
                        "warned_hint": bool(last_op_before and WAIT_PATTERNS.search(last_op_before["text"])),
                    })
                last_other = None
            # Пауза между двумя репликами оператора после «минутку»
            prev = last_op_before
            if prev is not None and t["offset_sec"] is not None and prev["offset_sec"] is not None:
                gap = t["offset_sec"] - prev["offset_sec"]
                if gap >= wait_threshold and not any(w["reply_turn"] == t["id"] for w in long_waits):
                    long_waits.append({
                        "from_turn": prev["id"], "reply_turn": t["id"], "seconds": round(gap, 1),
                        "warned_hint": bool(WAIT_PATTERNS.search(prev["text"])),
                    })
            last_op_before = t

    system_events = [{"turn": t["id"], "text": t["text"]} for t in turns if t["speaker"] == "system"]
    last_meaningful = next((t for t in reversed(turns) if t["speaker"] in ("client", "operator")), None)

    facts: dict[str, Any] = {
        "channel": channel,
        "is_call": is_call,
        "declared_language": dialog.get("language"),
        "client_language_detected": client_lang,
        "turns_by_speaker": by_speaker,
        "first_operator_turn": first_op["id"] if first_op else None,
        "first_operator_language_detected": detect_language(first_op["text"]) if first_op else None,
        "first_response_sec": round(first_response, 1) if first_response is not None else None,
        "first_response_from": first_response_from,
        "first_response_threshold_sec": first_threshold,
        "first_response_exceeded": (first_response is not None and first_response > first_threshold),
        "wait_threshold_sec": wait_threshold,
        "long_waits": long_waits,
        "system_events": system_events,
        "last_speaker": last_meaningful["speaker"] if last_meaningful else None,
        "duration_sec": (turns[-1]["offset_sec"] - turns[0]["offset_sec"])
        if turns and turns[-1]["offset_sec"] is not None and turns[0]["offset_sec"] is not None else None,
    }

    if is_call:
        # Темп речи оператора: слова / длительность реплики (длительность = до начала следующей реплики).
        words, secs = 0, 0.0
        for i, t in enumerate(turns[:-1]):
            nxt = turns[i + 1]
            if t["speaker"] == "operator" and t["offset_sec"] is not None and nxt["offset_sec"] is not None:
                d = nxt["offset_sec"] - t["offset_sec"]
                if 0 < d < 120:
                    words += len(t["text"].split())
                    secs += d
        facts["operator_words_per_minute"] = round(words / secs * 60) if secs > 5 else None
    return facts


def render_facts(facts: dict[str, Any]) -> str:
    """Текстовое представление фактов для промпта."""
    lines = [
        f"- Канал: {facts['channel']}. Заявленный язык: {facts['declared_language']}. "
        f"Язык клиента (автоопределение): {facts['client_language_detected']}.",
        f"- Первая реплика оператора: {facts['first_operator_turn']} "
        f"(язык по автоопределению: {facts['first_operator_language_detected']}).",
    ]
    if facts["first_response_sec"] is not None:
        lines.append(
            f"- Время первой реакции оператора: {fmt_dur(facts['first_response_sec'])} от {facts.get('first_response_from')} "
            f"(порог {fmt_dur(facts['first_response_threshold_sec'])}; "
            f"{'ПРЕВЫШЕН' if facts['first_response_exceeded'] else 'в пределах порога'})."
        )
    if facts["long_waits"]:
        for w in facts["long_waits"]:
            lines.append(
                f"- Долгое ожидание ответа оператора: {fmt_dur(w['seconds'])} между {w['from_turn']} и {w['reply_turn']}"
                f" (предупреждение об ожидании по ключевым словам: {'найдено' if w['warned_hint'] else 'не найдено'}; проверьте сами)."
            )
    else:
        lines.append(f"- Ожиданий ответа оператора длиннее {fmt_dur(facts['wait_threshold_sec'])} не обнаружено.")
    if facts["system_events"]:
        lines.append("- Системные события: " + "; ".join(f"{e['turn']}: {e['text']}" for e in facts["system_events"]))
    lines.append(f"- Последним (из клиента и оператора) писал: {facts['last_speaker']}.")
    if facts.get("is_call"):
        wpm = facts.get("operator_words_per_minute")
        lines.append(f"- Темп речи оператора (оценка по меткам времени): {wpm if wpm else 'недостаточно данных'} слов/мин. "
                     "Аудио в оценку не передаётся: дикцию и разборчивость по транскрипту не оценивать.")
    return "\n".join(lines)
