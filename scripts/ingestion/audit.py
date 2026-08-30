"""Audit aggregation and safe machine-readable report writers."""

from __future__ import annotations

import json
import os
import tempfile
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .models import AuditIssue, ChunkRecord, ExtractionRecord, SourceRecord


def validate_chunk_metadata(
    chunks: Iterable[ChunkRecord | Mapping[str, Any]],
) -> list[AuditIssue]:
    """Validate provenance and locators before chunks are sent for embedding.

    ``ChunkRecord`` intentionally permits nullable metadata while records are being
    assembled.  The embedding boundary is stricter: every chunk must identify its
    source URL and immutable source hash, and must retain at least one precise
    locator (page, section, spreadsheet range, or image identity).
    """

    issues: list[AuditIssue] = []
    for value in chunks:
        try:
            chunk = value if isinstance(value, ChunkRecord) else ChunkRecord.model_validate(value)
        except (TypeError, ValueError) as exc:
            raw_source = value.get("source_id") if isinstance(value, Mapping) else None
            raw_id = value.get("id") if isinstance(value, Mapping) else None
            issues.append(
                AuditIssue(
                    severity="error",
                    code="INVALID_CHUNK_METADATA",
                    message=f"chunk metadata cannot be validated ({type(exc).__name__})",
                    source_id=str(raw_source) if raw_source else None,
                    record_id=str(raw_id) if raw_id else None,
                )
            )
            continue

        if chunk.source_url is None or not chunk.source_hash:
            missing = []
            if chunk.source_url is None:
                missing.append("source_url")
            if not chunk.source_hash:
                missing.append("source_hash")
            issues.append(
                AuditIssue(
                    severity="error",
                    code="MISSING_CHUNK_PROVENANCE",
                    message=f"chunk is missing required provenance: {', '.join(missing)}",
                    source_id=chunk.source_id,
                    record_id=chunk.id,
                )
            )

        has_page_locator = chunk.page is not None
        has_section_locator = bool(chunk.section)
        has_sheet_locator = bool(chunk.sheet and chunk.cell_range)
        has_image_locator = bool(chunk.image_id and chunk.page is not None)
        if not (has_page_locator or has_section_locator or has_sheet_locator or has_image_locator):
            issues.append(
                AuditIssue(
                    severity="error",
                    code="MISSING_CHUNK_LOCATOR",
                    message=(
                        "chunk must retain a page, section, sheet/cell range, or image locator"
                    ),
                    source_id=chunk.source_id,
                    record_id=chunk.id,
                )
            )
        elif bool(chunk.sheet) != bool(chunk.cell_range):
            issues.append(
                AuditIssue(
                    severity="error",
                    code="INCOMPLETE_CHUNK_LOCATOR",
                    message="spreadsheet chunks require both sheet and cell_range",
                    source_id=chunk.source_id,
                    record_id=chunk.id,
                )
            )
    return issues


def _validated_issue_list(
    chunks: list[ChunkRecord], issues: Iterable[AuditIssue]
) -> list[AuditIssue]:
    """Append generated issues once while preserving caller-provided issue order."""

    result = list(issues)
    existing = {(issue.code, issue.source_id, issue.record_id, issue.message) for issue in result}
    for issue in validate_chunk_metadata(chunks):
        key = (issue.code, issue.source_id, issue.record_id, issue.message)
        if key not in existing:
            result.append(issue)
            existing.add(key)
    return result


def write_atomic(path: str | Path, payload: str | bytes) -> None:
    """Write a sibling temporary file and atomically replace *path*."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(payload, bytes) else "w"
    kwargs: dict[str, Any] = {} if mode == "wb" else {"encoding": "utf-8"}
    fd, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    try:
        with os.fdopen(fd, mode, **kwargs) as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    """Write stable UTF-8 JSON atomically."""

    write_atomic(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


class AuditCollector:
    """Collect source errors and record warnings without exposing source bodies."""

    def __init__(self) -> None:
        self.issues: list[AuditIssue] = []

    def add(self, issue: AuditIssue | Mapping[str, Any]) -> None:
        self.issues.append(
            issue if isinstance(issue, AuditIssue) else AuditIssue.model_validate(issue)
        )

    def error(
        self, code: str, message: str, *, source_id: str | None = None, record_id: str | None = None
    ) -> None:
        self.add(
            AuditIssue(
                severity="error",
                code=code,
                message=message,
                source_id=source_id,
                record_id=record_id,
            )
        )

    def warning(
        self, code: str, message: str, *, source_id: str | None = None, record_id: str | None = None
    ) -> None:
        self.add(
            AuditIssue(
                severity="warning",
                code=code,
                message=message,
                source_id=source_id,
                record_id=record_id,
            )
        )

    def extend(self, issues: Iterable[AuditIssue]) -> None:
        for issue in issues:
            self.add(issue)


def audit_report(
    sources: Iterable[SourceRecord],
    records: Iterable[ExtractionRecord],
    chunks: Iterable[ChunkRecord],
    issues: Iterable[AuditIssue] = (),
) -> dict[str, Any]:
    """Return the complete deterministic ``audit.json`` payload."""

    source_list = list(sources)
    record_list = list(records)
    chunk_list = list(chunks)
    issue_list = _validated_issue_list(chunk_list, issues)
    warning_counts = Counter(issue.code for issue in issue_list if issue.severity == "warning")
    error_counts = Counter(issue.code for issue in issue_list if issue.severity == "error")
    source_counts = Counter(chunk.source_id for chunk in chunk_list)
    return {
        "schema_version": 1,
        "source_count": len(source_list),
        "approved_source_count": len(source_list),
        "extraction_record_count": len(record_list),
        "chunk_count": len(chunk_list),
        "extraction_jsonl_line_count": len(record_list),
        "chunks_jsonl_line_count": len(chunk_list),
        "error_count": sum(error_counts.values()),
        "warning_count": sum(warning_counts.values()),
        "errors_by_code": dict(sorted(error_counts.items())),
        "warnings_by_code": dict(sorted(warning_counts.items())),
        "chunks_by_source": dict(sorted(source_counts.items())),
        "issues": [issue.model_dump(mode="json") for issue in issue_list],
    }


def source_summary(
    sources: Iterable[SourceRecord],
    records: Iterable[ExtractionRecord],
    chunks: Iterable[ChunkRecord],
    issues: Iterable[AuditIssue] = (),
) -> dict[str, Any]:
    """Return per-source counts and controlled warnings/errors."""

    record_list, chunk_list, issue_list = list(records), list(chunks), list(issues)
    result: list[dict[str, Any]] = []
    for source in sources:
        source_records = [record for record in record_list if record.source_id == source.id]
        source_chunks = [chunk for chunk in chunk_list if chunk.source_id == source.id]
        source_issues = [issue for issue in issue_list if issue.source_id == source.id]
        methods = Counter(record.extraction_method.value for record in source_records)
        result.append(
            {
                "source_id": source.id,
                "title": source.title,
                "source_url": str(source.source_url),
                "local_file": source.local_file,
                "status": source.status.value,
                "document_type": source.document_type.value,
                "media_type": source.media_type.value,
                "extraction_record_count": len(source_records),
                "chunk_count": len(source_chunks),
                "extraction_methods": dict(sorted(methods.items())),
                "warning_count": sum(issue.severity == "warning" for issue in source_issues),
                "error_count": sum(issue.severity == "error" for issue in source_issues),
                "issues": [issue.model_dump(mode="json") for issue in source_issues],
            }
        )
    return {"schema_version": 1, "sources": result}


def write_reports(
    output_dir: str | Path,
    sources: Iterable[SourceRecord],
    records: Iterable[ExtractionRecord],
    chunks: Iterable[ChunkRecord],
    issues: Iterable[AuditIssue] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Write audit and source summary reports, returning both payloads."""

    source_list, record_list, chunk_list, issue_list = (
        list(sources),
        list(records),
        list(chunks),
        list(issues),
    )
    issue_list = _validated_issue_list(chunk_list, issue_list)
    audit = audit_report(source_list, record_list, chunk_list, issue_list)
    summary = source_summary(source_list, record_list, chunk_list, issue_list)
    directory = Path(output_dir)
    write_json(directory / "audit.json", audit)
    write_json(directory / "source-summary.json", summary)
    return audit, summary


__all__ = [
    "AuditCollector",
    "audit_report",
    "validate_chunk_metadata",
    "source_summary",
    "write_atomic",
    "write_json",
    "write_reports",
]
