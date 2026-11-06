#!/bin/sh
# PMS_PYTHON_PROTOCOL=1
set -eu
: "${PMS_PYTHON:?PMS must provide its managed Python interpreter}"
cd "$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
case "${1:-run}" in
  run) exec "$PMS_PYTHON" -m app.workers.agent_worker ;;
  check) exec "$PMS_PYTHON" -m app.workers.readiness ;;
  *) exit 64 ;;
esac
