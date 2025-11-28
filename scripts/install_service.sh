#!/usr/bin/env bash

set -euo pipefail

APP_USER="${APP_USER:-${SUDO_USER:-$USER}}"
APP_GROUP="${APP_GROUP:-$APP_USER}"
APP_PORT="${APP_PORT:-5000}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-4}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVICE_NAME="${SERVICE_NAME:-exam}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_HOME="${APP_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$APP_HOME/.venv}"
ENV_FILE="${ENV_FILE:-/etc/${SERVICE_NAME}.env}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (e.g. sudo) to install the service." >&2
  exit 1
fi

if ! id "$APP_USER" >/dev/null 2>&1; then
  echo "User $APP_USER does not exist. Create it or set APP_USER before rerunning." >&2
  exit 1
fi

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python interpreter not found: $PYTHON_BIN" >&2
  exit 1
fi

mkdir -p "$APP_HOME" "$(dirname "$SERVICE_PATH")"

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$APP_HOME/requirements.txt" gunicorn

if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
# Environment for ${SERVICE_NAME}
# SECRET_KEY=change-me
# FLASK_ENV=production
PORT=${APP_PORT}
EOF
  chown "$APP_USER":"$APP_GROUP" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
else
  if grep -q '^PORT=' "$ENV_FILE"; then
    sed -i "s/^PORT=.*/PORT=${APP_PORT}/" "$ENV_FILE"
  else
    echo "PORT=${APP_PORT}" >> "$ENV_FILE"
  fi
fi

cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Exam Flask application
After=network.target

[Service]
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_HOME}
Environment=PORT=${APP_PORT}
EnvironmentFile=-${ENV_FILE}
ExecStart=${VENV_DIR}/bin/gunicorn --workers=${GUNICORN_WORKERS} --bind 0.0.0.0:\${PORT} app:app
Restart=on-failure
RestartSec=5
TimeoutStartSec=30

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service"

echo "Service ${SERVICE_NAME}.service installed and started."
echo "Check status with: systemctl status ${SERVICE_NAME}.service"
