"""Deterministic normalization shared by ingestion stages.

Normalization is deliberately conservative.  It removes representation noise from
downloaded documents, while leaving wording, numbers, and legal punctuation alone.
"""

from __future__ import annotations

import re
import unicodedata

from .models import ExtractionRecord

_BULLET = re.compile(r"^(?:[-*+•‣▪◦]|\d+[.)])\s+")
_SENTENCE_END = re.compile(r"[.!?:;,)\]}]$|[-–—]$")


def normalize_text(value: str | None, *, join_wrapped: bool = True) -> str:
    """Return source-preserving, deterministic visible text.

    Only obvious prose wrapping is joined: a line beginning with lower-case text
    may follow an unfinished line.  List items, headings, and lines ending in
    sentence punctuation remain separate so table/list structure is retained.
    """

    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value.replace("\u00ad", ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.split("\n")]
    output: list[str] = []
    for line in lines:
        if not line.strip():
            if output and output[-1] != "":
                output.append("")
            continue
        current = line.strip()
        if (
            join_wrapped
            and output
            and output[-1]
            and not _SENTENCE_END.search(output[-1])
            and not _BULLET.match(output[-1])
            and not _BULLET.match(current)
            and current[:1].islower()
        ):
            output[-1] = f"{output[-1]} {current}"
        else:
            output.append(current)
    while output and output[-1] == "":
        output.pop()
    return "\n".join(output)


def normalize_record(record: ExtractionRecord) -> ExtractionRecord:
    """Normalize one extraction record and recompute its content hash."""

    import hashlib

    content = normalize_text(record.content)
    return record.model_copy(
        update={
            "content": content,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
    )


def normalize_records(records: list[ExtractionRecord]) -> list[ExtractionRecord]:
    """Normalize records in-place order without changing their ordinals."""

    normalized: list[ExtractionRecord] = []
    for record in records:
        if normalize_text(record.content):
            normalized.append(normalize_record(record))
    return normalized


# Common spelling used by callers that treat this module as a text utility.
normalize = normalize_text


__all__ = ["normalize", "normalize_text", "normalize_record", "normalize_records"]
