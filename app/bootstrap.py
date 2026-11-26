import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Department, RuleVersion, User
from app.rules import load_template
from app.security import hash_password

log = logging.getLogger("kpi.bootstrap")


def bootstrap(db: Session) -> None:
    """Idempotent: ensure the admin account and the Finance department exist."""
    if settings.ADMIN_EMAIL and settings.ADMIN_PASSWORD:
        admin = db.scalar(select(User).where(User.email == settings.ADMIN_EMAIL))
        if admin is None:
            db.add(User(email=settings.ADMIN_EMAIL, full_name="Администратор",
                        password_hash=hash_password(settings.ADMIN_PASSWORD), role="admin", status="active"))
            log.info("Создан администратор %s", settings.ADMIN_EMAIL)
        elif admin.role != "admin" or admin.status != "active":
            admin.role, admin.status = "admin", "active"
    else:
        log.warning("ADMIN_EMAIL/ADMIN_PASSWORD не заданы — администратор не создан.")

    if db.scalar(select(Department.id).limit(1)) is None:
        rules = load_template("finance_notion5").model_dump(mode="json")
        dept = Department(slug="finance", name="Финансы", description="Head of Finance",
                          kpi_holder="Даниил", rules_json=rules, rules_version=1)
        db.add(dept)
        db.flush()
        db.add(RuleVersion(department_id=dept.id, version=1, rules_json=rules,
                           reason="Начальная редакция: Notion, 5 блоков, утро 12:00", source="seed"))
        log.info("Создан отдел «Финансы»")
    db.commit()
