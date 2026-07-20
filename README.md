# Home Network Monitor

A self-hosted dashboard for a UniFi (UDM-Pro) home network. It watches every
configured network for connected/disconnected devices, highlights devices
that showed up in the last N days, alerts when a switch or AP has been
offline too long, flags suspicious activity, and tracks uptime for one or
more websites. Everything is driven by a single `config.yml`, so it's meant
to be forked and re-pointed at a different UniFi setup without touching code.

Built to run on a Raspberry Pi 4. See [PLAN.md](PLAN.md) for the full design
and task breakdown this was built from.

## Contents

- [What it does](#what-it-does)
- [Quick start (development)](#quick-start-development)
- [Project layout](#project-layout)
- [Why these choices](#why-these-choices)
- [Setup guide](#setup-guide) — full Raspberry Pi deployment walkthrough
- [Configuration reference](#configuration-reference) — every `config.yml` field
- [Troubleshooting](#troubleshooting)
- [Contributing](#contributing)
- [License](#license)

## What it does

- **Networks** -- every network from `config.yml`, with connected and
  disconnected devices, last-seen times, and a highlight for anything first
  seen in the last `alerts.new_device_days` days (default 7).
- **Custom device names** -- give any device a nickname without losing the
  MAC address; every alert and internal lookup still keys off the MAC, the
  nickname is purely a label.
- **Infrastructure** -- switches and APs, with an alert once one has been
  offline for more than `alerts.infra_offline_minutes` (default 20).
- **Suspicious activity** -- two tiers: the UDM-Pro's own IDS/IPS alarms (if
  Threat Management is enabled) -- this is what catches things like port
  scans, since that needs packet-level inspection this app doesn't do itself
  -- plus a handful of explainable heuristics (a device on a network it's
  never used before, hostname/vendor mismatches, off-hours activity, rapid
  connect/disconnect flapping, a spike in new devices). See
  `netmon/heuristics.py` for the heuristics and `netmon/unifi_client.py`'s
  `AlarmRecord` for what an IDS/IPS alert carries (category, source/dest
  IP/port).
- **Website uptime** -- polls one or more URLs (e.g. your own site) from the
  Pi, opens an "incident" after N consecutive failures, and tracks downtime
  duration. Note this only tells you the site is unreachable *from your home
  network* -- see the note in `netmon/uptime.py` and task 7.4 in the plan.
- **Alerts** -- everything above lands in the Alerts page and, if configured,
  a Discord webhook.
- **Dark mode** -- follows your OS/browser preference by default; the toggle
  in the top bar overrides that and remembers your choice per-browser.

## Quick start (development)

This needs Python 3.11+. On the Pi, use `deploy/install.sh` instead (see
[Setup guide](#setup-guide)) -- this section is for running it locally to
look around.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-dev.txt

cp config.example.yml config.yml
export UNIFI_PASSWORD=whatever      # not used until you point it at a real controller
export DISCORD_WEBHOOK_URL=
export SESSION_SECRET=$(python -c "import secrets; print(secrets.token_hex(32))")

alembic upgrade head
python scripts/create_admin.py      # set a dashboard username/password
python scripts/seed_demo_data.py    # optional: fake devices/alerts/uptime data to look at
python run.py
```

Then open http://127.0.0.1:8080 and log in. Run `pytest` to run the test
suite (27 tests covering config loading, the device state machine, infra
offline alerting, the heuristics, and uptime incident tracking).

## Project layout

```
netmon/                 application code
  config.py              YAML config loading + validation
  models.py               SQLAlchemy models
  unifi_client.py         thin REST client for the UDM-Pro's controller API
  tracking.py              device online/offline state machine
  infra_monitor.py          switch/AP offline-duration alerting
  heuristics.py / ids_ingest.py   suspicious-activity detection (tier 2 / tier 1)
  uptime.py                 website health checks + incident tracking
  notify.py / alerts.py      Discord dispatch + alert composition/persistence
  scheduler.py               background polling loops
  web/                       FastAPI app, routes, templates, static assets
migrations/               Alembic schema migrations
scripts/                  create_admin.py, seed_demo_data.py
deploy/                   systemd unit, Caddyfile, install.sh
tests/                    pytest suite
```

## Why these choices

Every dependency was picked to install cleanly on a Raspberry Pi without
compiling anything: no bcrypt (uses passlib's pure-Python pbkdf2_sha256), no
uvloop/httptools (plain `uvicorn`), and a hand-rolled UniFi client over
`httpx` instead of a heavier library. See `netmon/unifi_client.py`'s
docstring for why that client isn't `aiounifi`.

---

## Setup guide

Written for a Raspberry Pi 4 running Raspberry Pi OS Lite (64-bit) and a
UDM-Pro. Adjust as needed for other UniFi controllers -- the REST endpoints
this app uses (`stat/sta`, `rest/user`, `stat/device`, `stat/alarm`) are the
same across UniFi OS consoles (UDM, UDM-Pro, UDM-SE, Cloud Key Gen2+).

### 1. Give the Pi a home on your network

This app's dashboard is LAN-only by design (see ["Why LAN-only"](#why-lan-only)
below), so where you plug the Pi in matters:

1. Wire the Pi into whichever network is meant to reach it -- for the setup
   this was designed against, that's the "Wired Adult" network.
2. In the UniFi controller, give the Pi a **DHCP reservation** (a fixed IP
   tied to its MAC) so `dashboard.bind_host` and your bookmarks don't break
   after a reboot. Settings -> Networks -> (your network) -> DHCP, or
   Client Devices -> (the Pi) -> "Use Fixed IP Address".

### 2. Create a read-only UniFi account for the app

Don't use your personal admin login. In the UniFi controller:

1. Settings -> Admins -> Add Admin.
2. Create a **local** account (not a UI/SSO account -- this app logs in with
   a username/password, which SSO-only accounts don't have).
3. Role: **Limited Admin** or, if your controller version offers it, a
   read-only / view-only role. This account only ever does GET/POST reads
   against the controller; it doesn't need to change anything.
4. Note the username and password -- they go in `config.yml` /
   `UNIFI_PASSWORD` (step 4).

### 3. (Optional but recommended) Enable Threat Management

The suspicious-activity Tier 1 signal (`netmon/ids_ingest.py`) reads the
UDM-Pro's own IDS/IPS alarms. If you want that, turn on Settings -> Security
-> Threat Management (IDS or IPS mode) before continuing. If you skip this,
the app still runs fine -- that tier just never has anything to report, and
you're relying on the Tier 2 heuristics alone.

**This is also how port scans get flagged.** This app doesn't do its own
packet-level scan detection -- it polls the controller's summary API
(connected clients, device status), which has no per-connection/per-port
data to look at. A port scan is instead caught by the UDM-Pro's own IPS
engine (Suricata-based) once Threat Management is on, categorized as
something like `Attempted-Recon`, and shows up here as a `suspicious` alert
with the source IP, destination IP/port, and category attached (see
`AlarmRecord.detail_suffix()` in `netmon/unifi_client.py`) -- for example:

> UniFi IDS/IPS: Attempted Information Leak (category=Attempted-Recon, src=10.72.8.55, dst=10.72.8.10:22)

If Threat Management is off, or your controller doesn't expose the alarms
endpoint at all (see the note below), you won't get port-scan alerts --
there's currently no fallback detection path for it.

**Note:** some controller versions/firmware return a 404 for the alarms
endpoint even with Threat Management enabled -- if your log shows a line
like `stat/alarm isn't available on this controller`, that's this app
noticing it and moving on gracefully (device/client tracking is unaffected);
it just means Tier 1 has nothing to ingest on your setup. There's no
workaround from this app's side yet -- if you hit this, it's worth checking
whether your controller has a newer alarm API under a `/v2/` path, and
opening an issue/PR (see [Contributing](#contributing)).

### 4. Install the app

```bash
git clone <your fork's URL> network-monitor
cd network-monitor
sudo bash deploy/install.sh
```

This creates a `netmon` system user, installs to `/opt/network-monitor`, sets
up a venv, and runs the DB migrations. It stops short of starting the
service or touching your firewall on purpose -- finish these steps first:

#### Edit `/opt/network-monitor/config.yml`

Start from what `deploy/install.sh` copied from `config.example.yml`. At
minimum:

- `networks`: list your VLANs (name, vlan_id, role). This is the single
  source of truth for what shows up as a "network" in the dashboard --
  devices on a VLAN not listed here still get tracked, just grouped under
  the fallback "Unknown" bucket, so list everything.
- `unifi.host`: your UDM-Pro's IP.
- `unifi.username`: the account from step 2 (password goes in `.env`, not
  here).
- `website_monitors`: the site(s) you want uptime tracked for.

Full field-by-field reference: [Configuration reference](#configuration-reference).

##### Finding your VLAN IDs

In the UniFi Network app (`https://<UDM-Pro-IP>/network/` or `unifi.ui.com`):
**Settings -> Networks**, click into each network. If the VLAN ID isn't shown
directly, flip the **Advanced** toggle from Auto to Manual to reveal a
**VLAN ID** field. Do this once per network and match it up:

```yaml
networks:
  - name: "Kids"
    vlan_id: 30      # <- whatever Settings > Networks > Kids showed you
    role: trusted
```

##### Finding `unifi.host` and `unifi.site`

- `host`: the address you type into your browser to reach the controller UI
  from the same network the Pi sits on -- it's literally that IP. You can
  cross-check it under **Settings -> Networks -> [Wired Adult]** as the
  network's Gateway IP.
- `site`: almost always `"default"`. If you've created more than one site,
  look at the browser URL while viewing the controller --
  `https://<host>/network/<SITE_ID>/dashboard` -- the `<SITE_ID>` segment is
  the exact value to use.

#### Edit `/opt/network-monitor/.env`

```bash
UNIFI_PASSWORD=<the read-only account's password>
DISCORD_WEBHOOK_URL=<see step 5>
SESSION_SECRET=<install.sh already generated this -- leave it>
```

#### Create your dashboard login

```bash
cd /opt/network-monitor
sudo -u netmon bash -c 'set -a && source .env && set +a && NETMON_CONFIG=/opt/network-monitor/config.yml venv/bin/python scripts/create_admin.py'
```

#### Start it

```bash
sudo systemctl enable --now network-monitor
sudo systemctl status network-monitor
journalctl -u network-monitor -f     # watch it come up
```

At this point the app is listening on `127.0.0.1:8080` only -- not reachable
from the network yet. That's what Caddy is for.

### 5. Create a Discord webhook (for alerts)

Discord server -> Server Settings -> Integrations -> Webhooks -> New
Webhook. Pick the channel you want alerts in, copy the webhook URL, and put
it in `.env` as `DISCORD_WEBHOOK_URL`. Leave it blank to disable Discord
alerts entirely (alerts still show up in the dashboard's Alerts page either
way).

### 6. Put Caddy in front of it

```bash
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy

sudo cp /opt/network-monitor/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl restart caddy
```

Now `https://<pi's IP>/` serves the dashboard. The certificate is
locally-trusted, not publicly signed (there's no public domain to get one
for), so your browser will warn once -- that's expected; accept it (or
import Caddy's root CA, printed by `caddy trust`, if you'd rather not see
the warning every time).

**About the `failed to install root certificate` / `pam_unix(sudo:auth)`
lines in `sudo systemctl status caddy`:** these are expected and harmless --
`tls internal` tries to install its local CA into the *Pi's own* system
trust store on every start, which is pointless on a headless box you browse
to from other devices, and it fails loudly because the unprivileged `caddy`
service user isn't in sudoers (correctly so). The Caddyfile in this repo sets
`skip_install_trust` globally so Caddy stops attempting it. If you set Caddy
up before this fix, re-copy the file and restart:

```bash
sudo cp /opt/network-monitor/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl restart caddy
```

The line to actually check in that output is nearer the bottom:
`"msg":"serving initial configuration"` -- if that's there, Caddy is fine
and listening; the certificate warning above it isn't what's breaking
anything. If the dashboard still isn't reachable after this, see
[Caddy is running but the dashboard isn't reachable](#caddy-is-running-but-the-dashboard-isnt-reachable).

#### Why LAN-only

This app maps your entire network -- every device, every switch and AP, all
your VLAN names and roles. That's exactly the kind of thing you don't want
reachable from the internet, especially once a public-facing DMZ exists on
the same router. `dashboard.bind_host: 127.0.0.1` means the app itself is
never reachable except through Caddy, and Caddy is only reachable because
the Pi's only network presence is the wired VLAN you placed it on in step 1.
There's no port-forward, no dynamic DNS, nothing exposed outward -- keep it
that way.

### 7. Restrict it further with firewall rules (optional but recommended)

Even though nothing routes the dashboard outward by default, it's worth
adding an explicit UniFi firewall rule blocking the Guest, IoT, and DMZ
networks from initiating connections to the Pi's IP on 443/8080, so a
compromised device on one of those networks can't reach it even if your
network topology changes later. Settings -> Security -> Firewall Rules ->
create a rule per source network, action "Drop", destination = the Pi's IP.

### 8. Confirm it's alerting

- Unplug a switch or AP for 20+ minutes (or lower `alerts.infra_offline_minutes`
  temporarily) and confirm an alert shows up in the dashboard and Discord.
- Connect a new device and confirm the "new device" highlight and alert.
- Take your website down (or point `website_monitors` at a URL that 404s)
  and confirm a downtime alert, then bring it back and confirm recovery.

If any of these don't fire, see [Troubleshooting](#troubleshooting).

---

## Configuration reference

Every key in `config.yml`, validated by `netmon/config.py`. Start from
`config.example.yml` rather than this document -- this is for looking up
what a field does, not for copy-pasting structure.

### Environment variable substitution

Anywhere a value is `${SOME_NAME}`, the loader replaces it with the
environment variable `SOME_NAME` at startup and fails fast with a clear
error if it isn't set. This only applies to actual YAML values -- a comment
that happens to contain `${...}` is left alone. Used for secrets
(`UNIFI_PASSWORD`, `DISCORD_WEBHOOK_URL`, `SESSION_SECRET`) so they never
have to live in the YAML file itself; see `deploy/install.sh` and
`network-monitor.service`'s `EnvironmentFile=` for how those get set on the
Pi.

### `networks` (required, at least one)

The single source of truth for what VLANs exist. A device on a VLAN not
listed here is still tracked, just grouped under a fallback "Unknown"
network in the dashboard -- list everything you want broken out.

| Field | Type | Notes |
|---|---|---|
| `name` | string | Display name. Also used to build the URL slug (`/networks/<slug>`) -- doesn't need to be URL-safe itself. |
| `vlan_id` | integer | Must be unique across all networks. Matched against each client's VLAN as reported by the controller. |
| `role` | string | One of `management`, `trusted`, `restricted`, `untrusted`, `infrastructure`, `lab`, `dmz`. Informational today (shown in the UI); a natural place to hang future role-based heuristics. |

### `unifi` (required)

| Field | Type | Default | Notes |
|---|---|---|---|
| `host` | string | -- | UDM-Pro's IP or hostname. |
| `username` | string | -- | A **local**, non-SSO account -- see [Setup guide step 2](#2-create-a-read-only-unifi-account-for-the-app). |
| `password` | string | -- | Almost always `${UNIFI_PASSWORD}`. |
| `site` | string | `"default"` | UniFi "site" name, only relevant if you've created more than one site in the controller. |
| `verify_ssl` | bool | `false` | UDM-Pro ships a self-signed cert; leave `false` unless you've installed a real one. |
| `poll_interval_seconds` | int | `30` | How often the app polls clients/devices/alarms. Minimum 5. |

### `alerts` (required)

| Field | Type | Default | Notes |
|---|---|---|---|
| `new_device_days` | int | `7` | A device is highlighted as "new" while `now - first_seen` is within this many days. |
| `infra_offline_minutes` | int | `20` | A switch/AP alert fires once it's been offline this long, once, until it recovers. |
| `discord_webhook_url` | string \| null | `null` | Leave unset/blank to disable Discord delivery -- alerts still appear in the dashboard. |

#### `alerts.heuristics`

Tier 2 suspicious-activity thresholds -- see `netmon/heuristics.py` for what
each one actually checks. Tune these for your own network's normal
behaviour; the defaults are reasonable starting points, not tuned to any
specific household.

| Field | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `true` | Turns off all Tier 2 heuristics at once (Tier 1 UniFi IDS/IPS ingestion is unaffected). |
| `flapping_window_minutes` | int | `10` | Window used to detect rapid connect/disconnect churn. |
| `flapping_threshold` | int | `6` | Connect+disconnect events within the window that counts as flapping. |
| `new_device_spike_window_minutes` | int | `15` | Window used to detect a burst of new devices. |
| `new_device_spike_threshold` | int | `5` | New devices within the window that counts as a spike. |
| `off_hours_min_history_sightings` | int | `50` | A device needs at least this many historical sightings before "active at an unusual hour" is trusted -- avoids flagging brand-new devices that simply don't have a baseline yet. |

### `website_monitors` (optional, list)

One entry per site/URL to track uptime for.

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | string | -- | Display name; also the key alerts and dashboard rows are grouped by. |
| `url` | string | -- | Must start with `http://` or `https://`. |
| `interval_seconds` | int | `60` | How often to check. Minimum 10. |
| `timeout_seconds` | int | `10` | Per-request timeout. |
| `failure_threshold` | int | `2` | Consecutive failed checks before a downtime incident (and alert) opens. |

### `database`

| Field | Type | Default | Notes |
|---|---|---|---|
| `path` | string | `"data/netmon.db"` | SQLite file path, relative to the working directory the app is started from. |

### `dashboard` (required)

| Field | Type | Default | Notes |
|---|---|---|---|
| `bind_host` | string | `"127.0.0.1"` | Leave as loopback -- Caddy is meant to be the only thing exposed off of it. See [Why LAN-only](#why-lan-only). |
| `bind_port` | int | `8080` | |
| `session_secret` | string | -- | Required, 16+ characters. Generate with `python -c "import secrets; print(secrets.token_hex(32))"`. Rotating it logs everyone out. |
| `session_max_age_hours` | int | `12` | How long a login session lasts before you have to sign in again. |

### `logging`

| Field | Type | Default | Notes |
|---|---|---|---|
| `level` | string | `"INFO"` | Standard Python logging levels (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `file` | string | `"data/netmon.log"` | Rotates at 5MB, keeps 3 backups (see `netmon/logging_setup.py`). |

---

## Troubleshooting

### "Configuration error: config.yml references ${X} but that environment variable is not set"

`.env` is missing that variable, or the systemd unit isn't loading it. Check:

```bash
sudo systemctl show network-monitor -p EnvironmentFiles
cat /opt/network-monitor/.env    # confirm the variable is actually there
```

### UniFi login fails / "UDM-Pro login failed with status 400/401/403"

- Confirm `unifi.username`/`UNIFI_PASSWORD` match a **local** account (Admins
  -> that user -> confirm it's not shown as an SSO/UI account). SSO-only
  accounts don't have a password this app can log in with.
- Confirm `unifi.host` is reachable from the Pi: `curl -k https://<host>/`
  from the Pi itself should at least get a TLS handshake, not a timeout.
- If you changed the account's role recently, log out of the UniFi web UI
  and back in once -- role changes sometimes don't take effect for existing
  sessions/tokens immediately.

### "Could not reach UDM-Pro" / connection timeouts in the logs

This is the scheduler's own error handling doing its job -- one failed poll
cycle logs a traceback and backs off, it doesn't crash the app. Check:

```bash
journalctl -u network-monitor -f
```

If it never recovers: verify the Pi can actually route to `unifi.host` (are
they on the same network, or does a firewall rule between VLANs block it?),
and that nothing else (a firewall rule, an ACL) is blocking outbound HTTPS
from the Pi.

### Certificate warnings in the browser when opening the dashboard

Expected -- see [Setup guide step 6](#6-put-caddy-in-front-of-it). Caddy's
`tls internal` directive issues a locally-trusted, not publicly-signed,
certificate (there's no public domain to get a real one for a LAN-only
dashboard). Either accept the one-time warning, or run `caddy trust` on the
Pi and import the printed root CA into your browser/OS to make the warning
go away for good.

### Caddy is running but the dashboard isn't reachable

`sudo systemctl status caddy` showing `active (running)` only means the
Caddy process is alive -- it doesn't confirm the proxy path to the app
actually works. Two `failed to install root certificate` /
`pam_unix(sudo:auth)` lines near the top of that output are a separate,
harmless issue (see [Setup guide step 6](#6-put-caddy-in-front-of-it)) --
don't let those distract from the real check, which is whether
`"msg":"serving initial configuration"` appears further down.

Work through these from the Pi itself first, then from another device:

```bash
# 1. Is the app itself actually up, independent of Caddy?
sudo systemctl status network-monitor
curl -v http://127.0.0.1:8080/login          # from the Pi -- expect a 200 with HTML

# 2. Is Caddy proxying to it correctly?
curl -vk https://127.0.0.1/login             # from the Pi -- expect the same 200

# 3. Is it reachable from elsewhere on the network?
curl -vk https://<pi's-IP>/login             # from your laptop/phone
```

What each failure at step 2/3 usually means:

- **Connection refused**: Caddy isn't listening on 443, or a firewall
  (`ufw`, or a UniFi rule) is blocking the port. Check
  `sudo ss -tlnp | grep :443` on the Pi.
- **502 Bad Gateway**: Caddy is up but `network-monitor` isn't -- go back to
  step 1. Check `journalctl -u network-monitor -e` for why it's not running
  (commonly a `config.yml`/`.env` problem -- see the other entries here).
- **Times out / no response at all**: reachability, not Caddy or the app --
  confirm the Pi's actual IP (`ip addr`) matches what you're browsing to,
  and that the device you're testing from is on the same network the Pi is
  wired into.
- **TLS handshake failure specifically** (not just a browser cert warning):
  confirm you copied the *current* `deploy/Caddyfile` from this repo, not an
  older or hand-edited version -- `sudo cat /etc/caddy/Caddyfile` to check.

### `unifi.verify_ssl: false` and you'd rather not disable cert verification

The UDM-Pro's default cert is self-signed and won't validate against a
public CA bundle. If you've replaced it with a real certificate (Settings ->
System -> Certificate -> your own cert), set `verify_ssl: true`. Otherwise
leave it `false` -- the connection is still encrypted, it's only the
certificate chain that isn't being verified.

### A device is grouped under a network that isn't in `config.yml`

Devices are matched to a network from `config.yml` by VLAN ID first, then by
network name. If a device shows up under a fallback "Unknown" bucket (or
under the wrong network), check:

- Is that VLAN actually listed in `config.yml`'s `networks:`? Devices on an
  unlisted VLAN always fall back to "Unknown".
- Does the `vlan_id` in `config.yml` match what the controller actually
  assigned (Settings -> Networks -> that network -> VLAN ID)?

### No suspicious-activity alerts ever fire

- Tier 1 (UniFi IDS/IPS -- this is also what flags port scans, see
  [Setup guide step 3](#3-optional-but-recommended-enable-threat-management))
  requires Threat Management to be turned on in the controller. Without it,
  `stat/alarm` simply has nothing to return, which is normal, not a bug.
- If `data/netmon.log` has a line like `stat/alarm isn't available on this
  controller`, your controller doesn't expose that endpoint at all (some
  versions return a 404 even with Threat Management on). This is handled
  gracefully -- logged once, doesn't affect anything else -- but it does
  mean Tier 1 (including port-scan alarms) has nothing to ingest on your
  setup right now.
- Tier 2 heuristics need history to work from. A brand new install with no
  sighting history won't flag "off-hours activity" or "never used this
  network before" for anything yet -- give it a few days.
- Confirm `alerts.heuristics.enabled: true` in `config.yml`.

### Dashboard shows 0 clients / devices never show up

If `data/netmon.log` shows a successful `Logged in to UDM-Pro at <host>`
line followed by an `ERROR ... UniFi poll cycle failed` traceback, the
login and connectivity are fine -- something later in that same poll cycle
is throwing before devices get saved to the database. Read the traceback:
whatever line it names is the thing to fix (e.g. a 404 on a specific
endpoint means that endpoint isn't available on your controller version).
Core device/infrastructure tracking is written to be resilient to the
optional IDS/IPS alarm fetch failing (see the comment in
`netmon/scheduler.py`'s `_unifi_cycle`) -- if you're seeing devices never
appear alongside an alarm-related error, make sure you're running a version
of this app that includes that fix.

### Discord alerts aren't arriving but dashboard alerts are

- `DISCORD_WEBHOOK_URL` empty/unset intentionally disables Discord delivery
  only -- alerts still land in the dashboard's Alerts page. That's expected,
  not a bug, if you haven't set a webhook.
- If it is set: test the webhook directly --
  `curl -X POST -H 'Content-Type: application/json' -d '{"content":"test"}' "$DISCORD_WEBHOOK_URL"`.
  If that fails, the URL itself is wrong or the webhook was deleted.

### Forgot the dashboard password

Re-run the admin script -- it updates the password for an existing username
rather than failing:

```bash
cd /opt/network-monitor
sudo -u netmon bash -c 'set -a && source .env && set +a && NETMON_CONFIG=/opt/network-monitor/config.yml venv/bin/python scripts/create_admin.py'
```

### Starting over

```bash
sudo systemctl stop network-monitor
rm /opt/network-monitor/data/netmon.db
cd /opt/network-monitor && sudo -u netmon venv/bin/alembic upgrade head
sudo systemctl start network-monitor
```

---

## License

MIT -- see [LICENSE](LICENSE).
