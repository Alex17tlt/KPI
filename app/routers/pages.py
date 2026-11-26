from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse

from app.models import User
from app.permissions import can_view_overview
from app.security import current_user

router = APIRouter()


@router.get("/")
def home(user: User = Depends(current_user)):
    return RedirectResponse("/dashboard" if can_view_overview(user) else "/w", status_code=303)
