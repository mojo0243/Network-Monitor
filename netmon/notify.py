"""Discord webhook sender (Phase 8, task 8.1)."""
from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger("netmon.notify")

_SEVERITY_COLOR = {
    "info": 0x2F8F82,      # matches the dashboard's accent teal
    "warning": 0xB8792E,
    "critical": 0xB4453B,
}


class DiscordNotifier:
    def __init__(self, webhook_url: str | None):
        self._webhook_url = webhook_url
        self._client = httpx.AsyncClient(timeout=10.0)

    @property
    def enabled(self) -> bool:
        return bool(self._webhook_url)

    async def close(self) -> None:
        await self._client.aclose()

    async def send_alert(self, title: str, message: str, severity: str = "info") -> None:
        if not self._webhook_url:
            logger.debug("Discord webhook not configured, skipping alert: %s", title)
            return

        payload = {
            "embeds": [
                {
                    "title": title,
                    "description": message,
                    "color": _SEVERITY_COLOR.get(severity, _SEVERITY_COLOR["info"]),
                }
            ]
        }

        for attempt in range(2):
            try:
                resp = await self._client.post(self._webhook_url, json=payload)
            except httpx.RequestError as exc:
                logger.warning("Failed to deliver Discord alert %r: %s", title, exc)
                return

            if resp.status_code == 429:
                retry_after = float(resp.json().get("retry_after", 1.0))
                logger.info("Discord rate-limited us, retrying in %.1fs", retry_after)
                await asyncio.sleep(retry_after)
                continue

            if resp.status_code >= 300:
                logger.warning("Discord webhook returned %s: %s", resp.status_code, resp.text[:200])
            return
