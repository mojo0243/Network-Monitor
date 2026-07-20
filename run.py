"""Entrypoint: `python run.py`. The systemd unit in deploy/ calls this too."""
from __future__ import annotations

import os
import sys

import uvicorn

from netmon.config import ConfigError, load_config
from netmon.web.app import create_app


def main() -> None:
    config_path = os.environ.get("NETMON_CONFIG", "config.yml")
    try:
        settings = load_config(config_path)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        raise SystemExit(1)

    app = create_app(settings)
    uvicorn.run(app, host=settings.dashboard.bind_host, port=settings.dashboard.bind_port)


if __name__ == "__main__":
    main()
