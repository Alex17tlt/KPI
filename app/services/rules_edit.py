"""Founder-facing edits of a rules document: money, weights, deadlines, calendar.
Engine-only imports (no DB/config) so it is unit-testable."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from pydantic import ValidationError

from app.engine.calendar import TIME_RE, parse_date
from app.engine.schema import Rules, fmt_num

DEFAULT_LABELS = {
    "time.morning": "Утренняя сдача", "time.evening": "Вечерняя сдача", "time.dz": "Сверка ДЗ",
    "time.kz": "Сверка КЗ", "time.payment": "Срок выплат", "time.deadline": "Сроки без указанного времени",
    "time.preparation": "Подготовка к выплатам", "setting.payroll_day": "День расчёта зарплат",
}


class EditError(ValueError):
    pass


def rule_label(rules: Rules, key: str) -> str:
    return rules.labels.get(key) or DEFAULT_LABELS.get(key) or key.split(".", 1)[-1]


def format_errors(e: ValidationError) -> str:
    out = []
    for err in e.errors():
        msg = str(err.get("msg", "")).removeprefix("Value error, ")
        loc = ".".join(str(x) for x in err.get("loc", ()))
        out.append(msg if err.get("type") == "value_error" or not loc else f"{loc}: {msg}")
    return "; ".join(out)


@dataclass
class RuleEdits:
    fixed: float
    bonus_cap: float
    amounts: dict[int, float]
    times: dict[str, str]
    settings: dict[str, int | None]
    holidays: list[str]
    working_dates: list[str]


def _money(raw: str | None, label: str) -> float:
    s = (raw or "").strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        raise EditError(f"{label}: нужно число.") from None
    if not math.isfinite(v) or v < 0:
        raise EditError(f"{label}: нужно неотрицательное число.")
    return round(v, 2)


def _dates(raw: str | None, label: str) -> list[str]:
    out = set()
    for token in re.split(r"[\s,;]+", raw or ""):
        if not token:
            continue
        try:
            out.add(parse_date(token).isoformat())
        except ValueError:
            raise EditError(f"{label}: «{token}» — нужен формат ГГГГ-ММ-ДД.") from None
    return sorted(out)


def parse_edits(form: dict[str, str], rules: Rules) -> RuleEdits:
    times = {}
    for key in rules.times:
        v = (form.get(f"time_{key}") or "").strip()
        if not TIME_RE.match(v):
            raise EditError(f"{rule_label(rules, 'time.' + key)}: нужен формат ЧЧ:ММ.")
        times[key] = v
    settings: dict[str, int | None] = {}
    for key in rules.settings:
        raw = (form.get(f"setting_{key}") or "").strip()
        if not raw:
            settings[key] = None
            continue
        if not raw.isdigit() or not 1 <= int(raw) <= 31:
            raise EditError(f"{rule_label(rules, 'setting.' + key)}: число от 1 до 31 либо пусто.")
        settings[key] = int(raw)
    return RuleEdits(
        fixed=_money(form.get("fixed"), "Фикс"),
        bonus_cap=_money(form.get("bonus_cap"), "Максимум KPI"),
        amounts={b.id: _money(form.get(f"amount_{b.id}"), f"Блок {b.id}") for b in rules.blocks},
        times=times, settings=settings,
        holidays=_dates(form.get("holidays"), "Нерабочие даты"),
        working_dates=_dates(form.get("working_dates"), "Дополнительные рабочие даты"),
    )


def apply_edits(rules: Rules, e: RuleEdits) -> Rules:
    """Returns a new validated Rules; raises ValidationError if the result is invalid."""
    data = rules.model_dump(mode="json")
    data["fixed"], data["bonus_cap"] = e.fixed, e.bonus_cap
    for b in data["blocks"]:
        if b["id"] in e.amounts:
            b["amount"] = e.amounts[b["id"]]
    for k, v in e.times.items():
        if k in data["times"]:
            data["times"][k] = v
    for k, v in e.settings.items():
        if k in data["settings"]:
            data["settings"][k] = v
    data["holidays"], data["working_dates"] = e.holidays, e.working_dates
    return Rules.model_validate(data)


def diff_rules(old: Rules, new: Rules) -> list[str]:
    cur, out = new.currency, []
    if old.fixed != new.fixed:
        out.append(f"Фикс: {fmt_num(old.fixed)} → {fmt_num(new.fixed)} {cur}")
    if old.bonus_cap != new.bonus_cap:
        out.append(f"Максимум KPI: {fmt_num(old.bonus_cap)} → {fmt_num(new.bonus_cap)} {cur}")
    old_blocks = {b.id: b for b in old.blocks}
    for b in new.blocks:
        o = old_blocks.get(b.id)
        if o and o.amount != b.amount:
            out.append(f"Блок {b.id} «{b.name}»: {fmt_num(o.amount)} → {fmt_num(b.amount)} {cur}")
    for k, v in new.times.items():
        if old.times.get(k) != v:
            out.append(f"{rule_label(new, 'time.' + k)}: {old.times.get(k, '—')} → {v}")
    for k, v in new.settings.items():
        if old.settings.get(k) != v:
            out.append(f"{rule_label(new, 'setting.' + k)}: {old.settings.get(k) or '—'} → {v or '—'}")
    for label, a, b in (("Нерабочие даты", old.holidays, new.holidays),
                        ("Доп. рабочие даты", old.working_dates, new.working_dates)):
        added, removed = sorted(set(b) - set(a)), sorted(set(a) - set(b))
        if added:
            out.append(f"{label}: добавлены {', '.join(added)}")
        if removed:
            out.append(f"{label}: удалены {', '.join(removed)}")
    if old.draft != new.draft:
        out.append("Статус правил: " + ("черновик" if new.draft else "подтверждены"))
    return out
