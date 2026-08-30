"""Native PDF extraction with deterministic, selective local OCR.

The extractor deliberately emits one record per non-empty page.  Keeping pages
separate preserves the source locator and avoids accidentally joining a clause
at the end of one page with the next page.  OCR is only attempted for pages
whose normalized native text is below the operational threshold in section
23.1 of the ingestion specification.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from pypdf import PdfReader

from ..models import AuditIssue, ExtractionMethod, ExtractionRecord, WarningCode

if TYPE_CHECKING:
    from ..models import SourceRecord


NATIVE_TEXT_MINIMUM = 80
OCR_MINIMUM = 20
OCR_IMPROVEMENT = 20
OCR_DPI = 250

# PDF text extraction does not expose font sizes reliably, so headings are inferred
# conservatively from isolated, short lines.  Numbered headings are the strongest
# signal and are also useful for carrying legal section context over a page break.
_NUMBERED_HEADING = re.compile(
    r"^\s*(?P<number>\d+(?:\.\d+){0,5})\s*[.)-]?\s+(?P<label>[^.!?;:]{2,160})$"
)
_UPPER_HEADING = re.compile(r"^[A-Z][A-Z0-9][A-Z0-9 /&()'\-]{2,119}$")


def _heading_candidate(line: str) -> tuple[str, int | None] | None:
    """Return a likely heading and its numeric depth without guessing prose."""

    value = " ".join(line.split())
    if not value or len(value) > 160:
        return None
    numbered = _NUMBERED_HEADING.fullmatch(value)
    if numbered:
        # Keep the source's conventional ``1.``, ``1)`` or ``1-`` marker in
        # the context so citations remain recognizable to a reviewer.
        return value, numbered.group("number").count(".") + 1
    # All-uppercase lines are common in official forms and legal PDFs.  Requiring
    # at least three visible characters avoids promoting page furniture such as
    # ``IR D`` or a lone punctuation mark to a section.
    if _UPPER_HEADING.fullmatch(value) and any(character.isalpha() for character in value):
        return value, 1
    # A short title-cased line is a weaker signal.  Restrict it to lines with two
    # or more words and no sentence punctuation to avoid carrying body prose.
    words = value.split()
    if (
        len(words) >= 2
        and len(words) <= 12
        and value[-1].isalnum()
        and all(word[:1].isupper() for word in words if word[:1].isalpha())
    ):
        return value, 1
    return None


def _page_heading_context(
    text: str, context: list[str], depths: list[int | None]
) -> tuple[list[str], list[int | None]]:
    """Update heading context from one page while retaining prior-page sections."""

    updated_context = list(context)
    updated_depths = list(depths)
    for raw_line in text.splitlines():
        candidate = _heading_candidate(raw_line)
        if candidate is None:
            continue
        heading, depth = candidate
        if depth is None:
            updated_context = [heading]
            updated_depths = [None]
            continue
        # A numbered child replaces an existing child at the same depth and keeps
        # its numbered parents.  This maintains ``1 > 1.2`` across page breaks
        # without ever combining records from separate source invocations.
        parent_length = depth - 1
        while len(updated_context) > parent_length:
            updated_context.pop()
            updated_depths.pop()
        updated_context.append(heading)
        updated_depths.append(depth)
    return updated_context, updated_depths


class PdfExtractionError(RuntimeError):
    """A source-level PDF failure that must be surfaced by the corpus builder."""

    def __init__(self, message: str, *, source_id: str, code: str = "PDF_MALFORMED") -> None:
        super().__init__(message)
        self.source_id = source_id
        self.code = code

    def as_issue(self) -> AuditIssue:
        """Return a compact audit issue without exposing a traceback."""

        return AuditIssue(
            severity="error",
            code=self.code,
            message=str(self),
            source_id=self.source_id,
        )


@dataclass(frozen=True)
class PdfExtractionResult:
    """Records and page-level audit issues for one PDF source."""

    records: list[ExtractionRecord]
    issues: list[AuditIssue]
    page_count: int


def normalize_pdf_text(value: str | None) -> str:
    """Apply only deterministic, source-preserving PDF text cleanup."""

    if not value:
        return ""
    # NFKC maps common presentation ligatures (for example, ``ﬁ``) to their
    # ordinary spelling.  A soft hyphen is a layout hint, not visible content.
    text = unicodedata.normalize("NFKC", value.replace("\u00ad", ""))
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def non_whitespace_length(value: str) -> int:
    """Count non-whitespace characters as required by the OCR thresholds."""

    return sum(not character.isspace() for character in value)


def _content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _looks_like_table(value: str) -> bool:
    """Detect likely column spacing without trying to infer cell relationships."""

    column_lines = sum(bool(re.search(r"\S {3,}\S", line)) for line in value.splitlines())
    return column_lines >= 2


def _source_path(source: SourceRecord | str | Path) -> tuple[Path, str]:
    if isinstance(source, (str, Path)):
        path = Path(source)
        source_id = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-") or "pdf-source"
        return path, source_id[:100]
    return Path(source.local_file), source.id


def _ocr_page(document: object, page_number: int, *, dpi: int, language: str) -> str:
    """Render and OCR one one-based page using local PyMuPDF and Tesseract."""

    # Imports are local so native-text PDFs do not require initializing either
    # the renderer or the Tesseract wrapper.
    import pymupdf as fitz
    import pytesseract
    from PIL import Image

    page = document.load_page(page_number - 1)  # type: ignore[attr-defined]
    scale = dpi / 72
    pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    return normalize_pdf_text(pytesseract.image_to_string(image, lang=language))


def extract_pdf_with_audit(
    source: SourceRecord | str | Path,
    *,
    source_id: str | None = None,
    ocr_enabled: bool = True,
    dpi: int = OCR_DPI,
    language: str = "eng",
) -> PdfExtractionResult:
    """Extract a PDF source and retain source/page-level audit information.

    ``SourceRecord`` is preferred because it supplies the canonical source ID.
    When a path is supplied directly, ``source_id`` can be used to override
    the deterministic stem-derived ID.  Malformed and encrypted PDFs raise
    :class:`PdfExtractionError`; callers processing a corpus can convert it to
    an audit issue with ``as_issue()`` and continue with the next source.
    """

    path, inferred_source_id = _source_path(source)
    source_id = source_id or inferred_source_id
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            # Some official PDFs are permission-encrypted but have no opening
            # password.  pypdf can read those documents after an empty-password
            # decrypt; only retain the encrypted failure when that attempt does
            # not succeed.  Keep the decrypt call inside its own guard because
            # pypdf may raise for malformed encryption dictionaries.
            try:
                decrypted = reader.decrypt("")
            except Exception as exc:
                raise PdfExtractionError(
                    f"encrypted PDF cannot be extracted: {path.name}",
                    source_id=source_id,
                    code="PDF_ENCRYPTED",
                ) from exc
            if not decrypted:
                raise PdfExtractionError(
                    f"encrypted PDF cannot be extracted: {path.name}",
                    source_id=source_id,
                    code="PDF_ENCRYPTED",
                )
        pages = list(reader.pages)
        page_count = len(pages)
    except PdfExtractionError:
        raise
    except Exception as exc:
        raise PdfExtractionError(
            f"malformed PDF: {path.name} ({type(exc).__name__})",
            source_id=source_id,
            code="PDF_MALFORMED",
        ) from exc

    records: list[ExtractionRecord] = []
    issues: list[AuditIssue] = []
    renderer: object | None = None
    heading_context: list[str] = []
    heading_depths: list[int | None] = []

    try:
        for page_number, page in enumerate(pages, start=1):
            try:
                native_text = normalize_pdf_text(page.extract_text() or "")
            except Exception as exc:
                raise PdfExtractionError(
                    f"malformed PDF page {page_number}: {path.name} ({type(exc).__name__})",
                    source_id=source_id,
                    code="PDF_MALFORMED",
                ) from exc

            native_length = non_whitespace_length(native_text)
            selected_text = native_text
            extraction_method = ExtractionMethod.PDF_TEXT
            warnings: list[WarningCode] = []
            ocr_text = ""

            if native_length < NATIVE_TEXT_MINIMUM and ocr_enabled:
                if renderer is None:
                    try:
                        import pymupdf as fitz

                        renderer = fitz.open(str(path))
                    except (OSError, RuntimeError, ValueError, TypeError) as exc:
                        raise PdfExtractionError(
                            f"unable to render PDF for OCR: {path.name} ({type(exc).__name__})",
                            source_id=source_id,
                            code="PDF_MALFORMED",
                        ) from exc
                try:
                    ocr_text = _ocr_page(renderer, page_number, dpi=dpi, language=language)
                except Exception as exc:
                    # Tesseract availability is a CLI preflight concern, but an
                    # attempted page cannot silently become a successful empty
                    # extraction when that prerequisite is missing.
                    raise PdfExtractionError(
                        "OCR unavailable for PDF page "
                        f"{page_number}: {path.name} ({type(exc).__name__})",
                        source_id=source_id,
                        code="PDF_OCR_UNAVAILABLE",
                    ) from exc

                ocr_length = non_whitespace_length(ocr_text)
                if ocr_length >= native_length + OCR_IMPROVEMENT:
                    selected_text = ocr_text
                    extraction_method = ExtractionMethod.PDF_OCR
                    warnings.append(WarningCode.OCR_USED)
                else:
                    warnings.append(WarningCode.OCR_WEAKER_THAN_NATIVE)

                if native_length < OCR_MINIMUM and ocr_length < OCR_MINIMUM:
                    issues.append(
                        AuditIssue(
                            severity="warning",
                            code=WarningCode.EMPTY_PAGE.value,
                            message=f"page {page_number} has fewer than 20 visible characters",
                            source_id=source_id,
                        )
                    )
                    continue
            elif native_length < OCR_MINIMUM:
                issues.append(
                    AuditIssue(
                        severity="warning",
                        code=WarningCode.EMPTY_PAGE.value,
                        message=f"page {page_number} has fewer than 20 visible characters",
                        source_id=source_id,
                    )
                )
                continue

            if not selected_text:
                continue
            if _looks_like_table(selected_text):
                warnings.append(WarningCode.TABLE_LAYOUT_LOSS)

            heading_context, heading_depths = _page_heading_context(
                selected_text, heading_context, heading_depths
            )

            records.append(
                ExtractionRecord(
                    record_id=f"{source_id}-p{page_number}",
                    source_id=source_id,
                    content_kind="section",
                    ordinal=len(records) + 1,
                    title_path=list(heading_context),
                    content=selected_text,
                    page=page_number,
                    page_end=page_number,
                    section=heading_context[-1] if heading_context else None,
                    sheet=None,
                    cell_range=None,
                    table_headers=[],
                    extraction_method=extraction_method,
                    warnings=warnings,
                    content_hash=_content_hash(selected_text),
                )
            )
    finally:
        if renderer is not None:
            renderer.close()  # type: ignore[attr-defined]

    return PdfExtractionResult(records=records, issues=issues, page_count=page_count)


def extract_pdf(
    source: SourceRecord | str | Path,
    *,
    source_id: str | None = None,
    ocr_enabled: bool = True,
    dpi: int = OCR_DPI,
    language: str = "eng",
) -> list[ExtractionRecord]:
    """Extract one PDF and return normalized records in stable page order."""

    return extract_pdf_with_audit(
        source,
        source_id=source_id,
        ocr_enabled=ocr_enabled,
        dpi=dpi,
        language=language,
    ).records


__all__ = [
    "NATIVE_TEXT_MINIMUM",
    "OCR_DPI",
    "OCR_IMPROVEMENT",
    "OCR_MINIMUM",
    "PdfExtractionError",
    "PdfExtractionResult",
    "extract_pdf",
    "extract_pdf_with_audit",
    "normalize_pdf_text",
    "non_whitespace_length",
]
