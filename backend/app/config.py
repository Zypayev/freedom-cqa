"""Настройки сервиса. Всё задаётся переменными окружения (см. .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent  # корень репозитория


def _load_dotenv() -> None:
    """Минимальный загрузчик .env без внешних зависимостей."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

# Цены USD за 1M токенов (input, output). Используются для оценки стоимости одной оценки.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-1": (10.00, 50.00),
}


@dataclass
class Settings:
    db_path: Path = field(default_factory=lambda: Path(os.getenv("CQA_DB_PATH", str(ROOT / "data" / "cqa.sqlite3"))))
    # Провайдер LLM: anthropic | ollama
    provider: str = field(default_factory=lambda: os.getenv("CQA_PROVIDER", "anthropic"))
    model: str = field(default_factory=lambda: os.getenv("CQA_MODEL", "claude-opus-5-5"))
    effort: str = field(default_factory=lambda: os.getenv("CQA_EFFORT", "high"))
    # Сколько независимых прогонов на диалог (голосование большинством по каждому критерию)
    votes: int = field(default_factory=lambda: int(os.getenv("CQA_VOTES", "1")))
    concurrency: int = field(default_factory=lambda: int(os.getenv("CQA_CONCURRENCY", "6")))
    # Серверный fallback Anthropic при отказе модели (refusal)
    fallbacks: bool = field(default_factory=lambda: os.getenv("CQA_FALLBACKS", "1") == "1")
    ollama_url: str = field(default_factory=lambda: os.getenv("OLLAMA_URL", "http://localhost:11434"))
    ollama_model: str = field(default_factory=lambda: os.getenv("CQA_OLLAMA_MODEL", "qwen3:14b"))
    # Контекст модели: самый длинный промпт DEV ≈ 8k токенов + ответ; 16k хватает и экономит видеопамять.
    ollama_num_ctx: int = field(default_factory=lambda: int(os.getenv("CQA_OLLAMA_NUM_CTX", "16384")))
    # Режим рассуждения у qwen3 и подобных: точнее, но в 2–3 раза медленнее. 0 — выкл.
    ollama_think: bool = field(default_factory=lambda: os.getenv("CQA_OLLAMA_THINK", "0") == "1")
    # Шаблон адреса API организаторов, {group_id} подставляется. Пример:
    # CQA_DATASET_URL=https://example.org/api/records?group_id={group_id}
    dataset_url: str = field(default_factory=lambda: os.getenv("CQA_DATASET_URL", ""))


settings = Settings()
