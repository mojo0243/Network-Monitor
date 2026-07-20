"""Website uptime monitoring (Phase 7).

Watches config.website_monitors from the Pi itself. Worth calling out (see
README.md's Setup guide): this only tells you the site is unreachable *from your home
network* -- if your home internet connection is the thing that's down, this
monitor goes quiet right along with it rather than alerting. Task 7.4 in the
plan covers pairing this with a free external checker for that blind spot.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import time

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from netmon.config import WebsiteMonitorConfig
from netmon.models import UptimeCheck, UptimeIncident, utcnow


@dataclasses.dataclass
class UptimeResult:
    check: UptimeCheck
    incident_opened: UptimeIncident | None = None
    incident_resolved: UptimeIncident | None = None


async def _perform_check(http_client: httpx.AsyncClient, monitor: WebsiteMonitorConfig) -> UptimeCheck:
    start = time.monotonic()
    try:
        resp = await http_client.get(monitor.url, timeout=monitor.timeout_seconds, follow_redirects=True)
        elapsed_ms = (time.monotonic() - start) * 1000
        status = "up" if resp.status_code < 500 else "down"
        return UptimeCheck(
            target_name=monitor.name,
            timestamp=utcnow(),
            status=status,
            status_code=resp.status_code,
            response_ms=elapsed_ms,
            error=None if status == "up" else f"HTTP {resp.status_code}",
        )
    except httpx.RequestError as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        return UptimeCheck(
            target_name=monitor.name,
            timestamp=utcnow(),
            status="down",
            status_code=None,
            response_ms=elapsed_ms,
            error=str(exc),
        )


async def _open_incident_if_threshold_met(
    session: AsyncSession, monitor: WebsiteMonitorConfig
) -> UptimeIncident | None:
    recent = await session.scalars(
        select(UptimeCheck)
        .where(UptimeCheck.target_name == monitor.name)
        .order_by(UptimeCheck.timestamp.desc())
        .limit(monitor.failure_threshold)
    )
    recent_list = list(recent)
    if len(recent_list) < monitor.failure_threshold:
        return None
    if any(c.status != "down" for c in recent_list):
        return None

    incident = UptimeIncident(target_name=monitor.name, started_at=recent_list[-1].timestamp)
    session.add(incident)
    return incident


async def run_check(
    session: AsyncSession, http_client: httpx.AsyncClient, monitor: WebsiteMonitorConfig
) -> UptimeResult:
    check = await _perform_check(http_client, monitor)
    session.add(check)
    await session.flush()

    open_incident = await session.scalar(
        select(UptimeIncident)
        .where(UptimeIncident.target_name == monitor.name, UptimeIncident.resolved_at.is_(None))
        .order_by(UptimeIncident.started_at.desc())
    )

    result = UptimeResult(check=check)

    if check.status == "up":
        if open_incident is not None:
            open_incident.resolved_at = check.timestamp
            result.incident_resolved = open_incident
    else:
        if open_incident is None:
            opened = await _open_incident_if_threshold_met(session, monitor)
            if opened is not None:
                result.incident_opened = opened

    return result
