"""Build the local normalized extraction and chunk corpus.

This CLI performs no network or cloud operations.  Source snapshots are treated as
immutable and all generated files are replaced atomically after the run completes.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPOSITORY_ROOT = _SCRIPT_DIR.parent
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

from scripts.ingestion.audit import (  # noqa: E402
    AuditCollector,
    write_atomic,
    write_json,
    write_reports,
)
from scripts.ingestion.chunking import chunk_records, estimate_tokens  # noqa: E402
from scripts.ingestion.models import (  # noqa: E402
    AuditIssue,
    ExtractionRecord,
    MediaType,
    SourceManifest,
    SourceRecord,
)
from scripts.ingestion.normalize import normalize_records  # noqa: E402
from scripts.ingestion.registry import (  # noqa: E402
    RegistryValidationError,
    load_registry,
    validate_registry,
)


def _write_scope_metadata(
    output: Path,
    manifest_path: Path,
    *,
    source_ids: set[str],
    requested_source_ids: set[str],
    complete: bool,
    includes_images: bool,
    chunks_sha256: str | None = None,
) -> None:
    """Record provenance required before a corpus can authorize stale deletion."""

    try:
        manifest_sha256 = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    except OSError:
        manifest_sha256 = None
    write_json(
        output / "corpus-scope.json",
        {
            "schema_version": 1,
            "complete": complete,
            "manifest_sha256": manifest_sha256,
            "chunks_sha256": chunks_sha256,
            "source_ids": sorted(source_ids),
            "requested_source_ids": sorted(requested_source_ids),
            "includes_images": includes_images,
        },
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build the offline IRD extraction corpus")
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-id", action="append", default=[], dest="source_ids")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--skip-images", action="store_true")
    parser.add_argument(
        "--guide-media-base-url",
        default=os.getenv("GUIDE_MEDIA_BASE_URL") or os.getenv("AZURE_GUIDE_MEDIA_BASE_URL"),
        help=(
            "trusted HTTPS origin for approved guide-image URLs; defaults to "
            "GUIDE_MEDIA_BASE_URL or AZURE_GUIDE_MEDIA_BASE_URL"
        ),
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--fail-on-warning", action="store_true")
    return parser


def _source_path(source: SourceRecord, manifest: Path) -> Path:
    return (manifest.parent / source.local_file).resolve()


def _read_cached_records(
    path: Path, source_ids: set[str], force: bool
) -> dict[str, list[ExtractionRecord]]:
    if force or not path.is_file():
        return {}
    result: dict[str, list[ExtractionRecord]] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = ExtractionRecord.model_validate(json.loads(line))
            if record.source_id in source_ids:
                result.setdefault(record.source_id, []).append(record)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        # A corrupt reusable artifact is not an extraction failure; rebuild it.
        return {}
    return result


def _issue_from_exception(source: SourceRecord, exc: Exception) -> AuditIssue:
    as_issue = getattr(exc, "as_issue", None)
    if callable(as_issue):
        issue = as_issue()
        if isinstance(issue, AuditIssue):
            return issue
    code = getattr(exc, "code", None) or (
        "XLSM_INVALID" if source.media_type is MediaType.XLSM else "EXTRACTION_FAILED"
    )
    return AuditIssue(
        severity="error",
        code=str(code),
        message=f"{source.id}: {type(exc).__name__}",
        source_id=source.id,
    )


def _extract(
    source: SourceRecord, manifest: Path, *, ocr_enabled: bool
) -> tuple[list[ExtractionRecord], list[AuditIssue]]:
    path = _source_path(source, manifest)
    try:
        if source.media_type is MediaType.PDF:
            from scripts.ingestion.extractors.pdf import extract_pdf_with_audit

            result = extract_pdf_with_audit(
                path,
                source_id=source.id,
                ocr_enabled=ocr_enabled,
            )
            issues = list(result.issues)
            if not ocr_enabled:
                # Pages omitted by the extractor and sparse native pages both need
                # OCR; make that fact explicit instead of silently indexing them.
                for page in range(1, result.page_count + 1):
                    record = next((item for item in result.records if item.page == page), None)
                    if record is None or len("".join(record.content.split())) < 80:
                        issues.append(
                            AuditIssue(
                                severity="error",
                                code="OCR_REQUIRED",
                                message=f"page {page} requires OCR but --skip-ocr was supplied",
                                source_id=source.id,
                            )
                        )
            return result.records, issues
        if source.media_type is MediaType.HTML:
            from scripts.ingestion.extractors.html import extract_html

            return extract_html(source, base_dir=manifest.parent), []
        if source.media_type is MediaType.XLSM:
            from scripts.ingestion.extractors.xlsm import extract_xlsm

            return extract_xlsm(path, source_id=source.id), []
        raise ValueError(f"unsupported media type: {source.media_type}")
    except Exception as exc:  # source-level failures are aggregated by design
        return [], [_issue_from_exception(source, exc)]


def _record_warnings(records: list[ExtractionRecord], collector: AuditCollector) -> None:
    for record in records:
        for warning in record.warnings:
            collector.warning(
                warning.value,
                f"{warning.value} on record",
                source_id=record.source_id,
                record_id=record.record_id,
            )


def _image_candidates(
    manifest_path: Path, output: Path, *, public_origin: str | None = None
) -> tuple[list[Any], list[AuditIssue]]:
    """Render selected image candidates and load explicitly approved image chunks."""

    issues: list[AuditIssue] = []

    try:
        module = importlib.import_module("scripts.ingestion.images")
    except ModuleNotFoundError:
        write_json(output / "image-candidates.json", {"schema_version": 1, "candidates": []})
        return [], issues

    render = getattr(module, "render_image_candidates", None) or getattr(
        module, "render_candidates", None
    )
    if callable(render):
        try:
            render(
                manifest_path,
                output / "rendered-images",
                metadata_path=output / "image-candidates.json",
            )
        except (OSError, ValueError, RuntimeError) as exc:
            issues.append(
                AuditIssue(
                    severity="error",
                    code="IMAGE_CANDIDATE_FAILED",
                    message=f"image candidate rendering failed: {type(exc).__name__}",
                )
            )
            write_json(output / "image-candidates.json", {"schema_version": 1, "candidates": []})
    else:
        write_json(output / "image-candidates.json", {"schema_version": 1, "candidates": []})

    image_manifest = manifest_path.parent / "guide-images.yaml"
    build_chunks = getattr(module, "build_guide_image_chunks", None)
    if image_manifest.is_file() and callable(build_chunks):
        try:
            return list(
                build_chunks(
                    image_manifest,
                    source_manifest=manifest_path,
                    public_origin=public_origin,
                )
            ), issues
        except (OSError, ValueError, RuntimeError) as exc:
            issues.append(
                AuditIssue(
                    severity="error",
                    code="IMAGE_CHUNK_FAILED",
                    message=f"approved guide-image chunking failed: {type(exc).__name__}",
                )
            )
    return [], issues


def build_corpus(args: argparse.Namespace) -> int:
    manifest_path = args.manifest.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    requested = set(args.source_ids)
    collector = AuditCollector()
    try:
        registry: SourceManifest = load_registry(manifest_path)
    except (OSError, RegistryValidationError, ValueError):
        collector.error("REGISTRY_INVALID", "source registry could not be loaded")
        write_atomic(output / "extraction.jsonl", "")
        write_atomic(output / "chunks.jsonl", "")
        write_reports(output, [], [], [], collector.issues)
        print("Corpus build failed: invalid source registry")
        return 1

    validation = validate_registry(
        registry, repository_root=_REPOSITORY_ROOT, manifest_path=manifest_path
    )
    for message in validation.get("warnings", []):
        collector.warning("REGISTRY_WARNING", message)
    for message in validation.get("errors", []):
        collector.error("REGISTRY_INVALID", message)
    if not validation.get("valid", False):
        write_atomic(output / "extraction.jsonl", "")
        write_atomic(output / "chunks.jsonl", "")
        write_reports(output, [], [], [], collector.issues)
        print(f"Corpus build failed: {len(validation.get('errors', []))} registry error(s)")
        return 1

    known = {source.id for source in registry.sources}
    unknown = requested - known
    if unknown:
        for source_id in sorted(unknown):
            collector.error(
                "UNKNOWN_SOURCE", f"unknown source ID: {source_id}", source_id=source_id
            )
        write_atomic(output / "extraction.jsonl", "")
        write_atomic(output / "chunks.jsonl", "")
        write_reports(output, [], [], [], collector.issues)
        print(f"Corpus build failed: unknown source ID(s): {', '.join(sorted(unknown))}")
        return 1

    sources = [
        source
        for source in registry.sources
        if source.review_status.value == "approved"
        and source.status.value != "excluded"
        and (not requested or source.id in requested)
    ]
    cached = _read_cached_records(
        output / "extraction.jsonl", {source.id for source in sources}, args.force
    )
    all_records: list[ExtractionRecord] = []
    for source in sources:
        if source.id in cached:
            records = normalize_records(cached[source.id])
            issues: list[AuditIssue] = []
        else:
            records, issues = _extract(source, manifest_path, ocr_enabled=not args.skip_ocr)
            records = normalize_records(records)
        collector.extend(issues)
        _record_warnings(records, collector)
        all_records.extend(records)
        print(f"{source.id}: {len(records)} extraction record(s)")

    chunks: list[Any] = []
    for source in sources:
        source_records = [record for record in all_records if record.source_id == source.id]
        chunks.extend(chunk_records(source_records, source))

    if not args.skip_images:
        image_chunks, image_issues = _image_candidates(
            manifest_path, output, public_origin=args.guide_media_base_url
        )
        chunks.extend(image_chunks)
        collector.extend(image_issues)

    extraction_lines = "".join(
        json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
        for record in all_records
    )
    chunks_lines = "".join(
        json.dumps(
            chunk.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        + "\n"
        for chunk in chunks
    )
    write_atomic(output / "extraction.jsonl", extraction_lines)
    write_atomic(output / "chunks.jsonl", chunks_lines)
    approved_source_ids = {
        source.id
        for source in registry.sources
        if source.review_status.value == "approved" and source.status.value != "excluded"
    }
    complete = (
        not args.skip_images
        and (not requested or requested == approved_source_ids)
        and not any(issue.severity == "error" for issue in collector.issues)
    )
    _write_scope_metadata(
        output,
        manifest_path,
        source_ids={source.id for source in sources},
        requested_source_ids=requested,
        complete=complete,
        includes_images=not args.skip_images,
        chunks_sha256=hashlib.sha256(chunks_lines.encode("utf-8")).hexdigest(),
    )
    audit, _summary = write_reports(output, sources, all_records, chunks, collector.issues)

    embedding_tokens = sum(estimate_tokens(chunk.embedding_text) for chunk in chunks)
    print(
        f"Corpus build {'completed with errors' if audit['error_count'] else 'completed'}: "
        f"{len(sources)} source(s), {len(all_records)} extraction record(s), "
        f"{len(chunks)} chunk(s), ~{embedding_tokens} embedding tokens"
    )
    if audit["error_count"] or (args.fail_on_warning and audit["warning_count"]):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    return build_corpus(_parser().parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
