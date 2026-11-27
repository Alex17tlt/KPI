import re
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.engine.calendar import parse_ts, tz
from app.engine.schema import fmt_num
from app.services.rules_edit import format_errors, rule_label
from app.services.visuals import GOOD as SCORE_GOOD, WARN as SCORE_WARN

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

ROLE_LABELS = {"admin": "Администратор", "founder": "Фаундер", "employee": "Сотрудник", "viewer": "Наблюдатель"}
STATUS_LABELS = {"pending": "Ожидает подтверждения", "active": "Активен", "disabled": "Отключён"}
TASK_STATUS = {"pending": "Ожидается", "overdue": "Просрочено", "done": "Сдано", "violation": "Нарушение"}
ACTION_LABELS = {
    "create_month": "Месяц открыт", "submit": "Сдача задачи", "issue_add": "Замечание добавлено",
    "issue_void": "Замечание отменено", "issue_repeat": "Отметка повтора", "analysis": "Разбор замечания",
    "metrics": "Показатели изменены", "close": "Месяц закрыт", "reopen": "Месяц открыт повторно",
    "rules_month": "Правила месяца изменены", "rules_template": "Шаблон правил изменён",
    "rules_confirm": "Правила подтверждены",
}


def _msk(value) -> str:
    if not value:
        return "—"
    try:
        return parse_ts(value).astimezone(tz("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        return str(value)


def _plain(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


templates.env.globals.update(ROLE_LABELS=ROLE_LABELS, STATUS_LABELS=STATUS_LABELS, TASK_STATUS=TASK_STATUS,
                             ACTION_LABELS=ACTION_LABELS, SCORE_GOOD=SCORE_GOOD, SCORE_WARN=SCORE_WARN,
                             rule_label=rule_label)


def _md(text) -> Markup:
    s = str(escape(text or ""))
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    return Markup(s)


templates.env.filters.update(num=fmt_num, msk=_msk, plain=_plain, md=_md)


async def form_dict(request: Request) -> dict[str, str]:
    form = await request.form()
    return {k: v for k, v in form.items() if isinstance(v, str)}


def form_int(form: dict[str, str], key: str = "revision") -> int | None:
    try:
        return int(form.get(key, ""))
    except ValueError:
        return None


def flash(request: Request, msg: str, kind: str = "ok") -> None:
    request.session["flash"] = [*request.session.get("flash", []), {"msg": msg, "kind": kind}]


def render(request: Request, name: str, *, status_code: int = 200, **ctx):
    ctx.setdefault("user", getattr(request.state, "user", None))
    ctx["unread"] = getattr(request.state, "unread", 0)
    ctx["flashes"] = request.session.pop("flash", [])
    ctx["impersonating"] = bool(request.session.get("imp_by"))
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def run_action(request: Request, db: Session, fn, ok_msg: str) -> bool:
    try:
        fn()
    except ValidationError as e:
        db.rollback()
        flash(request, "Правила не прошли проверку: " + format_errors(e), "error")
        return False
    except IntegrityError:
        db.rollback()
        flash(request, "Данные уже изменены. Обновите страницу и повторите.", "error")
        return False
    except ValueError as e:
        db.rollback()
        flash(request, str(e), "error")
        return False
    flash(request, ok_msg)
    return True
