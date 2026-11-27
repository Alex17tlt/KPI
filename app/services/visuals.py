"""View-models for the founder 'helicopter view'. Pure functions over engine results (no DB)."""
from __future__ import annotations

from datetime import date, timedelta

from app.engine.calculator import MonthCalculation
from app.engine.calendar import first_date, last_date, parse_date
from app.engine.schema import MetricScoring, Rules, ViolationScoring, fmt_num

GOOD, WARN = 85, 60


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def level(pct: float) -> str:
    return "good" if pct >= GOOD else ("warn" if pct >= WARN else "bad")


def is_final(calc: MonthCalculation) -> bool:
    return not calc.provisional


def score(calc: MonthCalculation) -> float:
    return calc.score_pct if is_final(calc) else calc.potential_score_pct


def _pct(coef: float) -> str:
    return fmt_num(coef * 100) + "%"


def _headroom(sc: ViolationScoring, count: int) -> str:
    for i, tier in enumerate(sc.tiers):
        if count <= tier.max_violations:
            nxt = sc.tiers[i + 1].coef if i + 1 < len(sc.tiers) else sc.else_coef
            left = tier.max_violations - count
            if left == 0:
                return f"Следующее нарушение снизит блок до {_pct(nxt)}"
            return f"Запас: {left} {plural(left, 'нарушение', 'нарушения', 'нарушений')} до снижения до {_pct(nxt)}"
    return "Лимит нарушений исчерпан"


def block_views(rules: Rules, calc: MonthCalculation) -> list[dict]:
    final, out = is_final(calc), []
    for b in calc.blocks:
        d = rules.block_by_id(b.id)
        sc = d.scoring if d else None
        shown = b.earned if final else b.potential
        if b.coefficient >= 1:
            status, label = "good", "В норме"
        elif b.coefficient > 0:
            status, label = "warn", "Частично"
        else:
            status, label = "bad", "Потерян"
        view = {"id": b.id, "name": b.name, "max": b.max, "shown": shown,
                "pct": round(shown / b.max * 100, 1) if b.max else 0, "status": status, "label": label,
                "violations": b.violations, "pending": b.pending, "dots": [], "extra": 0, "hint": b.reason}
        if b.id in calc.zeroed_blocks:
            view["hint"] = calc.zeroed_blocks[b.id]
        elif isinstance(sc, ViolationScoring):
            slots = sc.tiers[-1].max_violations + 1
            view["dots"] = ["on" if i < b.violations else "off" for i in range(slots)]
            view["extra"] = max(0, b.violations - slots)
            view["hint"] = _headroom(sc, b.violations)
        elif isinstance(sc, MetricScoring) and sc.metric in calc.missing_metrics:
            view.update(status="idle", label="Нет данных")
        out.append(view)
    return out


def money_split(calc: MonthCalculation) -> dict:
    final = is_final(calc)
    mx = calc.max_bonus
    gross = calc.gross if final else calc.potential_gross
    pen = calc.penalties_applied if final else calc.potential_penalties_applied
    bonus = calc.bonus if final else calc.potential_bonus
    lost = max(0.0, mx - gross)

    def w(v):
        return round(v / mx * 100, 2) if mx else 0

    return {"max": mx, "bonus": bonus, "lost": lost, "penalties": pen,
            "w_bonus": w(bonus), "w_lost": w(lost), "w_pen": w(pen), "final": final}


def day_strip(calc: MonthCalculation, month: str, start_date: str, today: date) -> list[dict]:
    by_day: dict[date, list[str]] = {}
    for r in calc.task_results:
        by_day.setdefault(r.task.date, []).append(r.status)
    start, d, end, out = parse_date(start_date), first_date(month), last_date(month), []
    while d <= end:
        st = by_day.get(d, [])
        bad = sum(s in ("violation", "overdue") for s in st)
        if d < start or not st:
            kind = "off"
        elif bad:
            kind = "bad"
        elif all(s == "done" for s in st):
            kind = "good"
        elif d >= today:
            kind = "future"
        else:
            kind = "warn"
        title = f"{d:%d.%m}: задач {len(st)}" + (f", нарушений {bad}" if bad else "") if st else f"{d:%d.%m}: задач нет"
        out.append({"day": d.day, "kind": kind, "title": title})
        d += timedelta(days=1)
    return out


def attention(calc: MonthCalculation | None, rules: Rules | None, unclosed: list[str]) -> list[str]:
    out = []
    if unclosed:
        out.append("Не закрыты месяцы: " + ", ".join(unclosed))
    if calc is None or rules is None:
        return out
    if rules.draft:
        out.append("Правила месяца — черновик")
    if calc.overdue_count:
        out.append(f"Просрочено и не сдано задач: {calc.overdue_count}")
    violations = sum(1 for t in calc.task_results if t.status == "violation")
    if violations:
        out.append(f"Сдано с нарушением: {violations}")
    out.extend(calc.zeroed_blocks.values())
    late = sum(1 for p in calc.penalty_rows if p.analysis_late)
    if late:
        out.append(f"Разбор замечаний просрочен: {late}")
    if calc.missing_metrics and calc.period_ended:
        labels = {m.key: m.label for m in rules.metrics}
        out.append("Не заполнено для закрытия: " + ", ".join(labels[k] for k in calc.missing_metrics))
    return out


def overview(rules: Rules, calc: MonthCalculation, month: str, start_date: str, today: date) -> dict:
    s = score(calc)
    return {"score": s, "level": level(s), "final": is_final(calc), "money": money_split(calc),
            "blocks": block_views(rules, calc), "days": day_strip(calc, month, start_date, today),
            "done": calc.completed_count, "total": calc.total_tasks, "overdue": calc.overdue_count,
            "currency": rules.currency}
