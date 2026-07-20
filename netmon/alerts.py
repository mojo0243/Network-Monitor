"""Alert composition, routing, and persistence (Phase 8, tasks 8.2/8.3;
also the suspicious-activity alert composer from task 6.3).

Every alert in the system is created through `raise_alert()` so there is one
place that (a) writes the `alerts` row and (b) decides whether it's worth
dispatching to Discord. Callers never talk to DiscordNotifier directly.
"""
from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from netmon.models import Alert, utcnow
from netmon.notify import DiscordNotifier

logger = logging.getLogger("netmon.alerts")

_TITLES = {
    "new_device": "New device",
    "infra_offline": "Switch/AP offline",
    "infra_recovered": "Switch/AP back online",
    "suspicious": "Suspicious activity",
    "website_down": "Website down",
    "website_recovered": "Website back up",
}


async def raise_alert(
    session: AsyncSession,
    notifier: DiscordNotifier,
    *,
    type_: str,
    severity: str,
    message: str,
    target: str | None = None,
    dedup_window_minutes: int | None = None,
) -> Alert | None:
    if dedup_window_minutes is not None:
        since = utcnow() - dt.timedelta(minutes=dedup_window_minutes)
        duplicate = await session.scalar(
            select(Alert).where(
                Alert.type == type_,
                Alert.target == target,
                Alert.created_at >= since,
            )
        )
        if duplicate is not None:
            return None

    alert = Alert(type=type_, severity=severity, message=message, target=target, created_at=utcnow())
    session.add(alert)
    await session.flush()

    title = _TITLES.get(type_, type_)
    await notifier.send_alert(title=title, message=message, severity=severity)

    return alert


async def acknowledge_alert(session: AsyncSession, alert_id: int) -> Alert | None:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        return None
    alert.acknowledged = True
    alert.acknowledged_at = utcnow()
    return alert
