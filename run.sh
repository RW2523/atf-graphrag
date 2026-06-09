#!/usr/bin/env bash
# Convenience launcher. Loads .env if present, then starts the API server.
set -e
cd "$(dirname "$0")"
if [ -f .env ]; then
  set -a; . ./.env; set +a
fi
echo "Starting ATF GraphRAG (profile=${ATF_PROFILE:-local}) ..."
python3 -m atf_graphrag serve
