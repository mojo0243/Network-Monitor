from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from netmon.db import session_scope
from netmon.models import User
from netmon.security import verify_password

router = APIRouter()
templates = Jinja2Templates(directory="netmon/web/templates")


@router.get("/login")
async def login_page(request: Request, next: str = "/networks"):
    if request.session.get("user_id"):
        return RedirectResponse(url=next, status_code=303)
    return templates.TemplateResponse(request, "login.html", {"next": next, "error": None})


@router.post("/login")
async def login_submit(request: Request, username: str = Form(...), password: str = Form(...), next: str = Form("/networks")):
    async with session_scope() as session:
        user = await session.scalar(select(User).where(User.username == username))

    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"next": next, "error": "Incorrect username or password."}, status_code=401
        )

    request.session["user_id"] = user.id
    request.session["username"] = user.username
    return RedirectResponse(url=next or "/networks", status_code=303)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
