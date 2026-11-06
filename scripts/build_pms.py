"""Build the unique PMS FastAPI source payload; no deployment paths or secrets included."""

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist/pms-payload"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline-deps", action="store_true", help="构建同平台离线 wheelhouse")
    args = parser.parse_args()
    subprocess.run(
        ["npm", "--prefix", "web", "ci", "--no-audit", "--no-fund"], cwd=ROOT, check=True
    )
    subprocess.run(["npm", "--prefix", "web", "run", "check"], cwd=ROOT, check=True)
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir(parents=True)
    for name in ("app", "migrations", "scripts", "deploy"):
        shutil.copytree(
            ROOT / name, OUTPUT / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )
    for name in (
        "requirements.txt",
        "pyproject.toml",
        "README.md",
        "alembic.ini",
        ".python-version",
    ):
        shutil.copy2(ROOT / name, OUTPUT / name)
    if args.offline_deps:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "wheel",
                "--only-binary=:all:",
                "--wheel-dir",
                str(OUTPUT / ".pms-wheelhouse"),
                "-r",
                str(ROOT / "requirements.txt"),
            ],
            check=True,
        )
    from app.db.schema import SCHEMA_REVISION

    (OUTPUT / "build-runtime.json").write_text(
        json.dumps(
            {
                "python": platform.python_version(),
                "system": platform.system(),
                "machine": platform.machine(),
                "schema_revision": SCHEMA_REVISION,
                "requirements_sha256": hashlib.sha256(
                    (ROOT / "requirements.txt").read_bytes()
                ).hexdigest(),
                "offline_dependencies": args.offline_deps,
            },
            indent=2,
        )
    )
    print("PMS artifact: dist/pms-payload")


if __name__ == "__main__":
    main()
