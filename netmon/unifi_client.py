"""Thin async client for the UDM-Pro's built-in UniFi Network controller.

This talks to the *unofficial* UniFi Network API (the same endpoints the
controller's own web UI uses) over plain httpx, rather than pulling in a
larger library like aiounifi. Two reasons:

1. One fewer dependency to install on a Pi, and no HA-specific abstractions
   (websocket entity model, etc.) that don't fit a simple polling loop.
2. Full control over retry/re-auth behaviour.

The tradeoff: this API is undocumented and has drifted across UniFi OS
versions before. If fields come back missing or renamed on your controller,
open your browser's devtools Network tab while browsing the UniFi UI and
compare against the endpoints below -- `_request()` is the only place that
needs to change. Every field access here uses `.get()` with a fallback for
exactly this reason.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import logging
from typing import Any, Literal

import httpx

from netmon.config import Settings, UnifiConfig

logger = logging.getLogger("netmon.unifi")


class UnifiAuthError(Exception):
    pass


class UnifiRequestError(Exception):
    pass


@dataclasses.dataclass
class ClientRecord:
    mac: str
    hostname: str | None
    vendor: str | None
    network: str
    is_wired: bool
    is_active: bool
    first_seen: dt.datetime | None
    last_seen: dt.datetime | None


@dataclasses.dataclass
class InfraRecord:
    unifi_id: str
    name: str
    kind: Literal["switch", "ap", "gateway", "other"]
    network: str
    mac: str | None
    is_online: bool


@dataclasses.dataclass
class EventRecord:
    key: str
    mac: str | None
    network: str | None
    message: str
    timestamp: dt.datetime


@dataclasses.dataclass
class AlarmRecord:
    key: str
    message: str
    timestamp: dt.datetime
    category: str | None = None
    src_ip: str | None = None
    dst_ip: str | None = None
    dst_port: int | None = None

    def detail_suffix(self) -> str:
        """Renders whatever of src/dst/port/category is present, for a more
        useful alert message than the raw msg alone -- this is what makes a
        port-scan alarm ("Attempted-Recon"/"Port Scan" category, one source
        IP hitting many destination ports) actually identifiable as one in
        the dashboard/Discord instead of a bare signature name.
        """
        parts = []
        if self.category:
            parts.append(f"category={self.category}")
        if self.src_ip:
            parts.append(f"src={self.src_ip}")
        if self.dst_ip:
            target = self.dst_ip
            if self.dst_port:
                target += f":{self.dst_port}"
            parts.append(f"dst={target}")
        return f" ({', '.join(parts)})" if parts else ""


def _epoch_ms_to_dt(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    try:
        # UniFi mixes seconds and milliseconds depending on endpoint/version.
        num = float(value)
        if num > 10_000_000_000:  # looks like milliseconds
            num /= 1000
        return dt.datetime.fromtimestamp(num, tz=dt.timezone.utc)
    except (TypeError, ValueError):
        return None


_DEVICE_TYPE_MAP = {
    "usw": "switch",
    "usw-lite": "switch",
    "uap": "ap",
    "udm": "gateway",
    "ugw": "gateway",
    "uxg": "gateway",
}


class UnifiClient:
    """One instance per app lifetime. Call `close()` on shutdown."""

    def __init__(self, config: UnifiConfig, settings: Settings):
        self._config = config
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=f"https://{config.host}",
            verify=config.verify_ssl,
            timeout=15.0,
        )
        self._csrf_token: str | None = None
        self._logged_in = False
        self._alarm_endpoint_warned = False

    async def close(self) -> None:
        await self._client.aclose()

    async def login(self) -> None:
        try:
            resp = await self._client.post(
                "/api/auth/login",
                json={"username": self._config.username, "password": self._config.password},
            )
        except httpx.RequestError as exc:
            raise UnifiAuthError(f"Could not reach UDM-Pro at {self._config.host}: {exc}") from exc

        if resp.status_code != 200:
            raise UnifiAuthError(
                f"UDM-Pro login failed with status {resp.status_code}. "
                f"Check unifi.username/password in config.yml."
            )

        self._csrf_token = resp.headers.get("x-csrf-token")
        self._logged_in = True
        logger.info("Logged in to UDM-Pro at %s", self._config.host)

    async def _api(self, method: str, path: str, json: dict | None = None) -> list[dict]:
        if not self._logged_in:
            await self.login()

        url = f"/proxy/network/api/s/{self._config.site}{path}"
        headers = {"X-CSRF-Token": self._csrf_token} if self._csrf_token else {}

        resp = await self._client.request(method, url, json=json, headers=headers)

        if resp.status_code == 401:
            # Session cookie expired -- re-auth once and retry.
            await self.login()
            headers = {"X-CSRF-Token": self._csrf_token} if self._csrf_token else {}
            resp = await self._client.request(method, url, json=json, headers=headers)

        if resp.status_code != 200:
            raise UnifiRequestError(f"{method} {url} returned {resp.status_code}: {resp.text[:200]}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise UnifiRequestError(f"{method} {url} did not return JSON") from exc

        return body.get("data", [])

    def _resolve_network(self, raw: dict) -> str:
        vlan = raw.get("vlan") or raw.get("vlan_id")
        if isinstance(vlan, int):
            net = self._settings.network_by_vlan(vlan)
            if net:
                return net.name

        name = raw.get("network") or raw.get("network_name")
        if isinstance(name, str):
            for net in self._settings.networks:
                if net.name.lower() == name.lower():
                    return net.name

        return "Unknown"

    # -- Clients -----------------------------------------------------------

    async def get_active_clients(self) -> list[ClientRecord]:
        """Currently-connected clients. Source: stat/sta."""
        raw_clients = await self._api("GET", "/stat/sta")
        return [self._parse_client(c, is_active=True) for c in raw_clients]

    async def get_known_clients(self) -> list[ClientRecord]:
        """Every client the controller has ever configured/remembered,
        including ones currently offline. Source: rest/user. Used to detect
        devices that have gone offline since the last poll.
        """
        raw_clients = await self._api("GET", "/rest/user")
        return [self._parse_client(c, is_active=False) for c in raw_clients]

    def _parse_client(self, raw: dict, is_active: bool) -> ClientRecord:
        mac = str(raw.get("mac", "")).lower()
        return ClientRecord(
            mac=mac,
            hostname=raw.get("hostname") or raw.get("name"),
            vendor=raw.get("oui"),
            network=self._resolve_network(raw),
            is_wired=bool(raw.get("is_wired", False)),
            is_active=is_active,
            first_seen=_epoch_ms_to_dt(raw.get("first_seen")),
            last_seen=_epoch_ms_to_dt(raw.get("last_seen")),
        )

    # -- Infrastructure ------------------------------------------------------

    async def get_devices(self) -> list[InfraRecord]:
        """Switches, APs, and the gateway itself. Source: stat/device."""
        raw_devices = await self._api("GET", "/stat/device")
        records = []
        for raw in raw_devices:
            device_type = str(raw.get("type", "")).lower()
            kind = _DEVICE_TYPE_MAP.get(device_type, "other")
            records.append(
                InfraRecord(
                    unifi_id=str(raw.get("_id") or raw.get("mac")),
                    name=raw.get("name") or raw.get("model") or raw.get("mac", "unknown"),
                    kind=kind,  # type: ignore[arg-type]
                    network=self._resolve_network(raw),
                    mac=raw.get("mac"),
                    is_online=raw.get("state") == 1,
                )
            )
        return records

    # -- Events & alarms -----------------------------------------------------

    async def get_recent_events(self, limit: int = 200) -> list[EventRecord]:
        """Connect/disconnect/roam events. Source: stat/event."""
        raw_events = await self._api("POST", "/stat/event", json={"_limit": limit, "_sort": "-time"})
        events = []
        for raw in raw_events:
            ts = _epoch_ms_to_dt(raw.get("time"))
            if ts is None:
                continue
            events.append(
                EventRecord(
                    key=str(raw.get("key", "")),
                    mac=raw.get("user") or raw.get("client") or raw.get("mac"),
                    network=raw.get("network") or raw.get("ssid"),
                    message=raw.get("msg", ""),
                    timestamp=ts,
                )
            )
        return events

    async def get_recent_alarms(self, limit: int = 200) -> list[AlarmRecord]:
        """IDS/IPS and other controller-raised alarms -- this is where a
        port scan against your network shows up, flagged by the UDM-Pro's
        own IPS engine (a Suricata-based scan/recon signature), *not*
        something this app detects itself from polled client data. Requires
        Settings -> Security -> Threat Management (IDS or IPS mode) enabled
        on the controller; see README.md's Setup guide, step 3.

        Some controller versions/firmware don't expose this endpoint at all
        (404 api.err.NotFound) even with Threat Management on -- rather than
        raising and taking the whole poll cycle down with it (this used to
        be a real bug: see scheduler.py's comment), that's treated as
        "no alarms available" and logged once, not every cycle.
        """
        try:
            raw_alarms = await self._api("POST", "/stat/alarm", json={"_limit": limit, "_sort": "-time"})
        except UnifiRequestError as exc:
            if not self._alarm_endpoint_warned:
                logger.warning(
                    "stat/alarm isn't available on this controller (%s). Tier 1 "
                    "suspicious-activity detection (UDM-Pro IDS/IPS, including port-scan "
                    "alarms) will stay empty; Tier 2 heuristics are unaffected. Confirm "
                    "Threat Management is enabled (Settings > Security > Threat Management) "
                    "if you expected this to work. This warning only prints once.",
                    exc,
                )
                self._alarm_endpoint_warned = True
            return []

        alarms = []
        for raw in raw_alarms:
            ts = _epoch_ms_to_dt(raw.get("time"))
            if ts is None:
                continue
            dst_port = raw.get("dst_port") or raw.get("dstport")
            alarms.append(
                AlarmRecord(
                    key=str(raw.get("key", "")),
                    message=raw.get("msg", "Unspecified UniFi alarm"),
                    timestamp=ts,
                    category=raw.get("catname") or raw.get("category"),
                    src_ip=raw.get("src_ip") or raw.get("srcip"),
                    dst_ip=raw.get("dst_ip") or raw.get("dstip"),
                    dst_port=int(dst_port) if dst_port else None,
                )
            )
        return alarms
