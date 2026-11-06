"""Private immutable objects. Only server-generated entity keys enter this boundary."""

import hashlib
import os
import re
import tempfile
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

KEY = re.compile(r"tenant/ten_[0-9A-Z]{26}/task/tsk_[0-9A-Z]{26}/art_[0-9A-Z]{26}")
CHUNK = 64 * 1024


class StorageError(Exception):
    """Messages intentionally contain no endpoint, credential or SDK response."""


class ObjectMissing(StorageError):
    pass


def validate_key(key):
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise StorageError("文件对象标识无效")


def verified_copy(source, target, size, digest):
    actual = hashlib.sha256()
    count = 0
    while chunk := source.read(CHUNK):
        count += len(chunk)
        if count > size:
            raise StorageError("文件完整性校验失败")
        actual.update(chunk)
        target.write(chunk)
    if count != size or actual.hexdigest() != digest:
        raise StorageError("文件完整性校验失败")
    target.seek(0)


class LocalObjects:
    backend = "local"

    def __init__(self, settings):
        self.root = Path(
            settings.artifact_local_dir or (Path(settings.data_dir or ".data") / "objects")
        ).absolute()

    def path(self, key):
        validate_key(key)
        path = self.root / key
        # Reject linked components, including a linked storage root, before any I/O.
        for part in (self.root, *path.relative_to(self.root).parents):
            candidate = part if part.is_absolute() else self.root / part
            if candidate.is_symlink():
                raise StorageError("文件对象路径不可用")
        if path.is_symlink() or not path.resolve().is_relative_to(self.root.resolve()):
            raise StorageError("文件对象路径不可用")
        return path

    def put(self, key, source, size, digest):
        path = self.path(key)
        temporary = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as target:
                temporary = target.name
                verified_copy(source, target, size, digest)
                target.flush()
                os.fsync(target.fileno())
            os.link(temporary, path)  # Atomic publish without replacing an existing object.
        except OSError:
            raise StorageError("文件存储暂不可用") from None
        finally:
            if temporary:
                Path(temporary).unlink(missing_ok=True)
        return {"backend": self.backend, "key": key}

    def read(self, reference, target, size, digest):
        try:
            with self.path(reference["key"]).open("rb") as source:
                verified_copy(source, target, size, digest)
        except FileNotFoundError:
            raise ObjectMissing("文件对象不存在，请联系管理员") from None
        except OSError:
            raise StorageError("文件存储暂不可用") from None

    def delete(self, reference):
        self.path(reference["key"]).unlink(missing_ok=True)

    def close(self):
        pass


class S3Objects:
    backend = "s3"

    def __init__(self, settings, client=None):
        self.bucket = settings.artifact_s3_bucket
        if not self.bucket:
            raise StorageError("S3 文件存储未配置")
        credentials = {}
        for field, name in (
            ("access_key", "aws_access_key_id"),
            ("secret_key", "aws_secret_access_key"),
            ("session_token", "aws_session_token"),
        ):
            value = getattr(settings, "artifact_s3_" + field)
            if value:
                credentials[name] = value.get_secret_value()
        self.client = client or boto3.client(
            "s3",
            endpoint_url=settings.artifact_s3_endpoint,
            region_name=settings.artifact_s3_region,
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=30,
                retries={"total_max_attempts": 1},
                s3={"addressing_style": "path"},
            ),
            **credentials,
        )

    def put(self, key, source, size, digest):
        validate_key(key)
        try:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=source,
                ContentLength=size,
                ContentType="application/octet-stream",
                IfNoneMatch="*",
                Metadata={"sha256": digest},
            )
        except (BotoCoreError, ClientError):
            raise StorageError("S3 文件存储暂不可用") from None
        return {"backend": self.backend, "bucket": self.bucket, "key": key}

    def check(self, reference):
        validate_key(reference["key"])
        if reference.get("bucket") != self.bucket:
            raise StorageError("历史文件存储配置已变更，请联系管理员")

    def read(self, reference, target, size, digest):
        self.check(reference)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=reference["key"])
            source = response["Body"]
            try:
                if response["ContentLength"] != size:
                    raise StorageError("文件完整性校验失败")
                verified_copy(source, target, size, digest)
            finally:
                source.close()
        except ClientError as exc:
            if exc.response["Error"]["Code"] in {"NoSuchKey", "404"}:
                raise ObjectMissing("文件对象不存在，请联系管理员") from None
            raise StorageError("S3 文件存储暂不可用") from None
        except BotoCoreError:
            raise StorageError("S3 文件存储暂不可用") from None

    def delete(self, reference):
        self.check(reference)
        try:
            self.client.delete_object(Bucket=self.bucket, Key=reference["key"])
        except (BotoCoreError, ClientError):
            raise StorageError("文件清理失败，需要管理员核对孤儿对象") from None

    def close(self):
        self.client.close()


def object_store(settings, backend=None):
    backend = backend or settings.artifact_storage_backend
    if backend not in {"local", "s3"}:
        raise StorageError("文件存储类型无效")
    try:
        return LocalObjects(settings) if backend == "local" else S3Objects(settings)
    except (BotoCoreError, ClientError):
        raise StorageError("文件存储配置不可用") from None
