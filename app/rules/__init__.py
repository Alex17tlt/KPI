import json
from pathlib import Path

from app.engine.schema import Rules

RULES_DIR = Path(__file__).parent


def load_template(name: str) -> Rules:
    """Load a bundled rules template, e.g. 'finance_notion5'."""
    path = RULES_DIR / f"{name}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Шаблон правил «{name}» не найден")
    return Rules.model_validate(json.loads(path.read_text(encoding="utf-8")))
