"""Structure-aware, deterministic chunk production for extracted records."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .models import ChunkRecord, ExtractionRecord, SourceRecord

PREFERRED_MIN_TOKENS = 600
PREFERRED_MAX_TOKENS = 900
HARD_MAX_TOKENS = 1050
MIN_MERGE_TOKENS = 250
OVERLAP_TOKENS = 100


def estimate_tokens(value: str) -> int:
    """Conservative offline estimate required by the corpus contract."""

    return (len(value) + 3) // 4


def _as_record(value: ExtractionRecord | Mapping[str, Any]) -> ExtractionRecord:
    return value if isinstance(value, ExtractionRecord) else ExtractionRecord.model_validate(value)


def _split_sentences(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", value) if part.strip()]


def _hard_split(value: str, limit: int, overlap: int) -> list[str]:
    pieces: list[str] = []
    start = 0
    while start < len(value):
        end = min(len(value), start + limit)
        if end < len(value):
            boundary = value.rfind(" ", start + limit // 2, end)
            if boundary > start:
                end = boundary
        piece = value[start:end].strip()
        if piece:
            pieces.append(piece)
        if end >= len(value):
            break
        next_start = max(start + 1, end - overlap)
        start = next_start
    return pieces


def split_record(record: ExtractionRecord, *, max_tokens: int = HARD_MAX_TOKENS) -> list[str]:
    """Split a record on paragraphs, then sentences, then character boundaries."""

    limit = max_tokens * 4
    text = record.content
    if len(text) <= limit:
        return [text]
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", text) if part.strip()]
    units: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph) <= limit:
            units.append(paragraph)
        else:
            for sentence in _split_sentences(paragraph):
                units.extend(
                    [sentence]
                    if len(sentence) <= limit
                    else _hard_split(sentence, limit, OVERLAP_TOKENS * 4)
                )
    pieces: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}\n\n{unit}" if current else unit
        if current and len(candidate) > limit:
            pieces.append(current)
            current = unit
        else:
            current = candidate
    if current:
        pieces.append(current)
    # If a pathological input has no useful delimiters, always enforce the hard cap.
    result: list[str] = []
    for piece in pieces:
        result.extend(
            _hard_split(piece, limit, OVERLAP_TOKENS * 4) if len(piece) > limit else [piece]
        )
    if record.content_kind == "table" and len(result) > 1 and record.table_headers:
        prefix = "Headers: " + " | ".join(record.table_headers)
        result = [piece if piece.startswith(prefix) else f"{prefix}\n{piece}" for piece in result]
    return result


def _compatible(left: ExtractionRecord, right: ExtractionRecord) -> bool:
    return (
        left.source_id == right.source_id
        and left.page == right.page
        and left.page_end == right.page_end
        and left.section == right.section
        and left.content_kind == right.content_kind
        and left.sheet == right.sheet
        and left.cell_range == right.cell_range
        and left.table_headers == right.table_headers
    )


def _locator(records: list[ExtractionRecord]) -> str:
    first, last = records[0], records[-1]
    parts = [
        f"o{first.ordinal}" if first.ordinal == last.ordinal else f"o{first.ordinal}-{last.ordinal}"
    ]
    if first.page is not None:
        parts.append(f"p{first.page}-{last.page_end or first.page}")
    if first.sheet:
        parts.append(f"sheet-{first.sheet}")
    if first.cell_range:
        parts.append(f"range-{first.cell_range}")
    if first.section:
        parts.append(f"section-{first.section}")
    return ":".join(parts)


def _safe_locator(records: list[ExtractionRecord]) -> str:
    """Use a key-safe locator label while retaining all locator dimensions."""

    return re.sub(r"[^a-z0-9]+", "-", _locator(records).lower()).strip("-")


def _hash_content(content: str, records: list[ExtractionRecord]) -> str:
    metadata = {
        "source_id": records[0].source_id,
        "locator": _locator(records),
        "page": records[0].page,
        "page_end": records[-1].page_end,
        "section": records[0].section,
        "sheet": records[0].sheet,
        "cell_range": records[0].cell_range,
        "table_headers": records[0].table_headers,
    }
    payload = json.dumps(
        {"content": content, "metadata": metadata}, sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def embedding_text(source: SourceRecord, record: ExtractionRecord, content: str) -> str:
    """Build compact retrieval context followed by verbatim normalized content."""

    lines = [
        f"Document: {source.title}",
        f"Document type: {source.document_type.value}",
    ]
    if source.tax_types:
        lines.append("Tax type: " + ", ".join(source.tax_types))
    if source.tax_year:
        lines.append(f"Tax year: {source.tax_year}")
    section = ": ".join(record.title_path) if record.title_path else record.section
    if section:
        lines.append(f"Section: {section}")
    if source.form_code:
        lines.append(f"Form: {source.form_code}")
    if source.aliases:
        lines.append("Aliases: " + ", ".join(source.aliases))
    return "\n".join([*lines, "", content])


def _make_chunk(source: SourceRecord, records: list[ExtractionRecord], content: str) -> ChunkRecord:
    first, last = records[0], records[-1]
    digest = _hash_content(content, records)
    locator = _safe_locator(records)
    return ChunkRecord(
        id=f"{source.id}-{locator}-{digest[:24]}",
        source_id=source.id,
        content=content,
        embedding_text=embedding_text(source, first, content),
        content_type=source.document_type.value,
        title=source.title,
        source_url=source.source_url,
        page=first.page,
        page_end=last.page_end or first.page,
        section=first.section,
        sheet=first.sheet,
        cell_range=first.cell_range,
        published_date=source.published_date,
        effective_from=source.effective_from,
        effective_to=source.effective_to,
        tax_year=source.tax_year,
        document_version=source.document_version,
        workflow_id=source.workflow_ids[0] if source.workflow_ids else None,
        authority_level=source.authority_level,
        authority_rank=source.authority_rank,
        tax_types=source.tax_types,
        taxpayer_types=source.taxpayer_types,
        language=source.language,
        status=source.status,
        supersedes=source.supersedes,
        form_code=source.form_code,
        tags=source.tags,
        source_hash=source.sha256,
        chunk_hash=digest,
    )


def chunk_records(
    records: Iterable[ExtractionRecord | Mapping[str, Any]],
    source: SourceRecord,
) -> list[ChunkRecord]:
    """Produce stable chunks without crossing source or structural boundaries."""

    ordered = sorted((_as_record(record) for record in records), key=lambda item: item.ordinal)
    chunks: list[ChunkRecord] = []
    pending: list[ExtractionRecord] = []
    pending_text: list[str] = []

    def flush() -> None:
        nonlocal pending, pending_text
        if pending:
            chunks.append(_make_chunk(source, pending, "\n\n".join(pending_text)))
        pending, pending_text = [], []

    for record in ordered:
        pieces = split_record(record)
        # A split record is kept structurally associated with itself, but each piece
        # gets its own stable locator and hash.  Avoid merging it with another block.
        if len(pieces) > 1:
            flush()
            for index, piece in enumerate(pieces):
                synthetic = record.model_copy(update={"ordinal": record.ordinal * 100000 + index})
                chunks.append(_make_chunk(source, [synthetic], piece))
            continue
        piece = pieces[0]
        if pending and (
            not _compatible(pending[-1], record)
            or estimate_tokens("\n\n".join([*pending_text, piece])) > PREFERRED_MAX_TOKENS
        ):
            flush()
        pending.append(record)
        pending_text.append(piece)
        if estimate_tokens("\n\n".join(pending_text)) >= PREFERRED_MAX_TOKENS:
            flush()
    flush()

    # The minimum target applies only to compatible neighboring blocks.  A tiny
    # trailing chunk is intentionally retained when no safe merge exists.
    deduped: list[ChunkRecord] = []
    seen: set[tuple[str, str]] = set()
    for chunk in chunks:
        key = (chunk.id.rsplit("-", 1)[0], chunk.chunk_hash or "")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(chunk)
    return deduped


# Friendly aliases for callers and prior prototypes.
build_chunks = chunk_records
chunk = chunk_records


__all__ = [
    "HARD_MAX_TOKENS",
    "MIN_MERGE_TOKENS",
    "OVERLAP_TOKENS",
    "PREFERRED_MAX_TOKENS",
    "PREFERRED_MIN_TOKENS",
    "build_chunks",
    "chunk",
    "chunk_records",
    "embedding_text",
    "estimate_tokens",
    "split_record",
]
