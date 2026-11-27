from sqlalchemy.orm import Session

from app.models import AuditLog, User, new_id, now_iso
from app.services.notifications import on_event


def log_action(db: Session, action: str, actor: User | None = None, *, department_id: int | None = None,
               month: str | None = None, reason: str = "", revision: int = 0, details: dict | None = None,
               actor_name: str | None = None) -> None:
    details = details or {}
    db.add(AuditLog(
        id=new_id(), department_id=department_id, month=month, action=action,
        actor_id=actor.id if actor else None,
        actor_name=actor_name or (actor.display_name if actor else "система"),
        at=now_iso(), reason=reason, revision=revision, details=details,
    ))
    on_event(db, action, actor, department_id, month, reason, details)
