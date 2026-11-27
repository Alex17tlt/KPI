from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.security import current_user
from app.services.notifications import latest, mark_all_read
from app.web import render

router = APIRouter()


@router.get("/notifications")
def feed(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    items = latest(db, user)
    unread_ids = {n.id for n in items if n.read_at is None}
    mark_all_read(db, user)
    db.commit()
    request.state.unread = 0
    return render(request, "notifications.html", items=items, unread_ids=unread_ids)
