import re
from datetime import timedelta
from html import escape

from sqlalchemy.orm import Session

from app.config import settings
from app.engine.calendar import iso, utcnow
from app.models import PasswordResetToken, User
from app.security import new_token

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RESET_TTL_MIN = 60


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def email_problem(email: str) -> str | None:
    if not EMAIL_RE.match(email) or len(email) > 320:
        return "Укажите корректный адрес почты."
    domains = settings.ALLOWED_EMAIL_DOMAINS
    if domains and email.rsplit("@", 1)[1] not in domains:
        return "Регистрация доступна только для адресов: " + ", ".join("@" + d for d in domains)
    return None


def password_problem(password: str) -> str | None:
    if len(password) < 10:
        return "Пароль должен быть не короче 10 символов."
    if len(password) > 128:
        return "Пароль слишком длинный (максимум 128 символов)."
    if password.isdigit() or password.isalpha():
        return "Пароль должен содержать и буквы, и цифры или символы."
    return None


def create_reset_link(db: Session, user: User) -> str:
    raw, token_hash = new_token()
    db.add(PasswordResetToken(user_id=user.id, token_hash=token_hash,
                              expires_at=iso(utcnow() + timedelta(minutes=RESET_TTL_MIN))))
    return f"{settings.BASE_URL}/reset/{raw}"


def reset_email(user: User, link: str) -> tuple[str, str]:
    hello = f"Здравствуйте, {escape(user.full_name)}!" if user.full_name else "Здравствуйте!"
    html = (f"<p>{hello}</p><p>Чтобы задать новый пароль для KPI Seven Group, перейдите по ссылке "
            f"(действует {RESET_TTL_MIN} минут):</p><p><a href=\"{escape(link)}\">{escape(link)}</a></p>"
            f"<p>Если вы не запрашивали восстановление, просто проигнорируйте письмо.</p>")
    return "Восстановление пароля — KPI Seven Group", html
