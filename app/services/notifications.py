"""In-app notifications, fed from the audit log: every logged action may notify someone.
To notify on a new action, add a branch to on_event()."""
import logging

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.engine.schema import fmt_num
from app.models import Department, Notification, User, now_iso

log = logging.getLogger("kpi.notify")


def _employees(db: Session, dept_id: int) -> list[User]:
    return list(db.scalars(select(User).where(User.department_id == dept_id, User.role == "employee",
                                              User.status == "active")))


def _founders(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.role.in_(("admin", "founder")), User.status == "active")))


def _admins(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.role == "admin", User.status == "active")))


def push(db: Session, users, kind: str, title: str, body: str = "", link: str = "",
         department_id: int | None = None, exclude_id: int | None = None) -> None:
    for u in {u.id: u for u in users}.values():
        if u.id == exclude_id:
            continue
        db.add(Notification(user_id=u.id, department_id=department_id, kind=kind, title=title[:300],
                            body=body[:4000], link=link[:300]))


def unread_count(db: Session, user: User) -> int:
    return db.scalar(select(func.count()).select_from(Notification)
                     .where(Notification.user_id == user.id, Notification.read_at.is_(None))) or 0


def latest(db: Session, user: User, limit: int = 100) -> list[Notification]:
    return list(db.scalars(select(Notification).where(Notification.user_id == user.id)
                           .order_by(Notification.id.desc()).limit(limit)))


def mark_all_read(db: Session, user: User) -> None:
    db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None))
               .values(read_at=now_iso()))


def _join(*parts: str) -> str:
    return "\n".join(p for p in parts if p)


def _bullets(items) -> str:
    return "\n".join(f"• {x}" for x in items or [])


def on_event(db: Session, action: str, actor: User | None, department_id: int | None,
             month: str | None, reason: str, details: dict) -> None:
    try:
        _dispatch(db, action, actor, department_id, month, reason, details or {})
    except Exception:  # a notification problem must never break the action itself
        log.exception("notification dispatch failed for %s", action)


def _dispatch(db, action, actor, department_id, month, reason, d):
    if action == "signup":
        push(db, _admins(db), "signup", "Новая заявка на доступ", d.get("email", ""), "/admin/users")
        return
    if department_id is None:
        return
    dept = db.get(Department, department_id)
    if dept is None:
        return
    ex = actor.id if actor else None
    q = f"?month={month}" if month else ""
    work, over = f"/w/{dept.slug}{q}", f"/d/{dept.slug}{q}"
    why = f"Причина: {reason}" if reason else ""

    def emp(kind, title, body="", link=work):
        push(db, _employees(db, department_id), kind, title, body, link, department_id, ex)

    def fnd(kind, title, body="", link=over):
        push(db, _founders(db), kind, title, body, link, department_id, ex)

    if action == "rules_month":
        emp("rules", f"Изменены правила за {month}", _join(_bullets(d.get("changes")), why))
    elif action == "rules_template" and not d.get("silent"):
        emp("rules", "Изменены правила с нового месяца",
            _join(_bullets(d.get("changes")), "Текущий месяц не меняется.", why))
    elif action == "rules_confirm":
        emp("rules", f"Правила за {month} подтверждены", why)
    elif action == "create_month":
        emp("month", f"Открыт месяц {month}", f"Учёт с {d.get('start_date', '')}")
    elif action == "issue_add":
        emp("issue", f"Новое замечание: {d.get('category', '')}",
            _join(d.get("description", ""), "Добавьте разбор: причина и меры, чтобы не повторилось."), work + "#issues")
    elif action == "issue_void":
        emp("issue", f"Замечание отменено: {d.get('category', '')}", why, work + "#issues")
    elif action == "issue_repeat":
        emp("issue", "Замечание отмечено как повтор (штраф удвоен)" if d.get("repeat") else "Отметка повтора снята",
            "", work + "#issues")
    elif action == "metrics":
        emp("metrics", f"Обновлены показатели за {month}")
    elif action == "close":
        emp("month", f"Месяц {month} закрыт",
            _join(f"KPI: {fmt_num(d.get('bonus'))}, итого с фиксом: {fmt_num(d.get('total'))}", why))
    elif action == "reopen":
        emp("month", f"Месяц {month} открыт для правок", why)
    elif action == "submit":
        checks = d.get("checks") or {}
        problems = (["сдано после срока"] if d.get("late") else []) + \
                   (["есть ответ «Нет»"] if not all(checks.values()) else [])
        if problems:
            fnd("violation", f"{dept.name}: нарушение — {d.get('title', '')}",
                _join("; ".join(problems), f"Комментарий: {d['comment']}" if d.get("comment") else ""),
                over + "#tasks")
    elif action == "analysis":
        fnd("analysis", f"{dept.name}: сдан разбор замечания", "", over + "#issues")
