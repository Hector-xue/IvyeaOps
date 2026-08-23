#!/usr/bin/env bash
# Render deploy/*.template into deploy/dist/ using values from
# deploy/install.conf. Idempotent — re-run after editing install.conf.
#
# Templates use ${VAR} placeholders (envsubst). Only the variables listed
# in EXPECTED below are substituted; literal '$host' etc. in the nginx
# config are passed through untouched.
set -euo pipefail

cd "$(dirname "$0")/.."
CONF=deploy/install.conf
DIST=deploy/dist

if [ ! -f "$CONF" ]; then
  echo "error: $CONF not found." >&2
  echo "       cp deploy/install.conf.example $CONF, edit it, then re-run." >&2
  exit 1
fi

# shellcheck disable=SC1090
set -a; . "$CONF"; set +a

EXPECTED=(SERVER_NAME INSTALL_DIR IVYEA_OPS_USER IVYEA_OPS_PORT PYTHON_BIN
          CPU_ALERT_LOG CLAUDECODEUI_PORT SKILL_STUDIO_DIST)
for v in "${EXPECTED[@]}"; do
  if [ -z "${!v:-}" ]; then
    echo "error: $v is empty in $CONF" >&2
    exit 1
  fi
done

if ! command -v envsubst >/dev/null 2>&1; then
  echo "error: envsubst not installed. Install with: dnf install gettext  (or apt install gettext-base)" >&2
  exit 1
fi

# Build the variable list once so envsubst only touches our placeholders
# (NOT nginx's $host, $remote_addr, etc.)
VARS=$(printf '${%s} ' "${EXPECTED[@]}")

mkdir -p "$DIST/nginx" "$DIST/systemd" "$DIST/cron.d"

render() {
  local src=$1 dst=$2
  envsubst "$VARS" < "$src" > "$dst"
  echo "  rendered: $dst"
}

render deploy/nginx/ivyea-ops.conf.template      "$DIST/nginx/ivyea-ops.conf"
render deploy/systemd/ivyea-ops.service.template "$DIST/systemd/ivyea-ops.service"
render deploy/cron.d/ivyea-ops-cpu-alert.template "$DIST/cron.d/ivyea-ops-cpu-alert"

cat <<EOF

Done. Next steps:

  # 1. nginx (after certbot has issued the cert)
  sudo cp $DIST/nginx/ivyea-ops.conf /etc/nginx/conf.d/
  sudo nginx -t && sudo systemctl reload nginx

  # 2. systemd
  sudo cp $DIST/systemd/ivyea-ops.service /etc/systemd/system/
  sudo systemctl daemon-reload
  sudo systemctl enable --now ivyea-ops.service

  # 3. CPU alert cron
  sudo mkdir -p \$(dirname $CPU_ALERT_LOG)
  sudo cp $DIST/cron.d/ivyea-ops-cpu-alert /etc/cron.d/

EOF
