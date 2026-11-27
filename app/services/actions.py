"""Every state change on a department month: validate → bump revision (optimistic lock) → audit → commit."""
from __future__ import annotations

import math

from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import log_action
from app.engine import calculate_month, generate_tasks, local_date, month_of, parse_date, utcnow
from app.engine.calendar import check_month
from app.engine.schema import Rules
from app.models import Department, IssueRecord, MonthRecord, TaskSubmission, User, new_id, now_iso
from app.services.departments import department_rules, save_department_rules
from app.services.months import (calculate_record, current_month, find_month, issue_history, latest_month,
                                 rules_of, state_from_record)
from app.services.rules_edit import RuleEdits, apply_edits, diff_rules, format_errors

STALE = "Данные месяца уже изменены в другой вкладке или другим участником. Обновите страницу и повторите."


class ActionError(ValueError):
    pass


# ── helpers ───────────────────────────────────────────────────────────────────
def _text(value, label: str, max_len: int, required: bool = True) -> str:
    v = (value or "").strip()
    if required and not v:
        raise ActionError(f"{label}: заполните поле.")
    if len(v) > max_len:
        raise ActionError(f"{label}: не более {max_len} символов.")
    return v


def _ensure_open(rec: MonthRecord) -> None:
    if rec.closure_json:
        raise ActionError("Месяц закрыт. Для правок его нужно открыть с указанием причины.")


def _bump(db: Session, rec: MonthRecord, expected: int | None) -> int:
    if expected is None or rec.revision != expected:
        raise ActionError(STALE)
    res = db.execute(update(MonthRecord)
                     .where(MonthRecord.id == rec.id, MonthRecord.revision == expected)
                     .values(revision=expected + 1, updated_at=now_iso())
                     .execution_options(synchronize_session=False))
    if res.rowcount != 1:
        raise ActionError(STALE)
    db.expire(rec, ["revision", "updated_at"])
    return expected + 1


def _log(db, rec, user, action, revision, reason="", **details):
    log_action(db, action, user, department_id=rec.department_id, month=rec.month,
               reason=reason, revision=revision, details=details)


def _today(rules: Rules):
    return local_date(utcnow(), rules.timezone)


def _issue(rec: MonthRecord, issue_id: str) -> IssueRecord:
    item = next((x for x in rec.issues if x.id == issue_id), None)
    if item is None:
        raise ActionError("Замечание не найдено.")
    return item


# ── months ────────────────────────────────────────────────────────────────────
def open_month(db: Session, dept: Department, month: str, start_date: str, actor: User | None) -> MonthRecord:
    try:
        check_month(month)
        start = parse_date((start_date or "").strip())
    except ValueError as e:
        raise ActionError(str(e)) from None
    if month_of(start) != month:
        raise ActionError("Начало учёта должно быть внутри выбранного месяца.")
    if find_month(db, dept.id, month):
        raise ActionError("Такой месяц уже открыт.")
    rules = department_rules(dept)
    generate_tasks(month, rules, start.isoformat())
    rec = MonthRecord(department_id=dept.id, month=month, rules_json=rules.model_dump(mode="json"),
                      start_date=start.isoformat(), metrics_json={}, revision=0)
    db.add(rec)
    log_action(db, "create_month", actor, department_id=dept.id, month=month,
               details={"start_date": start.isoformat(), "rules": rules.version, "template_version": dept.rules_version})
    db.commit()
    return rec


def ensure_current_month(db: Session, dept: Department, month: str) -> MonthRecord | None:
    """Auto-opens the current month once the department already has history (start = 1st)."""
    rec = find_month(db, dept.id, month)
    if rec or month != current_month() or latest_month(db, dept.id) is None:
        return rec
    try:
        return open_month(db, dept, month, f"{month}-01", None)
    except (IntegrityError, ActionError):
        db.rollback()
        return find_month(db, dept.id, month)


# ── employee actions ──────────────────────────────────────────────────────────
def submit_task(db: Session, rec: MonthRecord, user: User, task_id: str, answers: dict[str, str],
                comment: str, expected: int | None) -> None:
    _ensure_open(rec)
    rules = rules_of(rec)
    task = next((t for t in generate_tasks(rec.month, rules, rec.start_date) if t.id == task_id), None)
    if task is None:
        raise ActionError("Задача не относится к этому месяцу или текущим правилам.")
    if any(s.task_id == task_id for s in rec.submissions):
        raise ActionError("Эта задача уже сдана. Первая дата и ответы сохранены.")
    checks: dict[str, bool] = {}
    for c in task.checks:
        v = answers.get(c.key)
        if v not in ("yes", "no"):
            raise ActionError(f"Ответьте на проверку «{c.label}».")
        checks[c.key] = v == "yes"
    comment = _text(comment, "Комментарий", 4000, required=False)
    if not all(checks.values()) and not comment:
        raise ActionError("Есть ответ «Нет» — добавьте комментарий: чего не хватает, кому задан вопрос "
                          "и когда ожидается исправление.")
    rev = _bump(db, rec, expected)
    db.add(TaskSubmission(month_id=rec.id, task_id=task_id, at=now_iso(), checks_json=checks,
                          comment=comment, user_id=user.id, by_name=user.display_name))
    _log(db, rec, user, "submit", rev, task_id=task_id, title=task.title, checks=checks,
         late=utcnow() > task.deadline, comment=comment)
    db.commit()


def add_analysis(db: Session, rec: MonthRecord, user: User, issue_id: str, text: str, expected: int | None) -> None:
    _ensure_open(rec)
    item = _issue(rec, issue_id)
    if item.voided:
        raise ActionError("Замечание отменено; разбор больше не требуется.")
    text = _text(text, "Разбор: причина и меры", 8000)
    at = now_iso()
    new_text = f"{item.analysis}\n\nДополнение {at}\n{text}" if item.analysis else text
    if len(new_text) > 20000:
        raise ActionError("Разбор слишком длинный. Сократите дополнение.")
    rev = _bump(db, rec, expected)
    before = {"analysis": item.analysis, "analysis_at": item.analysis_at}
    item.analysis = new_text
    item.analysis_at = item.analysis_at or at   # a later supplement keeps the original deadline result
    _log(db, rec, user, "analysis", rev, issue_id=issue_id, before=before, submitted_at=at)
    db.commit()


# ── founder actions ───────────────────────────────────────────────────────────
def add_issue(db: Session, rec: MonthRecord, user: User, form: dict, expected: int | None) -> None:
    _ensure_open(rec)
    rules = rules_of(rec)
    pen = rules.penalties
    try:
        d = parse_date((form.get("date") or "").strip())
    except ValueError:
        raise ActionError("Дата замечания: некорректная дата.") from None
    if month_of(d) != rec.month:
        raise ActionError("Дата замечания должна быть внутри выбранного месяца.")
    if d > _today(rules):
        raise ActionError("Замечание нельзя датировать будущим числом.")
    if d < parse_date(rec.start_date):
        raise ActionError("Дата раньше начала учёта: такое замечание не будет учтено.")
    kind, severity = form.get("kind", ""), form.get("severity", "")
    if kind not in pen.kind_labels:
        raise ActionError("Укажите тип замечания.")
    if severity not in pen.severity_amounts:
        raise ActionError("Укажите уровень ошибки.")
    item = IssueRecord(
        id=new_id(), month_id=rec.id, department_id=rec.department_id, date=d.isoformat(), kind=kind,
        severity=severity, category=_text(form.get("category"), "Категория", 120),
        description=_text(form.get("description"), "Описание", 4000),
        found_by=_text(form.get("found_by"), "Кто обнаружил", 200),
        found_by_owner=form.get("found_by_owner") == "on",
        corrected_before_sending=form.get("corrected_before_sending") == "on",
        repeat=form.get("repeat") == "on", created_at=now_iso(), created_by_id=user.id)
    rev = _bump(db, rec, expected)
    db.add(item)
    _log(db, rec, user, "issue_add", rev, issue_id=item.id, kind=kind, severity=severity,
         category=item.category, description=item.description, repeat=item.repeat)
    db.commit()


def void_issue(db: Session, rec: MonthRecord, user: User, issue_id: str, reason: str, expected: int | None) -> None:
    _ensure_open(rec)
    item = _issue(rec, issue_id)
    if item.voided:
        raise ActionError("Замечание уже отменено.")
    reason = _text(reason, "Причина отмены", 2000)
    rev = _bump(db, rec, expected)
    item.voided, item.void_reason = True, reason
    _log(db, rec, user, "issue_void", rev, reason=reason, issue_id=issue_id, category=item.category)
    db.commit()


def set_repeat(db: Session, rec: MonthRecord, user: User, issue_id: str, value: bool, expected: int | None) -> None:
    _ensure_open(rec)
    item = _issue(rec, issue_id)
    if item.voided:
        raise ActionError("Замечание отменено.")
    if item.repeat == value:
        return
    rev = _bump(db, rec, expected)
    item.repeat = value
    _log(db, rec, user, "issue_repeat", rev, issue_id=issue_id, repeat=value)
    db.commit()


def set_metrics(db: Session, rec: MonthRecord, user: User, form: dict, expected: int | None) -> None:
    _ensure_open(rec)
    rules = rules_of(rec)
    old = dict(rec.metrics_json or {})
    new = dict(old)
    for m in rules.metrics:
        raw = (form.get(f"metric_{m.key}") or "").strip().replace(" ", "").replace(",", ".")
        evidence = _text(form.get(f"evidence_{m.key}"), m.evidence_label, 4000, required=False)
        value = None
        if raw:
            try:
                value = float(raw)
            except ValueError:
                raise ActionError(f"{m.label}: нужно число.") from None
            if not math.isfinite(value) or not m.min_value <= value <= m.max_value:
                raise ActionError(f"{m.label}: значение от {m.min_value:g} до {m.max_value:g}.")
        if value is not None and m.require_evidence and not evidence:
            raise ActionError(f"{m.label}: добавьте основание — ссылку на расчёт или пояснение.")
        new[m.key] = {"value": value, "evidence": evidence}
    if new == old:
        return
    rev = _bump(db, rec, expected)
    rec.metrics_json = new
    _log(db, rec, user, "metrics", rev, before=old, after=new)
    db.commit()


def close_month(db: Session, rec: MonthRecord, user: User, note: str, expected: int | None) -> None:
    _ensure_open(rec)
    note = _text(note, "Основание закрытия", 4000)
    rules = rules_of(rec)
    if month_of(_today(rules)) <= rec.month:
        raise ActionError("Закрыть месяц можно после его окончания по московскому времени.")
    if rules.draft:
        raise ActionError("Сначала подтвердите правила месяца (снимите статус черновика).")
    calc = calculate_record(db, rec)
    if calc.missing_metrics:
        labels = {m.key: m.label for m in rules.metrics}
        raise ActionError("Для закрытия заполните: " + ", ".join(labels[k] for k in calc.missing_metrics))
    if not calc.period_ended or not calc.total_tasks or any(b.pending > 0 for b in calc.blocks):
        raise ActionError("Для закрытия должны наступить все сроки и быть сформирован календарь задач.")
    rev = _bump(db, rec, expected)
    rec.closure_json = {"at": now_iso(), "by": user.display_name, "note": note}
    _log(db, rec, user, "close", rev, reason=note, bonus=calc.bonus, total=calc.total, penalties=calc.penalties_applied)
    db.commit()


def reopen_month(db: Session, rec: MonthRecord, user: User, reason: str, expected: int | None) -> None:
    if not rec.closure_json:
        raise ActionError("Месяц уже открыт.")
    reason = _text(reason, "Причина повторного открытия", 2000)
    rev = _bump(db, rec, expected)
    before = rec.closure_json
    rec.closure_json = None
    _log(db, rec, user, "reopen", rev, reason=reason, before=before)
    db.commit()


def confirm_rules(db: Session, rec: MonthRecord, user: User, reason: str, expected: int | None) -> None:
    _ensure_open(rec)
    reason = _text(reason, "Основание подтверждения", 2000)
    rules = rules_of(rec)
    if not rules.draft:
        raise ActionError("Правила месяца уже подтверждены.")
    data = rules.model_dump(mode="json")
    data["draft"] = False
    rev = _bump(db, rec, expected)
    rec.rules_json = Rules.model_validate(data).model_dump(mode="json")
    _log(db, rec, user, "rules_confirm", rev, reason=reason)
    db.commit()


def save_rule_edits(db: Session, dept: Department, rec: MonthRecord | None, user: User, edits: RuleEdits,
                    scope: str, reason: str, expected: int | None) -> list[str]:
    reason = _text(reason, "Причина изменения", 2000)
    changed: list[str] = []
    if scope in ("month", "both"):
        if rec is None:
            raise ActionError("Месяц не открыт — изменить можно только шаблон.")
        _ensure_open(rec)
        old = rules_of(rec)
        new = apply_edits(old, edits)
        diff = diff_rules(old, new)
        if diff:
            new_ids = {t.id for t in generate_tasks(rec.month, new, rec.start_date)}
            if any(s.task_id not in new_ids for s in rec.submissions):
                raise ActionError("Изменение календаря удалит задачу с сохранённой отметкой. "
                                  "Верните прежние даты или измените только шаблон.")
            rev = _bump(db, rec, expected)
            rec.rules_json = new.model_dump(mode="json")
            _log(db, rec, user, "rules_month", rev, reason=reason, changes=diff)
            changed.append(f"месяц {rec.month}")
    if scope in ("template", "both"):
        old = department_rules(dept)
        new = apply_edits(old, edits)
        diff = diff_rules(old, new)
        if diff:
            save_department_rules(db, dept, new, user, reason, source="manual",
                                  changes=diff, silent=bool(changed))
            changed.append("шаблон для новых месяцев")
    if not changed:
        raise ActionError("Значения не отличаются от текущих — сохранять нечего.")
    db.commit()
    return changed


# ── preview (no writes) ───────────────────────────────────────────────────────
def build_preview(db: Session, dept: Department, rec: MonthRecord | None, edits: RuleEdits, scope: str) -> dict:
    base = rules_of(rec) if rec and scope != "template" else department_rules(dept)
    diff = diff_rules(base, apply_edits(base, edits))
    upper = rec.month if rec else current_month()
    recs = list(db.scalars(select(MonthRecord).where(MonthRecord.department_id == dept.id, MonthRecord.month <= upper)
                           .order_by(MonthRecord.month.desc()).limit(3)))
    rows = []
    for r in recs:
        try:
            state, hist = state_from_record(r), issue_history(db, dept.id, r.id)
            before = calculate_month(state, None, hist)
            state.rules = apply_edits(state.rules, edits)
            after = calculate_month(state, None, hist)
            rows.append({"month": r.month, "before": before, "after": after})
        except ValidationError as e:
            rows.append({"month": r.month, "error": format_errors(e)})
        except ValueError as e:
            rows.append({"month": r.month, "error": str(e)})
    blocks = []
    if rows and "after" in rows[0]:
        after_by_id = {b.id: b for b in rows[0]["after"].blocks}
        before_by_id = {b.id: b for b in rows[0]["before"].blocks}
        for bid in sorted(set(after_by_id) | set(before_by_id)):
            b0, b1 = before_by_id.get(bid), after_by_id.get(bid)
            blocks.append({"id": bid, "name": (b1 or b0).name,
                           "before": b0.potential if b0 else 0, "after": b1.potential if b1 else 0,
                           "max_before": b0.max if b0 else 0, "max_after": b1.max if b1 else 0})
    return {"diff": diff, "rows": rows, "blocks": blocks, "currency": base.currency,
            "block_month": rows[0]["month"] if rows else None}
