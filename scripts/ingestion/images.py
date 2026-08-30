"""Render reviewed guide-page image candidates from the local source registry.

Image rendering is deliberately manifest driven.  A source only contributes pages listed
in its ``render_pages`` field and each selected page is rendered as one complete page;
embedded PDF image objects are never inspected or published.  Candidate metadata is kept
separate from the source registry so that a reviewer can explicitly approve an image before
it is converted into a searchable ``guide_image`` chunk.

This module has no cloud or upload path.  The public functions are intentionally small so
the corpus builder (WP-05) can call ``render_image_candidates`` and
``build_guide_image_chunks`` without importing the image CLI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

try:  # Running as ``python -m scripts.ingestion.images``.
    from .models import (
        ChunkRecord,
        DocumentStatus,
        ImageRecord,
        ReviewStatus,
        SourceManifest,
        SourceRecord,
    )
    from .registry import load_registry
except ImportError:  # Running the file directly from the ingestion directory.
    from models import (  # type: ignore
        ChunkRecord,
        DocumentStatus,
        ImageRecord,
        ReviewStatus,
        SourceManifest,
        SourceRecord,
    )
    from registry import load_registry  # type: ignore

SCHEMA_VERSION = 1
DEFAULT_LONG_EDGE = 1600
DEFAULT_WEBP_QUALITY = 85
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_IMAGE_ID_MAX_LENGTH = 100


class ImageRenderError(RuntimeError):
    """Raised when a selected PDF page cannot be rendered safely."""


def _slug(value: str, *, limit: int = _IMAGE_ID_MAX_LENGTH) -> str:
    result = _SLUG_RE.sub("-", value.lower()).strip("-")
    return (result or "guide-image")[:limit].rstrip("-")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _normalise_page_text(value: str | None) -> str:
    if not value:
        return ""
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = "\n".join(line.rstrip() for line in value.splitlines())
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _source_manifest_and_base(
    manifest: SourceManifest | str | Path,
) -> tuple[SourceManifest, Path]:
    if isinstance(manifest, SourceManifest):
        # A model does not retain the path it was loaded from.  The normal WP-05 invocation
        # runs from ``backend/`` while registry paths are relative to ``data/metadata``;
        # discover that stable repository location for in-memory registries.
        current = Path.cwd().resolve()
        for candidate in (current, *current.parents):
            metadata_dir = candidate / "data" / "metadata"
            if metadata_dir.is_dir():
                return manifest, metadata_dir
        return manifest, current
    path = Path(manifest)
    return load_registry(path), path.resolve().parent


def _resolve_source_path(source: SourceRecord, manifest_base: Path) -> Path:
    """Resolve a registry path while keeping the local source boundary intact."""

    path = (manifest_base / source.local_file).resolve()
    # ``SourceRecord`` paths normally resolve under the repository.  The check here is
    # intentionally limited to preventing accidental filesystem-wide reads when callers
    # construct a temporary manifest with ``..`` segments.
    if not path.exists():
        raise ImageRenderError(f"source file does not exist for {source.id}: {path}")
    return path


def _page_texts(path: Path, pages: Sequence[int]) -> dict[int, str]:
    """Extract only selected page text, without OCR or network access."""

    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path), strict=False)
    except Exception as exc:  # pragma: no cover - error details vary by pypdf version
        raise ImageRenderError(f"unable to read PDF for image candidates: {path.name}") from exc
    result: dict[int, str] = {}
    for page_number in pages:
        try:
            result[page_number] = _normalise_page_text(reader.pages[page_number - 1].extract_text())
        except Exception as exc:  # pragma: no cover - malformed page implementation detail
            raise ImageRenderError(
                f"unable to extract page text for image candidate {path.name} p{page_number}"
            ) from exc
    return result


def _render_webp(
    path: Path, page_number: int, *, long_edge: int, quality: int
) -> tuple[bytes, int, int]:
    """Render one complete PDF page to deterministic, metadata-free WebP bytes."""

    import pymupdf as fitz
    from PIL import Image

    try:
        document = fitz.open(str(path))
    except Exception as exc:  # pragma: no cover - renderer errors depend on local PDF build
        raise ImageRenderError(f"unable to open PDF for image rendering: {path.name}") from exc
    try:
        if page_number < 1 or page_number > document.page_count:
            raise ImageRenderError(
                f"selected image page is outside PDF bounds: {path.name} p{page_number} "
                f"(page_count={document.page_count})"
            )
        page = document.load_page(page_number - 1)
        rect = page.rect
        source_long_edge = max(float(rect.width), float(rect.height))
        if source_long_edge <= 0:
            raise ImageRenderError(
                f"PDF page has no renderable dimensions: {path.name} p{page_number}"
            )
        scale = long_edge / source_long_edge
        # An explicit matrix and alpha=False avoid renderer defaults and transparent-page
        # differences between PyMuPDF releases.
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        output = tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024)
        try:
            image.save(
                output,
                format="WEBP",
                quality=quality,
                method=6,
                lossless=False,
                exact=True,
                exif=b"",
                icc_profile=None,
            )
            output.seek(0)
            data = output.read()
        finally:
            output.close()
        return data, image.width, image.height
    finally:
        document.close()


def _candidate_id(source: SourceRecord, page: int) -> str:
    return _slug(f"{source.id}-page-{page}")


def _candidate_metadata(
    source: SourceRecord,
    *,
    page: int,
    page_text: str,
    rendered_path: Path,
    binary_sha256: str,
    byte_size: int,
    width: int,
    height: int,
) -> dict[str, Any]:
    """Build a draft manifest entry using only registry and selected-page text."""

    title = f"{source.title} — page {page}"
    # This deliberately describes provenance, not an inferred UI action.  A reviewer can
    # replace it with a more specific description after inspecting the rendered page.
    alt_text = f"Page {page} from {source.title}"
    first_text = next((line.strip() for line in page_text.splitlines() if line.strip()), "")
    caption = first_text[:300] if first_text else f"Page {page} from {source.title}."
    return {
        "image_id": _candidate_id(source, page),
        "public_url": None,
        "title": title,
        "alt_text": alt_text,
        "caption": caption,
        "source_id": source.id,
        "source_url": str(source.source_url),
        "page": page,
        "workflow_id": source.workflow_ids[0] if source.workflow_ids else None,
        "tax_types": list(source.tax_types),
        "taxpayer_types": list(source.taxpayer_types),
        "effective_from": source.effective_from.isoformat() if source.effective_from else None,
        "effective_to": source.effective_to.isoformat() if source.effective_to else None,
        "status": source.status.value,
        "review_status": ReviewStatus.DRAFT.value,
        "binary_sha256": binary_sha256,
        "byte_size": byte_size,
        "width": width,
        "height": height,
        "rendered_path": rendered_path.as_posix(),
        "page_text": page_text,
    }


def render_image_candidates(
    source_manifest: SourceManifest | str | Path,
    output_dir: str | Path,
    *,
    metadata_path: str | Path | None = None,
    source_ids: Iterable[str] | None = None,
    long_edge: int = DEFAULT_LONG_EDGE,
    quality: int = DEFAULT_WEBP_QUALITY,
) -> list[dict[str, Any]]:
    """Render selected source pages and return draft candidate metadata.

    Only PDF sources with an explicit non-empty ``render_pages`` list are considered.  The
    output filename is based on the candidate ID and the first 16 hexadecimal characters of
    the binary SHA-256.  Existing bytes are reused when the hash and dimensions match,
    which makes repeated local runs stable and cheap.
    """

    if long_edge < 1:
        raise ValueError("long_edge must be positive")
    if not 1 <= quality <= 100:
        raise ValueError("quality must be between 1 and 100")
    registry, manifest_base = _source_manifest_and_base(source_manifest)
    wanted = set(source_ids or ())
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    candidates: list[dict[str, Any]] = []

    for source in registry.sources:
        if wanted and source.id not in wanted:
            continue
        if (
            source.media_type.value != "application/pdf"
            or not source.render_pages
            or source.review_status is not ReviewStatus.APPROVED
            or source.status is DocumentStatus.EXCLUDED
        ):
            continue
        path = _resolve_source_path(source, manifest_base)
        page_texts = _page_texts(path, source.render_pages)
        for page in source.render_pages:
            data, width, height = _render_webp(path, page, long_edge=long_edge, quality=quality)
            digest = _sha256_bytes(data)
            image_id = _candidate_id(source, page)
            filename = f"{image_id}.{digest[:16]}.webp"
            destination = output / filename
            if not destination.exists() or destination.read_bytes() != data:
                destination.write_bytes(data)
            candidates.append(
                _candidate_metadata(
                    source,
                    page=page,
                    page_text=page_texts.get(page, ""),
                    rendered_path=destination,
                    binary_sha256=digest,
                    byte_size=len(data),
                    width=width,
                    height=height,
                )
            )

    candidates.sort(key=lambda item: (item["source_id"], item["page"] or 0, item["image_id"]))
    if metadata_path is not None:
        write_candidate_metadata(candidates, metadata_path)
    return candidates


def write_candidate_metadata(candidates: Sequence[Mapping[str, Any]], path: str | Path) -> None:
    """Write deterministic local candidate metadata as JSON (never a public URL)."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, "candidates": list(candidates)}
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(target)


def _load_image_manifest(path: str | Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    target = Path(path)
    with target.open(encoding="utf-8") as stream:
        payload = yaml.safe_load(stream) or {}
    if not isinstance(payload, dict):
        raise ValueError("guide image manifest must contain a mapping")
    if payload.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
        raise ValueError("unsupported guide image manifest schema_version")
    entries = payload.get("images", [])
    if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
        raise ValueError("guide image manifest images must be a list of mappings")
    return payload, entries


def _image_record(entry: Mapping[str, Any]) -> ImageRecord:
    allowed = {
        "image_id",
        "public_url",
        "title",
        "alt_text",
        "caption",
        "source_id",
        "source_url",
        "page",
        "workflow_id",
        "tax_types",
        "taxpayer_types",
        "effective_from",
        "effective_to",
        "status",
        "review_status",
        "binary_sha256",
        "byte_size",
        "width",
        "height",
    }
    record = ImageRecord.model_validate(
        {key: value for key, value in entry.items() if key in allowed}
    )
    if record.page is None:
        raise ValueError(f"image page is required: {record.image_id}")
    return record


def load_approved_image_records(path: str | Path) -> list[tuple[ImageRecord, dict[str, Any]]]:
    """Load only approved image records, retaining optional reviewed text metadata."""

    _, entries = _load_image_manifest(path)
    approved: list[tuple[ImageRecord, dict[str, Any]]] = []
    for raw in entries:
        record = _image_record(raw)
        if record.review_status is ReviewStatus.APPROVED:
            approved.append((record, dict(raw)))
    return approved


def _source_by_id(
    source_manifest: SourceManifest | str | Path | None,
) -> dict[str, SourceRecord]:
    if source_manifest is None:
        return {}
    registry, _ = _source_manifest_and_base(source_manifest)
    return {source.id: source for source in registry.sources}


def _validate_public_image_url(public_url: str | None, public_origin: str | None) -> None:
    """Validate the immutable public-media URL and optional trusted origin."""

    if not public_url:
        raise ValueError("approved image public_url is required")
    image = urlsplit(public_url)
    if (
        image.scheme != "https"
        or not image.hostname
        or image.username
        or image.password
        or image.query
        or image.fragment
        or not image.path.lower().endswith(".webp")
    ):
        raise ValueError("approved image public_url must be a credential-free HTTPS WebP URL")
    if public_origin is None:
        return
    origin = urlsplit(public_origin.rstrip("/"))
    if (
        origin.scheme != "https"
        or not origin.hostname
        or origin.username
        or origin.password
        or origin.query
        or origin.fragment
        or image.scheme != origin.scheme
        or image.netloc != origin.netloc
    ):
        raise ValueError("approved image public_url is outside the configured media origin")
    origin_path = origin.path.rstrip("/")
    if origin_path and not (image.path == origin_path or image.path.startswith(f"{origin_path}/")):
        raise ValueError("approved image public_url is outside the configured media origin path")


def build_guide_image_chunks(
    image_manifest: str | Path | Sequence[Mapping[str, Any]],
    *,
    source_manifest: SourceManifest | str | Path | None = None,
    public_origin: str | None = None,
    media_origin: str | None = None,
) -> list[ChunkRecord]:
    """Convert explicitly approved image entries into ``guide_image`` chunks.

    Draft and rejected entries are ignored before chunk construction.  The function accepts
    either the YAML manifest path or an in-memory list, enabling WP-05 to integrate image
    chunks without knowing how candidates were rendered.
    """

    if isinstance(image_manifest, (str, Path)):
        approved = load_approved_image_records(image_manifest)
    else:
        approved = []
        for raw in image_manifest:
            record = _image_record(raw)
            if record.review_status is ReviewStatus.APPROVED:
                approved.append((record, dict(raw)))

    if public_origin is not None and media_origin is not None and public_origin != media_origin:
        raise ValueError("public_origin and media_origin must not disagree")
    trusted_origin = public_origin if public_origin is not None else media_origin
    if approved and source_manifest is None:
        raise ValueError("source_manifest is required to validate approved image provenance")
    sources = _source_by_id(source_manifest)
    chunks: list[ChunkRecord] = []
    for image, raw in approved:
        source = sources.get(image.source_id)
        if source is None:
            raise ValueError(f"approved image references unknown source: {image.source_id}")
        if source.review_status is not ReviewStatus.APPROVED:
            raise ValueError(f"approved image source is not approved: {source.id}")
        if source.status is DocumentStatus.EXCLUDED:
            raise ValueError(f"approved image source is excluded: {source.id}")
        allowed_source_urls = {str(source.source_url)}
        if source.final_url is not None:
            allowed_source_urls.add(str(source.final_url))
        if str(image.source_url) not in allowed_source_urls:
            raise ValueError(
                f"approved image source_url does not match registry source: {image.image_id}"
            )
        if source.render_pages and image.page not in source.render_pages:
            raise ValueError(
                f"approved image page is not selected by registry source: {image.image_id}"
            )
        if image.status is DocumentStatus.EXCLUDED:
            raise ValueError(f"excluded image cannot become a guide chunk: {image.image_id}")
        _validate_public_image_url(
            str(image.public_url) if image.public_url else None,
            trusted_origin,
        )
        page_text = _normalise_page_text(str(raw.get("page_text", "")))
        workflow = image.workflow_id or ""
        content_parts = [image.title, image.alt_text]
        if image.caption:
            content_parts.append(image.caption)
        if workflow:
            content_parts.append(f"Workflow: {workflow}")
        if page_text:
            content_parts.append(f"Reviewed page text:\n{page_text}")
        content = "\n".join(content_parts)
        document_title = source.title if source else image.title
        document_type = source.document_type.value if source else "return_guide"
        tax_types = list(image.tax_types) or (list(source.tax_types) if source else [])
        taxpayer_types = list(image.taxpayer_types) or (
            list(source.taxpayer_types) if source else []
        )
        authority_level = source.authority_level if source else None
        authority_rank = source.authority_rank if source else None
        source_hash = source.sha256 if source else None
        embedding_lines = [
            f"Document: {document_title}",
            f"Document type: {document_type}",
        ]
        if tax_types:
            embedding_lines.append(f"Tax type: {', '.join(tax_types)}")
        if image.page:
            embedding_lines.append(f"Page: {image.page}")
        if workflow:
            embedding_lines.append(f"Workflow: {workflow}")
        embedding_text = "\n".join(embedding_lines) + f"\n\n{content}"
        locator = f"{image.source_id}:page:{image.page or 0}:image:{image.image_id}"
        hash_input = json.dumps(
            {"content": content, "locator": locator},
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        chunk_hash = _sha256_bytes(hash_input)
        chunk_id = f"guide-image-{image.image_id}-{chunk_hash[:24]}"
        chunks.append(
            ChunkRecord(
                id=chunk_id,
                source_id=image.source_id,
                content=content,
                embedding_text=embedding_text,
                content_type="guide_image",
                title=image.title,
                source_url=image.source_url,
                page=image.page,
                page_end=image.page,
                workflow_id=image.workflow_id,
                authority_level=authority_level,
                authority_rank=authority_rank,
                tax_types=tax_types,
                taxpayer_types=taxpayer_types,
                effective_from=image.effective_from,
                effective_to=image.effective_to,
                status=image.status,
                source_hash=source_hash,
                chunk_hash=chunk_hash,
                image_id=image.image_id,
                image_url=image.public_url,
                image_alt_text=image.alt_text,
                image_caption=image.caption,
            )
        )
    return sorted(chunks, key=lambda chunk: chunk.id)


# Friendly aliases for the corpus builder and downstream workers.
render_candidates = render_image_candidates
guide_image_chunks = build_guide_image_chunks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="data/metadata/sources.yaml", help="source registry")
    parser.add_argument(
        "--images-manifest",
        default="data/metadata/guide-images.yaml",
        help="guide-image approval manifest (not modified unless --write-draft-manifest)",
    )
    parser.add_argument(
        "--output-dir",
        default="data/processed/corpus/rendered-images",
        help="directory for local rendered WebP candidates",
    )
    parser.add_argument(
        "--candidates-output",
        default="data/processed/corpus/image-candidates.json",
        help="JSON metadata output for local candidates",
    )
    parser.add_argument("--source-id", action="append", dest="source_ids")
    parser.add_argument("--long-edge", type=int, default=DEFAULT_LONG_EDGE)
    parser.add_argument("--quality", type=int, default=DEFAULT_WEBP_QUALITY)
    parser.add_argument(
        "--chunks-output",
        help="optional JSONL output for approved guide-image ChunkRecord objects",
    )
    args = parser.parse_args(argv)
    try:
        candidates = render_image_candidates(
            args.manifest,
            args.output_dir,
            metadata_path=args.candidates_output,
            source_ids=args.source_ids,
            long_edge=args.long_edge,
            quality=args.quality,
        )
        chunks = build_guide_image_chunks(args.images_manifest, source_manifest=args.manifest)
        if args.chunks_output:
            target = Path(args.chunks_output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                "".join(chunk.model_dump_json() + "\n" for chunk in chunks), encoding="utf-8"
            )
        print(json.dumps({"candidates": len(candidates), "approved_chunks": len(chunks)}))
        return 0
    except (OSError, ValueError, ImageRenderError) as exc:
        print(f"image ingestion error: {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI
    raise SystemExit(main())


__all__ = [
    "DEFAULT_LONG_EDGE",
    "DEFAULT_WEBP_QUALITY",
    "ImageRenderError",
    "build_guide_image_chunks",
    "guide_image_chunks",
    "load_approved_image_records",
    "render_candidates",
    "render_image_candidates",
    "write_candidate_metadata",
]
