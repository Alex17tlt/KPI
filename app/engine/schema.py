"""Department KPI rules as strict, versioned, JSON-serialisable data.

No department-specific Python: the calculator interprets these rules.
extra="forbid" everywhere makes the format safe for LLM edits — unknown or
misspelled fields are rejected instead of being silently ignored.
"""
from __future__ import annotations

import math
from typing import Annotated, Literal, Union
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .calendar import TIME_RE, WorkCalendar, parse_date


def fmt_num(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".rstrip("0").rstrip(".").replace(",", " ")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


KEY = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,59}$"


class Check(Strict):
    key: str = Field(pattern=KEY)
    label: str = Field(min_length=1, max_length=300)


class AlsoCounts(Strict):
    """This task's check also decides a violation in another block (lateness ignored there)."""
    block: int
    check: str


class Schedule(Strict):
    type: Literal["daily_working", "weekly", "monthly_day", "nth_working_day"]
    weekday: int | None = Field(default=None, ge=0, le=6, description="0 = понедельник")
    day: int | None = Field(default=None, ge=1, le=31)
    day_setting: str | None = Field(default=None, description="Имя параметра в settings с днём месяца")
    n: int | None = Field(default=None, ge=1, le=23)
    shift: Literal["previous_working", "none"] = "previous_working"

    @model_validator(mode="after")
    def _fields(self):
        if self.type == "weekly" and self.weekday is None:
            raise ValueError("Для еженедельной задачи нужен weekday")
        if self.type == "monthly_day" and self.day is None and not self.day_setting:
            raise ValueError("Для ежемесячной задачи нужен day или day_setting")
        if self.type == "nth_working_day" and self.n is None:
            raise ValueError("Для задачи «N-й рабочий день» нужен n")
        return self


class TaskTemplate(Strict):
    key: str = Field(pattern=KEY)
    title: str = Field(min_length=1, max_length=300)
    detail: str = ""
    detail_monday: str | None = None
    block: int
    kind: str = Field(default="task", pattern=KEY)
    time: str = Field(description="Имя из times или ЧЧ:ММ")
    schedule: Schedule
    checks: list[Check] = Field(min_length=1)
    zero_block_if_late: bool = False
    zero_reason: str = ""
    also_counts: list[AlsoCounts] = Field(default_factory=list)


class ViolationTier(Strict):
    max_violations: int = Field(ge=0)
    coef: float = Field(ge=0, le=1)


class ViolationScoring(Strict):
    type: Literal["violation_tiers"]
    tiers: list[ViolationTier] = Field(min_length=1)
    else_coef: float = Field(default=0, ge=0, le=1)


class MetricTier(Strict):
    min_value: float | None = None
    max_value: float | None = None
    max_events: int | None = Field(default=None, ge=0)
    coef: float = Field(ge=0, le=1)


class MetricScoring(Strict):
    type: Literal["metric_tiers"]
    metric: str
    event_kind: str | None = None
    tiers: list[MetricTier] = Field(min_length=1)
    missing_tiers: list[MetricTier] = Field(default_factory=list)
    else_coef: float = Field(default=0, ge=0, le=1)


Scoring = Annotated[Union[ViolationScoring, MetricScoring], Field(discriminator="type")]


class Block(Strict):
    id: int = Field(ge=1, le=99)
    name: str = Field(min_length=1, max_length=200)
    amount: float = Field(ge=0)
    description: str = ""
    scoring: Scoring


class Metric(Strict):
    key: str = Field(pattern=KEY)
    label: str = Field(min_length=1, max_length=200)
    unit: str = ""
    min_value: float = 0
    max_value: float = 100
    require_evidence: bool = True
    evidence_label: str = "Основание"


class PenaltyRules(Strict):
    severity_amounts: dict[str, float] = Field(
        default_factory=lambda: {"minor": 50, "significant": 200, "critical": 500})
    severity_labels: dict[str, str] = Field(
        default_factory=lambda: {"minor": "Незначительная", "significant": "Существенная", "critical": "Критическая"})
    kind_labels: dict[str, str] = Field(
        default_factory=lambda: {"error": "Ошибка", "alert": "Пропущенный алерт"})
    exempt_kinds: list[str] = Field(default_factory=lambda: ["alert"])
    self_found_exempt: bool = True
    repeat_multiplier: float = Field(default=2, ge=1)
    repeat_window_months: int = Field(default=3, ge=0, le=24)
    analysis_deadline_working_days: int = Field(default=2, ge=0, le=30)
    late_analysis_fee: float = Field(default=50, ge=0)


class Rules(Strict):
    schema_version: Literal[1] = 1
    version: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=200)
    draft: bool = False
    timezone: str = "Europe/Moscow"
    currency: str = "$"
    fixed: float = Field(ge=0)
    bonus_cap: float = Field(ge=0)
    times: dict[str, str] = Field(default_factory=dict)
    settings: dict[str, int | None] = Field(default_factory=dict)
    holidays: list[str] = Field(default_factory=list)
    working_dates: list[str] = Field(default_factory=list)
    metrics: list[Metric] = Field(default_factory=list)
    blocks: list[Block] = Field(min_length=1)
    tasks: list[TaskTemplate] = Field(default_factory=list)
    penalties: PenaltyRules = Field(default_factory=PenaltyRules)
    warnings: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)   # e.g. {"time.morning": "Утренняя сдача"}

    def resolve_time(self, ref: str) -> str:
        if ref in self.times:
            return self.times[ref]
        if TIME_RE.match(ref):
            return ref
        raise ValueError(f"Неизвестное время «{ref}»")

    def calendar(self) -> WorkCalendar:
        return WorkCalendar(self.holidays, self.working_dates)

    def block_by_id(self, block_id: int) -> Block | None:
        return next((b for b in self.blocks if b.id == block_id), None)

    @model_validator(mode="after")
    def _consistency(self):
        try:
            ZoneInfo(self.timezone)
        except Exception:
            raise ValueError(f"Неизвестный часовой пояс «{self.timezone}»") from None
        for name, value in self.times.items():
            if not TIME_RE.match(value):
                raise ValueError(f"Время «{name}»: нужен формат ЧЧ:ММ")
        ids = [b.id for b in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("Номера блоков должны быть уникальными")
        if any(not math.isfinite(b.amount) for b in self.blocks) or \
                sum(b.amount for b in self.blocks) > self.bonus_cap + 1e-6:
            raise ValueError(f"Суммы блоков должны быть неотрицательными и в сумме не больше "
                             f"{fmt_num(self.bonus_cap)} {self.currency}")
        metric_keys = {m.key for m in self.metrics}
        if len(metric_keys) != len(self.metrics):
            raise ValueError("Ключи показателей должны быть уникальными")
        for b in self.blocks:
            if isinstance(b.scoring, MetricScoring) and b.scoring.metric not in metric_keys:
                raise ValueError(f"Блок {b.id}: неизвестный показатель «{b.scoring.metric}»")
        task_keys: set[str] = set()
        for t in self.tasks:
            if t.key in task_keys:
                raise ValueError(f"Повторяющийся ключ задачи «{t.key}»")
            task_keys.add(t.key)
            if t.block not in ids:
                raise ValueError(f"Задача «{t.key}»: блок {t.block} не существует")
            self.resolve_time(t.time)
            checks = {c.key for c in t.checks}
            if len(checks) != len(t.checks):
                raise ValueError(f"Задача «{t.key}»: ключи проверок должны быть уникальными")
            for ac in t.also_counts:
                if ac.block not in ids:
                    raise ValueError(f"Задача «{t.key}»: блок {ac.block} не существует")
                if ac.check not in checks:
                    raise ValueError(f"Задача «{t.key}»: нет проверки «{ac.check}»")
            ds = t.schedule.day_setting
            if ds is not None:
                if ds not in self.settings:
                    raise ValueError(f"Задача «{t.key}»: параметр «{ds}» не задан в settings")
                v = self.settings[ds]
                if v is not None and (isinstance(v, bool) or not 1 <= v <= 31):
                    raise ValueError(f"Параметр «{ds}»: число от 1 до 31 либо пустое значение")
        hol = {parse_date(d) for d in self.holidays}
        work = {parse_date(d) for d in self.working_dates}
        if hol & work:
            raise ValueError("Одна дата не может одновременно быть рабочей и нерабочей")
        return self
