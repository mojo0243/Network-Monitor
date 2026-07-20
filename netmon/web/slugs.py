"""Turns network names like "Proxmox/GitLab" into URL-safe slugs and back,
so /networks/{slug}/devices works without needing a separate network id
column anywhere -- config.yml's network list stays the single source of
truth for what networks exist.
"""
from __future__ import annotations

import re

from netmon.config import NetworkConfig, Settings


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "network"


def network_by_slug(settings: Settings, slug: str) -> NetworkConfig | None:
    for net in settings.networks:
        if slugify(net.name) == slug:
            return net
    return None
