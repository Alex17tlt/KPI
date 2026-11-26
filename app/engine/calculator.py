"""Generic KPI calculator. Behaviour-compatible port of the original lib/kpi.ts."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from .calendar import (MONTHS_GEN, at_local, calendar_months_before, check_month, end_of_local_date,
                       first_date, iso, last_date, local_date, month_of, nominal_date, parse_date,
                       parse_ts, shift_month, utcnow)
from .schema import AlsoCounts, Check, MetricScoring, MetricTier, Rules, ViolationScoring, fmt_num


# ── data ──────────────────────────────────────────────────────────────────────
@dataclass
class Task:
    id: str
    date: date
    time: str
    deadline: datetime
    title: str
    detail: str
    block: int
    kind: str
    checks: list[Check]
    zero_block_if_late: bool = False
    zero_reason: str = ""
    also_counts: list[AlsoCounts] = field(default_factory=list)

    @property
    def deadline_at(self) -> str:
        return iso(self.deadline)


@dataclass
class Submission:
    task_id: str
    at: datetime
    checks: dict[str, bool]
    comment: str = ""
    by: str = ""


@dataclass
class Issue:
    id: str
    date: str
    kind: str
    severity: str
    category: str = ""
    description: str = ""
    found_by: str = ""
    found_by_owner: bool = False
    corrected_before_sending: bool = False
    repeat: bool = False
    analysis: str = ""
    analysis_at: datetime | None = None
    created_at: str = ""
    voided: bool = False


@dataclass
class MetricValue:
    value: float | None = None
    evidence: str = ""


@dataclass
class Closure:
    at: datetime
    by: str = ""
    note: str = ""


@dataclass
class MonthState:
    month: str
    rules: Rules
    start_date: str
    submissions: dict[str, Submission] = field(default_factory=dict)
    issues: list[Issue] = field(default_factory=list)
    metrics: dict[str, MetricValue] = field(default_factory=dict)
    closure: Closure | None = None


@dataclass
class BlockResult:
    id: int
    name: str
    max: float
    violations: int
    coefficient: float
    earned: float
    potential: float
    pending: int
    reason: str


@dataclass
class PenaltyRow:
    issue_id: str
    base: float
    multiplier: float
    analysis_late: bool
    analysis_fee: float
    total: float
    exempt: bool
    reason: str
    analysis_due: datetime
    repeat_suggested: bool


@dataclass
class TaskResult:
    task: Task
    submission: Submission | None
    status: str  # pending | overdue | done | violation


@dataclass
class MonthCalculation:
    blocks: list[BlockResult]
    penalties_raw: float
    penalties_applied: float
    potential_penalties_applied: float
    bonus: float
    potential_bonus: float
    total: float
    potential_total: float
    gross: float
    potential_gross: float
    max_bonus: float
    score_pct: float
    potential_score_pct: float
    missing_metrics: list[str]
    overdue_count: int
    completed_count: int
    total_tasks: int
    provisional: bool
    period_ended: bool
    zeroed_blocks: dict[int, str]
    penalty_rows: list[PenaltyRow]
    task_results: list[TaskResult]
    warnings: list[str]
    evaluated_at: datetime


# ── tasks ─────────────────────────────────────────────────────────────────────
_PLACEHOLDER = re.compile(r"\{nominal(?P<off>[+-]\d+)?(?P<gen>:gen)?\}")


def _render(text: str, nominal_month: str) -> str:
    """{nominal-1} -> 2026-10 ; {nominal-2:gen} -> 'сентября'."""
    def rep(m: re.Match) -> str:
        mo = shift_month(nominal_month, int(m["off"] or 0))
        return MONTHS_GEN[int(mo[5:]) - 1] if m["gen"] else mo
    return _PLACEHOLDER.sub(rep, text or "")


def generate_tasks(month: str, rules: Rules, start_date: str) -> list[Task]:
    """Tasks belong to the month of their actual (shifted) deadline."""
    check_month(month)
    start = parse_date(start_date)
    cal = rules.calendar()
    first, last = first_date(month), last_date(month)
    next_month = shift_month(month, 1)
    out: list[Task] = []

    def add(task_id: str, d: date, tpl, nominal: str) -> None:
        if month_of(d) != month or d < start:
            return
        hhmm = rules.resolve_time(tpl.time)
        detail = tpl.detail
        if tpl.detail_monday and tpl.schedule.type == "daily_working" and d.weekday() == 0:
            detail = tpl.detail_monday
        out.append(Task(
            id=task_id, date=d, time=hhmm, deadline=at_local(d, hhmm, rules.timezone),
            title=_render(tpl.title, nominal), detail=_render(detail, nominal),
            block=tpl.block, kind=tpl.kind, checks=list(tpl.checks),
            zero_block_if_late=tpl.zero_block_if_late, zero_reason=tpl.zero_reason,
            also_counts=list(tpl.also_counts),
        ))

    for tpl in rules.tasks:
        s = tpl.schedule
        if s.type == "daily_working":
            d = first
            while d <= last:
                if cal.is_working(d):
                    add(f"daily:{d.isoformat()}:{tpl.key}", d, tpl, month)
                d += timedelta(days=1)
        elif s.type == "weekly":
            d, stop = first, first_date(next_month) + timedelta(days=6)
            while d <= stop:
                if d.weekday() == s.weekday:
                    actual = cal.previous_working(d) if s.shift == "previous_working" else d
                    add(f"weekly:{d.isoformat()}:{tpl.key}", actual, tpl, month_of(d))
                d += timedelta(days=1)
        elif s.type == "monthly_day":
            day = s.day if s.day is not None else rules.settings.get(s.day_setting or "")
            if day is None:
                continue
            for nm in (month, next_month):
                nominal = nominal_date(nm, int(day))
                actual = cal.previous_working(nominal) if s.shift == "previous_working" else nominal
                add(f"monthly:{nm}:{tpl.key}", actual, tpl, nm)
        elif s.type == "nth_working_day":
            for nm in (month, next_month):
                add(f"monthly:{nm}:{tpl.key}", cal.nth_working(nm, s.n), tpl, nm)

    out.sort(key=lambda t: (t.deadline, t.id))
    return out


def previous_working_date(d, rules: Rules) -> date:
    return rules.calendar().previous_working(parse_date(d))


def analysis_deadline(issue: Issue, rules: Rules) -> datetime:
    due = rules.calendar().add_working_days(parse_date(issue.date), rules.penalties.analysis_deadline_working_days)
    return end_of_local_date(due, rules.timezone)


# ── status & scoring ──────────────────────────────────────────────────────────
def _submitted(sub: Submission | None, now: datetime) -> Submission | None:
    return sub if sub is not None and sub.at <= now else None


def task_status(task: Task, sub: Submission | None, now) -> str:
    now = parse_ts(now)
    s = _submitted(sub, now)
    if s is None:
        return "overdue" if now > task.deadline else "pending"
    if not all(isinstance(s.checks.get(c.key), bool) for c in task.checks):
        return "violation" if now > task.deadline else "pending"
    if s.at > task.deadline or any(s.checks.get(c.key) is not True for c in task.checks):
        return "violation"
    return "done"


def _violation_coef(scoring: ViolationScoring, count: int) -> float:
    for tier in scoring.tiers:
        if count <= tier.max_violations:
            return tier.coef
    return scoring.else_coef


def _metric_coef(tiers: list[MetricTier], else_coef: float, value: float | None, events: int) -> float:
    for t in tiers:
        if t.min_value is not None and (value is None or value < t.min_value):
            continue
        if t.max_value is not None and (value is None or value > t.max_value):
            continue
        if t.max_events is not None and events > t.max_events:
            continue
        return t.coef
    return else_coef


def calculate_month(state: MonthState, now=None, history: list[Issue] | tuple = ()) -> MonthCalculation:
    rules, pen, tzname = state.rules, state.rules.penalties, state.rules.timezone
    now = parse_ts(now) if now is not None else utcnow()
    tasks = generate_tasks(state.month, rules, state.start_date)
    metric_defs = {m.key: m for m in rules.metrics}

    for key, mv in state.metrics.items():
        m = metric_defs.get(key)
        if m is None or mv.value is None:
            continue
        v = mv.value
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) \
                or v < m.min_value or v > m.max_value:
            raise ValueError(f"{m.label}: значение должно быть от {fmt_num(m.min_value)} "
                             f"до {fmt_num(m.max_value)} либо оставаться незаполненным")

    effective = state.closure.at if state.closure and state.closure.at <= now else now
    today = local_date(effective, tzname)
    start, month_last = parse_date(state.start_date), last_date(state.month)
    results = [TaskResult(t, state.submissions.get(t.id), task_status(t, state.submissions.get(t.id), effective))
               for t in tasks]

    ids = [b.id for b in rules.blocks]
    violations = {i: set() for i in ids}
    pending = {i: set() for i in ids}
    eligible = {i: set() for i in ids}
    zeroed: dict[int, str] = {}

    for r in results:
        t = r.task
        eligible[t.block].add(t.id)
        if r.status in ("overdue", "violation"):
            violations[t.block].add(t.id)
        if r.status == "pending":
            pending[t.block].add(t.id)
        s = _submitted(r.submission, effective)
        past = effective > t.deadline
        if t.zero_block_if_late and ((s and s.at > t.deadline) or (not s and past)):
            zeroed.setdefault(t.block, t.zero_reason or f"Просрочена задача «{t.title}»: блок обнулён")
        for ac in t.also_counts:
            eligible[ac.block].add(t.id)
            val = s.checks.get(ac.check) if s else None
            if (not s and past) or val is False or (s and not isinstance(val, bool) and past):
                violations[ac.block].add(t.id)
            elif not s or not isinstance(val, bool):
                pending[ac.block].add(t.id)

    unique = list({i.id: i for i in state.issues}.values())
    issues = [i for i in unique if not i.voided and month_of(parse_date(i.date)) == state.month
              and start <= parse_date(i.date) <= today]

    active = [b for b in rules.blocks if b.amount > 0]
    required = {b.scoring.metric for b in active if isinstance(b.scoring, MetricScoring)}

    def is_missing(key: str) -> bool:
        mv, m = state.metrics.get(key), metric_defs[key]
        return mv is None or mv.value is None or (m.require_evidence and not (mv.evidence or "").strip())

    missing = [m.key for m in rules.metrics if m.key in required and is_missing(m.key)]
    period_ended = effective > end_of_local_date(month_last, tzname)
    has_eligible = start <= month_last

    warnings = list(rules.warnings)
    for tpl in rules.tasks:
        if tpl.schedule.day_setting and rules.settings.get(tpl.schedule.day_setting) is None:
            warnings.append(f"Срок задачи «{tpl.title}» не установлен: эта задача не включена в оценку.")
    if start > first_date(state.month) and has_eligible:
        warnings.append(f"Учёт начат {start.isoformat()}. Более ранние обязанности не оцениваются; "
                        f"автоматического пропорционального пересчёта бонуса нет.")
    if not has_eligible or not tasks:
        warnings.append("В периоде нет оцениваемых задач. Начисление автоматически не подтверждается.")

    blocks: list[BlockResult] = []
    for b in active:
        if isinstance(b.scoring, MetricScoring):
            sc, m = b.scoring, metric_defs[b.scoring.metric]
            mv = state.metrics.get(sc.metric)
            events = sum(1 for i in issues if i.kind == sc.event_kind) if sc.event_kind else 0
            miss = sc.metric in missing
            c = _metric_coef(sc.missing_tiers if miss else sc.tiers, sc.else_coef, None if miss else mv.value, events)
            potential = b.amount * c if has_eligible and tasks else 0
            not_ready = miss or not period_ended
            kind_label = pen.kind_labels.get(sc.event_kind or "", sc.event_kind or "")
            reason = (f"Нужны значение «{m.label}» и основание" if miss
                      else f"{m.label}: {fmt_num(mv.value)}{m.unit}; {kind_label.lower()}: {events}")
            blocks.append(BlockResult(b.id, b.name, b.amount, events, c, 0 if not_ready else potential,
                                      potential, 1 if not_ready else 0, reason))
        else:
            count = len(violations[b.id])
            c = 0.0 if b.id in zeroed else _violation_coef(b.scoring, count)
            potential = b.amount * c if eligible[b.id] else 0
            earned = potential if not pending[b.id] and period_ended else 0
            reason = zeroed.get(b.id) or f"Нарушений: {count}; ожидается задач: {len(pending[b.id])}"
            blocks.append(BlockResult(b.id, b.name, b.amount, count, c, earned, potential, len(pending[b.id]), reason))

    hist = list({i.id: i for i in [*history, *unique]}.values())
    rows: list[PenaltyRow] = []
    for i in issues:
        exempt = i.kind in pen.exempt_kinds or (pen.self_found_exempt and i.found_by_owner)
        due = analysis_deadline(i, rules)
        valid_analysis = bool(i.analysis.strip()) and i.analysis_at is not None and i.analysis_at <= effective
        late = (not exempt) and ((i.analysis_at > due) if valid_analysis else (effective > due))
        idate, cat = parse_date(i.date), i.category.strip()
        lower = calendar_months_before(idate, pen.repeat_window_months)
        repeat_suggested = i.kind not in pen.exempt_kinds and bool(cat) and any(
            not p.voided and p.id != i.id and p.kind not in pen.exempt_kinds and not p.found_by_owner
            and p.category.strip() == cat and parse_date(p.date) >= lower
            and (parse_date(p.date) < idate or (p.date == i.date and p.created_at < i.created_at))
            for p in hist)
        base = 0 if exempt else pen.severity_amounts.get(i.severity, 0)
        mult = 1 if exempt else (pen.repeat_multiplier if i.repeat else 1)
        fee = pen.late_analysis_fee if late else 0
        if i.kind in pen.exempt_kinds:
            reason = f"{pen.kind_labels.get(i.kind, i.kind)} влияет на соответствующий блок, денежного штрафа нет"
        elif exempt:
            reason = "Ошибка найдена самим сотрудником: денежного штрафа нет"
        elif i.repeat:
            reason = "Подтверждённый повтор: основной штраф удвоен"
        else:
            reason = "Ошибка обнаружена другим сотрудником"
        rows.append(PenaltyRow(i.id, base, mult, late, fee, base * mult + fee, exempt, reason, due, repeat_suggested))

    cap = rules.bonus_cap
    gross = min(cap, sum(b.earned for b in blocks))
    potential_gross = min(cap, sum(b.potential for b in blocks))
    raw = sum(r.total for r in rows)
    applied, p_applied = min(gross, raw), min(potential_gross, raw)
    bonus, p_bonus = max(0.0, gross - applied), max(0.0, potential_gross - p_applied)
    max_bonus = min(cap, sum(b.amount for b in active))
    provisional = (state.closure is None or not period_ended or bool(missing) or rules.draft
                   or not tasks or any(b.pending > 0 for b in blocks))

    return MonthCalculation(
        blocks=blocks, penalties_raw=raw, penalties_applied=applied, potential_penalties_applied=p_applied,
        bonus=bonus, potential_bonus=p_bonus, total=rules.fixed + bonus, potential_total=rules.fixed + p_bonus,
        gross=gross, potential_gross=potential_gross, max_bonus=max_bonus,
        score_pct=round(bonus / max_bonus * 100, 1) if max_bonus else 0.0,
        potential_score_pct=round(p_bonus / max_bonus * 100, 1) if max_bonus else 0.0,
        missing_metrics=missing,
        overdue_count=sum(1 for r in results if r.status == "overdue"),
        completed_count=sum(1 for r in results if r.status in ("done", "violation")),
        total_tasks=len(tasks), provisional=provisional, period_ended=period_ended, zeroed_blocks=zeroed,
        penalty_rows=rows, task_results=results, warnings=warnings, evaluated_at=effective,
    )
