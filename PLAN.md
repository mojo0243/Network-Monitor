# Home Network Monitor — Project Plan

A self-hosted dashboard for the UDM-Pro network (Adults / Kids / IoT / Guests / Wired-Adult / iDRAC / Proxmox+GitLab / Training Lab / Training DMZ / new Web-Site DMZ), running on a Raspberry Pi 4. It shows connected and disconnected devices per network with last-seen times, highlights devices new in the last 7 days, alerts when a switch or AP has been offline more than 20 minutes, flags suspicious activity, and tracks uptime for `https://m4cybersolutions.com`.

**Decisions locked in with you:**
- **Alerts** go to a **Discord webhook**.
- **Dashboard** is **LAN-only** (bound to the wired-adult/management VLAN) **with a login**, since it exposes full network topology and a public DMZ is going up next to it.

---

## Architecture at a glance

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.11+, FastAPI | Async-friendly for polling + serving, light enough for a Pi 4 |
| Poller/scheduler | asyncio background tasks (or APScheduler) inside the same process | One systemd service instead of coordinating several |
| UniFi client | `aiounifi` (the library Home Assistant uses for UniFi) | Handles UniFi OS's auth quirks already, actively maintained |
| Database | SQLite + SQLAlchemy + Alembic | Zero admin overhead, fine for this data volume on a Pi |
| Frontend | Jinja2 templates + vanilla JS (fetch polling) | No Node build step to maintain on the Pi or for forks |
| Notifications | Discord webhook (pluggable for forks) | Per your answer above |
| Auth | Local username/password, session cookie, bound to LAN interface | Per your answer above |
| Reverse proxy | Caddy | Automatic local TLS + basic auth layer, tiny config |
| Process mgmt | systemd | Standard on Raspberry Pi OS, auto-restart on crash/reboot |

### Config schema preview (`config.yml`)

```yaml
networks:
  - name: "Wired Adult"
    vlan_id: 10
    role: management
  - name: "Adults Wi-Fi"
    vlan_id: 20
    role: trusted
  - name: "Kids"
    vlan_id: 30
    role: trusted
  - name: "IoT"
    vlan_id: 40
    role: restricted
  - name: "Guests"
    vlan_id: 50
    role: untrusted
  - name: "iDRAC"
    vlan_id: 60
    role: management
  - name: "Proxmox/GitLab"
    vlan_id: 70
    role: infrastructure
  - name: "Training Lab"
    vlan_id: 80
    role: lab
  - name: "Training DMZ"
    vlan_id: 90
    role: dmz
  - name: "Web Site DMZ"
    vlan_id: 100
    role: dmz

unifi:
  host: "192.168.1.1"
  username: "monitor-ro"
  password: "${UNIFI_PASSWORD}"   # env-var override, never commit real value
  site: "default"
  verify_ssl: false

alerts:
  new_device_days: 7
  infra_offline_minutes: 20
  discord_webhook_url: "${DISCORD_WEBHOOK_URL}"

website_monitors:
  - name: "m4cybersolutions.com"
    url: "https://m4cybersolutions.com"
    interval_seconds: 60
    timeout_seconds: 10
    failure_threshold: 2

dashboard:
  bind_host: "192.168.1.5"   # wired-adult VLAN interface only
  bind_port: 8080
  session_secret: "${SESSION_SECRET}"
```

---

## How to read the task list

Every **Task** carries a complexity score (1–10). Anything scoring **above 3** is broken into **Subtasks** (and those into sub-subtasks) until every leaf item scores **3 or lower** — those leaf items are what you actually implement one at a time.

---

## Phase 0 — Prerequisites

| ID | Item | Complexity |
|---|---|---|
| 0.1 | Flash Raspberry Pi OS Lite (64-bit), enable SSH | 1 |
| 0.2 | Create a local, non-SSO, read-only admin account on the UDM-Pro for API access | 2 |
| 0.3 | Create a Discord channel + webhook URL for alerts | 1 |
| 0.4 | Install Python 3.11+, git, create project venv on the Pi | 2 |

---

## Phase 1 — Configuration System

**Task 1.1 — Design the YAML config schema** — complexity **4** → decomposed:

| ID | Subtask | Complexity |
|---|---|---|
| 1.1.1 | Networks/VLAN section (name, vlan_id, role) | 2 |
| 1.1.2 | UniFi controller section (host, credentials, site, verify_ssl) | 2 |
| 1.1.3 | Alerting/thresholds section (new_device_days, offline_minutes, webhook) | 2 |
| 1.1.4 | Website-monitor section (targets, interval, timeout, failure_threshold) | 2 |

**Task 1.2 — Implement config loader & validation** — complexity **4** → decomposed:

| ID | Subtask | Complexity |
|---|---|---|
| 1.2.1 | Pydantic models mirroring the schema | 2 |
| 1.2.2 | YAML load + env-var override + friendly validation errors | 3 |
| 1.2.3 | Ship `config.example.yml` in the repo | 1 |

---

## Phase 2 — Data Layer

**Task 2.1 — Design the SQLite schema** — complexity **5** → decomposed:

| ID | Subtask | Complexity |
|---|---|---|
| 2.1.1 | `devices` table (mac, hostname, network, vendor, custom_name, first_seen, last_seen) | 2 |
| 2.1.2 | `device_sightings` history table (mac, network, timestamp, event type) | 2 |
| 2.1.3 | `infrastructure` table (switch/AP id, name, network, status, last_seen) | 2 |
| 2.1.4 | `alerts` table (type, severity, message, created_at, acknowledged) | 2 |
| 2.1.5 | `uptime_checks` table (target, timestamp, status, response_ms) | 2 |

**Task 2.2 — Implement ORM + migrations** — complexity **4** → decomposed:

| ID | Subtask | Complexity |
|---|---|---|
| 2.2.1 | SQLAlchemy models | 2 |
| 2.2.2 | Alembic migration setup | 2 |
| 2.2.3 | DB session/connection module | 1 |

---

## Phase 3 — UniFi Controller Integration

**Task 3.1 — Authenticate to the UDM-Pro controller** — complexity **4** → decomposed:

| ID | Subtask | Complexity |
|---|---|---|
| 3.1.1 | Login/session handling via `aiounifi` | 3 |
| 3.1.2 | Self-signed certificate trust option | 1 |
| 3.1.3 | Session refresh/reconnect on expiry | 2 |

| ID | Task | Complexity |
|---|---|---|
| 3.2 | Fetch sites & network/VLAN list | 2 |
| 3.3 | Fetch connected clients per network | 3 |
| 3.4 | Fetch historical/offline clients | 3 |
| 3.5 | Fetch switch/AP inventory + status | 3 |

**Task 3.6 — Ingest UniFi events/alarms feed** — complexity **4** → decomposed:

| ID | Subtask | Complexity |
|---|---|---|
| 3.6.1 | Poll connect/disconnect/roam events | 2 |
| 3.6.2 | Poll IDS/IPS alarms (if Threat Management is enabled on the UDM-Pro) | 3 |
| 3.6.3 | Normalize events into an internal event model | 2 |

---

## Phase 4 — Device Tracking Engine

**Task 4.1 — Online/offline state machine** — complexity **4** → decomposed:

| ID | Subtask | Complexity |
|---|---|---|
| 4.1.1 | Reconcile poll results against `devices` table (upsert) | 2 |
| 4.1.2 | Mark offline after N consecutive missed polls | 2 |
| 4.1.3 | Record `last_seen` + sighting entries | 1 |

| ID | Task | Complexity |
|---|---|---|
| 4.2 | New-device detection (highlight devices first seen < 7 days) | 2 |
| 4.3 | Per-network grouping for display | 2 |

---

## Phase 5 — Infrastructure (Switch/AP) Monitoring

| ID | Task | Complexity |
|---|---|---|
| 5.1 | Track switch/AP status from each poll | 2 |
| 5.2 | Offline-duration timer + 20-minute alert trigger | 3 |
| 5.3 | Alert de-duplication while device remains offline | 2 |

---

## Phase 6 — Suspicious Activity Detection

| ID | Task | Complexity |
|---|---|---|
| 6.1 | Ingest native UniFi IDS/IPS alarms as suspicious-activity alerts (Tier 1 — do this first, highest signal-to-effort) | 3 |

**Task 6.2 — Custom heuristics engine (Tier 2)** — complexity **6** → decomposed:

| ID | Subtask | Complexity |
|---|---|---|
| 6.2.1 | Heuristic: a known device appears on a network/VLAN it has never used before (possible lateral movement) | 3 |
| 6.2.2 | Heuristic: MAC-vendor OUI doesn't match the device's known fingerprint (possible spoofing) | 3 |
| 6.2.3 | Heuristic: activity outside a device's learned active-hours baseline | 3 |
| 6.2.4 | Heuristic: rapid connect/disconnect churn ("flapping") in a short window | 2 |
| 6.2.5 | Heuristic: spike in new-device count within a short window (rogue AP / guest-network abuse) | 2 |

| ID | Task | Complexity |
|---|---|---|
| 6.3 | Suspicious-activity alert composer (assembles heuristic hits into one alert record) | 2 |

> Note: rule-based heuristics like these will produce false positives sometimes — that's expected and fine for a home setup. Tier 1 (native UniFi IDS/IPS) is the higher-quality signal; Tier 2 is a supplementary layer, not a replacement for it.

---

## Phase 7 — Website Uptime Monitoring

| ID | Task | Complexity |
|---|---|---|
| 7.1 | HTTPS health-check worker for `https://m4cybersolutions.com` | 3 |
| 7.2 | Downtime detection via consecutive-failure threshold + incident tracking | 3 |
| 7.3 | Uptime % and response-time history calculation for display | 2 |
| 7.4 | Optional: note/integrate an external vantage-point check (e.g. a free-tier cloud cron ping), since monitoring only from home won't detect the site being down if your home internet is what's down | 2 |

---

## Phase 8 — Alerting & Notification Dispatch

| ID | Task | Complexity |
|---|---|---|
| 8.1 | Discord webhook sender module | 2 |
| 8.2 | Alert-routing rules (alert type/severity → dispatch) | 2 |
| 8.3 | Alert persistence + acknowledge/mute in DB | 2 |

---

## Phase 9 — Backend API (FastAPI)

**Task 9.1 — Auth (LAN-only + login)** — complexity **4** → decomposed:

| ID | Subtask | Complexity |
|---|---|---|
| 9.1.1 | Username/password login, hashed password stored in config or DB | 2 |
| 9.1.2 | Session/cookie handling + login-required middleware | 2 |
| 9.1.3 | Bind the service to the wired management-VLAN interface only | 1 |

**Task 9.2 — REST endpoints** — complexity **5** → decomposed:

| ID | Subtask | Complexity |
|---|---|---|
| 9.2.1 | `GET /networks` — list networks + summary counts | 1 |
| 9.2.2 | `GET /networks/{id}/devices` — connected/disconnected + last-seen | 2 |
| 9.2.3 | `GET /infrastructure` — switches/APs + status | 1 |
| 9.2.4 | `GET/POST /alerts` — list/filter/acknowledge | 2 |
| 9.2.5 | `GET /uptime` — website monitor history/summary | 1 |
| 9.2.6 | `PATCH /networks/{id}/devices/{mac}/label` — set/clear a custom name for a device, keyed by MAC | 2 |

| ID | Task | Complexity |
|---|---|---|
| 9.3 | Live-refresh mechanism (short polling endpoint or WebSocket) | 3 |

---

## Phase 10 — Frontend Dashboard

| ID | Task | Complexity |
|---|---|---|
| 10.1 | Base layout & navigation (Networks / Infrastructure / Alerts / Uptime) | 3 |
| 10.2 | Network/device view — per-network table, online/offline state, new-device highlight | 3 |
| 10.3 | Infrastructure status view — switch/AP cards + offline banner | 2 |
| 10.4 | Alerts view — list, severity styling, acknowledge action | 2 |
| 10.5 | Website uptime view — status, uptime %, incident history, simple chart | 3 |
| 10.6 | Responsive styling (usable from a phone) | 2 |
| 10.7 | Inline device-nickname editor — click-to-edit custom name next to the MAC in the device view; MAC stays visible even after a nickname is set | 2 |

> Custom names are cosmetic only — every internal lookup, sighting record, and alert still keys off the MAC address. Where a nickname exists, alert text and the dashboard show `"Nickname (mac)"` instead of just the raw MAC; devices without one just show the MAC as today.

---

## Phase 11 — Orchestration / Scheduler

**Task 11.1 — Background scheduler tying all pollers together** — complexity **4** → decomposed:

| ID | Subtask | Complexity |
|---|---|---|
| 11.1.1 | asyncio/APScheduler jobs with configurable intervals per poller | 2 |
| 11.1.2 | Error handling/backoff on poll failure | 2 |
| 11.1.3 | Structured logging across all background jobs | 2 |

---

## Phase 12 — Packaging & Deployment

| ID | Task | Complexity |
|---|---|---|
| 12.1 | systemd service unit | 2 |
| 12.2 | Caddy reverse proxy with local TLS, in front of the LAN-only login | 3 |
| 12.3 | Firewall/VLAN placement guidance (bind to wired-adult VLAN only) | 1 |
| 12.4 | Backup/retention — SQLite backup cron, log rotation, data-retention policy | 2 |
| 12.5 | One-command installer script (venv, deps, systemd unit, default config) | 3 |

---

## Phase 13 — Documentation

| ID | Task | Complexity |
|---|---|---|
| 13.1 | README — overview, features, screenshots | 2 |
| 13.2 | Setup guide — UDM-Pro account creation, config.yml walkthrough, Pi install steps | 3 |
| 13.3 | Config reference — every YAML key documented | 2 |
| 13.4 | Troubleshooting guide — cert errors, auth failures, VLAN routing | 2 |
| 13.5 | Contributing guide + license (for open-sourcing) | 1 |

---

## Phase 14 — Testing & Validation

| ID | Task | Complexity |
|---|---|---|
| 14.1 | Unit tests — config loader, state machine, heuristics | 3 |
| 14.2 | Integration test against the real UDM-Pro (manual checklist) | 2 |
| 14.3 | Resource/soak test on actual Pi 4 hardware | 2 |
| 14.4 | End-to-end alert-path test (trigger each alert type, confirm Discord delivery) | 2 |

---

## Suggested build order (milestones)

1. **Milestone A — Visibility MVP:** Phases 0 → 1 → 2 → 3 → 4, plus the `/networks` and `/networks/{id}/devices` endpoints (9.2.1–9.2.2) and a bare-bones device view (10.1–10.2). Result: you can see every device on every network, online or not, with last-seen times.
2. **Milestone B — Infra alerts + Discord:** Phase 5, then Phase 8, wired into the dashboard's Infrastructure and Alerts views (10.3–10.4). Result: offline switch/AP alerts land in Discord.
3. **Milestone C — Website uptime:** Phase 7 + its dashboard view (10.5).
4. **Milestone D — Suspicious activity:** Phase 6 (Tier 1 first, Tier 2 heuristics after).
5. **Milestone E — Lock it down and ship it:** Phase 9's auth (9.1), Phase 11 (scheduler hardening), Phase 12 (deployment), Phase 13 (docs), Phase 14 (testing).

This order gets you a useful dashboard fast (Milestone A) and defers the heavier security/heuristics work until the foundation is solid.

---

## Next steps

Say the word and I'll start on Milestone A (Phases 0–4 + the first API/UI slice) — happy to work through it phase by phase, or all at once if you'd rather review it in bigger chunks.
