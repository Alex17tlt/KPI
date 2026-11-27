from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.llm.chat import SUGGESTIONS, answer
from app.models import ChatMessage, User, new_id
from app.permissions import can_view_overview, ensure, get_department
from app.security import current_user, limiter
from app.services.departments import list_departments
from app.web import flash, render

router = APIRouter()


def _thread(request: Request) -> str:
    if not request.session.get("chat_thread"):
        request.session["chat_thread"] = new_id()
    return request.session["chat_thread"]


@router.get("/chat")
def chat_page(request: Request, dept: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    ensure(can_view_overview(user))
    msgs = list(db.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id,
                                                     ChatMessage.thread == _thread(request))
                           .order_by(ChatMessage.id)))
    return render(request, "chat.html", msgs=msgs, depts=list_departments(db), dept=dept,
                  suggestions=SUGGESTIONS, llm_ready=bool(settings.OPENROUTER_API_KEY))


@router.post("/chat")
def chat_post(request: Request, question: str = Form(...), dept: str = Form(""),
              user: User = Depends(current_user), db: Session = Depends(get_db)):
    ensure(can_view_overview(user))
    q = question.strip()[:2000]
    if q:
        key = f"chat:{user.id}"
        if limiter.blocked(key, limit=30, window=3600):
            flash(request, "Слишком много вопросов за час. Попробуйте позже.", "error")
        else:
            limiter.hit(key)
            answer(db, user, _thread(request), q, get_department(db, dept) if dept else None)
    return RedirectResponse(f"/chat?dept={dept}#end", status_code=303)


@router.post("/chat/new")
def chat_new(request: Request, dept: str = Form(""), user: User = Depends(current_user)):
    request.session["chat_thread"] = new_id()
    return RedirectResponse(f"/chat?dept={dept}", status_code=303)
