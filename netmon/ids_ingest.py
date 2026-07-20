"""Tier 1 suspicious-activity source: the UDM-Pro's own IDS/IPS alarms
(Phase 6, task 6.1). Requires Threat Management to be turned on in the
UniFi controller -- if it isn't, `get_recent_alarms()` will just come back
empty and this becomes a no-op, which is fine.
"""
from __future__ import annotations

import datetime as dt

from netmon.unifi_client import AlarmRecord, UnifiClient


class IdsIngestor:
    """Stateful across the app's lifetime: remembers the newest alarm
    timestamp it has already surfaced so the same alarm isn't re-alerted
    on every poll. Resets on restart -- worst case you get one repeat
    alert for anything that fired in roughly the last polling cycle before
    a restart, which is an acceptable tradeoff for not needing a persisted
    cursor table.
    """

    def __init__(self) -> None:
        self._last_seen: dt.datetime | None = None

    async def poll_new_alarms(self, client: UnifiClient) -> list[AlarmRecord]:
        alarms = await client.get_recent_alarms(limit=100)
        if not alarms:
            return []

        alarms.sort(key=lambda a: a.timestamp)

        if self._last_seen is None:
            # First poll after startup: don't replay the controller's entire
            # alarm history, just start the cursor from here.
            self._last_seen = alarms[-1].timestamp
            return []

        new_alarms = [a for a in alarms if a.timestamp > self._last_seen]
        if new_alarms:
            self._last_seen = new_alarms[-1].timestamp
        return new_alarms
