from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.engine.calendar import iso, utcnow

ROLES = ("admin", "founder", "employee", "viewer")
STATUSES = ("pending", "active", "disabled")


def now_iso() -> str:
    return iso(utcnow())


def new_id() -> str:
    return str(uuid.uuid4())


class Department(Base):
    __tablename__ = "departments"
    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    kpi_holder: Mapped[str] = mapped_column(String(200), default="")
    rules_json: Mapped[dict] = mapped_column(JSON)          # template for new months
    rules_version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[str] = mapped_column(String(30), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(30), default=now_iso, onupdate=now_iso)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="employee")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"), nullable=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    session_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(30), default=now_iso)
    last_login_at: Mapped[str | None] = mapped_column(String(30), nullable=True)

    department: Mapped[Department | None] = relationship()

    @property
    def display_name(self) -> str:
        return self.full_name or self.email

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_founder(self) -> bool:
        return self.role in ("admin", "founder")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[str] = mapped_column(String(30), default=now_iso)
    expires_at: Mapped[str] = mapped_column(String(30))
    used_at: Mapped[str | None] = mapped_column(String(30), nullable=True)


class RuleVersion(Base):
    """History of a department's rules template (manual edits, imports, LLM-assisted changes)."""
    __tablename__ = "rule_versions"
    __table_args__ = (UniqueConstraint("department_id", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    rules_json: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(20), default="manual")   # seed | manual | import | llm
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[str] = mapped_column(String(30), default=now_iso)


class MonthRecord(Base):
    __tablename__ = "month_records"
    __table_args__ = (UniqueConstraint("department_id", "month"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id", ondelete="CASCADE"), index=True)
    month: Mapped[str] = mapped_column(String(7))
    rules_json: Mapped[dict] = mapped_column(JSON)       # frozen snapshot for this month
    start_date: Mapped[str] = mapped_column(String(10))
    metrics_json: Mapped[dict] = mapped_column(JSON, default=dict)   # {key: {value, evidence}}
    closure_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # {at, by, note}
    revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[str] = mapped_column(String(30), default=now_iso)
    updated_at: Mapped[str] = mapped_column(String(30), default=now_iso, onupdate=now_iso)

    department: Mapped[Department] = relationship()
    submissions: Mapped[list[TaskSubmission]] = relationship(
        back_populates="month_record", cascade="all, delete-orphan")
    issues: Mapped[list[IssueRecord]] = relationship(
        back_populates="month_record", cascade="all, delete-orphan", order_by="IssueRecord.created_at")


class TaskSubmission(Base):
    __tablename__ = "task_submissions"
    __table_args__ = (UniqueConstraint("month_id", "task_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    month_id: Mapped[int] = mapped_column(ForeignKey("month_records.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str] = mapped_column(String(200))
    at: Mapped[str] = mapped_column(String(30))
    checks_json: Mapped[dict] = mapped_column(JSON)
    comment: Mapped[str] = mapped_column(Text, default="")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    by_name: Mapped[str] = mapped_column(String(200), default="")

    month_record: Mapped[MonthRecord] = relationship(back_populates="submissions")


class IssueRecord(Base):
    __tablename__ = "issues"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    month_id: Mapped[int] = mapped_column(ForeignKey("month_records.id", ondelete="CASCADE"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id", ondelete="CASCADE"), index=True)
    date: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(30))
    category: Mapped[str] = mapped_column(String(120), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    found_by: Mapped[str] = mapped_column(String(200), default="")
    found_by_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    corrected_before_sending: Mapped[bool] = mapped_column(Boolean, default=False)
    repeat: Mapped[bool] = mapped_column(Boolean, default=False)
    analysis: Mapped[str] = mapped_column(Text, default="")
    analysis_at: Mapped[str | None] = mapped_column(String(30), nullable=True)
    created_at: Mapped[str] = mapped_column(String(30), default=now_iso)
    voided: Mapped[bool] = mapped_column(Boolean, default=False)
    void_reason: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    month_record: Mapped[MonthRecord] = relationship(back_populates="issues")


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id", ondelete="SET NULL"), nullable=True, index=True)
    month: Mapped[str | None] = mapped_column(String(7), nullable=True)
    action: Mapped[str] = mapped_column(String(60))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_name: Mapped[str] = mapped_column(String(200), default="")
    at: Mapped[str] = mapped_column(String(30), default=now_iso, index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    revision: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
