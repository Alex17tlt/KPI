from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.calendar import check_month
from app.models import Department, User
from app.security import Forbidden


def get_department(db: Session, slug: str) -> Department:
    dept = db.scalar(select(Department).where(Department.slug == slug, Department.active.is_(True)))
    if dept is None:
        raise HTTPException(404, "Отдел не найден")
    return dept


def can_view_overview(user: User) -> bool:
    return user.is_founder or user.role == "viewer"


def can_work(user: User, dept: Department) -> bool:
    """Submit tasks / write analyses: the department's employee, plus founders & admin (unified access)."""
    return user.is_founder or (user.role == "employee" and user.department_id == dept.id)


def ensure(condition: bool) -> None:
    if not condition:
        raise Forbidden()


def month_param(value: str | None, default: str) -> str:
    if not value:
        return default
    try:
        return check_month(value)
    except ValueError:
        raise HTTPException(400, "Месяц должен быть в формате ГГГГ-ММ") from None
