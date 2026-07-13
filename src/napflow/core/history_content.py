"""Content-addressed run-history values (D34, M4).

This module owns the byte-level persisted-value contract independently of
event wiring. ``content-blobs/1`` is activated by store-backed EventStreams,
which encode every declared event payload path before the shared
JSONL/WebSocket fan-out.

Blobs are scoped to one run and live beside its JSONL as
``<run-id>.blobs/<sha256-hex>``.  A blob is written and fsynced before a
descriptor can be returned; an existing digest is verified and never
overwritten.  Runtime values are only read and copied -- persistence never
mutates them.
"""

import json
import math
import os
import re
import stat
from base64 import b64decode, b64encode
from binascii import Error as Base64Error
from contextlib import suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

DEFAULT_INLINE_THRESHOLD_BYTES = 64 * 1024

_TAG = "$napflow"
_HASH_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
_REASON_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_BINARY_FIELDS = {"__binary__", "content_type", "base64"}
_BLOB_FIELDS = {"kind", "hash", "bytes", "media_type", "codec"}
_OMITTED_FIELDS = _BLOB_FIELDS | {"reason"}
_LITERAL_FIELDS = {"kind", "value"}
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x0400)
_HAS_DIRECTORY_FDS = all(
    function in os.supports_dir_fd for function in (os.open, os.stat, os.unlink)
)

ContentCodec = Literal["utf-8", "json", "binary"]


class ContentStoreError(ValueError):
    """A value cannot be persisted or its descriptor is malformed."""


class ContentMissingError(ContentStoreError):
    """A valid blob descriptor references content that is not present."""


class ContentCorruptError(ContentStoreError):
    """Stored content does not match its immutable descriptor."""


class ContentOmittedError(ContentStoreError):
    """A valid omission descriptor was resolved instead of full content."""

    def __init__(self, descriptor: dict[str, Any]):
        self.hash = descriptor["hash"]
        self.byte_count = descriptor["bytes"]
        self.media_type = descriptor["media_type"]
        self.codec = descriptor["codec"]
        self.reason = descriptor["reason"]
        super().__init__(
            "run-history content was explicitly omitted "
            f"({self.reason}; {self.byte_count} bytes; {self.hash})"
        )


@dataclass(frozen=True)
class _EncodedValue:
    data: bytes
    codec: ContentCodec
    media_type: str


@dataclass(frozen=True)
class _ExternalDescriptor:
    hash: str
    byte_count: int
    media_type: str
    codec: ContentCodec
    reason: str | None = None


class RunContentStore:
    """Encode and resolve persisted values for one canonical run log.

    The default threshold is measured over the exact bytes described by the
    codec.  Values of exactly the threshold remain inline; only larger values
    become blobs.  Directory creation is lazy, so all-inline runs gain no
    companion directory.
    """

    def __init__(
        self,
        log_path: Path,
        *,
        inline_threshold_bytes: int = DEFAULT_INLINE_THRESHOLD_BYTES,
    ):
        if type(inline_threshold_bytes) is not int or inline_threshold_bytes < 0:
            raise ValueError("inline_threshold_bytes must be a non-negative integer")
        self.log_path = Path(log_path)
        self.blob_dir = self.log_path.with_name(f"{self.log_path.stem}.blobs")
        self.inline_threshold_bytes = inline_threshold_bytes

    def persist(self, value: Any, media_type: str | None = None) -> Any:
        """Return the collision-safe persisted form of ``value``.

        Small JSON-compatible values remain inline.  A small object with a
        top-level ``$napflow`` key is escaped as ``kind: literal``; a large
        value is stored once and represented by the exact D34 blob envelope.
        """
        encoded = _encode_value(value, media_type)
        if len(encoded.data) <= self.inline_threshold_bytes:
            inline = value if type(value) is str else _json_copy(value)
            if type(inline) is dict and _TAG in inline:
                return {_TAG: {"kind": "literal", "value": inline}}
            return inline

        digest = sha256(encoded.data).hexdigest()
        self._write_blob(digest, encoded.data)
        return {
            _TAG: {
                "kind": "blob",
                "hash": f"sha256:{digest}",
                "bytes": len(encoded.data),
                "media_type": encoded.media_type,
                "codec": encoded.codec,
            }
        }

    def omit(
        self,
        value: Any,
        reason: str,
        media_type: str | None = None,
    ) -> dict[str, Any]:
        """Describe an explicit hard-limit omission without writing bytes."""
        if type(reason) is not str or _REASON_RE.fullmatch(reason) is None:
            raise ContentStoreError(
                "omission reason must be a lowercase snake-case code"
            )
        encoded = _encode_value(value, media_type)
        return {
            _TAG: {
                "kind": "omitted",
                "hash": f"sha256:{sha256(encoded.data).hexdigest()}",
                "bytes": len(encoded.data),
                "media_type": encoded.media_type,
                "codec": encoded.codec,
                "reason": reason,
            }
        }

    def resolve(self, value: Any) -> Any:
        """Resolve one schema-declared persisted value.

        Ordinary inline data is copied through.  Tagged descriptors are
        validated exactly; blob length and SHA-256 are checked before decode.
        Omission, absence, corruption, and malformed protocol data are never
        replaced with a plausible partial value.
        """
        if type(value) is not dict or set(value) != {_TAG}:
            return _json_copy(value)
        descriptor = value[_TAG]
        if type(descriptor) is not dict:
            raise ContentStoreError("persisted-value descriptor must be an object")

        kind = descriptor.get("kind")
        if kind == "literal":
            if set(descriptor) != _LITERAL_FIELDS:
                raise ContentStoreError(
                    "literal descriptor must contain exactly kind and value"
                )
            return _json_copy(descriptor["value"])
        if kind not in ("blob", "omitted"):
            raise ContentStoreError(f"unknown persisted-value kind {kind!r}")

        external = _validate_external_descriptor(descriptor, omitted=kind == "omitted")
        if external.reason is not None:
            raise ContentOmittedError(descriptor)

        digest = external.hash.removeprefix("sha256:")
        data = self._read_blob(digest, external.byte_count)
        actual_hash = f"sha256:{sha256(data).hexdigest()}"
        if actual_hash != external.hash:
            raise ContentCorruptError(
                f"blob hash mismatch for {external.hash}: found {actual_hash}"
            )
        return _decode_value(data, external.codec, external.media_type, external.hash)

    def _ensure_blob_dir(self) -> None:
        created = False
        try:
            self.blob_dir.mkdir()
            created = True
        except FileExistsError:
            pass
        except OSError as error:
            raise ContentStoreError(
                f"cannot create run content directory {self.blob_dir}"
            ) from error
        self._validate_blob_dir()
        if created:
            try:
                _fsync_directory(self.blob_dir.parent)
            except OSError as error:
                with suppress(OSError):
                    self.blob_dir.rmdir()
                raise ContentStoreError(
                    f"cannot publish run content directory {self.blob_dir}"
                ) from error

    def _validate_blob_dir(self) -> os.stat_result:
        try:
            current = self.blob_dir.lstat()
        except FileNotFoundError as error:
            raise ContentMissingError(
                f"run content directory is missing: {self.blob_dir}"
            ) from error
        except OSError as error:
            raise ContentCorruptError(
                f"cannot inspect run content directory {self.blob_dir}"
            ) from error
        if not stat.S_ISDIR(current.st_mode) or (
            getattr(current, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ContentCorruptError(
                f"run content path is not a directory: {self.blob_dir}"
            )
        return current

    def _open_blob_directory(self) -> tuple[int | None, tuple[int, int]]:
        """Open and pin the run blob directory where dir-fd APIs exist."""
        before = self._validate_blob_dir()
        token = (before.st_dev, before.st_ino)
        if not _HAS_DIRECTORY_FDS:
            # Windows exposes no stdlib openat-style primitive. Static
            # reparse points are rejected above and identity is checked again
            # after access; D37 explicitly excludes a malicious local process
            # swapping filesystem entries during that narrow interval.
            return None, token
        flags = os.O_RDONLY
        flags |= getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            directory_fd = os.open(self.blob_dir, flags)
            opened = os.fstat(directory_fd)
        except OSError as error:
            with suppress(UnboundLocalError, OSError):
                os.close(directory_fd)
            raise ContentCorruptError(
                f"cannot pin run content directory {self.blob_dir}"
            ) from error
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (
                opened.st_dev,
                opened.st_ino,
            )
            != token
        ):
            with suppress(OSError):
                os.close(directory_fd)
            raise ContentCorruptError(
                f"run content directory changed during open: {self.blob_dir}"
            )
        return directory_fd, token

    def _require_blob_directory_unchanged(
        self, directory_fd: int | None, token: tuple[int, int]
    ) -> None:
        current = self._validate_blob_dir()
        if (current.st_dev, current.st_ino) != token:
            raise ContentCorruptError(
                f"run content directory changed during access: {self.blob_dir}"
            )
        if directory_fd is not None:
            try:
                opened = os.fstat(directory_fd)
            except OSError as error:
                raise ContentCorruptError(
                    f"cannot revalidate run content directory {self.blob_dir}"
                ) from error
            if (opened.st_dev, opened.st_ino) != token:
                raise ContentCorruptError(
                    f"run content directory handle changed: {self.blob_dir}"
                )

    def _write_blob(self, digest: str, data: bytes) -> None:
        self._ensure_blob_dir()
        path = self.blob_dir / digest
        directory_fd, directory_token = self._open_blob_directory()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_BINARY", 0)
        fd = -1
        created = False
        try:
            try:
                fd = (
                    os.open(digest, flags, 0o666, dir_fd=directory_fd)
                    if directory_fd is not None
                    else os.open(path, flags, 0o666)
                )
                created = True
            except FileExistsError:
                try:
                    existing = self._read_blob_file(
                        path,
                        len(data),
                        directory_fd=directory_fd,
                        digest=digest,
                    )
                except FileNotFoundError as error:
                    raise ContentCorruptError(
                        f"existing content-addressed blob disappeared: {digest}"
                    ) from error
                self._require_blob_directory_unchanged(directory_fd, directory_token)
                if existing != data:
                    raise ContentCorruptError(
                        f"existing content-addressed blob does not match {digest}"
                    ) from None
                return
            with os.fdopen(fd, "wb") as blob:
                fd = -1
                blob.write(data)
                blob.flush()
                os.fsync(blob.fileno())
            if directory_fd is not None:
                os.fsync(directory_fd)
            else:
                _fsync_directory(self.blob_dir)
            self._require_blob_directory_unchanged(directory_fd, directory_token)
        except OSError as error:
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)
            if created:
                self._remove_blob(path, digest, directory_fd)
            raise ContentStoreError(f"cannot write run content blob {path}") from error
        except BaseException:
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)
            if created:
                self._remove_blob(path, digest, directory_fd)
            raise
        finally:
            if directory_fd is not None:
                with suppress(OSError):
                    os.close(directory_fd)

    @staticmethod
    def _remove_blob(path: Path, digest: str, directory_fd: int | None) -> None:
        with suppress(OSError):
            if directory_fd is not None:
                os.unlink(digest, dir_fd=directory_fd)
            else:
                path.unlink()

    def _read_blob(self, digest: str, expected_bytes: int) -> bytes:
        try:
            self._validate_blob_dir()
        except ContentMissingError as error:
            raise ContentMissingError(
                f"run-history blob is missing: sha256:{digest}"
            ) from error
        path = self.blob_dir / digest
        directory_fd, directory_token = self._open_blob_directory()
        try:
            data = self._read_blob_file(
                path,
                expected_bytes,
                directory_fd=directory_fd,
                digest=digest,
            )
            self._require_blob_directory_unchanged(directory_fd, directory_token)
            return data
        except FileNotFoundError as error:
            raise ContentMissingError(
                f"run-history blob is missing: sha256:{digest}"
            ) from error
        finally:
            if directory_fd is not None:
                with suppress(OSError):
                    os.close(directory_fd)

    @staticmethod
    def _read_blob_file(
        path: Path,
        expected_bytes: int,
        *,
        directory_fd: int | None = None,
        digest: str | None = None,
    ) -> bytes:
        try:
            before = (
                os.stat(digest, dir_fd=directory_fd, follow_symlinks=False)
                if directory_fd is not None and digest is not None
                else path.lstat()
            )
        except FileNotFoundError:
            raise
        except OSError as error:
            raise ContentCorruptError(
                f"cannot inspect run content blob {path}"
            ) from error
        if not stat.S_ISREG(before.st_mode) or (
            getattr(before, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ContentCorruptError(f"run content blob is not a regular file: {path}")
        flags = os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_BINARY", 0)
        try:
            fd = (
                os.open(digest, flags, dir_fd=directory_fd)
                if directory_fd is not None and digest is not None
                else os.open(path, flags)
            )
        except FileNotFoundError:
            raise
        except OSError as error:
            raise ContentCorruptError(f"cannot open run content blob {path}") from error
        try:
            try:
                opened = os.fstat(fd)
            except OSError as error:
                raise ContentCorruptError(
                    f"cannot inspect open run content blob {path}"
                ) from error
            if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (
                before.st_dev,
                before.st_ino,
            ):
                raise ContentCorruptError(
                    f"run content blob changed during open: {path}"
                )
            if opened.st_size != expected_bytes:
                raise ContentCorruptError(
                    f"blob size mismatch for {path.name}: "
                    f"expected {expected_bytes}, found {opened.st_size}"
                )
            with os.fdopen(fd, "rb") as blob:
                fd = -1
                try:
                    data = blob.read(expected_bytes + 1)
                    after = os.fstat(blob.fileno())
                except (MemoryError, OSError, OverflowError) as error:
                    raise ContentCorruptError(
                        f"cannot read run content blob {path}"
                    ) from error
                if len(data) != expected_bytes or after.st_size != expected_bytes:
                    raise ContentCorruptError(
                        f"blob size changed while reading {path.name}"
                    )
                return data
        finally:
            if fd >= 0:
                with suppress(OSError):
                    os.close(fd)


def _validate_external_descriptor(
    descriptor: dict[str, Any], *, omitted: bool
) -> _ExternalDescriptor:
    expected = _OMITTED_FIELDS if omitted else _BLOB_FIELDS
    if set(descriptor) != expected:
        kind = "omitted" if omitted else "blob"
        raise ContentStoreError(f"{kind} descriptor has missing or unexpected fields")
    content_hash = descriptor["hash"]
    match = _HASH_RE.fullmatch(content_hash) if type(content_hash) is str else None
    if match is None:
        raise ContentStoreError("content hash must be sha256:<64 lowercase hex>")
    byte_count = descriptor["bytes"]
    if type(byte_count) is not int or byte_count < 0:
        raise ContentStoreError("content bytes must be a non-negative integer")
    media_type = _validate_media_type(descriptor["media_type"])
    codec = descriptor["codec"]
    if codec not in ("utf-8", "json", "binary"):
        raise ContentStoreError(f"unknown content codec {codec!r}")
    reason = descriptor.get("reason")
    if omitted and (type(reason) is not str or _REASON_RE.fullmatch(reason) is None):
        raise ContentStoreError("omission reason must be a lowercase snake-case code")
    return _ExternalDescriptor(
        hash=content_hash,
        byte_count=byte_count,
        media_type=media_type,
        codec=codec,
        reason=reason,
    )


def _encode_value(value: Any, media_type: str | None) -> _EncodedValue:
    # ruamel's round-trip loader deliberately preserves scalar/container
    # subclasses (for example ``ScalarInt`` and ``CommentedMap``).  They are
    # formatting wrappers around logical JSON values, not Python-only payload
    # types, so discard that YAML presentation metadata at the persistence
    # boundary before choosing a codec or hashing bytes.
    try:
        value = _normalize_json_model(value)
    except ContentStoreError:
        raise
    except (MemoryError, OverflowError, RecursionError) as error:
        raise ContentStoreError(
            "content value is not strict JSON-compatible"
        ) from error
    if type(value) is str:
        try:
            data = value.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ContentStoreError("text content is not valid UTF-8") from error
        except MemoryError as error:
            raise ContentStoreError("text content cannot be encoded") from error
        return _EncodedValue(
            data=data,
            codec="utf-8",
            media_type=_selected_media_type(media_type, "text/plain; charset=utf-8"),
        )

    binary = _binary_bytes(value)
    if binary is not None:
        data, content_type = binary
        return _EncodedValue(
            data=data,
            codec="binary",
            media_type=_selected_media_type(media_type, content_type),
        )

    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
        data = text.encode("utf-8")
    except ContentStoreError:
        raise
    except (
        MemoryError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
        UnicodeError,
    ) as error:
        raise ContentStoreError(
            "content value is not strict JSON-compatible"
        ) from error
    return _EncodedValue(
        data=data,
        codec="json",
        media_type=_selected_media_type(media_type, "application/json"),
    )


def _binary_bytes(value: Any) -> tuple[bytes, str] | None:
    if (
        type(value) is not dict
        or set(value) != _BINARY_FIELDS
        or value.get("__binary__") is not True
        or type(value.get("content_type")) is not str
        or type(value.get("base64")) is not str
    ):
        return None
    content_type = value["content_type"]
    try:
        content_type = _validate_media_type(content_type)
    except ContentStoreError:
        return None
    try:
        data = b64decode(value["base64"], validate=True)
    except (Base64Error, UnicodeError, ValueError):
        return None
    except (MemoryError, OverflowError) as error:
        raise ContentStoreError("binary content cannot be decoded") from error
    try:
        canonical = b64encode(data).decode("ascii")
    except (MemoryError, OverflowError) as error:
        raise ContentStoreError("binary content cannot be verified") from error
    if canonical != value["base64"]:
        return None
    return data, content_type


def _decode_value(
    data: bytes,
    codec: ContentCodec,
    media_type: str,
    content_hash: str,
) -> Any:
    try:
        if codec == "utf-8":
            return data.decode("utf-8")
        if codec == "json":
            return json.loads(data, parse_constant=_reject_json_constant)
        return {
            "__binary__": True,
            "content_type": media_type,
            "base64": b64encode(data).decode("ascii"),
        }
    except (
        MemoryError,
        OverflowError,
        RecursionError,
        UnicodeError,
        ValueError,
    ) as error:
        raise ContentCorruptError(
            f"blob cannot be decoded as {codec}: {content_hash}"
        ) from error


def _json_copy(value: Any) -> Any:
    try:
        return _normalize_json_model(value)
    except ContentStoreError:
        raise
    except (
        MemoryError,
        OverflowError,
        RecursionError,
        TypeError,
        ValueError,
        UnicodeError,
    ) as error:
        raise ContentStoreError(
            "content value is not strict JSON-compatible"
        ) from error


def _validate_media_type(value: Any) -> str:
    if (
        type(value) is not str
        or not value.strip()
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        raise ContentStoreError("content media_type must be a non-empty MIME string")
    return value


def _selected_media_type(value: str | None, default: str) -> str:
    return _validate_media_type(default if value is None else value)


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON number {value}")


def _normalize_json_model(value: Any) -> Any:
    """Return built-in JSON values or reject a shape JSON would lose.

    Built-in subclasses are accepted because ruamel uses them to retain YAML
    spelling/comments.  Their presentation metadata has no runtime meaning;
    tuples, sets, non-string mapping keys, and unrelated Python objects remain
    invalid rather than being silently stringified or reshaped.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        normalized = float(value)
        if not math.isfinite(normalized):
            raise ContentStoreError("content contains a non-finite JSON number")
        return normalized
    if isinstance(value, list):
        return [_normalize_json_model(item) for item in value]
    if isinstance(value, dict):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContentStoreError("content object keys must be strings")
            normalized[str(key)] = _normalize_json_model(item)
        return normalized
    raise ContentStoreError(f"content value has non-JSON type {type(value).__name__}")


def _fsync_directory(path: Path) -> None:
    """Durably publish a new directory entry where the platform permits."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        with suppress(OSError):
            os.close(fd)
