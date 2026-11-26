from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.accounts import create_reset_link, email_problem, normalize_email, password_problem, reset_email
from app.audit import log_action
from app.config import settings
from app.db import get_db
from app.emails import send_email
from app.engine.calendar import parse_ts, utcnow
from app.models import PasswordResetToken, User, now_iso
from app.security import (current_user, hash_password, hash_token, limiter, load_session_user, login_session,
                          needs_rehash, verify_password)
from app.web import flash, render

router = APIRouter()


def _ip(request: Request) -> str:
    return request.client.host if request.client else "-"


def _safe_next(value: str | None) -> str:
    return value if value and value.startswith("/") and not value.startswith("//") else "/"


@router.get("/login")
def login_page(request: Request, next: str = "/", db: Session = Depends(get_db)):
    if load_session_user(request, db):
        return RedirectResponse("/", status_code=303)
    return render(request, "auth/login.html", next=_safe_next(next))


@router.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...), next: str = Form("/"),
          db: Session = Depends(get_db)):
    email = normalize_email(email)
    key = f"login:{_ip(request)}:{email}"

    def fail(msg: str, code: int = 400):
        return render(request, "auth/login.html", error=msg, email=email, next=_safe_next(next), status_code=code)

    if limiter.blocked(key):
        return fail("Слишком много попыток входа. Подождите 15 минут.", 429)
    user = db.scalar(select(User).where(User.email == email))
    if not verify_password(user.password_hash if user else None, password):
        limiter.hit(key)
        return fail("Неверная почта или пароль.")
    if user.status == "pending":
        return fail("Учётная запись ожидает подтверждения администратором.", 403)
    if user.status == "disabled":
        return fail("Учётная запись отключена. Обратитесь к администратору.", 403)
    limiter.reset(key)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = now_iso()
    db.commit()
    login_session(request, user)
    return RedirectResponse(_safe_next(next), status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/signup")
def signup_page(request: Request):
    return render(request, "auth/signup.html")


@router.post("/signup")
def signup(request: Request, background: BackgroundTasks, full_name: str = Form(""), email: str = Form(...),
           password: str = Form(...), password2: str = Form(...), db: Session = Depends(get_db)):
    email = normalize_email(email)
    error = email_problem(email) or password_problem(password)
    if not error and password != password2:
        error = "Пароли не совпадают."
    if not error and db.scalar(select(User.id).where(User.email == email)):
        error = "Пользователь с такой почтой уже зарегистрирован. Войдите или восстановите пароль."
    if error:
        return render(request, "auth/signup.html", error=error, email=email, full_name=full_name, status_code=400)
    user = User(email=email, full_name=full_name.strip()[:200], password_hash=hash_password(password),
                role="employee", status="pending")
    db.add(user)
    log_action(db, "signup", None, actor_name=email, details={"email": email})
    db.commit()
    if settings.ADMIN_EMAIL:
        background.add_task(send_email, settings.ADMIN_EMAIL, "Новая заявка на доступ к KPI",
                            f"<p>Заявка от {email}. Подтвердите доступ: "
                            f"<a href=\"{settings.BASE_URL}/admin/users\">{settings.BASE_URL}/admin/users</a></p>")
    return render(request, "auth/message.html", title="Заявка отправлена",
                  text="Администратор подтвердит доступ и назначит отдел. После этого вы сможете войти.")


@router.get("/forgot")
def forgot_page(request: Request):
    return render(request, "auth/forgot.html")


@router.post("/forgot")
def forgot(request: Request, background: BackgroundTasks, email: str = Form(...), db: Session = Depends(get_db)):
    email = normalize_email(email)
    key = f"forgot:{_ip(request)}"
    if not limiter.blocked(key, limit=5):
        limiter.hit(key)
        user = db.scalar(select(User).where(User.email == email))
        if user and user.status == "active":
            link = create_reset_link(db, user)
            log_action(db, "password_reset_requested", user)
            db.commit()
            subject, html = reset_email(user, link)
            background.add_task(send_email, user.email, subject, html)
    return render(request, "auth/message.html", title="Проверьте почту",
                  text="Если такая учётная запись существует, мы отправили письмо со ссылкой. Ссылка действует 60 минут.")


def _valid_token(db: Session, raw: str) -> PasswordResetToken | None:
    token = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hash_token(raw)))
    if token is None or token.used_at or parse_ts(token.expires_at) < utcnow():
        return None
    return token


@router.get("/reset/{raw}")
def reset_page(request: Request, raw: str, db: Session = Depends(get_db)):
    if not _valid_token(db, raw):
        return render(request, "auth/message.html", title="Ссылка недействительна",
                      text="Ссылка устарела или уже использована. Запросите новую.", status_code=400)
    return render(request, "auth/reset.html", raw=raw)


@router.post("/reset/{raw}")
def reset(request: Request, raw: str, password: str = Form(...), password2: str = Form(...),
          db: Session = Depends(get_db)):
    token = _valid_token(db, raw)
    user = db.get(User, token.user_id) if token else None
    if not token or not user or user.status != "active":
        return render(request, "auth/message.html", title="Ссылка недействительна",
                      text="Ссылка устарела или уже использована. Запросите новую.", status_code=400)
    error = password_problem(password) or (None if password == password2 else "Пароли не совпадают.")
    if error:
        return render(request, "auth/reset.html", raw=raw, error=error, status_code=400)
    user.password_hash = hash_password(password)
    user.must_change_password = False
    user.session_version += 1
    db.execute(update(PasswordResetToken).where(PasswordResetToken.user_id == user.id,
                                                PasswordResetToken.used_at.is_(None)).values(used_at=now_iso()))
    log_action(db, "password_reset", user)
    db.commit()
    login_session(request, user)
    flash(request, "Пароль обновлён.")
    return RedirectResponse("/", status_code=303)


@router.get("/account/password")
def password_page(request: Request, user: User = Depends(current_user)):
    return render(request, "auth/password.html", forced=user.must_change_password)


@router.post("/account/password")
def change_password(request: Request, current: str = Form(...), password: str = Form(...),
                    password2: str = Form(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    def fail(msg: str):
        return render(request, "auth/password.html", error=msg, forced=user.must_change_password, status_code=400)

    if request.session.get("imp_by"):
        return fail("Во время входа от имени другого пользователя менять пароль нельзя.")
    if not verify_password(user.password_hash, current):
        return fail("Текущий пароль указан неверно.")
    if (problem := password_problem(password)):
        return fail(problem)
    if password != password2:
        return fail("Пароли не совпадают.")
    if password == current:
        return fail("Новый пароль должен отличаться от текущего.")
    user.password_hash = hash_password(password)
    user.must_change_password = False
    user.session_version += 1
    log_action(db, "password_change", user)
    db.commit()
    login_session(request, user)
    flash(request, "Пароль изменён.")
    return RedirectResponse("/", status_code=303)
