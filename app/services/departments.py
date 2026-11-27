from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import log_action
from app.engine.schema import Rules
from app.models import Department, RuleVersion, User


def list_departments(db: Session, only_active: bool = True) -> list[Department]:
    q = select(Department).order_by(Department.name)
    if only_active:
        q = q.where(Department.active.is_(True))
    return list(db.scalars(q))


def department_rules(dept: Department) -> Rules:
    return Rules.model_validate(dept.rules_json)


def save_department_rules(db: Session, dept: Department, rules: Rules, actor: User | None, reason: str,
                          source: str = "manual", changes: list[str] | None = None,
                          silent: bool = False) -> RuleVersion:
    """New template version; existing months keep their frozen snapshot.
    silent=True: the same change was already announced for the month (no duplicate notification)."""
    data = rules.model_dump(mode="json")
    dept.rules_version += 1
    dept.rules_json = data
    version = RuleVersion(department_id=dept.id, version=dept.rules_version, rules_json=data,
                          reason=reason, source=source, created_by_id=actor.id if actor else None)
    db.add(version)
    log_action(db, "rules_template", actor, department_id=dept.id, reason=reason,
               details={"version": dept.rules_version, "source": source, "changes": changes or [], "silent": silent})
    return version
