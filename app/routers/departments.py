from collections import Counter

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.engine import local_date, utcnow
from app.engine.calendar import last_date
from app.models import AuditLog, Department, IssueRecord, MonthRecord, User
from app.permissions import can_view_overview, ensure, get_department, month_param
from app.security import current_user, require_founder
from app.services.actions import (add_issue, build_preview, close_month, confirm_rules, ensure_current_month,
                                  open_month, reopen_month, save_rule_edits, set_metrics, set_repeat, void_issue)
from app.services.departments import department_rules
from app.services.months import current_month, find_month, month_list, rules_of, safe_calc
from app.services.rules_edit import diff_rules, format_errors, parse_edits
from app.web import flash, form_dict, form_int, render, run_action

router = APIRouter()


@router.get("/d/{slug}")
def department_month(request: Request, slug: str, month: str | None = None, status: str = "",
                     user: User = Depends(current_user), db: Session = Depends(get_db)):
    ensure(can_view_overview(user))
    dept = get_department(db, slug)
    cur = current_month()
    month = month_param(month, cur)
    rec = ensure_current_month(db, dept, month) if user.is_founder else find_month(db, dept.id, month)
    months = month_list(db, dept.id)
    ctx = {"dept": dept, "month": month, "rec": rec, "months": months, "status": status}
    tzname = department_rules(dept).timezone
    today = local_date(utcnow(), tzname)
    ctx["default_start"] = today.isoformat() if (month == cur and not months) else f"{month}-01"
    if rec is None:
        return render(request, "dept_month.html", **ctx)

    rules = rules_of(rec)
    calc, error = safe_calc(db, rec)
    ctx.update(rules=rules, calc=calc, error=error, closed=bool(rec.closure_json), issues=[], tasks=[], counts={})
    if calc:
        pen = {p.issue_id: p for p in calc.penalty_rows}
        ctx["issues"] = [{"r": i, "pen": pen.get(i.id)}
                         for i in sorted(rec.issues, key=lambda x: (x.date, x.created_at), reverse=True)]
        counts = Counter(t.status for t in calc.task_results)
        counts[""] = len(calc.task_results)
        ctx["counts"] = counts
        ctx["tasks"] = [t for t in calc.task_results if not status or t.status == status]
    ctx["today_max"] = min(today, last_date(month)).isoformat()
    ctx["categories"] = sorted({c for c in db.scalars(select(IssueRecord.category)
                                                      .where(IssueRecord.department_id == dept.id)) if c})
    ctx["audit"] = list(db.scalars(select(AuditLog).where(AuditLog.department_id == dept.id, AuditLog.month == month)
                                   .order_by(AuditLog.at.desc()).limit(50)))
    return render(request, "dept_month.html", **ctx)


@router.post("/d/{slug}/months")
def create_month(request: Request, slug: str, month: str = Form(...), start_date: str = Form(...),
                 user: User = Depends(require_founder), db: Session = Depends(get_db)):
    dept = get_department(db, slug)
    run_action(request, db, lambda: open_month(db, dept, month, start_date, user), f"Месяц {month} открыт.")
    return RedirectResponse(f"/d/{slug}?month={month}", status_code=303)


ACTIONS = {
    "issue": (lambda db, rec, u, f: add_issue(db, rec, u, f, form_int(f)), "Замечание добавлено.", "issues"),
    "void": (lambda db, rec, u, f: void_issue(db, rec, u, f.get("issue_id", ""), f.get("reason", ""), form_int(f)),
             "Замечание отменено.", "issues"),
    "repeat": (lambda db, rec, u, f: set_repeat(db, rec, u, f.get("issue_id", ""), f.get("value") == "1", form_int(f)),
               "Отметка повтора обновлена.", "issues"),
    "metrics": (lambda db, rec, u, f: set_metrics(db, rec, u, f, form_int(f)), "Показатели сохранены.", "metrics"),
    "close": (lambda db, rec, u, f: close_month(db, rec, u, f.get("note", ""), form_int(f)), "Месяц закрыт.", "closure"),
    "reopen": (lambda db, rec, u, f: reopen_month(db, rec, u, f.get("reason", ""), form_int(f)),
               "Месяц снова открыт.", "closure"),
    "confirm-rules": (lambda db, rec, u, f: confirm_rules(db, rec, u, f.get("reason", ""), form_int(f)),
                      "Правила месяца подтверждены.", "closure"),
}


@router.post("/d/{slug}/{month}/{action}")
def month_action(request: Request, slug: str, month: str, action: str, form: dict = Depends(form_dict),
                 user: User = Depends(require_founder), db: Session = Depends(get_db)):
    if action not in ACTIONS:
        raise HTTPException(404, "Неизвестное действие")
    dept = get_department(db, slug)
    month = month_param(month, month)
    rec = find_month(db, dept.id, month)
    if rec is None:
        raise HTTPException(404, "Месяц не открыт")
    fn, ok, anchor = ACTIONS[action]
    run_action(request, db, lambda: fn(db, rec, user, form), ok)
    return RedirectResponse(f"/d/{slug}?month={month}#{anchor}", status_code=303)


# ── weights & rules editor ────────────────────────────────────────────────────
def _rules_ctx(dept: Department, rec: MonthRecord | None, month: str, values: dict | None = None) -> dict:
    template = department_rules(dept)
    base = rules_of(rec) if rec else template
    month_editable = bool(rec) and not rec.closure_json
    values = values or {}
    return {"dept": dept, "rec": rec, "month": month, "base": base, "template": template,
            "month_editable": month_editable, "template_diff": diff_rules(base, template) if rec else [],
            "values": values, "scope": values.get("scope") or ("both" if month_editable else "template"),
            "preview": None}


@router.get("/d/{slug}/rules")
def rules_page(request: Request, slug: str, month: str | None = None,
               user: User = Depends(require_founder), db: Session = Depends(get_db)):
    dept = get_department(db, slug)
    month = month_param(month, current_month())
    return render(request, "rules_edit.html", **_rules_ctx(dept, find_month(db, dept.id, month), month))


@router.post("/d/{slug}/rules")
def rules_post(request: Request, slug: str, form: dict = Depends(form_dict),
               user: User = Depends(require_founder), db: Session = Depends(get_db)):
    dept = get_department(db, slug)
    month = month_param(form.get("month"), current_month())
    rec = find_month(db, dept.id, month)
    scope = form.get("scope") if form.get("scope") in ("both", "month", "template") else "template"
    form["scope"] = scope
    ctx = _rules_ctx(dept, rec, month, form)
    base = rules_of(rec) if rec and scope != "template" else department_rules(dept)
    try:
        edits = parse_edits(form, base)
        if form.get("do") == "preview":
            ctx["preview"] = build_preview(db, dept, rec, edits, scope)
            return render(request, "rules_edit.html", **ctx)
    except ValidationError as e:
        flash(request, "Правила не прошли проверку: " + format_errors(e), "error")
        return render(request, "rules_edit.html", status_code=400, **ctx)
    except ValueError as e:
        flash(request, str(e), "error")
        return render(request, "rules_edit.html", status_code=400, **ctx)
    ok = run_action(request, db, lambda: save_rule_edits(db, dept, rec, user, edits, scope,
                                                         form.get("reason", ""), form_int(form)),
                    "Изменения сохранены.")
    if ok:
        return RedirectResponse(f"/d/{slug}?month={month}", status_code=303)
    return render(request, "rules_edit.html", status_code=400, **_rules_ctx(dept, rec, month, form))
