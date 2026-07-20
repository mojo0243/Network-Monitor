#!/usr/bin/env bash
# One-command installer for the Raspberry Pi (task 12.5). Run this from
# inside the cloned repo:
#
#   cd network-monitor
#   sudo bash deploy/install.sh
#
# What it does, in order: creates a dedicated system user, installs the app
# to /opt/network-monitor, sets up a Python venv, runs the DB migrations,
# and installs (but does not blindly enable) the systemd unit. It does NOT
# install Caddy or touch your firewall -- see README.md's Setup guide for
# those steps, which are worth doing deliberately rather than from a script.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run this with sudo: sudo bash deploy/install.sh" >&2
  exit 1
fi

INSTALL_DIR="/opt/network-monitor"
SERVICE_USER="netmon"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "==> Installing system packages (python3-venv)"
apt-get update -qq
apt-get install -y python3-venv python3-pip >/dev/null

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "==> Creating system user '$SERVICE_USER'"
  useradd --system --create-home --shell /usr/sbin/nologin "$SERVICE_USER"
fi

echo "==> Copying application to $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
rsync -a --delete \
  --exclude ".git" \
  --exclude "venv" \
  --exclude "data" \
  --exclude "config.yml" \
  --exclude ".env" \
  "$REPO_DIR"/ "$INSTALL_DIR"/

if [[ ! -f "$INSTALL_DIR/config.yml" ]]; then
  echo "==> No config.yml found, copying config.example.yml -- EDIT THIS before starting the service"
  cp "$INSTALL_DIR/config.example.yml" "$INSTALL_DIR/config.yml"
fi

if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  echo "==> Generating $INSTALL_DIR/.env with a fresh session secret"
  SESSION_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
  cat > "$INSTALL_DIR/.env" <<EOF
UNIFI_PASSWORD=changeme
DISCORD_WEBHOOK_URL=
SESSION_SECRET=${SESSION_SECRET}
EOF
  echo "    -> Edit $INSTALL_DIR/.env and set UNIFI_PASSWORD and DISCORD_WEBHOOK_URL"
fi

echo "==> Creating Python venv"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"

mkdir -p "$INSTALL_DIR/data"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"

echo "==> Running database migrations"
sudo -u "$SERVICE_USER" bash -c "cd '$INSTALL_DIR' && set -a && source .env && set +a && NETMON_CONFIG='$INSTALL_DIR/config.yml' venv/bin/alembic upgrade head"

echo "==> Installing systemd unit"
cp "$INSTALL_DIR/deploy/network-monitor.service" /etc/systemd/system/network-monitor.service
systemctl daemon-reload

cat <<EOF

Install complete. Before starting the service:

  1. Edit $INSTALL_DIR/config.yml (networks, unifi host/username, website_monitors)
  2. Edit $INSTALL_DIR/.env (UNIFI_PASSWORD, DISCORD_WEBHOOK_URL)
  3. Create your dashboard login:
       cd $INSTALL_DIR && sudo -u $SERVICE_USER bash -c 'set -a && source .env && set +a && NETMON_CONFIG=$INSTALL_DIR/config.yml venv/bin/python scripts/create_admin.py'
  4. Start it:
       sudo systemctl enable --now network-monitor
  5. Set up Caddy as the LAN-facing reverse proxy -- see README.md's Setup guide

EOF
