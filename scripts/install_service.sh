#!/usr/bin/env bash

set -euo pipefail

APP_USER="${APP_USER:-${SUDO_USER:-$USER}}"
APP_GROUP="${APP_GROUP:-$APP_USER}"
APP_PORT="${APP_PORT:-8443}"
HTTP_REDIRECT_PORT="${HTTP_REDIRECT_PORT:-5000}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-4}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVICE_NAME="${SERVICE_NAME:-exam}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_HOME="${APP_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}"
VENV_DIR="${VENV_DIR:-$APP_HOME/.venv}"
ENV_FILE="${ENV_FILE:-/etc/${SERVICE_NAME}.env}"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}.service"
INSTANCE_DIR="${INSTANCE_DIR:-$APP_HOME/instance}"
UPLOAD_DIR="${UPLOAD_DIR:-$APP_HOME/static/uploads}"
BACKUP_DIR="${BACKUP_DIR:-$INSTANCE_DIR/backups}"
CERT_DIR="${CERT_DIR:-$APP_HOME/certs}"
CERT_FILE="${CERT_FILE:-$CERT_DIR/selfsigned.crt}"
KEY_FILE="${KEY_FILE:-$CERT_DIR/selfsigned.key}"
TLS_DAYS="${TLS_DAYS:-365}"

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
mkdir -p "$INSTANCE_DIR" "$UPLOAD_DIR" "$BACKUP_DIR" "$CERT_DIR"

if [[ ! -f "$CERT_FILE" || ! -f "$KEY_FILE" ]]; then
  openssl req -x509 -nodes -newkey rsa:2048 -keyout "$KEY_FILE" -out "$CERT_FILE" -days "$TLS_DAYS" -subj "/CN=${SERVICE_NAME}"
  chmod 640 "$CERT_FILE" "$KEY_FILE"
  chown "$APP_USER":"$APP_GROUP" "$CERT_FILE" "$KEY_FILE"
fi

"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$APP_HOME/requirements.txt" gunicorn

if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
# Environment for ${SERVICE_NAME}
# SECRET_KEY=change-me
# FLASK_ENV=production
PORT=${APP_PORT}
UPLOAD_FOLDER=${UPLOAD_DIR}
BACKUP_FOLDER=${BACKUP_DIR}
# DATABASE_URL=sqlite:///${INSTANCE_DIR}/exam_app.db
CERT_FILE=${CERT_FILE}
KEY_FILE=${KEY_FILE}
EOF
  chown "$APP_USER":"$APP_GROUP" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
else
  if grep -q '^PORT=' "$ENV_FILE"; then
    sed -i "s/^PORT=.*/PORT=${APP_PORT}/" "$ENV_FILE"
  else
    echo "PORT=${APP_PORT}" >> "$ENV_FILE"
  fi
  if ! grep -q '^UPLOAD_FOLDER=' "$ENV_FILE"; then
    echo "UPLOAD_FOLDER=${UPLOAD_DIR}" >> "$ENV_FILE"
  fi
  if ! grep -q '^BACKUP_FOLDER=' "$ENV_FILE"; then
    echo "BACKUP_FOLDER=${BACKUP_DIR}" >> "$ENV_FILE"
  fi
  if ! grep -q '^CERT_FILE=' "$ENV_FILE"; then
    echo "CERT_FILE=${CERT_FILE}" >> "$ENV_FILE"
  fi
  if ! grep -q '^KEY_FILE=' "$ENV_FILE"; then
    echo "KEY_FILE=${KEY_FILE}" >> "$ENV_FILE"
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
ExecStart=${VENV_DIR}/bin/gunicorn --workers=${GUNICORN_WORKERS} --bind 0.0.0.0:\${PORT} --certfile=${CERT_FILE} --keyfile=${KEY_FILE} wsgi:app
Restart=on-failure
RestartSec=5
TimeoutStartSec=30

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"
systemctl restart "${SERVICE_NAME}.service"

# Redirect HTTP port to HTTPS port
if command -v iptables >/dev/null 2>&1; then
  if ! iptables -t nat -C PREROUTING -p tcp --dport "${HTTP_REDIRECT_PORT}" -j REDIRECT --to-ports "${APP_PORT}" 2>/dev/null; then
    iptables -t nat -A PREROUTING -p tcp --dport "${HTTP_REDIRECT_PORT}" -j REDIRECT --to-ports "${APP_PORT}"
  fi
  if ! iptables -t nat -C OUTPUT -p tcp --dport "${HTTP_REDIRECT_PORT}" -j REDIRECT --to-ports "${APP_PORT}" 2>/dev/null; then
    iptables -t nat -A OUTPUT -p tcp --dport "${HTTP_REDIRECT_PORT}" -j REDIRECT --to-ports "${APP_PORT}"
  fi
fi

echo "Service ${SERVICE_NAME}.service installed and started."
echo "Check status with: systemctl status ${SERVICE_NAME}.service"
