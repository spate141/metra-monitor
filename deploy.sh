#!/usr/bin/env bash
# Deploy metra-agent on the VM: git pull, uv sync, reinstall the systemd unit
# only if it changed, restart the service, health-check. Run from the repo root
# on the VM (~/apps/metra-agent).
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "ERROR: .env not found. Copy .env.example -> .env and fill in secrets first." >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "ERROR: uv not found on PATH. Install it: curl -LsSf https://astral.sh/uv/install.sh | sh" >&2
  exit 1
fi

BEFORE=$(git rev-parse HEAD)
git pull --ff-only
AFTER=$(git rev-parse HEAD)

uv sync

if [ "$BEFORE" != "$AFTER" ] && git diff --name-only "$BEFORE" "$AFTER" | grep -q '^systemd/metra-agent.service$'; then
  echo "systemd unit changed -- reinstalling"
  sed -i "s/YOUR_VM_USER/$USER/g" systemd/metra-agent.service
  sudo cp systemd/metra-agent.service /etc/systemd/system/
  sudo systemctl daemon-reload
fi

sudo systemctl restart metra-agent
sleep 2
curl -sf localhost:8010/health && echo || { echo "health check failed -- check: sudo journalctl -u metra-agent -n 50"; exit 1; }
