import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.engine import generate_tasks
from app.engine.calendar import shift_month
from app.engine.schema import Rules
from app.rules import load_template
from app.services.rules_edit import EditError, apply_edits, diff_rules, parse_edits

TEMPLATES = sorted((Path(__file__).resolve().parents[1] / "app" / "rules").glob("*.json"))


@pytest.mark.parametrize("path", TEMPLATES, ids=lambda p: p.stem)
def test_template_valid_and_generates_two_years(path):
    rules = Rules.model_validate(json.loads(path.read_text(encoding="utf-8")))
    block_ids = {b.id for b in rules.blocks}
    month, seen = "2026-01", set()
    for _ in range(24):
        ids = [t.id for t in generate_tasks(month, rules, "2026-01-01")]
        assert len(ids) == len(set(ids)), f"дубли задач в {month}"
        assert not seen & set(ids), f"задача попала в два месяца ({month})"
        seen |= set(ids)
        month = shift_month(month, 1)
    assert all(t.block in block_ids for t in rules.tasks)


def _form(rules: Rules, **override) -> dict:
    form = {"fixed": str(rules.fixed), "bonus_cap": str(rules.bonus_cap), "holidays": "", "working_dates": ""}
    form.update({f"amount_{b.id}": str(b.amount) for b in rules.blocks})
    form.update({f"time_{k}": v for k, v in rules.times.items()})
    form.update({f"setting_{k}": "" if v is None else str(v) for k, v in rules.settings.items()})
    form.update(override)
    return form


def test_weight_edit_changes_only_money():
    rules = load_template("finance_notion5")
    new = apply_edits(rules, parse_edits(_form(rules, amount_1="700", amount_3="300"), rules))
    assert [b.amount for b in new.blocks] == [700, 500, 300, 300, 200]
    assert new.tasks == rules.tasks and new.penalties == rules.penalties
    assert any("Блок 1" in line for line in diff_rules(rules, new))


def test_weights_over_cap_rejected():
    rules = load_template("finance_notion5")
    with pytest.raises(ValidationError, match="2 000"):
        apply_edits(rules, parse_edits(_form(rules, amount_1="900"), rules))


def test_bad_time_and_date_rejected():
    rules = load_template("finance_notion5")
    with pytest.raises(EditError):
        parse_edits(_form(rules, time_morning="25:00"), rules)
    with pytest.raises(EditError):
        parse_edits(_form(rules, holidays="31.12.2026"), rules)
