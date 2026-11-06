"""Private local-storage backup with manifest verification and isolated restore rehearsal."""

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import asyncpg
from sqlalchemy.engine import make_url

from app.core.config import get_settings


class BackupError(ValueError):
    pass


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_bundle(root):
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise BackupError("备份清单路径无效")
        path = root / relative
        if any(p.is_symlink() for p in [path, *path.parents]) or not path.is_file():
            raise BackupError("备份包含链接或缺失文件")
        if digest(path) != expected:
            raise BackupError("备份完整性校验失败")
    if not {"database.dump", "credential.key"}.issubset(manifest["files"]):
        raise BackupError("备份缺少数据库或凭据主密钥")
    return manifest


def restore_name(value):
    if not re.fullmatch(r"begin_agent_restore_[a-z0-9_]{1,40}", value):
        raise BackupError("恢复目标只能是全新 begin_agent_restore_ 前缀数据库")
    return value


def pg_environment(url, database=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PG")}
    env["PGDATABASE"] = database or url.database
    env["PGPORT"] = str(url.port or 5432)
    if "sslmode" in url.query:
        env["PGSSLMODE"] = url.query["sslmode"]
    for key, value in [
        ("PGHOST", url.host),
        ("PGPORT", url.port),
        ("PGUSER", url.username),
        ("PGPASSWORD", url.password),
    ]:
        if value is not None:
            env[key] = str(value)
    return env


async def snapshot(url):
    conn = await asyncpg.connect(
        url.set(drivername="postgresql").render_as_string(hide_password=False)
    )
    try:
        privileged = await conn.fetchval(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        if not privileged:
            raise BackupError("全库备份核对需要受控的迁移/备份角色，不能使用租户 API 角色")
        remote = await conn.fetchval(
            "SELECT count(*) FROM task_artifact WHERE artifact_type='FILE' "
            "AND content->'storage'->>'backend' IS DISTINCT FROM 'local'"
        )
        if remote:
            raise BackupError("存在非本地文件引用，此备份不能覆盖所有对象")
        tables = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
        )
        hashes = {}
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            for table in tables:
                name = table["tablename"].replace('"', '""')
                h, count = hashlib.sha256(), 0
                query = (
                    'SELECT row_to_json(t)::text AS data FROM public."'
                    + name
                    + '" t ORDER BY row_to_json(t)::text'
                )
                async for row in conn.cursor(query):
                    raw = row["data"].encode()
                    h.update(len(raw).to_bytes(8, "big"))
                    h.update(raw)
                    count += 1
                hashes[table["tablename"]] = {"rows": count, "sha256": h.hexdigest()}
        return hashes
    finally:
        await conn.close()


def copy_private(source, target):
    if source.is_symlink() or not source.is_file():
        raise BackupError("备份源必须是普通文件")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(source, target)
    target.chmod(0o600)


async def backup(args, settings):
    if not args.writers_stopped:
        raise BackupError("请先停止 API/Worker 写入，再使用 --writers-stopped")
    if settings.artifact_storage_backend != "local":
        raise BackupError("此备份流程仅覆盖私有本地对象；S3 须使用桶备份流程")
    root = Path(args.directory).absolute()
    if root.exists():
        raise BackupError("备份目录必须全新，拒绝覆盖")
    source = make_url(os.environ.get(args.database_env) or settings.database_url)
    before = await snapshot(source)
    root.mkdir(parents=True, mode=0o700)
    dump = root / "database.dump"
    dump.touch(mode=0o600)
    subprocess.run(
        [
            str(Path(args.pg_bin) / "pg_dump"),
            "-Fc",
            "--no-owner",
            "--no-acl",
            "--schema=public",
            "--file",
            str(dump),
        ],
        env=pg_environment(source),
        check=True,
        capture_output=True,
    )
    if settings.credential_encryption_key:
        key = settings.credential_encryption_key.get_secret_value().encode()
        from cryptography.fernet import Fernet

        Fernet(key)
        target_key = root / "credential.key"
        target_key.touch(mode=0o600)
        target_key.write_bytes(key)
    else:
        copy_private(Path(settings.credential_key_file), root / "credential.key")
    objects = Path(settings.artifact_local_dir or Path(settings.data_dir or ".data") / "objects")
    if objects.is_symlink():
        raise BackupError("对象根目录不能为符号链接")
    for path in sorted(objects.rglob("*")):
        if path.is_symlink():
            raise BackupError("对象目录包含符号链接")
        if path.is_file():
            copy_private(path, root / "objects" / path.relative_to(objects))
    after = await snapshot(source)
    if before != after:
        raise BackupError("备份期间数据库发生写入，备份未标记有效，请停写后重试")
    manifest = {
        "version": 1,
        "tables": before,
        "files": {
            str(p.relative_to(root)): digest(p) for p in sorted(root.rglob("*")) if p.is_file()
        },
        "storage": "local",
        "writers_stopped_asserted": True,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (root / "manifest.json").chmod(0o600)
    verify_bundle(root)
    return {
        "status": "verified",
        "tables": len(before),
        "files": len(manifest["files"]),
        "directory": str(root),
        "contains_credential_key": True,
    }


async def restore(args, settings):
    root = Path(args.directory).absolute()
    manifest = verify_bundle(root)
    name = restore_name(args.database)
    source = make_url(os.environ.get(args.database_env) or settings.database_url)
    if source.host not in {None, "localhost", "127.0.0.1", "::1"}:
        raise BackupError("恢复演练只允许本机数据库")
    target = source.set(database=name)
    data = Path(args.data_directory).absolute()
    if data.exists():
        raise BackupError("恢复数据目录必须全新，拒绝覆盖")
    conn = await asyncpg.connect(
        source.set(drivername="postgresql", database="postgres").render_as_string(
            hide_password=False
        )
    )
    try:
        if await conn.fetchval("SELECT 1 FROM pg_database WHERE datname=$1", name):
            raise BackupError("恢复数据库已存在，拒绝覆盖")
        await conn.execute('CREATE DATABASE "' + name + '"')
    finally:
        await conn.close()
    subprocess.run(
        [
            str(Path(args.pg_bin) / "pg_restore"),
            "--exit-on-error",
            "--clean",
            "--if-exists",
            "--single-transaction",
            "--no-owner",
            "--no-acl",
            "--dbname",
            name,
            str(root / "database.dump"),
        ],
        env=pg_environment(target),
        check=True,
        capture_output=True,
    )
    actual = await snapshot(target)
    if actual != manifest["tables"]:
        raise BackupError("恢复数据库与备份清单不一致")
    data.mkdir(parents=True, mode=0o700)
    copy_private(root / "credential.key", data / "model-credential.key")
    for relative in manifest["files"]:
        if relative.startswith("objects/"):
            copy_private(root / relative, data / relative)
    return {
        "status": "restored_and_verified",
        "tables": len(actual),
        "database": name,
        "data_directory": str(data),
        "credential_key_equal": digest(data / "model-credential.key")
        == digest(root / "credential.key"),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "verify", "restore"])
    parser.add_argument("directory")
    parser.add_argument("--writers-stopped", action="store_true")
    parser.add_argument("--database-env", default="AGENT_BACKUP_DATABASE_URL")
    parser.add_argument("--pg-bin", default="/opt/homebrew/opt/postgresql@17/bin")
    parser.add_argument("--database")
    parser.add_argument("--data-directory")
    args = parser.parse_args()
    if args.action == "restore" and (not args.database or not args.data_directory):
        parser.error("restore 需要 --database 与 --data-directory")
    try:
        if args.action == "verify":
            manifest = verify_bundle(args.directory)
            result = {"status": "verified", "files": len(manifest["files"])}
        else:
            result = asyncio.run(
                (backup if args.action == "backup" else restore)(args, get_settings())
            )
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        # Never print subprocess stderr, database DSNs or credentials.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "error": str(exc) if isinstance(exc, BackupError) else type(exc).__name__,
                },
                ensure_ascii=False,
            )
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
