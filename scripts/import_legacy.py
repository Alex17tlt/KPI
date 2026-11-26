"""Import data from the old ChatGPT-Sites app.

Export: sign in to the old site as owner, open <site>/api/kpi in the browser and save
the JSON (e.g. legacy.json). Then:  python -m scripts.import_legacy legacy.json [--replace]
"""
import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

from app import models  # noqa: F401
from app.bootstrap import bootstrap
from app.db import Base, SessionLocal, engine
from app.engine import iso, parse_ts
from app.engine.schema import Rules
from app.models import AuditLog, Department, IssueRecord, MonthRecord, TaskSubmission
from app.rules import load_template
from app.services.months import calculate_record

TIME_MAP = {"morningTime": "morning", "eveningTime": "evening", "dzTime": "dz", "kzTime": "kz",
            "paymentTime": "payment", "deadlineTime": "deadline", "preparationTime": "preparation"}


def rules_from_policy(policy: dict) -> Rules:
    data = load_template(f"finance_{policy['profile']}").model_dump(mode="json")
    data["fixed"] = policy["fixed"]
    for block in data["blocks"]:
        block["amount"] = policy["amounts"][str(block["id"])]
    for js_key, key in TIME_MAP.items():
        if js_key in policy:
            data["times"][key] = policy[js_key]
    data["settings"]["payroll_day"] = policy.get("payrollDay")
    data["holidays"] = sorted(set(policy.get("holidays") or []))
    data["working_dates"] = sorted(set(policy.get("workingDates") or []))
    data["draft"] = bool(policy.get("draft", data["draft"]))
    return Rules.model_validate(data)


def _ts(value):
    return iso(parse_ts(value)) if value else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file", type=Path)
    ap.add_argument("--department", default="finance")
    ap.add_argument("--replace", action="store_true", help="перезаписать уже импортированные месяцы")
    args = ap.parse_args()

    data = json.loads(args.file.read_text(encoding="utf-8"))
    months = data.get("months") or []
    names = {m["userId"]: (m.get("displayName") or m.get("email") or m["userId"]) for m in data.get("members") or []}
    if not months:
        print("В файле нет месяцев (ожидается JSON из GET /api/kpi).")
        return 1

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        bootstrap(db)
        dept = db.scalar(select(Department).where(Department.slug == args.department))
        if dept is None:
            print(f"Отдел «{args.department}» не найден.")
            return 1

        imported: list[str] = []
        for rec in sorted(months, key=lambda r: r["month"]):
            month = rec["month"]
            existing = db.scalar(select(MonthRecord).where(MonthRecord.department_id == dept.id,
                                                           MonthRecord.month == month))
            if existing and not args.replace:
                print(f"{month}: уже есть, пропуск (используйте --replace)")
                continue
            if existing:
                db.delete(existing)
                db.flush()
            policy = rec["policy"]
            rules = rules_from_policy(policy)
            closure = rec.get("closure")
            row = MonthRecord(
                department_id=dept.id, month=month, rules_json=rules.model_dump(mode="json"),
                start_date=policy["startDate"], revision=int(rec.get("revision") or 0),
                metrics_json={"revenue_percent": {"value": rec.get("revenuePercent"),
                                                  "evidence": rec.get("revenueEvidence") or ""}},
                closure_json={"at": _ts(closure["at"]), "by": names.get(closure.get("by"), closure.get("by", "")),
                              "note": closure.get("note", "")} if closure else None,
            )
            for sub in (rec.get("submissions") or {}).values():
                row.submissions.append(TaskSubmission(
                    task_id=sub["taskId"], at=_ts(sub["at"]), checks_json=sub.get("checks") or {},
                    comment=sub.get("comment") or "", by_name=names.get(sub.get("by"), sub.get("by") or "")))
            for i in rec.get("issues") or []:
                row.issues.append(IssueRecord(
                    id=i["id"], department_id=dept.id, date=i["date"], kind=i["kind"], severity=i["severity"],
                    category=i.get("category") or "", description=i.get("description") or "",
                    found_by=i.get("foundBy") or "", found_by_owner=bool(i.get("foundByDaniel")),
                    corrected_before_sending=bool(i.get("correctedBeforeSending")), repeat=bool(i.get("repeat")),
                    analysis=i.get("analysis") or "", analysis_at=_ts(i.get("analysisAt")),
                    created_at=_ts(i.get("createdAt")) or "", voided=bool(i.get("voided"))))
            db.add(row)
            imported.append(month)
            print(f"{month}: импортировано (редакция {policy['profile']}, отметок {len(row.submissions)}, "
                  f"замечаний {len(row.issues)})")

        added = 0
        for a in data.get("audit") or []:
            if db.get(AuditLog, a["id"]):
                continue
            db.add(AuditLog(id=a["id"], department_id=dept.id, month=a.get("month"), action=a["action"],
                            actor_name=a.get("actorName") or "", at=_ts(a["at"]), reason=a.get("reason") or "",
                            revision=int(a.get("revision") or 0),
                            details={"legacy": True, "actor_id": a.get("actorId"), "data": a.get("details")}))
            added += 1
        db.commit()
        print(f"Журнал: добавлено записей {added}")

        print("\nПроверка расчёта (сравните с исходным сайтом):")
        for month in imported:
            rec = db.scalar(select(MonthRecord).where(MonthRecord.department_id == dept.id, MonthRecord.month == month))
            calc = calculate_record(db, rec)
            state = "предварительно" if calc.provisional else "итог"
            print(f"  {month}: KPI {calc.bonus:.2f} (прогноз {calc.potential_bonus:.2f}), штрафы {calc.penalties_raw:.2f}, "
                  f"итого {calc.total:.2f} — {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
