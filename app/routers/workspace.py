from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.engine import local_date, utcnow
from app.models import User
from app.permissions import can_work, ensure, get_department, month_param
from app.security import current_user
from app.services.actions import add_analysis, ensure_current_month, submit_task
from app.services.departments import list_departments
from app.services.months import current_month, find_month, month_list, rules_of, safe_calc
from app.web import form_dict, form_int, render, run_action

router = APIRouter()


@router.get("/w")
def workspace_index(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if user.role == "employee":
        if not user.department:
            return render(request, "error.html", title="Отдел не назначен",
                          text="Администратор ещё не назначил вам отдел.")
        return RedirectResponse(f"/w/{user.department.slug}", status_code=303)
    ensure(user.is_founder)
    depts = list_departments(db)
    if len(depts) == 1:
        return RedirectResponse(f"/w/{depts[0].slug}", status_code=303)
    return render(request, "pick_department.html", depts=depts)


@router.get("/w/{slug}")
def workspace(request: Request, slug: str, month: str | None = None,
              user: User = Depends(current_user), db: Session = Depends(get_db)):
    dept = get_department(db, slug)
    ensure(can_work(user, dept))
    month = month_param(month, current_month())
    rec = ensure_current_month(db, dept, month)
    ctx = {"dept": dept, "month": month, "rec": rec, "months": month_list(db, dept.id)}
    if rec is None:
        return render(request, "workspace.html", **ctx)
    rules = rules_of(rec)
    calc, error = safe_calc(db, rec)
    ctx.update(rules=rules, calc=calc, error=error, closed=bool(rec.closure_json),
               now_tasks=[], upcoming=[], done=[], issues=[])
    if calc:
        today = local_date(utcnow(), rules.timezone)
        horizon = today + timedelta(days=7)
        results = calc.task_results
        ctx["now_tasks"] = [r for r in results if r.submission is None
                            and (r.status == "overdue" or r.task.date <= today)]
        ctx["upcoming"] = [r for r in results if r.submission is None and r.status == "pending"
                           and today < r.task.date <= horizon]
        ctx["done"] = [r for r in results if r.submission is not None][::-1]
        pen = {p.issue_id: p for p in calc.penalty_rows}
        ctx["issues"] = [{"r": i, "pen": pen.get(i.id)}
                         for i in sorted(rec.issues, key=lambda x: x.date, reverse=True) if not i.voided]
    return render(request, "workspace.html", **ctx)


def _month_for_work(db: Session, user: User, slug: str, month: str):
    dept = get_department(db, slug)
    ensure(can_work(user, dept))
    rec = find_month(db, dept.id, month_param(month, month))
    if rec is None:
        raise HTTPException(404, "Месяц не открыт")
    return dept, rec


@router.post("/w/{slug}/{month}/submit")
def submit(request: Request, slug: str, month: str, form: dict = Depends(form_dict),
           user: User = Depends(current_user), db: Session = Depends(get_db)):
    dept, rec = _month_for_work(db, user, slug, month)
    answers = {k[len("check_"):]: v for k, v in form.items() if k.startswith("check_")}
    run_action(request, db, lambda: submit_task(db, rec, user, form.get("task_id", ""), answers,
                                                form.get("comment", ""), form_int(form)), "Задача сдана.")
    return RedirectResponse(f"/w/{slug}?month={rec.month}", status_code=303)


@router.post("/w/{slug}/{month}/analysis")
def analysis(request: Request, slug: str, month: str, form: dict = Depends(form_dict),
             user: User = Depends(current_user), db: Session = Depends(get_db)):
    dept, rec = _month_for_work(db, user, slug, month)
    run_action(request, db, lambda: add_analysis(db, rec, user, form.get("issue_id", ""),
                                                 form.get("analysis", ""), form_int(form)), "Разбор сохранён.")
    return RedirectResponse(f"/w/{slug}?month={rec.month}#issues", status_code=303)
