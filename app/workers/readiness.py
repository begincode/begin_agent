import json
import math
import os
import socket
import time
from itertools import islice
from pathlib import Path


def directory(settings):
    return Path(settings.data_dir or ".data") / "workers"


def report(settings, worker_id):
    root = directory(settings)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{os.getpid()}.json"
    temp = root / f"{os.getpid()}.tmp"
    temp.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "worker_id": worker_id,
                "hostname": socket.gethostname(),
                "heartbeat": time.time(),
            }
        )
    )
    temp.replace(target)


def inspect(settings):
    """Local PID checks cannot establish the health of workers on another host."""
    now = time.time()
    threshold = max(5, settings.worker_lease_seconds)
    result = dict(
        active=0,
        stale=0,
        invalid=0,
        foreign=0,
        unobservable=0,
        truncated=False,
        threshold_seconds=threshold,
        scope="same_host_shared_directory",
        newest_age_seconds=None,
    )
    try:
        paths = list(islice(directory(settings).glob("*.json"), 257))
    except OSError:
        result["unobservable"] = 1
        paths = []
    result["truncated"] = len(paths) > 256
    for path in paths[:256]:
        try:
            if path.is_symlink() or path.stat().st_size > 16384:
                raise ValueError("invalid heartbeat file")
            data = json.loads(path.read_text())
            pid, heartbeat = data["pid"], data["heartbeat"]
            if (
                type(pid) is not int
                or not 0 < pid < 2**31
                or path.stem != str(pid)
                or type(heartbeat) not in (int, float)
                or not math.isfinite(heartbeat)
            ):
                raise ValueError("invalid heartbeat fields")
            # Compatibility with heartbeats written before hostname was explicit.
            host = data.get("hostname") or data.get("worker_id", "").rsplit(":", 2)[0]
            if host != socket.gethostname():
                result["foreign"] += 1
                continue
            age = now - heartbeat
            if age < 0:
                raise ValueError("future heartbeat")
            if age > threshold:
                result["stale"] += 1
                continue
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                result["stale"] += 1
                continue
            except PermissionError:
                result["unobservable"] += 1
                continue
            result["active"] += 1
            current = result["newest_age_seconds"]
            result["newest_age_seconds"] = round(age if current is None else min(current, age), 1)
        except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError):
            result["invalid"] += 1
    result["ready"] = result["active"] > 0
    return result


def ready(settings):
    return inspect(settings)["ready"]


def main():
    from app.core.config import get_settings

    available = ready(get_settings())
    print("worker ready" if available else "worker not ready")
    raise SystemExit(0 if available else 1)


if __name__ == "__main__":
    main()
