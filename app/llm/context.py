"""Compact, pre-calculated facts for LLM prompts. The LLM explains numbers; it never computes them."""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine import local_date, utcnow
from app.engine.schema import MetricScoring, Rules
from app.models import AuditLog, Department, MonthRecord
from app.services.departments import department_rules
from app.services.months import rules_of, safe_calc
from app.services.visuals import is_final, score

STATUS_RU = {"pending": "ожидается", "overdue": "просрочено, не сдано", "done": "сдано", "violation": "нарушение"}
SCOPE_RU = {"rules_month": "правила месяца", "rules_template": "шаблон для новых месяцев",
            "rules_confirm": "подтверждение правил"}


def _t(s: str | None, n: int = 300) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[:n] + "…"


def _metric_tiers(sc: MetricScoring) -> str:
    parts = []
    for t in sc.tiers:
        cond = []
        if t.min_value is not None:
            cond.append(f"≥{t.min_value:g}")
        if t.max_value is not None:
            cond.append(f"≤{t.max_value:g}")
        if t.max_events is not None:
            cond.append(f"событий «{sc.event_kind}» ≤{t.max_events}")
        parts.append(" и ".join(cond) + f" → {t.coef * 100:g}%")
    return f"показатель {sc.metric}: " + "; ".join(parts) + f"; иначе {sc.else_coef * 100:g}%"


def rules_summary(rules: Rules) -> dict:
    blocks = []
    for b in rules.blocks:
        if b.amount <= 0:
            continue
        sc = b.scoring
        if isinstance(sc, MetricScoring):
            scoring = _metric_tiers(sc)
        else:
            scoring = "; ".join(f"≤{t.max_violations} нарушений → {t.coef * 100:g}%" for t in sc.tiers) + \
                      f"; больше → {sc.else_coef * 100:g}%"
        blocks.append({"id": b.id, "name": b.name, "max": b.amount, "description": b.description, "scoring": scoring})
    p = rules.penalties
    return {"title": rules.title, "draft": rules.draft, "currency": rules.currency, "fixed": rules.fixed,
            "bonus_cap": rules.bonus_cap, "blocks": blocks,
            "penalties": {"by_severity": p.severity_amounts, "repeat_multiplier": p.repeat_multiplier,
                          "analysis_deadline_working_days": p.analysis_deadline_working_days,
                          "late_analysis_fee": p.late_analysis_fee}}


def month_facts(db: Session, rec: MonthRecord, detailed: bool) -> dict:
    out: dict = {"month": rec.month, "closed": bool(rec.closure_json)}
    calc, err = safe_calc(db, rec)
    if err:
        out["error"] = err
        return out
    rules, final = rules_of(rec), is_final(calc)
    out.update(status="итог" if final else "прогноз", score_pct=score(calc),
               kpi=calc.bonus if final else calc.potential_bonus, kpi_max=calc.max_bonus,
               total_with_fixed=calc.total if final else calc.potential_total, penalties=calc.penalties_raw,
               tasks_total=calc.total_tasks, tasks_submitted=calc.completed_count, tasks_overdue=calc.overdue_count)
    if not detailed:
        out["blocks"] = [{"name": b.name, "coef_pct": b.coefficient * 100, "violations": b.violations}
                         for b in calc.blocks]
        return out
    out["blocks"] = [{"id": b.id, "name": b.name, "max": b.max, "coef_pct": b.coefficient * 100,
                      "violations": b.violations, "forecast": b.potential, "earned": b.earned,
                      "tasks_pending": b.pending, "note": b.reason} for b in calc.blocks]
    out["metrics"] = rec.metrics_json or {}
    viol = []
    for r in calc.task_results:
        if r.status not in ("violation", "overdue"):
            continue
        item = {"date": r.task.date.isoformat(), "block": r.task.block, "task": r.task.title,
                "status": STATUS_RU[r.status]}
        s = r.submission
        if s:
            item["late"] = s.at > r.task.deadline
            failed = [c.label for c in r.task.checks if s.checks.get(c.key) is not True]
            if failed:
                item["failed_checks"] = failed
            if s.comment:
                item["comment"] = _t(s.comment)
        viol.append(item)
    out["violations_total"] = len(viol)
    out["violations"] = viol[-40:]
    pen = {p.issue_id: p for p in calc.penalty_rows}
    labels = rules.penalties.kind_labels
    out["issues"] = [{"date": i.date, "kind": labels.get(i.kind, i.kind), "severity": i.severity,
                      "category": i.category, "description": _t(i.description), "repeat": i.repeat,
                      "voided": i.voided, "penalty": pen[i.id].total if i.id in pen else 0,
                      "analysis": _t(i.analysis) or None,
                      "analysis_late": pen[i.id].analysis_late if i.id in pen else False}
                     for i in rec.issues][-30:]
    out["warnings"] = calc.warnings
    if rec.closure_json:
        out["closure_note"] = _t(rec.closure_json.get("note"))
    return out


def department_facts(db: Session, dept: Department, months: int = 6, detailed: int = 3) -> dict:
    recs = list(db.scalars(select(MonthRecord).where(MonthRecord.department_id == dept.id)
                           .order_by(MonthRecord.month.desc()).limit(months)))
    rules = rules_of(recs[0]) if recs else department_rules(dept)
    changes = db.scalars(select(AuditLog).where(AuditLog.department_id == dept.id,
                                                AuditLog.action.in_(tuple(SCOPE_RU)))
                         .order_by(AuditLog.at.desc()).limit(10))
    return {"department": dept.name, "kpi_holder": dept.kpi_holder, "rules": rules_summary(rules),
            "months": [month_facts(db, r, i < detailed) for i, r in enumerate(recs)],
            "rule_changes": [{"date": a.at[:10], "by": a.actor_name, "scope": SCOPE_RU[a.action],
                              "month": a.month, "reason": _t(a.reason), "changes": (a.details or {}).get("changes", [])}
                             for a in changes]}


def build_context(db: Session, depts: list[Department]) -> str:
    data = {"today": local_date(utcnow(), "Europe/Moscow").isoformat(),
            "departments": [department_facts(db, d) for d in depts]}
    return json.dumps(data, ensure_ascii=False, default=str)
