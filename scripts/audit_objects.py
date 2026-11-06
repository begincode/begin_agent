"""Read-only local object/reference inventory. Never deletes or moves objects."""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.models.platform import TaskArtifact
from app.services.object_storage import LocalObjects, StorageError


def inventory(root, limit=100000):
    files, invalid, truncated = set(), [], False
    if root.is_symlink():
        raise ValueError("对象目录不能为符号链接")
    for index, path in enumerate(root.rglob("*")):
        if index >= limit:
            truncated = True
            break
        if path.is_symlink():
            invalid.append(str(path.relative_to(root)))
            continue
        if path.is_file():
            files.add(str(path.relative_to(root)))
    return files, invalid[:100], truncated


async def audit(settings, verify_content=False, limit=100000):
    url = os.environ.get("AGENT_BACKUP_DATABASE_URL") or settings.database_url
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            privileged = await conn.scalar(
                text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname=current_user")
            )
            if not privileged:
                raise ValueError("完整对象清查需要受控备份角色")
            rows = (
                await conn.execute(
                    select(TaskArtifact.artifact_code, TaskArtifact.content)
                    .where(TaskArtifact.artifact_type == "FILE")
                    .limit(limit + 1)
                )
            ).all()
        store = LocalObjects(settings)
        actual, invalid, inventory_truncated = await asyncio.to_thread(inventory, store.root, limit)
        references, missing, corrupt, unverified = set(), [], [], []
        for code, content in rows[:limit]:
            ref = content.get("storage", {})
            if ref.get("backend") != "local":
                unverified.append(code)
                continue
            try:
                path = store.path(ref["key"])
                references.add(ref["key"])
                if not path.is_file():
                    missing.append(code)
                elif verify_content:
                    expected_size = content["size_bytes"]
                    if (
                        not isinstance(expected_size, int)
                        or not 0 <= expected_size <= 100 * 1024 * 1024
                    ):
                        corrupt.append(code)
                        continue

                    def check():
                        if path.stat().st_size != expected_size:
                            return False
                        h = hashlib.sha256()
                        with path.open("rb") as source:
                            while chunk := source.read(65536):
                                h.update(chunk)
                        return h.hexdigest() == content["sha256"]

                    if not await asyncio.to_thread(check):
                        corrupt.append(code)
            except (StorageError, KeyError, OSError, TypeError):
                corrupt.append(code)
        truncated = inventory_truncated or len(rows) > limit
        complete = not truncated and not unverified and not invalid
        # When references are truncated, no file can safely be classified as unreferenced.
        unreferenced = sorted(actual - references) if len(rows) <= limit else []
        return {
            "read_only": True,
            "complete": complete,
            "references": len(references),
            "objects": len(actual),
            "missing": missing[:100],
            "corrupt": corrupt[:100],
            "invalid_paths": invalid,
            "remote_unverified": len(unverified),
            "truncated": truncated,
            "unreferenced_count": len(unreferenced),
            "unreferenced_sample": unreferenced[:100],
            "content_verified": verify_content,
            "deletions": 0,
        }
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-content", action="store_true")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        result = asyncio.run(audit(get_settings(), args.verify_content))
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(
            json.dumps(
                {k: v for k, v in result.items() if not isinstance(v, list)}, ensure_ascii=False
            )
        )
        raise SystemExit(
            0 if result["complete"] and not result["missing"] and not result["corrupt"] else 2
        )
    except Exception:
        print('{"status":"failed","error":"对象核对未完成，请检查受控配置与权限"}')
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
