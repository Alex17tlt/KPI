from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.accounts import create_reset_link, email_problem, normalize_email, reset_email
from app.audit import log_action
from app.config import settings
from app.db import get_db
from app.emails import send_email
from app.models import ROLES, STATUSES, Department, User
from app.security import hash_password, login_session, require_admin, temp_password
from app.services.departments import list_departments
from app.web import flash, render

router = APIRouter(prefix="/admin")


def _user(db: Session, uid: int) -> User:
    u = db.get(User, uid)
    if u is None:
        raise HTTPException(404, "Пользователь не найден")
    return u


def _dept_id(db: Session, raw: str) -> int | None:
    if not raw:
        return None
    if not raw.isdigit() or db.get(Department, int(raw)) is None:
        raise HTTPException(400, "Отдел не найден")
    return int(raw)


def _snapshot(u: User) -> dict:
    return {"role": u.role, "status": u.status, "department_id": u.department_id, "full_name": u.full_name}


def _temp_page(request: Request, u: User, password: str, created: bool):
    resp = render(request, "admin/temp_password.html", target=u, password=password, created=created)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/users")
def users_page(request: Request, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    users = db.scalars(select(User).order_by(case((User.status == "pending", 0), else_=1), User.email)).all()
    return render(request, "admin/users.html", users=users, departments=list_departments(db, only_active=False),
                  pending=sum(1 for u in users if u.status == "pending"))


@router.post("/users")
def create_user(request: Request, email: str = Form(...), full_name: str = Form(""), role: str = Form("employee"),
                department_id: str = Form(""), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    email = normalize_email(email)
    error = email_problem(email)
    if not error and role not in ROLES:
        error = "Неизвестная роль."
    if not error and db.scalar(select(User.id).where(User.email == email)):
        error = "Пользователь с такой почтой уже существует."
    if error:
        flash(request, error, "error")
        return RedirectResponse("/admin/users", status_code=303)
    password = temp_password()
    u = User(email=email, full_name=full_name.strip()[:200], password_hash=hash_password(password), role=role,
             status="active", department_id=_dept_id(db, department_id), must_change_password=True)
    db.add(u)
    log_action(db, "user_create", admin, details={"user": email, "role": role})
    db.commit()
    return _temp_page(request, u, password, created=True)


@router.post("/users/{uid}")
def update_user(request: Request, uid: int, background: BackgroundTasks, role: str = Form(...),
                status: str = Form(...), department_id: str = Form(""), full_name: str = Form(""),
                admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = _user(db, uid)
    if role not in ROLES or status not in STATUSES:
        flash(request, "Неизвестная роль или статус.", "error")
        return RedirectResponse("/admin/users", status_code=303)
    if u.id == admin.id and (role != "admin" or status != "active"):
        flash(request, "Нельзя понизить или отключить собственную учётную запись.", "error")
        return RedirectResponse("/admin/users", status_code=303)
    before = _snapshot(u)
    was_pending = u.status == "pending"
    u.full_name = full_name.strip()[:200]
    u.department_id = _dept_id(db, department_id)
    u.role = role
    if status == "disabled" and u.status != "disabled":
        u.session_version += 1
    u.status = status
    after = _snapshot(u)
    if before != after:
        log_action(db, "user_update", admin, details={"user": u.email, "before": before, "after": after})
        db.commit()
        flash(request, f"Изменения для {u.email} сохранены.")
        if was_pending and status == "active":
            background.add_task(send_email, u.email, "Доступ к KPI открыт",
                                f"<p>Ваш доступ подтверждён. Войти: <a href=\"{settings.BASE_URL}/login\">"
                                f"{settings.BASE_URL}/login</a></p>")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{uid}/reset-password")
def reset_password(request: Request, uid: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = _user(db, uid)
    if u.id == admin.id:
        flash(request, "Для своей учётной записи используйте смену пароля.", "error")
        return RedirectResponse("/admin/users", status_code=303)
    password = temp_password()
    u.password_hash = hash_password(password)
    u.must_change_password = True
    u.session_version += 1
    log_action(db, "admin_temp_password", admin, details={"user": u.email})
    db.commit()
    return _temp_page(request, u, password, created=False)


@router.post("/users/{uid}/send-reset")
def send_reset(request: Request, uid: int, background: BackgroundTasks, admin: User = Depends(require_admin),
               db: Session = Depends(get_db)):
    u = _user(db, uid)
    if u.status != "active":
        flash(request, "Письмо можно отправить только активному пользователю.", "error")
        return RedirectResponse("/admin/users", status_code=303)
    link = create_reset_link(db, u)
    log_action(db, "admin_send_reset", admin, details={"user": u.email})
    db.commit()
    subject, html = reset_email(u, link)
    background.add_task(send_email, u.email, subject, html)
    note = "" if settings.RESEND_API_KEY else " RESEND_API_KEY не задан — ссылка записана в лог сервера."
    flash(request, f"Письмо для сброса пароля отправлено на {u.email}.{note}")
    return RedirectResponse("/admin/users", status_code=303)


@router.post("/users/{uid}/impersonate")
def impersonate(request: Request, uid: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    u = _user(db, uid)
    if u.id == admin.id or u.status != "active":
        flash(request, "Войти можно только от имени другого активного пользователя.", "error")
        return RedirectResponse("/admin/users", status_code=303)
    log_action(db, "impersonate_start", admin, details={"user": u.email})
    db.commit()
    request.session.clear()
    request.session.update({"uid": u.id, "sv": u.session_version,
                            "imp_by": admin.id, "imp_sv": admin.session_version})
    return RedirectResponse("/", status_code=303)


@router.post("/stop-impersonation")
def stop_impersonation(request: Request, db: Session = Depends(get_db)):
    admin = db.get(User, request.session.get("imp_by") or 0)
    target_id = request.session.get("uid")
    if admin is None or not admin.is_admin or admin.status != "active" \
            or admin.session_version != request.session.get("imp_sv"):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)
    target = db.get(User, target_id) if target_id else None
    log_action(db, "impersonate_stop", admin, details={"user": target.email if target else None})
    db.commit()
    login_session(request, admin)
    return RedirectResponse("/admin/users", status_code=303)
