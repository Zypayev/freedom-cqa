"""Сборка промпта и JSON-схемы ответа из конфигурации чек-листа.

Ни одного критерия в коде: всё берётся из чек-листа, поэтому новый или заменённый
критерий начинает работать сразу после сохранения через интерфейс.
"""
from __future__ import annotations

from typing import Any

from .facts import fmt_dur, render_facts
from .scoring import allowed_answers

SPEAKER_LABEL = {
    "operator": "ОПЕРАТОР",
    "client": "КЛИЕНТ",
    "bot": "БОТ (контекст, не оценивается)",
    "system": "СИСТЕМА (событие)",
}

LANG_NAMES = {"ru": "русском", "kz": "казахском", "en": "английском"}


def channel_key(channel: str | None) -> str:
    return "call" if channel and "call" in channel else "chat"


def criterion_instruction(criterion: dict[str, Any], channel: str | None) -> str:
    instr = criterion.get("instructions") or {}
    if isinstance(instr, str):
        return instr
    return instr.get(channel_key(channel)) or instr.get("default") or ""


def applicable_criteria(checklist: dict[str, Any], channel: str | None) -> list[dict[str, Any]]:
    """Критерии, применимые к каналу (поле channels у критерия опционально)."""
    key = channel_key(channel)
    out = []
    for c in checklist["criteria"]:
        chans = c.get("channels")
        if chans and key not in [channel_key(ch) for ch in chans]:
            continue
        out.append(c)
    return out


def answer_legend(criterion: dict[str, Any]) -> str:
    ctype = criterion["type"]
    if ctype == "binary":
        s = "Ответ: \"yes\" (Да) или \"no\" (Нет)"
    elif ctype == "flag":
        s = "Ответ: \"yes\" (нарушение выявлено) или \"no\" (не выявлено). Баллов не даёт"
    else:
        steps = "; ".join(f"\"{st['value']}\" — {st['label']}" for st in sorted(criterion["scale"], key=lambda x: -x["value"]))
        s = f"Шкала (выберите ровно одну ступень): {steps}"
    if criterion.get("na_allowed"):
        s += f". \"na\" (N/A) разрешён ТОЛЬКО если: {criterion.get('na_condition') or 'критерий неприменим'}"
    else:
        s += ". N/A для этого критерия запрещён"
    return s


def build_system_prompt(checklist: dict[str, Any], channel: str | None) -> str:
    lang = LANG_NAMES.get(checklist.get("explanation_language", "ru"), "русском")
    sections = {s["id"]: s["title"] for s in checklist.get("sections", [])}
    crit_blocks = []
    for c in applicable_criteria(checklist, channel):
        crit_blocks.append(
            f"### Критерий {c.get('number', '')} · id=\"{c['id']}\" · {c['title']}\n"
            f"Секция: {sections.get(c.get('section'), '—')}. Вес: {c.get('weight', 0)}.\n"
            f"{criterion_instruction(c, channel)}\n"
            f"{answer_legend(c)}."
        )
    kb = checklist.get("knowledge_base") or ""
    kb_block = (
        f"\n## База знаний компании\nИспользуйте для проверки корректности консультации. "
        f"Ошибкой считайте только противоречие базе знаний или самому диалогу.\n{kb}\n" if kb.strip() else
        "\n## База знаний\nНе предоставлена. Не придумывайте тарифы и правила компании; "
        "фактическую ошибку фиксируйте только если она противоречит самому диалогу или очевидна.\n"
    )
    return f"""Вы — старший эксперт контроля качества контакт-центра брокерской компании.
Вы оцениваете работу ОПЕРАТОРА в одном диалоге строго по чек-листу ниже.

## Общие правила
{checklist.get('global_rules', '')}

## Как оценивать каждый критерий
1. Найдите реплики, относящиеся к критерию. Читайте условия буквально: если условие «Да» состоит из нескольких частей, должны выполняться все.
2. Сначала решите вопрос применимости. N/A — только если он разрешён и условие неприменимости выполнено. Если действие было нужно, но не сделано — это «Нет», а не N/A.
3. Выберите ответ. Не додумывайте действий, которых нет в тексте. Не наказывайте за то, чего критерий не требует.
4. Доказательства: в evidence_turn_ids укажите ID реплик (оператора или системы, при необходимости — клиента), которые подтверждают вывод.
   Если действие пропущено — укажите в expected_at_turn_id реплику, где оно ожидалось, и проверенный участок checked_from/checked_to.
   Для N/A заполните na_reason: какое условие неприменимости выполнено.
5. explanation — 1–3 предложения на {lang} языке в формате: «<Критерий> — <ответ>. <Что произошло>. Основание — <реплики tXX>».
   Цитаты из диалога приводите на языке оригинала.
6. confidence: high — вывод однозначен; medium — есть сомнения в трактовке; low — данных недостаточно или трактовка спорна.
7. Используйте вычисленные факты (время, паузы, язык) как точные данные — они посчитаны программно.
8. Диалог может быть на русском, казахском или английском — оценивайте одинаково строго.
{kb_block}
## Чек-лист: {checklist.get('name', '')}
{chr(10).join(crit_blocks)}

Верните JSON строго по схеме: сначала summary (2–3 предложения о сути обращения на {lang} языке), затем объект по каждому критерию."""


def render_transcript(turns: list[dict[str, Any]]) -> str:
    lines = []
    for t in turns:
        label = SPEAKER_LABEL.get(t["speaker"], t["speaker"].upper())
        when = t["timestamp"] if t["timestamp"] else fmt_dur(t["offset_sec"])
        rel = f" (+{fmt_dur(t['offset_sec'])})" if t["offset_sec"] is not None and t["timestamp"] and "T" in str(t["timestamp"]) else ""
        lines.append(f"[{t['id']}] {when}{rel} {label}: {t['text']}")
    return "\n".join(lines)


def build_user_prompt(dialog: dict[str, Any], turns: list[dict[str, Any]], facts: dict[str, Any],
                      correction: str | None = None) -> str:
    text = (
        f"Диалог {dialog.get('conversation_id') or dialog.get('id')}.\n\n"
        f"## Вычисленные факты\n{render_facts(facts)}\n\n"
        f"## Транскрипт\n{render_transcript(turns)}"
    )
    if correction:
        text += f"\n\n## Исправьте предыдущий ответ\nВ предыдущей попытке были ошибки:\n{correction}"
    return text


def build_schema(checklist: dict[str, Any], channel: str | None, turns: list[dict[str, Any]]) -> dict[str, Any]:
    ids = [t["id"] for t in turns]
    ids_or_empty = ids + [""]
    props: dict[str, Any] = {"summary": {"type": "string"}}
    order = ["summary"]
    for c in applicable_criteria(checklist, channel):
        props[c["id"]] = {
            "type": "object",
            "properties": {
                "analysis": {"type": "string", "description": "Краткий разбор условий критерия и найденных реплик"},
                "evidence_turn_ids": {"type": "array", "items": {"type": "string", "enum": ids}},
                "expected_at_turn_id": {"type": "string", "enum": ids_or_empty},
                "checked_from": {"type": "string", "enum": ids_or_empty},
                "checked_to": {"type": "string", "enum": ids_or_empty},
                "answer": {"type": "string", "enum": allowed_answers(c)},
                "na_reason": {"type": "string"},
                "explanation": {"type": "string"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
            },
            "required": ["analysis", "evidence_turn_ids", "expected_at_turn_id", "checked_from", "checked_to",
                         "answer", "na_reason", "explanation", "confidence"],
            "additionalProperties": False,
        }
        order.append(c["id"])
    return {"type": "object", "properties": props, "required": order, "additionalProperties": False}
