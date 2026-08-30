"""Validate and publish explicitly approved guide images to public Blob Storage.

The default operation is a local dry run.  It validates the image approval manifest,
content-hashed WebP files, target paths, and public-origin configuration without creating
an Azure client.  Blob uploads are only attempted when ``--publish`` is supplied.

Credentials are read only from the environment at publish time.  The manifest is never
rewritten by this command; the machine-readable report contains the stable public URLs
that can be used when the approved image metadata is promoted to the search index.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:  # Running from the repository root.
    from scripts.ingestion.images import load_approved_image_records
except ModuleNotFoundError:  # Running as ``uv run python ../scripts/...`` from backend/.
    from ingestion.images import load_approved_image_records  # type: ignore[no-redef]


IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
DEFAULT_CONTAINER = "guide-images"
DEFAULT_PREFIX = "guides"
DEFAULT_REPORT = "data/processed/corpus/guide-image-publish-report.json"
_HASH_PREFIX_RE = re.compile(r"^[0-9a-f]{16}$")
_CONTAINER_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])?$")
_PREFIX_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class ConfigurationError(ValueError):
    """Raised for missing or invalid publication configuration."""


@dataclass(frozen=True)
class PublishConfig:
    base_url: str
    container: str
    prefix: str
    connection_string: str | None = None
    account_url: str | None = None
    account_key: str | None = None


@dataclass
class ValidatedImage:
    image_id: str
    path: Path
    blob_name: str
    public_url: str
    byte_size: int
    width: int
    height: int
    binary_sha256: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validate_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ConfigurationError("public media base URL must be an HTTPS URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError(
            "public media base URL must not contain credentials, query, or fragment"
        )
    return base_url


def _load_config(args: argparse.Namespace, *, publish: bool) -> PublishConfig:
    base_value = (
        args.base_url
        or os.getenv("GUIDE_MEDIA_BASE_URL")
        or os.getenv("AZURE_GUIDE_MEDIA_BASE_URL", "")
    )
    if not base_value:
        raise ConfigurationError(
            "missing public media base URL (set GUIDE_MEDIA_BASE_URL or AZURE_GUIDE_MEDIA_BASE_URL)"
        )
    base_url = _validate_base_url(base_value)

    container = (
        args.container or os.getenv("AZURE_GUIDE_MEDIA_CONTAINER", DEFAULT_CONTAINER)
    ).strip()
    if not _CONTAINER_RE.fullmatch(container):
        raise ConfigurationError("public media container must be a valid Azure Blob container name")
    prefix = (args.prefix or DEFAULT_PREFIX).strip("/")
    if (
        not prefix
        or not _PREFIX_RE.fullmatch(prefix)
        or any(part in {".", ".."} for part in prefix.split("/"))
    ):
        raise ConfigurationError("public media prefix must be a relative path")
    if any(not part or part.isspace() for part in prefix.split("/")):
        raise ConfigurationError("public media prefix must not contain empty path components")

    connection_string = os.getenv("AZURE_GUIDE_MEDIA_CONNECTION_STRING") or None
    account_url = os.getenv("AZURE_GUIDE_MEDIA_ACCOUNT_URL") or None
    account_key = os.getenv("AZURE_GUIDE_MEDIA_ACCOUNT_KEY") or None
    if publish and not connection_string and not (account_url and account_key):
        raise ConfigurationError(
            "publishing requires AZURE_GUIDE_MEDIA_CONNECTION_STRING or "
            "AZURE_GUIDE_MEDIA_ACCOUNT_URL with AZURE_GUIDE_MEDIA_ACCOUNT_KEY"
        )
    if account_url:
        account_url = _validate_base_url(account_url)
    return PublishConfig(
        base_url=base_url,
        container=container,
        prefix=prefix,
        connection_string=connection_string,
        account_url=account_url,
        account_key=account_key,
    )


def _image_dimensions(path: Path) -> tuple[str, int, int]:
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return image.format or "", image.width, image.height
    except Exception as exc:  # Pillow raises several format-specific exceptions.
        raise ValueError(f"unable to decode image as WebP: {path.name}") from exc


def _validate_images(
    approved: list[tuple[Any, dict[str, Any]]], directory: Path, config: PublishConfig
) -> tuple[list[ValidatedImage], list[str]]:
    if not approved:
        return [], []
    if not directory.is_dir():
        raise ValueError(f"image directory does not exist: {directory}")

    validated: list[ValidatedImage] = []
    errors: list[str] = []
    seen_ids: set[str] = set()
    for record, _raw in approved:
        image_id = record.image_id
        try:
            if image_id in seen_ids:
                raise ValueError(f"duplicate approved image ID: {image_id}")
            seen_ids.add(image_id)
            if record.review_status.value != "approved":
                raise ValueError("image is not approved")
            if record.status.value == "excluded":
                raise ValueError("excluded images cannot be published")
            if record.page is None:
                raise ValueError("page is required")
            if not record.binary_sha256:
                raise ValueError("binary_sha256 is required")
            if record.byte_size is None or record.width is None or record.height is None:
                raise ValueError("byte_size, width, and height are required")
            if not _HASH_PREFIX_RE.fullmatch(record.binary_sha256[:16]):
                raise ValueError("binary_sha256 must be a lowercase SHA-256 hash")

            filename = f"{image_id}.{record.binary_sha256[:16]}.webp"
            path = (directory / filename).resolve()
            root = directory.resolve()
            if path.parent != root or not path.is_file():
                raise ValueError(f"rendered image is missing: {filename}")
            byte_size = path.stat().st_size
            if byte_size != record.byte_size:
                raise ValueError(
                    f"byte size mismatch (manifest={record.byte_size}, file={byte_size})"
                )
            digest = _sha256_file(path)
            if digest != record.binary_sha256:
                raise ValueError("binary SHA-256 does not match the manifest")
            image_format, width, height = _image_dimensions(path)
            if image_format != "WEBP":
                raise ValueError(f"expected WebP, found {image_format or 'unknown format'}")
            if (width, height) != (record.width, record.height):
                raise ValueError(
                    f"dimensions mismatch (manifest={record.width}x{record.height}, "
                    f"file={width}x{height})"
                )
            blob_name = f"{config.prefix}/{filename}"
            validated.append(
                ValidatedImage(
                    image_id=image_id,
                    path=path,
                    blob_name=blob_name,
                    public_url=f"{config.base_url}/{blob_name}",
                    byte_size=byte_size,
                    width=width,
                    height=height,
                    binary_sha256=digest,
                )
            )
        except (OSError, ValueError) as exc:
            errors.append(f"{image_id}: {exc}")
    return validated, errors


def _upload_images(images: list[ValidatedImage], config: PublishConfig) -> tuple[int, list[str]]:
    try:
        from azure.storage.blob import BlobServiceClient, ContentSettings
    except ImportError as exc:  # pragma: no cover - dependency is locked in backend/pyproject.toml
        raise ConfigurationError("azure-storage-blob is required for --publish") from exc

    if config.connection_string:
        service = BlobServiceClient.from_connection_string(config.connection_string)
    else:
        # account_url and account_key are both guaranteed by _load_config for publish mode.
        service = BlobServiceClient(account_url=config.account_url, credential=config.account_key)
    container = service.get_container_client(config.container)
    uploaded = 0
    errors: list[str] = []
    settings = ContentSettings(content_type="image/webp", cache_control=IMMUTABLE_CACHE_CONTROL)
    try:
        for image in images:
            try:
                blob = container.get_blob_client(image.blob_name)
                with image.path.open("rb") as stream:
                    blob.upload_blob(stream, overwrite=True, content_settings=settings)
                uploaded += 1
            except Exception as exc:  # Azure SDK errors are intentionally summarized.
                errors.append(f"{image.image_id}: upload failed ({type(exc).__name__})")
    finally:
        close = getattr(service, "close", None)
        if close:
            close()
    return uploaded, errors


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/metadata/guide-images.yaml")
    parser.add_argument("--directory", default="data/processed/corpus/rendered-images")
    parser.add_argument("--report", default=DEFAULT_REPORT)
    parser.add_argument("--base-url", help="public media base URL (overrides environment)")
    parser.add_argument("--container", help=f"Azure Blob container (default: {DEFAULT_CONTAINER})")
    parser.add_argument(
        "--prefix", default=DEFAULT_PREFIX, help=f"blob path prefix (default: {DEFAULT_PREFIX})"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--dry-run", action="store_true", help="validate without cloud mutation (default)"
    )
    modes.add_argument("--publish", action="store_true", help="upload validated approved images")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    publish = bool(args.publish)
    report: dict[str, Any] = {
        "schema_version": 1,
        "mode": "publish" if publish else "dry-run",
        "manifest": str(Path(args.manifest)),
        "directory": str(Path(args.directory)),
        "images": [],
        "errors": [],
    }
    try:
        config = _load_config(args, publish=publish)
        report.update(
            {
                "base_url": config.base_url,
                "container": config.container,
                "prefix": config.prefix,
                "cache_control": IMMUTABLE_CACHE_CONTROL,
                "content_type": "image/webp",
            }
        )
        approved = load_approved_image_records(args.manifest)
        images, validation_errors = _validate_images(approved, Path(args.directory), config)
        report["approved_count"] = len(approved)
        report["validated_count"] = len(images)
        report["errors"].extend(validation_errors)
        report["images"] = [
            {
                "image_id": image.image_id,
                "blob_name": image.blob_name,
                "public_url": image.public_url,
                "binary_sha256": image.binary_sha256,
                "byte_size": image.byte_size,
                "width": image.width,
                "height": image.height,
                "status": "validated",
            }
            for image in images
        ]
        if validation_errors:
            report["status"] = "failed"
            report["uploaded_count"] = 0
            return_code = 1
        elif publish:
            uploaded, upload_errors = _upload_images(images, config)
            report["uploaded_count"] = uploaded
            report["errors"].extend(upload_errors)
            report["status"] = "completed" if not upload_errors else "failed"
            return_code = 0 if not upload_errors else 1
        else:
            report["uploaded_count"] = 0
            report["status"] = "validated"
            return_code = 0
    except ConfigurationError as exc:
        report["status"] = "invalid_configuration"
        report["errors"].append(str(exc))
        return_code = 2
    except (OSError, ValueError) as exc:
        report["status"] = "failed"
        report["errors"].append(str(exc))
        return_code = 1

    try:
        _write_report(Path(args.report), report)
    except OSError as exc:
        print(f"guide image publishing error: unable to write report ({type(exc).__name__})")
        return 1
    print(
        json.dumps(
            {
                "mode": report["mode"],
                "status": report["status"],
                "approved": report.get("approved_count", 0),
                "validated": report.get("validated_count", 0),
                "uploaded": report.get("uploaded_count", 0),
                "errors": len(report["errors"]),
            },
            sort_keys=True,
        )
    )
    return return_code


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())
