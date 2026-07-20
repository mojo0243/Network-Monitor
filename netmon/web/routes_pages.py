from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from netmon.web.slugs import slugify

router = APIRouter()
templates = Jinja2Templates(directory="netmon/web/templates")


def _nav_context(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "username": request.session.get("username"),
        "networks": [{"name": n.name, "slug": slugify(n.name)} for n in settings.networks],
    }


@router.get("/")
async def index():
    return RedirectResponse(url="/networks")


@router.get("/networks")
async def networks_page(request: Request):
    return templates.TemplateResponse(request, "networks.html", _nav_context(request))


@router.get("/networks/{slug}")
async def network_detail_page(slug: str, request: Request):
    ctx = _nav_context(request)
    ctx["slug"] = slug
    return templates.TemplateResponse(request, "network_detail.html", ctx)


@router.get("/infrastructure")
async def infrastructure_page(request: Request):
    return templates.TemplateResponse(request, "infrastructure.html", _nav_context(request))


@router.get("/alerts")
async def alerts_page(request: Request):
    return templates.TemplateResponse(request, "alerts.html", _nav_context(request))


@router.get("/uptime")
async def uptime_page(request: Request):
    return templates.TemplateResponse(request, "uptime.html", _nav_context(request))
