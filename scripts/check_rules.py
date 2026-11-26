"""Pre-deploy check: validate every department's rules and recalculate every month.
Usage:  python -m scripts.check_rules    (exit code 1 on any problem)"""
import sys

# Windows consoles default to cp1252, and UnicodeEncodeError is a ValueError,
# so a Russian status line would be reported as a failed template.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from pydantic import ValidationError
from sqlalchemy import select

from app import models  # noqa: F401
from app.db import SessionLocal
from app.engine import generate_tasks
from app.engine.schema import Rules
from app.models import Department, MonthRecord
from app.services.months import calculate_record, current_month
from app.services.rules_edit import format_errors


def _err(e: Exception) -> str:
    return format_errors(e) if isinstance(e, ValidationError) else str(e)


def main() -> int:
    problems = 0
    with SessionLocal() as db:
        for dept in db.scalars(select(Department).order_by(Department.id)):
            try:
                rules = Rules.model_validate(dept.rules_json)
                month = current_month(rules.timezone)
                generate_tasks(month, rules, f"{month}-01")
                print(f"[ok]   {dept.slug}: шаблон v{dept.rules_version} ({rules.version})")
            except ValueError as e:
                problems += 1
                print(f"[FAIL] {dept.slug}: шаблон — {_err(e)}")
            for rec in db.scalars(select(MonthRecord).where(MonthRecord.department_id == dept.id)
                                  .order_by(MonthRecord.month)):
                try:
                    calc = calculate_record(db, rec)
                    print(f"[ok]   {dept.slug} {rec.month}: KPI {calc.bonus:.2f}, прогноз {calc.potential_bonus:.2f}")
                except ValueError as e:
                    problems += 1
                    print(f"[FAIL] {dept.slug} {rec.month}: {_err(e)}")
    print("Проблем не найдено." if not problems else f"Проблем: {problems}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
