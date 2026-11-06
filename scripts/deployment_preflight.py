"""Read-only deployment checks; never call model providers, MCP services or cloud buckets."""

import argparse
import asyncio
import json
import platform
import stat
import sys
from pathlib import Path

from cryptography.fernet import Fernet
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.db.schema import SCHEMA_REVISION


def runtime_checks(python_version, system, local_rehearsal=False):
    return [
        {"check": "python_3_12", "passed": tuple(python_version[:2]) == (3, 12)},
        {"check": "linux_host", "passed": system == "Linux", "required": not local_rehearsal},
    ]


async def database_checks(url, kind):
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            role = (
                await conn.execute(
                    text("SELECT rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user")
                )
            ).one()
            version = int(await conn.scalar(text("SELECT current_setting('server_version_num')")))
            revision = await conn.scalar(text("SELECT version_num FROM alembic_version"))
            role_ok = not role.rolsuper and (
                role.rolbypassrls if kind == "worker" else not role.rolbypassrls
            )
            needed = {
                "worker": ["worker_pulse", "agent_task", "task_artifact"],
                "api": ["agent_creation_receipt", "diagnostic_acknowledgment", "worker_pulse"],
            }[kind]
            readable = all(
                [
                    await conn.scalar(
                        text("SELECT has_table_privilege(current_user,:table,'SELECT')"),
                        {"table": t},
                    )
                    for t in needed
                ]
            )
        return [
            {"check": kind + "_role", "passed": role_ok},
            {"check": kind + "_postgresql_16", "passed": 160000 <= version < 170000},
            {"check": kind + "_migration", "passed": revision == SCHEMA_REVISION},
            {"check": kind + "_tables_readable", "passed": readable},
        ]
    except Exception:
        return [{"check": kind + "_database", "passed": False}]
    finally:
        await engine.dispose()


async def inspect(settings, local_rehearsal=False):
    checks = runtime_checks(sys.version_info, platform.system(), local_rehearsal)
    checks.extend(await database_checks(settings.database_url, "api"))
    if settings.worker_database_url and settings.worker_database_url != settings.database_url:
        checks.extend(await database_checks(settings.worker_database_url, "worker"))
    else:
        checks.append({"check": "separate_worker_database_role", "passed": False})
    try:
        if settings.credential_encryption_key:
            Fernet(settings.credential_encryption_key.get_secret_value().encode())
        else:
            path = Path(settings.credential_key_file)
            if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) & 0o077:
                raise ValueError()
            Fernet(path.read_bytes())
        key_ok = True
    except Exception:
        key_ok = False
    checks.append({"check": "persistent_credential_key", "passed": key_ok})
    checks.append({"check": "password_accounts", "passed": settings.auth_mode == "password"})
    for purpose, value in [("data", settings.data_dir), ("logs", settings.log_dir)]:
        path = Path(value) if value else None
        checks.append(
            {
                "check": purpose + "_directory",
                "passed": bool(path and path.is_absolute() and path.is_dir()),
            }
        )
    if settings.artifact_storage_backend == "s3":
        checks.append({"check": "real_cloud_bucket", "passed": False, "required": False})
    required = [c for c in checks if c.get("required", True)]
    return {
        "status": "ready_for_local_rehearsal"
        if all(c["passed"] for c in required) and local_rehearsal
        else "ready"
        if all(c["passed"] for c in required)
        else "not_ready",
        "checks": checks,
        "schema_revision": SCHEMA_REVISION,
        "read_only": True,
        "external_calls": 0,
        "production_linux_verified": platform.system() == "Linux"
        and all(c["passed"] for c in required),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-rehearsal", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(inspect(get_settings(), args.local_rehearsal))
    except Exception:
        result = {"status": "not_ready", "error": "configuration_invalid", "read_only": True}
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1 if result["status"] == "not_ready" else 0)


if __name__ == "__main__":
    main()
