import json
import os
import time
from pathlib import Path


def directory(settings):
    return Path(settings.data_dir or ".data") / "workers"


def report(settings, worker_id):
    root = directory(settings)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{os.getpid()}.json"
    temp = root / f"{os.getpid()}.tmp"
    temp.write_text(
        json.dumps({"pid": os.getpid(), "worker_id": worker_id, "heartbeat": time.time()})
    )
    temp.replace(target)


def ready(settings):
    for path in directory(settings).glob("*.json"):
        try:
            data = json.loads(path.read_text())
            if not 0 <= time.time() - data["heartbeat"] <= max(5, settings.worker_lease_seconds):
                continue
            os.kill(data["pid"], 0)
            return True
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return False


def main():
    from app.core.config import get_settings

    available = ready(get_settings())
    print("worker ready" if available else "worker not ready")
    raise SystemExit(0 if available else 1)


if __name__ == "__main__":
    main()
