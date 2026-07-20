"""Login-required middleware and session helpers (Phase 9, task 9.1).

The dashboard has exactly one purpose-built access control: you're either
logged in or you're not (no roles/permissions -- this is a single-household
tool). Network-level isolation (LAN-only reachability) is handled by Caddy
and the Pi's network placement, not by this app; see README.md's Setup guide.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

_EXEMPT_PATHS = {"/login", "/favicon.ico"}
_EXEMPT_PREFIXES = ("/static/",)


def is_logged_in(request: Request) -> bool:
    return bool(request.session.get("user_id"))


class LoginRequiredMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in _EXEMPT_PATHS or path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        if not is_logged_in(request):
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
            return RedirectResponse(url=f"/login?next={path}", status_code=303)

        return await call_next(request)
