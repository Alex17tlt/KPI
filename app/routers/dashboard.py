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
from app.services.visuals import attention, block_views, is_final, money_split, score
from app.web import render

router = APIRouter()
TREND_MONTHS = 6


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
                item.update(rules=rules_of(r), final=is_final(calc), score=score(calc))
            trend.append(item)
        current = trend[-1] if trend else None
        if current and current.get("calc"):
            current["blocks"] = block_views(current["rules"], current["calc"])
            current["money"] = money_split(current["calc"])
        unclosed = [r.month for r in recs if r.month < month and not r.closure_json]
        att = attention(current.get("calc") if current else None, current.get("rules") if current else None, unclosed)
        rows.append({"dept": dept, "trend": trend, "current": current, "attention": att})
    rows.sort(key=lambda r: r["current"]["score"] if r["current"] and "score" in r["current"] else 1000)
    return render(request, "dashboard.html", rows=rows, month=month)
