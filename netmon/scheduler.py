"""Background task orchestration (Phase 11).

One asyncio task per poller (the UniFi poll cycle, and one per configured
website monitor), all started/stopped from the FastAPI app's lifespan. Each
loop backs off on repeated failure instead of hammering an unreachable
controller or site every `poll_interval_seconds` regardless (task 11.1.2).
"""
from __future__ import annotations

import asyncio
import logging

import httpx

from netmon.alerts import raise_alert
from netmon.config import Settings, WebsiteMonitorConfig
from netmon.db import session_scope
from netmon.heuristics import run_heuristics
from netmon.ids_ingest import IdsIngestor
from netmon.infra_monitor import reconcile_infra
from netmon.notify import DiscordNotifier
from netmon.tracking import reconcile_devices
from netmon.unifi_client import UnifiClient
from netmon.uptime import run_check

logger = logging.getLogger("netmon.scheduler")

_MAX_BACKOFF_SECONDS = 600


class Scheduler:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.unifi_client = UnifiClient(settings.unifi, settings)
        self.notifier = DiscordNotifier(settings.alerts.discord_webhook_url)
        self.ids_ingestor = IdsIngestor()
        self.http_client = httpx.AsyncClient()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._unifi_loop(), name="unifi-poll"))
        for monitor in self.settings.website_monitors:
            self._tasks.append(asyncio.create_task(self._uptime_loop(monitor), name=f"uptime-{monitor.name}"))
        logger.info("Scheduler started: 1 UniFi poller, %d uptime monitor(s)", len(self.settings.website_monitors))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.unifi_client.close()
        await self.notifier.close()
        await self.http_client.aclose()
        logger.info("Scheduler stopped")

    async def _unifi_loop(self) -> None:
        consecutive_failures = 0
        while True:
            try:
                await self._unifi_cycle()
                consecutive_failures = 0
                delay = self.settings.unifi.poll_interval_seconds
            except asyncio.CancelledError:
                raise
            except Exception:
                consecutive_failures += 1
                logger.exception("UniFi poll cycle failed (%d in a row)", consecutive_failures)
                delay = min(
                    self.settings.unifi.poll_interval_seconds * (2**consecutive_failures),
                    _MAX_BACKOFF_SECONDS,
                )
            await asyncio.sleep(delay)

    async def _unifi_cycle(self) -> None:
        active_clients = await self.unifi_client.get_active_clients()
        known_clients = await self.unifi_client.get_known_clients()
        infra_records = await self.unifi_client.get_devices()

        # Tier 1 IDS/IPS ingestion is a bonus signal, not core functionality --
        # it must never be able to take device/infra tracking down with it.
        # (This is exactly what caused clients to never reach the database:
        # an unhandled error here used to abort the whole cycle before the
        # session_scope() block below ever ran.)
        try:
            new_alarms = await self.ids_ingestor.poll_new_alarms(self.unifi_client)
        except Exception:
            logger.exception("Skipping IDS/IPS alarm check for this cycle")
            new_alarms = []

        async with session_scope() as session:
            reconcile_result = await reconcile_devices(session, active_clients, known_clients)
            infra_result = await reconcile_infra(
                session, infra_records, self.settings.alerts.infra_offline_minutes
            )
            findings = await run_heuristics(session, self.settings.alerts.heuristics, reconcile_result)

            new_device_days = self.settings.alerts.new_device_days
            for device in reconcile_result.new_devices:
                await raise_alert(
                    session,
                    self.notifier,
                    type_="new_device",
                    severity="info",
                    message=(
                        f"{device.display_name()} ({device.mac}) joined '{device.network}'. "
                        f"Highlighted as new for the next {new_device_days} days."
                    ),
                    target=device.mac,
                )

            for infra in infra_result.newly_offline_alerts:
                await raise_alert(
                    session,
                    self.notifier,
                    type_="infra_offline",
                    severity="warning",
                    message=(
                        f"'{infra.name}' ({infra.kind}) on '{infra.network}' has been offline for "
                        f"{self.settings.alerts.infra_offline_minutes}+ minutes."
                    ),
                    target=infra.unifi_id,
                )

            for infra in infra_result.recovered:
                await raise_alert(
                    session,
                    self.notifier,
                    type_="infra_recovered",
                    severity="info",
                    message=f"'{infra.name}' ({infra.kind}) on '{infra.network}' is back online.",
                    target=infra.unifi_id,
                )

            for finding in findings:
                await raise_alert(
                    session,
                    self.notifier,
                    type_="suspicious",
                    severity="warning",
                    message=f"[{finding.heuristic}] {finding.message}",
                    target=finding.target,
                    dedup_window_minutes=30,
                )

            for alarm in new_alarms:
                await raise_alert(
                    session,
                    self.notifier,
                    type_="suspicious",
                    severity="critical",
                    message=f"UniFi IDS/IPS: {alarm.message}{alarm.detail_suffix()}",
                    target=alarm.src_ip,
                )

    async def _uptime_loop(self, monitor: WebsiteMonitorConfig) -> None:
        consecutive_failures = 0
        while True:
            try:
                await self._uptime_cycle(monitor)
                consecutive_failures = 0
                delay = monitor.interval_seconds
            except asyncio.CancelledError:
                raise
            except Exception:
                consecutive_failures += 1
                logger.exception("Uptime check for %s failed (%d in a row)", monitor.name, consecutive_failures)
                delay = min(monitor.interval_seconds * (2**consecutive_failures), _MAX_BACKOFF_SECONDS)
            await asyncio.sleep(delay)

    async def _uptime_cycle(self, monitor: WebsiteMonitorConfig) -> None:
        async with session_scope() as session:
            result = await run_check(session, self.http_client, monitor)

            if result.incident_opened:
                await raise_alert(
                    session,
                    self.notifier,
                    type_="website_down",
                    severity="critical",
                    message=(
                        f"{monitor.name} failed {monitor.failure_threshold} consecutive health checks "
                        f"({result.check.error or f'HTTP {result.check.status_code}'})."
                    ),
                    target=monitor.name,
                )

            if result.incident_resolved:
                duration = result.incident_resolved.duration_seconds()
                duration_text = f" Downtime: {duration / 60:.1f} minutes." if duration is not None else ""
                await raise_alert(
                    session,
                    self.notifier,
                    type_="website_recovered",
                    severity="info",
                    message=f"{monitor.name} is back up.{duration_text}",
                    target=monitor.name,
                )
