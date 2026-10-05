"""SourceStore adapters: private S3 in the cloud, a local directory otherwise.

Two rules drive the whole design:

1. **The server picks the key.** A client never proposes an object path, and
   every read validates that the key sits under the owner's prefix. A key
   outside it is a permission error, not a miss.
2. **Stored bytes are the truth.** A submitted content-length or MIME header is
   advisory; ``head()`` reports what is actually stored and the completion path
   re-checks it against the configured limits.
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from metis_contracts import AdapterMode, ErrorCode, MetisError, ObjectRef, SystemClock, settings

if TYPE_CHECKING:  # pragma: no cover
    from mypy_boto3_s3.type_defs import ObjectIdentifierTypeDef


def owner_prefix(user_id: str) -> str:
    """Deterministic, collision-free per-owner prefix.

    The subject is hashed so a raw authenticated identifier never appears in an
    object path (paths leak into logs and error messages).
    """
    digest = hashlib.sha256(user_id.encode()).hexdigest()[:32]
    return f"users/{digest}"


def build_key(user_id: str, capture_id: str, extension: str) -> str:
    return f"{owner_prefix(user_id)}/captures/{capture_id}{extension}"


_EXTENSIONS = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "application/json": ".json",
    "text/plain": ".txt",
}


def extension_for(content_type: str | None) -> str:
    base = (content_type or "").split(";")[0].strip().lower()
    return _EXTENSIONS.get(base, ".bin")


class LocalSourceStore:
    """Filesystem object store for local mode.

    The "upload URL" is an API route the browser PUTs to; there is no signing
    because nothing leaves the machine. The route still enforces the owner
    prefix and the byte limit, so the local and cloud paths exercise the same
    validation code.
    """

    mode = AdapterMode.LOCAL

    def __init__(self, root: str | None = None, clock: Any | None = None) -> None:
        self._root = Path(root or settings().local_objects_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._clock = clock or SystemClock()
        self.bucket = "local"

    def _path(self, ref: ObjectRef) -> Path:
        target = (self._root / ref.key).resolve()
        root = self._root.resolve()
        if not str(target).startswith(str(root) + "/"):
            # Defends against a traversal attempt in a key.
            raise MetisError(ErrorCode.FORBIDDEN, "object key escapes the store root")
        return target

    def upload_target(
        self, user_id: str, *, capture_id: str, content_type: str, max_bytes: int, ttl_seconds: int
    ) -> tuple[ObjectRef, dict[str, Any]]:
        key = build_key(user_id, capture_id, extension_for(content_type))
        ref = ObjectRef(bucket=self.bucket, key=key, content_type=content_type)
        instructions = {
            "url": f"/v1/local-uploads/{capture_id}",
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "fields": {},
            "object_key": key,
            "expires_at": (self._clock.now() + timedelta(seconds=ttl_seconds)).isoformat(),
            "max_bytes": max_bytes,
        }
        return ref, instructions

    def head(self, ref: ObjectRef) -> ObjectRef | None:
        path = self._path(ref)
        if not path.is_file():
            return None
        body = path.read_bytes()
        return ref.model_copy(
            update={"size_bytes": len(body), "checksum_sha256": hashlib.sha256(body).hexdigest()}
        )

    def get_bytes(self, ref: ObjectRef) -> bytes:
        path = self._path(ref)
        if not path.is_file():
            raise MetisError(ErrorCode.OBJECT_MISSING, f"object {ref.key} is not stored")
        return path.read_bytes()

    def put_bytes(self, ref: ObjectRef, body: bytes, *, content_type: str) -> ObjectRef:
        path = self._path(ref)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return ref.model_copy(
            update={
                "size_bytes": len(body),
                "checksum_sha256": hashlib.sha256(body).hexdigest(),
                "content_type": content_type,
            }
        )

    def delete(self, ref: ObjectRef) -> None:
        path = self._path(ref)
        if path.is_file():
            path.unlink()

    def delete_prefix(self, prefix: str) -> int:
        """Used by the purge job; returns the number of objects removed."""
        target = (self._root / prefix).resolve()
        if not str(target).startswith(str(self._root.resolve())):
            raise MetisError(ErrorCode.FORBIDDEN, "prefix escapes the store root")
        if not target.is_dir():
            return 0
        count = sum(1 for p in target.rglob("*") if p.is_file())
        shutil.rmtree(target)
        return count

    def owns_key(self, user_id: str, key: str) -> bool:
        return key.startswith(owner_prefix(user_id) + "/")


class S3SourceStore:
    """Private S3 bucket. No public access, no client-chosen keys.

    ``generate_presigned_post`` is used rather than a presigned PUT because the
    POST policy can bind a content-length range server-side. The stored object
    is still re-checked with ``head_object`` before any processing starts.
    """

    mode = AdapterMode.CLOUD

    def __init__(self, bucket: str | None = None, clock: Any | None = None) -> None:
        import boto3

        cfg = settings()
        self.bucket = bucket or cfg.aws.sources_bucket or ""
        if not self.bucket:
            raise ValueError("METIS_SOURCES_BUCKET is required in cloud mode")
        self._clock = clock or SystemClock()
        self._s3 = boto3.client("s3", region_name=cfg.aws.region)

    def upload_target(
        self, user_id: str, *, capture_id: str, content_type: str, max_bytes: int, ttl_seconds: int
    ) -> tuple[ObjectRef, dict[str, Any]]:
        key = build_key(user_id, capture_id, extension_for(content_type))
        post = self._s3.generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[
                {"Content-Type": content_type},
                ["content-length-range", 1, max_bytes],
            ],
            ExpiresIn=ttl_seconds,
        )
        ref = ObjectRef(bucket=self.bucket, key=key, content_type=content_type)
        instructions = {
            "url": post["url"],
            "method": "POST",
            "fields": post["fields"],
            "headers": {},
            "object_key": key,
            "expires_at": (self._clock.now() + timedelta(seconds=ttl_seconds)).isoformat(),
            "max_bytes": max_bytes,
        }
        return ref, instructions

    def head(self, ref: ObjectRef) -> ObjectRef | None:
        try:
            resp = self._s3.head_object(Bucket=ref.bucket or self.bucket, Key=ref.key)
        except self._s3.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        checksum = (resp.get("ChecksumSHA256") or "").strip() or None
        return ref.model_copy(
            update={
                "size_bytes": int(resp["ContentLength"]),
                "content_type": resp.get("ContentType"),
                "checksum_sha256": checksum if checksum and len(checksum) == 64 else ref.checksum_sha256,
            }
        )

    def get_bytes(self, ref: ObjectRef) -> bytes:
        try:
            resp = self._s3.get_object(Bucket=ref.bucket or self.bucket, Key=ref.key)
        except self._s3.exceptions.NoSuchKey as exc:
            raise MetisError(ErrorCode.OBJECT_MISSING, f"object {ref.key} is not stored") from exc
        return resp["Body"].read()

    def put_bytes(self, ref: ObjectRef, body: bytes, *, content_type: str) -> ObjectRef:
        self._s3.put_object(
            Bucket=ref.bucket or self.bucket,
            Key=ref.key,
            Body=body,
            ContentType=content_type,
            ChecksumAlgorithm="SHA256",
            ServerSideEncryption="aws:kms",
        )
        return ref.model_copy(
            update={
                "size_bytes": len(body),
                "checksum_sha256": hashlib.sha256(body).hexdigest(),
                "content_type": content_type,
            }
        )

    def delete(self, ref: ObjectRef) -> None:
        self._s3.delete_object(Bucket=ref.bucket or self.bucket, Key=ref.key)

    def delete_prefix(
        self, prefix: str, *, cursor: str | None = None, batch: int = 500
    ) -> tuple[int, str | None]:
        """Resumable prefix delete for the purge job.

        Returns ``(deleted_count, next_cursor)``. A caller that runs out of time
        persists the cursor and continues, so deletion is never half-finished
        with no way to resume.
        """
        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": batch}
        if cursor:
            kwargs["ContinuationToken"] = cursor
        resp = self._s3.list_objects_v2(**kwargs)
        keys: list[ObjectIdentifierTypeDef] = [
            {"Key": o["Key"]} for o in resp.get("Contents", []) if "Key" in o
        ]
        if keys:
            self._s3.delete_objects(Bucket=self.bucket, Delete={"Objects": keys, "Quiet": True})
        return len(keys), resp.get("NextContinuationToken")

    def owns_key(self, user_id: str, key: str) -> bool:
        return key.startswith(owner_prefix(user_id) + "/")


def build_source_store(clock: Any | None = None) -> Any:
    from metis_contracts import Mode

    if settings().mode is Mode.CLOUD:
        return S3SourceStore(clock=clock)
    return LocalSourceStore(clock=clock)
