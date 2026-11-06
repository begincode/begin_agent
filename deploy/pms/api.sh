#!/bin/sh
# PMS_PYTHON_PROTOCOL=1
set -eu
: "${PMS_PYTHON:?PMS must provide its managed Python interpreter}"
: "${PMS_PORT:?PMS must allocate an API port}"
cd "$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)"
exec "$PMS_PYTHON" -m uvicorn app.api.main:app --host "${PMS_LISTEN_HOST:-127.0.0.1}" --port "$PMS_PORT" --root-path "${PMS_ROOT_PATH:-}" --no-access-log
