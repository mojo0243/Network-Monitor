"""JSON API the dashboard's JS polls (Phase 9, task 9.2 + 9.2.6 for nicknames).

Deliberately plain dict serialization rather than a separate schema layer --
this is a small, single-purpose API and the extra indirection wouldn't earn
its keep.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select

from netmon.db import session_scope
from netmon.models import Alert, Device, InfraDevice, UptimeCheck, UptimeIncident, utcnow
from netmon.tracking import devices_grouped_by_network
from netmon.web.slugs import network_by_slug, slugify

router = APIRouter(prefix="/api")


def _iso(value: dt.datetime | None) -> str | None:
    """Every stored datetime is naive UTC (see models.utcnow) -- append "Z"
    explicitly so the browser's `new Date(...)` parses it as UTC instead of
    silently treating it as local time.
    """
    return value.isoformat() + "Z" if value else None


def _device_dict(device: Device, new_device_days: int) -> dict:
    return {
        "mac": device.mac,
        "hostname": device.hostname,
        "vendor": device.vendor,
        "custom_name": device.custom_name,
        "display_name": device.display_name(),
        "network": device.network,
        "is_wired": device.is_wired,
        "is_online": device.is_online,
        "is_new": device.is_new(new_device_days),
        "first_seen": _iso(device.first_seen),
        "last_seen": _iso(device.last_seen),
    }


@router.get("/networks")
async def list_networks(request: Request):
    settings = request.app.state.settings
    async with session_scope() as session:
        grouped = await devices_grouped_by_network(session)

    out = []
    for net in settings.networks:
        devices = grouped.get(net.name, [])
        out.append(
            {
                "name": net.name,
                "slug": slugify(net.name),
                "vlan_id": net.vlan_id,
                "role": net.role,
                "device_count": len(devices),
                "online_count": sum(1 for d in devices if d.is_online),
                "new_count": sum(1 for d in devices if d.is_new(settings.alerts.new_device_days)),
            }
        )
    return out


@router.get("/networks/{slug}/devices")
async def network_devices(slug: str, request: Request):
    settings = request.app.state.settings
    net = network_by_slug(settings, slug)
    if net is None:
        return JSONResponse({"detail": "Unknown network"}, status_code=404)

    async with session_scope() as session:
        devices = await session.scalars(
            select(Device).where(Device.network == net.name).order_by(Device.is_online.desc(), Device.last_seen.desc())
        )
        devices = list(devices)

    return {
        "network": {"name": net.name, "slug": slug, "vlan_id": net.vlan_id, "role": net.role},
        "devices": [_device_dict(d, settings.alerts.new_device_days) for d in devices],
    }


@router.patch("/devices/{mac}/label")
async def set_device_label(mac: str, request: Request):
    settings = request.app.state.settings
    body = await request.json()
    custom_name = (body.get("custom_name") or "").strip() or None

    async with session_scope() as session:
        device = await session.scalar(select(Device).where(Device.mac == mac))
        if device is None:
            return JSONResponse({"detail": "Unknown device"}, status_code=404)
        device.custom_name = custom_name
        await session.flush()
        result = _device_dict(device, settings.alerts.new_device_days)

    return result


@router.get("/infrastructure")
async def list_infrastructure(request: Request):
    settings = request.app.state.settings
    async with session_scope() as session:
        rows = await session.scalars(select(InfraDevice).order_by(InfraDevice.network, InfraDevice.name))
        rows = list(rows)

    now = utcnow()
    out = []
    for row in rows:
        offline_minutes = None
        if not row.is_online and row.offline_since:
            offline_minutes = round((now - row.offline_since).total_seconds() / 60, 1)
        out.append(
            {
                "unifi_id": row.unifi_id,
                "name": row.name,
                "kind": row.kind,
                "network": row.network,
                "mac": row.mac,
                "is_online": row.is_online,
                "last_seen": _iso(row.last_seen),
                "offline_since": _iso(row.offline_since),
                "offline_minutes": offline_minutes,
                "past_alert_threshold": offline_minutes is not None
                and offline_minutes >= settings.alerts.infra_offline_minutes,
            }
        )
    return out


@router.get("/alerts")
async def list_alerts(request: Request, acknowledged: bool | None = None, limit: int = 200):
    query = select(Alert).order_by(Alert.created_at.desc()).limit(limit)
    if acknowledged is not None:
        query = query.where(Alert.acknowledged == acknowledged)

    async with session_scope() as session:
        rows = list(await session.scalars(query))

    return [
        {
            "id": a.id,
            "type": a.type,
            "severity": a.severity,
            "message": a.message,
            "target": a.target,
            "created_at": _iso(a.created_at),
            "acknowledged": a.acknowledged,
            "acknowledged_at": _iso(a.acknowledged_at),
        }
        for a in rows
    ]


@router.post("/alerts/{alert_id}/ack")
async def ack_alert(alert_id: int):
    from netmon.alerts import acknowledge_alert

    async with session_scope() as session:
        alert = await acknowledge_alert(session, alert_id)
        if alert is None:
            return JSONResponse({"detail": "Unknown alert"}, status_code=404)
        return {"id": alert.id, "acknowledged": alert.acknowledged}


@router.get("/uptime")
async def uptime_summary(request: Request):
    settings = request.app.state.settings
    now = utcnow()
    since_24h = now - dt.timedelta(hours=24)

    out = []
    async with session_scope() as session:
        for monitor in settings.website_monitors:
            recent_checks = list(
                await session.scalars(
                    select(UptimeCheck)
                    .where(UptimeCheck.target_name == monitor.name)
                    .order_by(UptimeCheck.timestamp.desc())
                    .limit(100)
                )
            )
            checks_24h = [c for c in recent_checks if c.timestamp >= since_24h]
            uptime_pct = (
                round(100 * sum(1 for c in checks_24h if c.status == "up") / len(checks_24h), 2)
                if checks_24h
                else None
            )

            open_incident = await session.scalar(
                select(UptimeIncident)
                .where(UptimeIncident.target_name == monitor.name, UptimeIncident.resolved_at.is_(None))
                .order_by(UptimeIncident.started_at.desc())
            )
            recent_incidents = list(
                await session.scalars(
                    select(UptimeIncident)
                    .where(UptimeIncident.target_name == monitor.name)
                    .order_by(UptimeIncident.started_at.desc())
                    .limit(10)
                )
            )

            out.append(
                {
                    "name": monitor.name,
                    "url": monitor.url,
                    "is_up": open_incident is None,
                    "uptime_pct_24h": uptime_pct,
                    "last_check_at": _iso(recent_checks[0].timestamp) if recent_checks else None,
                    "last_response_ms": recent_checks[0].response_ms if recent_checks else None,
                    "checks": [
                        {"timestamp": _iso(c.timestamp), "status": c.status, "response_ms": c.response_ms}
                        for c in reversed(recent_checks)
                    ],
                    "incidents": [
                        {
                            "started_at": _iso(i.started_at),
                            "resolved_at": _iso(i.resolved_at),
                            "duration_seconds": i.duration_seconds(),
                        }
                        for i in recent_incidents
                    ],
                }
            )

    return out
