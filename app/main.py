import logging
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from app import models  # noqa: F401  (registers tables)
from app.bootstrap import bootstrap
from app.config import settings
from app.db import Base, SessionLocal, engine
from app.routers import admin, auth, chat, dashboard, departments, notifications, pages, workspace
from app.security import Forbidden, LoginRequired, PasswordChangeRequired
from app.web import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        bootstrap(db)
    yield


app = FastAPI(title="Seven Group KPI", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def origin_guard(request: Request, call_next):
    """CSRF baseline: reject state-changing requests coming from other sites."""
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        allowed = {settings.BASE_URL, str(request.base_url).rstrip("/")}
        if (origin and origin not in allowed) or request.headers.get("sec-fetch-site") == "cross-site":
            return PlainTextResponse("Запрос с другого сайта отклонён.", status_code=403)
    return await call_next(request)


app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY, session_cookie="kpi_session",
                   max_age=settings.SESSION_MAX_AGE, same_site="lax", https_only=settings.COOKIE_SECURE)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(notifications.router)
app.include_router(chat.router)
app.include_router(dashboard.router)
app.include_router(departments.router)
app.include_router(workspace.router)
app.include_router(pages.router)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.exception_handler(LoginRequired)
async def _login_required(request: Request, _exc):
    return RedirectResponse(f"/login?next={quote(request.url.path)}", status_code=303)


@app.exception_handler(PasswordChangeRequired)
async def _password_change(request: Request, _exc):
    return RedirectResponse("/account/password", status_code=303)


@app.exception_handler(Forbidden)
async def _forbidden(request: Request, _exc):
    return render(request, "error.html", status_code=403, title="Нет доступа",
                  text="У вашей роли нет прав на этот раздел.")


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    titles = {404: "Страница не найдена", 400: "Некорректный запрос", 405: "Метод не поддерживается"}
    return render(request, "error.html", status_code=exc.status_code,
                  title=titles.get(exc.status_code, "Ошибка"), text=str(exc.detail))
