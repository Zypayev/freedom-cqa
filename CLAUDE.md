# CQA — инструкции для Claude Code

Прототип оценки качества диалогов для хакатона Freedom Broker. Общаемся с пользователем на русском.

**Перед работой прочитай [docs/HISTORY.md](docs/HISTORY.md).** Там задание, критерии отбора, ответы организаторов, ловушки чек-листа, принятые решения, находки на данных DEV, текущий статус и следующие шаги. Материалы задания (PDF) лежат в `docs/materials/`.

## Главное

- **Цель №1 — точность относительно эталона организаторов** (10 контрольных транскриптов). Любое изменение промпта или формулировок проверяй на странице «Точность» по эталонной разметке DEV.
- **Баллы считает код** (`backend/app/scoring.py`), модель только отвечает по критериям. Не переносить арифметику в промпт.
- **В коде нет ни одного критерия.** Промпт и схема собираются из JSON чек-листа (`backend/app/prompt.py`), начальный чек-лист — `backend/app/checklists/chat_quality_v1.json`. Новые правила оценки добавляются в формулировки критериев, а не в код.
- **Модель — бесплатная локальная:** Ollama `qwen3:14b` на сервере команды с RTX 4070 Super через SSH-туннель (`OLLAMA_URL=http://localhost:11434`). Платный API Anthropic не использовать без явной просьбы пользователя.
- **Не подставлять заранее подготовленные результаты.** Жюри проверяет, что система действительно оценивает новые диалоги.

## Команды

```bash
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt   # один раз
cd backend && .venv/bin/python -m pytest -q                                       # тесты (без сети)
cd backend && .venv/bin/uvicorn app.main:app --port 8000                          # сервер + интерфейс
```

Windows (PowerShell; Python 3.12 стоит в `%LOCALAPPDATA%\Programs\Python\Python312`):

```powershell
cd backend; .\.venv\Scripts\python.exe -m pytest -q
cd backend; .\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

## Структура

- `backend/app/` — API (`main.py`), оценщик (`evaluator.py`), промпт (`prompt.py`), факты кодом (`facts.py`), баллы (`scoring.py`), LLM-адаптеры (`llm.py`), бизнес-логика (`services.py`), SQLite (`db.py`).
- `frontend/` — vanilla JS без сборки (маршруты в `js/app.js`, экраны в `js/views/`), монтируется в `#cqa-root`, стили под `.cqa` (для Dynamics 365).
- `.env` — не в Git. Содержит ссылку DEV с подписью доступа: не публиковать.
- `data/raw/dev.json`, `docs/materials/` — данные и материалы организаторов, не в Git (репозиторий будет публичным).
