from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine import (Closure, Issue, MetricValue, MonthCalculation, MonthState, Rules, Submission,
                        calculate_month, local_date, month_of, parse_ts, try_parse_ts, utcnow)
from app.models import IssueRecord, MonthRecord


def current_month(tzname: str = "Europe/Moscow") -> str:
    return month_of(local_date(utcnow(), tzname))


def rules_of(rec: MonthRecord) -> Rules:
    return Rules.model_validate(rec.rules_json)


def issue_from_row(r: IssueRecord) -> Issue:
    return Issue(id=r.id, date=r.date, kind=r.kind, severity=r.severity, category=r.category or "",
                 description=r.description or "", found_by=r.found_by or "", found_by_owner=r.found_by_owner,
                 corrected_before_sending=r.corrected_before_sending, repeat=r.repeat,
                 analysis=r.analysis or "", analysis_at=try_parse_ts(r.analysis_at),
                 created_at=r.created_at or "", voided=r.voided)


def state_from_record(rec: MonthRecord) -> MonthState:
    subs = {s.task_id: Submission(s.task_id, parse_ts(s.at), dict(s.checks_json or {}), s.comment or "", s.by_name or "")
            for s in rec.submissions}
    metrics = {k: MetricValue(v.get("value"), v.get("evidence") or "") for k, v in (rec.metrics_json or {}).items()}
    closure = None
    if rec.closure_json:
        closure = Closure(parse_ts(rec.closure_json["at"]), rec.closure_json.get("by", ""), rec.closure_json.get("note", ""))
    return MonthState(month=rec.month, rules=rules_of(rec), start_date=rec.start_date, submissions=subs,
                      issues=[issue_from_row(i) for i in rec.issues], metrics=metrics, closure=closure)


def issue_history(db: Session, department_id: int, exclude_month_id: int | None = None) -> list[Issue]:
    q = select(IssueRecord).where(IssueRecord.department_id == department_id)
    if exclude_month_id is not None:
        q = q.where(IssueRecord.month_id != exclude_month_id)
    return [issue_from_row(r) for r in db.scalars(q)]


def calculate_record(db: Session, rec: MonthRecord, now=None) -> MonthCalculation:
    return calculate_month(state_from_record(rec), now, issue_history(db, rec.department_id, rec.id))


def safe_calc(db: Session, rec: MonthRecord) -> tuple[MonthCalculation | None, str | None]:
    """A broken month must not break the page/dashboard for others."""
    try:
        return calculate_record(db, rec), None
    except ValueError as e:
        return None, str(e)


def find_month(db: Session, department_id: int, month: str) -> MonthRecord | None:
    return db.scalar(select(MonthRecord).where(MonthRecord.department_id == department_id, MonthRecord.month == month))


def latest_month(db: Session, department_id: int) -> MonthRecord | None:
    return db.scalar(select(MonthRecord).where(MonthRecord.department_id == department_id)
                     .order_by(MonthRecord.month.desc()).limit(1))


def month_list(db: Session, department_id: int) -> list[str]:
    return list(db.scalars(select(MonthRecord.month).where(MonthRecord.department_id == department_id)
                           .order_by(MonthRecord.month.desc())))
