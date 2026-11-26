from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import MonthRecord, User
from app.permissions import can_view_overview, ensure
from app.security import current_user
from app.services.actions import ensure_current_month
from app.services.departments import list_departments
from app.services.months import current_month, rules_of, safe_calc
from app.web import render

router = APIRouter()
TREND_MONTHS = 6


def _attention(recs: list[MonthRecord], current: dict | None, month: str) -> list[str]:
    out = []
    unclosed = [r.month for r in recs if r.month < month and not r.closure_json]
    if unclosed:
        out.append("Не закрыты месяцы: " + ", ".join(unclosed))
    if not current or not current.get("calc"):
        return out
    k, rules = current["calc"], current["rules"]
    if rules.draft:
        out.append("Правила месяца — черновик")
    if k.overdue_count:
        out.append(f"Просрочено и не сдано задач: {k.overdue_count}")
    violations = sum(1 for t in k.task_results if t.status == "violation")
    if violations:
        out.append(f"Сдано с нарушением: {violations}")
    out.extend(k.zeroed_blocks.values())
    late = sum(1 for p in k.penalty_rows if p.analysis_late)
    if late:
        out.append(f"Разбор замечаний просрочен: {late}")
    if k.missing_metrics and k.period_ended:
        labels = {m.key: m.label for m in rules.metrics}
        out.append("Не заполнено для закрытия: " + ", ".join(labels[x] for x in k.missing_metrics))
    return out


@router.get("/dashboard")
def dashboard(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ensure(can_view_overview(user))
    month = current_month()
    rows = []
    for dept in list_departments(db):
        if user.is_founder:
            ensure_current_month(db, dept, month)
        recs = list(db.scalars(select(MonthRecord).where(MonthRecord.department_id == dept.id)
                               .order_by(MonthRecord.month.desc()).limit(TREND_MONTHS)))[::-1]
        trend = []
        for r in recs:
            calc, error = safe_calc(db, r)
            item = {"month": r.month, "rec": r, "calc": calc, "error": error}
            if calc:
                item["rules"] = rules_of(r)
                item["final"] = not calc.provisional
                item["score"] = calc.score_pct if item["final"] else calc.potential_score_pct
            trend.append(item)
        current = trend[-1] if trend else None
        rows.append({"dept": dept, "trend": trend, "current": current, "attention": _attention(recs, current, month)})
    rows.sort(key=lambda r: r["current"]["score"] if r["current"] and "score" in r["current"] else 1000)
    return render(request, "dashboard.html", rows=rows, month=month)
