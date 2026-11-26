import math
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from app.engine import (Closure, Issue, MetricValue, MonthState, Rules, Submission, analysis_deadline,
                        calculate_month, generate_tasks, iso, local_date, local_time, parse_ts,
                        previous_working_date, task_status)
from app.engine.calendar import at_local
from app.rules import load_template

END = parse_ts("2026-12-03T12:00:00.000Z")
EVIDENCE = "Реестр ДЗ, сверка закрытого месяца"
MS = timedelta(milliseconds=1)


def successful(month="2026-11", start=None) -> MonthState:
    start = start or f"{month}-01"
    rules = load_template("finance_notion5")
    st = MonthState(month=month, rules=rules, start_date=start,
                    metrics={"revenue_percent": MetricValue(97, EVIDENCE)})
    for t in generate_tasks(month, rules, start):
        st.submissions[t.id] = Submission(t.id, t.deadline, {c.key: True for c in t.checks}, "", "Даниил")
    return st


def calc(st, now=END, history=()):
    return calculate_month(st, now, list(history))


def block(st, bid, now=END):
    return next(b for b in calc(st, now).blocks if b.id == bid)


def tasks_of(st, pred):
    return [t for t in generate_tasks(st.month, st.rules, st.start_date) if pred(t)]


def fail(st, task, key=None):
    st.submissions[task.id].checks[key or task.checks[0].key] = False


def issue(**kw) -> Issue:
    data = dict(id="issue-1", date="2026-11-13", kind="error", severity="minor", category="spend",
                description="Расхождение в спенде", found_by="HR", found_by_owner=False,
                corrected_before_sending=False, repeat=False, analysis="Причина найдена; контроль добавлен",
                analysis_at=parse_ts("2026-11-16T12:00:00.000Z"), created_at="2026-11-13T12:00:00.000Z")
    data.update(kw)
    return Issue(**data)


def test_perfect_closed_month():
    st = successful()
    st.closure = Closure(END, "HR", "Все записи проверены")
    r = calc(st)
    assert [b.earned for b in r.blocks] == [600, 500, 400, 300, 200]
    assert r.bonus == 2000 and r.total == 4500 and r.provisional is False


@pytest.mark.parametrize("count,expected", [(0, 600), (1, 300), (3, 300), (4, 0)])
def test_daily_block_boundaries(count, expected):
    st = successful()
    for t in tasks_of(st, lambda t: t.block == 1)[:count]:
        fail(st, t)
    assert block(st, 1).earned == expected


@pytest.mark.parametrize("count,expected", [(0, 500), (1, 250), (2, 250), (3, 0)])
def test_reconciliation_block_boundaries(count, expected):
    st = successful()
    for t in tasks_of(st, lambda t: t.block == 2 and t.kind != "payment")[:count]:
        fail(st, t)
    assert block(st, 2).earned == expected


@pytest.mark.parametrize("count,expected", [(0, 300), (1, 150), (3, 150), (4, 0)])
def test_expense_block_boundaries(count, expected):
    st = successful()
    for t in tasks_of(st, lambda t: t.kind == "morning")[:count]:
        fail(st, t, "cards")
    assert block(st, 4).earned == expected


@pytest.mark.parametrize("count,expected", [(0, 200), (1, 100), (2, 0)])
def test_planning_block_boundaries(count, expected):
    st = successful()
    for t in tasks_of(st, lambda t: t.kind == "plan")[:count]:
        fail(st, t)
    assert block(st, 5).earned == expected


@pytest.mark.parametrize("percent,alerts,expected",
                         [(95, 0, 400), (94.999, 0, 200), (90, 1, 200), (89.999, 0, 0),
                          (100, 1, 200), (100, 2, 0), (0, 0, 0)])
def test_revenue_boundaries_with_alerts(percent, alerts, expected):
    st = successful()
    st.metrics["revenue_percent"] = MetricValue(percent, EVIDENCE)
    st.issues = [issue(id=f"alert-{i}", kind="alert", analysis="", analysis_at=None) for i in range(alerts)]
    assert block(st, 3).earned == expected
    assert calc(st).penalties_raw == 0


def test_unknown_revenue_differs_from_zero_and_needs_evidence():
    st = successful()
    st.metrics["revenue_percent"] = MetricValue(None, EVIDENCE)
    assert block(st, 3).earned == 0
    assert block(st, 3).potential == 400
    assert calc(st).missing_metrics == ["revenue_percent"]
    st.metrics["revenue_percent"] = MetricValue(0, EVIDENCE)
    assert block(st, 3).potential == 0
    assert calc(st).missing_metrics == []
    st.metrics["revenue_percent"] = MetricValue(97, " ")
    assert calc(st).missing_metrics == ["revenue_percent"]
    assert block(st, 3).earned == 0


def test_single_late_buyer_payment_zeroes_block_2():
    st = successful()
    t = tasks_of(st, lambda t: t.zero_block_if_late)[0]
    assert block(st, 2).earned == 500
    st.submissions[t.id].at = t.deadline + MS
    assert block(st, 2).earned == 0
    assert 2 in calc(st).zeroed_blocks
    del st.submissions[t.id]
    assert block(st, 2).earned == 0


def test_payment_error_without_delay_is_one_violation():
    st = successful()
    fail(st, tasks_of(st, lambda t: t.zero_block_if_late)[0])
    assert block(st, 2).earned == 250
    assert 2 not in calc(st).zeroed_blocks


def test_late_and_bad_checks_count_once():
    st = successful()
    t = tasks_of(st, lambda t: t.kind == "morning")[0]
    s = st.submissions[t.id]
    s.at = t.deadline + timedelta(hours=1)
    s.checks = {c.key: False for c in t.checks}
    assert block(st, 1).violations == 1
    assert block(st, 4).violations == 1


def test_late_morning_with_correct_cards_is_not_block4_violation():
    st = successful()
    t = tasks_of(st, lambda t: t.kind == "morning")[0]
    st.submissions[t.id].at = t.deadline + timedelta(seconds=60)
    assert block(st, 1).violations == 1
    assert block(st, 4).violations == 0
    del st.submissions[t.id]
    assert block(st, 4).violations == 1


def test_future_tasks_not_penalised_and_empty_month_not_earned():
    st = successful()
    st.submissions = {}
    st.metrics = {"revenue_percent": MetricValue(None, "")}
    r = calc(st, "2026-11-01T06:00:00.000Z")
    assert r.overdue_count == 0 and not r.zeroed_blocks and r.penalties_raw == 0
    assert r.bonus == 0 and r.potential_bonus == 2000 and r.provisional
    assert all(b.earned == 0 for b in r.blocks)


def test_start_date_excludes_earlier_tasks_and_issues():
    st = successful("2026-11", "2026-11-16")
    assert all(t.date >= date(2026, 11, 16) for t in generate_tasks(st.month, st.rules, st.start_date))
    st.issues = [issue(date="2026-11-13")]
    assert calc(st).penalties_raw == 0
    st.start_date = "2026-12-01"
    assert generate_tasks(st.month, st.rules, st.start_date) == []
    assert calc(st).potential_bonus == 0
    assert calc(st).provisional


def test_weekend_shift_to_friday_and_february_last_day():
    rules = load_template("finance_notion5")
    feb = generate_tasks("2026-02", rules, "2026-01-01")
    pay = next(t for t in feb if t.id == "monthly:2026-02:infra-pay-2")
    assert pay.date == date(2026, 2, 27) and pay.time == "23:59"
    june = generate_tasks("2026-06", rules, "2026-01-01")
    assert next(t for t in june if t.id == "monthly:2026-06:buyers-pay-2").date == date(2026, 6, 19)


def test_shift_from_next_month_not_lost_or_duplicated():
    rules = load_template("finance_notion5")
    jan = generate_tasks("2025-01", rules, "2025-01-01")
    feb = generate_tasks("2025-02", rules, "2025-01-01")
    tid = "monthly:2025-02:advertiser"
    assert [t.date for t in jan if t.id == tid] == [date(2025, 1, 31)]
    assert not [t for t in feb if t.id == tid]
    assert len({t.id for t in jan + feb}) == len(jan) + len(feb)


def test_manual_calendar_shifts_weekly_task():
    rules = load_template("finance_notion5")
    rules.holidays = ["2026-11-02"]
    assert previous_working_date("2026-11-02", rules) == date(2026, 10, 30)
    oct_ = generate_tasks("2026-10", rules, "2026-01-01")
    assert next(t for t in oct_ if t.id == "weekly:2026-11-02:plan").date == date(2026, 10, 30)
    rules.working_dates = ["2026-11-01"]
    assert previous_working_date("2026-11-02", rules) == date(2026, 11, 1)


def test_penalties_repeat_and_fixed_protection():
    st = successful()
    st.issues = [issue(), issue(id="2", severity="significant", repeat=True),
                 issue(id="3", severity="critical", repeat=True), issue(id="4", severity="critical", repeat=True)]
    r = calc(st)
    assert r.penalties_raw == 2450 and r.penalties_applied == 2000
    assert r.bonus == 0 and r.total == 2500
    assert r.penalty_rows[1].multiplier == 2


def test_self_found_voided_and_duplicates_not_penalised():
    st = successful()
    normal = issue()
    st.issues = [normal, normal, issue(id="own", found_by_owner=True, analysis="", analysis_at=None),
                 issue(id="voided", severity="critical", voided=True),
                 issue(id="voided-alert", kind="alert", voided=True)]
    r = calc(st)
    assert r.penalties_raw == 50
    assert len(r.penalty_rows) == 2
    assert block(st, 3).violations == 0


def test_analysis_deadline_two_working_days_exact_boundary():
    st = successful()
    item = issue(analysis="", analysis_at=None)
    st.issues = [item]
    assert iso(analysis_deadline(item, st.rules)) == "2026-11-17T20:59:59.999Z"
    assert calc(st, "2026-11-17T20:59:59.999Z").penalties_raw == 50
    assert calc(st, "2026-11-17T21:00:00.000Z").penalties_raw == 100
    item.analysis = "Причина и новая проверка"
    item.analysis_at = parse_ts("2026-11-17T20:59:59.999Z")
    assert calc(st).penalties_raw == 50
    item.analysis_at = parse_ts("2026-11-17T21:00:00.000Z")
    assert calc(st).penalties_raw == 100


def test_repeat_suggestion_window_requires_confirmation():
    st = successful()
    st.issues = [issue(date="2026-11-16", repeat=False)]
    history = [issue(id="old", date="2026-08-16", created_at="2026-08-16T10:00:00Z")]
    r = calc(st, history=history)
    assert r.penalty_rows[0].repeat_suggested is True
    assert r.penalty_rows[0].multiplier == 1
    history[0].date = "2026-08-15"
    assert calc(st, history=history).penalty_rows[0].repeat_suggested is False
    history[0].date = "2026-08-16"
    history[0].voided = True
    assert calc(st, history=history).penalty_rows[0].repeat_suggested is False


def test_closure_freezes_calculation():
    st = successful()
    st.issues = [issue(date="2026-11-30", created_at="2026-11-30T12:00:00Z", analysis="", analysis_at=None)]
    st.closure = Closure(parse_ts("2026-12-01T12:00:00Z"), "HR", "Закрыто")
    r = calc(st, "2026-12-20T12:00:00Z")
    assert r.evaluated_at == st.closure.at
    assert r.penalties_raw == 50
    st.closure = None
    assert calc(st, "2026-12-20T12:00:00Z").penalties_raw == 100


def test_notion_example_month():
    st = successful()
    mornings = tasks_of(st, lambda t: t.kind == "morning")
    fail(st, mornings[0], "balance")
    fail(st, mornings[0], "cards")
    fail(st, mornings[1], "balance")
    st.metrics["revenue_percent"] = MetricValue(96, EVIDENCE)
    st.issues = [issue()]
    r = calc(st)
    assert r.bonus == 1500 and r.total == 4000


def test_problem_month_example():
    st = successful()
    mornings = tasks_of(st, lambda t: t.kind == "morning")
    for t in mornings[:5]:
        fail(st, t, "balance")
    for t in mornings[:2]:
        fail(st, t, "cards")
    pay = tasks_of(st, lambda t: t.zero_block_if_late)[1]
    st.submissions[pay.id].at = pay.deadline + timedelta(days=1)
    fail(st, tasks_of(st, lambda t: t.kind == "plan")[0])
    st.metrics["revenue_percent"] = MetricValue(91, EVIDENCE)
    st.issues = [issue(id="alert", kind="alert"), issue(id="critical", severity="critical"),
                 issue(id="significant", severity="significant")]
    r = calc(st)
    assert r.gross == 450 and r.penalties_raw == 700 and r.penalties_applied == 450
    assert r.bonus == 0 and r.total == 2500


def test_oleg4_is_draft_with_four_blocks():
    rules = load_template("finance_oleg4")
    st = MonthState(month="2026-11", rules=rules, start_date="2026-11-01")
    tasks = generate_tasks(st.month, rules, st.start_date)
    assert next(t for t in tasks if t.kind == "morning").time == "15:00"
    assert any(t.kind == "dz" for t in tasks) and any(t.kind == "kz" for t in tasks)
    assert any(t.kind == "closing" and "2026-09" in t.detail for t in tasks)
    assert not any(t.kind == "evening" for t in tasks)
    r = calc(st)
    assert [b.id for b in r.blocks] == [1, 2, 4, 5]
    assert r.missing_metrics == [] and r.penalties_raw == 0 and r.provisional


def test_calendar_bound_to_moscow():
    assert local_date(parse_ts("2026-11-15T21:30:00Z"), "Europe/Moscow") == date(2026, 11, 16)
    assert local_time(parse_ts("2026-11-15T21:30:00Z"), "Europe/Moscow") == "00:30"
    assert iso(at_local(date(2026, 11, 16), "12:00", "Europe/Moscow")) == "2026-11-16T09:00:00.000Z"


def test_incomplete_or_future_submission_does_not_count():
    st = successful()
    t = tasks_of(st, lambda t: t.kind == "morning")[0]
    s = st.submissions[t.id]
    del s.checks["cards"]
    assert task_status(t, s, t.deadline + MS) == "violation"
    s.checks["cards"] = True
    assert task_status(t, s, t.deadline - MS) == "pending"


def test_invalid_metric_values_and_bonus_cap_rejected():
    st = successful()
    for bad in (-1, 101, math.nan):
        st.metrics["revenue_percent"] = MetricValue(bad, EVIDENCE)
        with pytest.raises(ValueError, match="Доля выручки"):
            calc(st)
    data = load_template("finance_notion5").model_dump(mode="json")
    data["blocks"][0]["amount"] = 601
    with pytest.raises(ValidationError, match="2 000"):
        Rules.model_validate(data)


def test_rules_reject_unknown_fields_and_export_schema():
    data = load_template("finance_notion5").model_dump(mode="json")
    data["blocks"][0]["weight"] = 1        # typo/hallucination must fail
    with pytest.raises(ValidationError):
        Rules.model_validate(data)
    assert "properties" in Rules.model_json_schema()   # used by the LLM rule editor in stage 3
