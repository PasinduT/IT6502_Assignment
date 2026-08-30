"""Pydantic contracts shared by the ingestion workers.

The models in this module describe data exchanged between registry, extraction,
chunking, and image workers.  They deliberately contain validation and
serialization only; file extraction and publication behavior belongs to the
corresponding work packages.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from datetime import date
from enum import Enum, StrEnum
from typing import Any, Literal

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = 1
SHA256_PATTERN = r"^[0-9a-f]{64}$"
SLUG_PATTERN = r"^[a-z0-9][a-z0-9-]{2,99}$"
TAX_YEAR_PATTERN = r"^[0-9]{4}/[0-9]{4}$"


class ContractModel(BaseModel):
    """Base model with strict, assignment-validating contract behavior."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class DocumentType(StrEnum):
    ACT = "act"
    GAZETTE = "gazette"
    CIRCULAR = "circular"
    PUBLIC_NOTICE = "public_notice"
    TAX_CALENDAR = "tax_calendar"
    RETURN_FORM = "return_form"
    RETURN_GUIDE = "return_guide"
    PORTAL_GUIDE = "portal_guide"
    SPREADSHEET_TEMPLATE = "spreadsheet_template"
    OFFICIAL_WEBPAGE = "official_webpage"


class AuthorityLevel(StrEnum):
    LEGISLATION = "legislation"
    GAZETTE = "gazette"
    OFFICIAL_CIRCULAR = "official_circular"
    OFFICIAL_NOTICE = "official_notice"
    OFFICIAL_CALENDAR = "official_calendar"
    OFFICIAL_FORM = "official_form"
    OFFICIAL_GUIDE = "official_guide"
    OFFICIAL_WEBPAGE = "official_webpage"


AUTHORITY_RANKS: dict[AuthorityLevel, int] = {
    AuthorityLevel.LEGISLATION: 100,
    AuthorityLevel.GAZETTE: 90,
    AuthorityLevel.OFFICIAL_CIRCULAR: 80,
    AuthorityLevel.OFFICIAL_NOTICE: 70,
    AuthorityLevel.OFFICIAL_CALENDAR: 60,
    AuthorityLevel.OFFICIAL_FORM: 50,
    AuthorityLevel.OFFICIAL_GUIDE: 40,
    AuthorityLevel.OFFICIAL_WEBPAGE: 30,
}


class MediaType(StrEnum):
    PDF = "application/pdf"
    HTML = "text/html"
    XLSM = "application/vnd.ms-excel.sheet.macroEnabled.12"


class Language(StrEnum):
    EN = "en"
    SI = "si"
    TA = "ta"


class DocumentStatus(StrEnum):
    CURRENT = "current"
    HISTORICAL = "historical"
    SUPERSEDED = "superseded"
    EXCLUDED = "excluded"


class ReviewStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"


class ExtractionMethod(StrEnum):
    PDF_TEXT = "pdf_text"
    PDF_OCR = "pdf_ocr"
    HTML_DOM = "html_dom"
    XLSM_XML = "xlsm_xml"


class ContentKind(StrEnum):
    """Common extraction block kinds.

    Extractors may use a more specific kind in future releases, so the
    ``ExtractionRecord`` field remains a string for forward compatibility.
    """

    SECTION = "section"
    DOCUMENT_SECTION = "document_section"
    PARAGRAPH = "paragraph"
    LIST = "list"
    TABLE = "table"
    WORKSHEET = "worksheet"
    FIELD = "field"


class WarningCode(StrEnum):
    OCR_USED = "OCR_USED"
    OCR_WEAKER_THAN_NATIVE = "OCR_WEAKER_THAN_NATIVE"
    EMPTY_PAGE = "EMPTY_PAGE"
    SPARSE_PAGE = "SPARSE_PAGE"
    TABLE_LAYOUT_LOSS = "TABLE_LAYOUT_LOSS"
    MALFORMED_HEADING = "MALFORMED_HEADING"
    HIDDEN_SHEET_SKIPPED = "HIDDEN_SHEET_SKIPPED"
    FORMULA_WITHOUT_CACHED_VALUE = "FORMULA_WITHOUT_CACHED_VALUE"
    DATA_VALIDATION_PARTIAL = "DATA_VALIDATION_PARTIAL"
    HTML_MAIN_FALLBACK = "HTML_MAIN_FALLBACK"


def _non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} cannot be empty")
    return value.strip()


def _validate_slug(value: str, field_name: str) -> str:
    value = _non_empty(value, field_name)
    if not re.fullmatch(SLUG_PATTERN, value):
        raise ValueError(f"{field_name} must be a lowercase slug")
    return value


def _validate_hash(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(SHA256_PATTERN, value):
        raise ValueError(f"{field_name} must be 64 lowercase hexadecimal characters")
    return value


def _validate_https(value: AnyHttpUrl | None, field_name: str) -> AnyHttpUrl | None:
    if value is not None and value.scheme != "https":
        raise ValueError(f"{field_name} must use HTTPS")
    return value


class SourceRecord(ContractModel):
    """One source in the approved source registry."""

    id: str
    title: str
    source_url: AnyHttpUrl
    local_file: str
    media_type: MediaType
    document_type: DocumentType
    authority_level: AuthorityLevel
    authority_rank: int = Field(ge=0)
    jurisdiction: Literal["LK"]
    language: Language
    status: DocumentStatus
    review_status: ReviewStatus
    sha256: str = Field(pattern=SHA256_PATTERN)
    final_url: AnyHttpUrl | None = None
    published_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    tax_year: str | None = None
    document_version: str | None = None
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: list[str] = Field(default_factory=list)
    tax_types: list[str] = Field(default_factory=list)
    taxpayer_types: list[str] = Field(default_factory=list)
    form_code: str | None = None
    workflow_ids: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None
    render_pages: list[int] = Field(default_factory=list)
    exclusion_reason: str | None = None

    @field_validator("id")
    @classmethod
    def id_is_slug(cls, value: str) -> str:
        return _validate_slug(value, "id")

    @field_validator("title", "local_file")
    @classmethod
    def required_text_is_non_empty(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @field_validator("source_url")
    @classmethod
    def source_url_is_https(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        return _validate_https(value, "source_url")  # type: ignore[return-value]

    @field_validator("final_url")
    @classmethod
    def final_url_is_https(cls, value: AnyHttpUrl | None) -> AnyHttpUrl | None:
        return _validate_https(value, "final_url")

    @field_validator("sha256")
    @classmethod
    def sha_is_lower_hex(cls, value: str) -> str:
        return _validate_hash(value, "sha256")

    @field_validator("tax_year")
    @classmethod
    def tax_year_is_canonical(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(TAX_YEAR_PATTERN, value):
            raise ValueError("tax_year must use canonical YYYY/YYYY format")
        return value

    @field_validator("supersedes", "superseded_by")
    @classmethod
    def related_ids_are_slugs(cls, values: list[str]) -> list[str]:
        return [_validate_slug(value, "source ID") for value in values]

    @field_validator("render_pages")
    @classmethod
    def render_pages_are_positive(cls, values: list[int]) -> list[int]:
        if len(values) != len(set(values)):
            raise ValueError("render_pages must not contain duplicates")
        if any(value < 1 for value in values):
            raise ValueError("render_pages must contain positive page numbers")
        return values

    @model_validator(mode="after")
    def validate_relationships(self) -> SourceRecord:
        if self.authority_rank != AUTHORITY_RANKS[self.authority_level]:
            raise ValueError("authority_rank must match authority_level")
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot be earlier than effective_from")
        if self.id in self.supersedes or self.id in self.superseded_by:
            raise ValueError("a source cannot supersede itself")
        if self.status is DocumentStatus.EXCLUDED and not self.exclusion_reason:
            raise ValueError("exclusion_reason is required for excluded sources")
        return self


class SourceManifest(ContractModel):
    """Top-level source registry shape and cross-record invariants."""

    schema_version: Literal[1] = SCHEMA_VERSION
    allowed_hosts: list[str]
    sources: list[SourceRecord] = Field(default_factory=list)

    @field_validator("allowed_hosts")
    @classmethod
    def hosts_are_non_empty(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            host = _non_empty(value, "allowed host").lower().rstrip(".")
            if "/" in host or "://" in host:
                raise ValueError("allowed_hosts must contain hostnames only")
            normalized.append(host)
        if len(normalized) != len(set(normalized)):
            raise ValueError("allowed_hosts must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_registry(self) -> SourceManifest:
        source_ids = [source.id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("source IDs must be globally unique")
        allowed = set(self.allowed_hosts)
        for source in self.sources:
            host = (source.source_url.host or "").lower().rstrip(".")
            if host not in allowed:
                raise ValueError(f"source_url host is not in allowed_hosts: {host}")
        known = set(source_ids)
        for source in self.sources:
            unknown = (set(source.supersedes) | set(source.superseded_by)) - known
            if unknown:
                raise ValueError(f"unknown related source ID(s): {sorted(unknown)}")
        return self


class ExtractionRecord(ContractModel):
    """One normalized, locator-aware extraction record."""

    schema_version: Literal[1] = SCHEMA_VERSION
    record_id: str
    source_id: str
    content_kind: str
    ordinal: int = Field(ge=1)
    title_path: list[str] = Field(default_factory=list)
    content: str
    page: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    section: str | None = None
    sheet: str | None = None
    cell_range: str | None = None
    table_headers: list[str] = Field(default_factory=list)
    extraction_method: ExtractionMethod
    warnings: list[WarningCode] = Field(default_factory=list)
    content_hash: str = Field(pattern=SHA256_PATTERN)

    @field_validator("record_id")
    @classmethod
    def record_id_is_non_empty(cls, value: str) -> str:
        return _non_empty(value, "record_id")

    @field_validator("source_id")
    @classmethod
    def source_id_is_slug(cls, value: str) -> str:
        return _validate_slug(value, "source_id")

    @field_validator("content_kind")
    @classmethod
    def content_kind_is_non_empty(cls, value: str) -> str:
        return _non_empty(value, "content_kind")

    @field_validator("content")
    @classmethod
    def content_is_non_empty(cls, value: str) -> str:
        return _non_empty(value, "content")

    @field_validator("title_path", "table_headers")
    @classmethod
    def text_lists_are_clean(cls, values: list[str]) -> list[str]:
        return [_non_empty(value, "text value") for value in values]

    @field_validator("content_hash")
    @classmethod
    def content_hash_is_lower_hex(cls, value: str) -> str:
        return _validate_hash(value, "content_hash")

    @model_validator(mode="after")
    def validate_locator(self) -> ExtractionRecord:
        if self.page_end is not None and self.page is None:
            raise ValueError("page is required when page_end is provided")
        if self.page and self.page_end and self.page_end < self.page:
            raise ValueError("page_end cannot be earlier than page")
        if self.extraction_method in (ExtractionMethod.PDF_TEXT, ExtractionMethod.PDF_OCR):
            if self.page is None:
                raise ValueError("PDF extraction records require page")
        if self.extraction_method is ExtractionMethod.XLSM_XML and not self.sheet:
            raise ValueError("XLSM extraction records require sheet")
        return self


class ChunkRecord(ContractModel):
    """Searchable chunk and its stable source metadata."""

    schema_version: Literal[1] = SCHEMA_VERSION
    id: str
    source_id: str
    content: str
    embedding_text: str
    content_type: str
    title: str
    source_url: AnyHttpUrl | None = None
    page: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    section: str | None = None
    sheet: str | None = None
    cell_range: str | None = None
    published_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    tax_year: str | None = None
    document_version: str | None = None
    workflow_id: str | None = None
    authority_level: AuthorityLevel | None = None
    authority_rank: int | None = Field(default=None, ge=0)
    tax_types: list[str] = Field(default_factory=list)
    taxpayer_types: list[str] = Field(default_factory=list)
    language: Language | None = None
    status: DocumentStatus | None = None
    supersedes: list[str] = Field(default_factory=list)
    form_code: str | None = None
    tags: list[str] = Field(default_factory=list)
    source_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    chunk_hash: str | None = Field(default=None, pattern=SHA256_PATTERN)
    image_id: str | None = None
    image_url: AnyHttpUrl | None = None
    image_alt_text: str | None = None
    image_caption: str | None = None

    @field_validator("id", "content_type", "title", "content", "embedding_text")
    @classmethod
    def required_fields_are_non_empty(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)

    @field_validator("source_id")
    @classmethod
    def source_id_is_slug(cls, value: str) -> str:
        return _validate_slug(value, "source_id")

    @field_validator("source_url", "image_url")
    @classmethod
    def urls_are_https(cls, value: AnyHttpUrl | None, info: Any) -> AnyHttpUrl | None:
        return _validate_https(value, info.field_name)

    @field_validator("tax_year")
    @classmethod
    def tax_year_is_canonical(cls, value: str | None) -> str | None:
        if value is not None and not re.fullmatch(TAX_YEAR_PATTERN, value):
            raise ValueError("tax_year must use canonical YYYY/YYYY format")
        return value

    @field_validator("supersedes")
    @classmethod
    def supersedes_are_slugs(cls, values: list[str]) -> list[str]:
        return [_validate_slug(value, "source ID") for value in values]

    @model_validator(mode="after")
    def validate_chunk_locator(self) -> ChunkRecord:
        if self.page_end is not None and self.page is None:
            raise ValueError("page is required when page_end is provided")
        if self.page and self.page_end and self.page_end < self.page:
            raise ValueError("page_end cannot be earlier than page")
        if self.authority_level is not None:
            expected = AUTHORITY_RANKS[self.authority_level]
            if self.authority_rank is not None and self.authority_rank != expected:
                raise ValueError("authority_rank must match authority_level")
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot be earlier than effective_from")
        return self


class ImageRecord(ContractModel):
    """Reviewed guide-image metadata and provenance."""

    image_id: str
    public_url: AnyHttpUrl | None = None
    title: str
    alt_text: str
    caption: str | None = None
    source_id: str
    source_url: AnyHttpUrl
    page: int | None = Field(default=None, ge=1)
    workflow_id: str | None = None
    tax_types: list[str] = Field(default_factory=list)
    taxpayer_types: list[str] = Field(default_factory=list)
    effective_from: date | None = None
    effective_to: date | None = None
    status: DocumentStatus
    review_status: ReviewStatus
    binary_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    byte_size: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)

    @field_validator("image_id", "title", "alt_text", "source_id")
    @classmethod
    def image_required_text_is_valid(cls, value: str, info: Any) -> str:
        if info.field_name in {"image_id", "source_id"}:
            return _validate_slug(value, info.field_name)
        return _non_empty(value, info.field_name)

    @field_validator("source_url", "public_url")
    @classmethod
    def image_urls_are_https(cls, value: AnyHttpUrl | None, info: Any) -> AnyHttpUrl | None:
        return _validate_https(value, info.field_name)

    @model_validator(mode="after")
    def validate_image_dates(self) -> ImageRecord:
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to cannot be earlier than effective_from")
        return self


class AuditIssue(ContractModel):
    """A machine-readable source, extraction, or validation issue."""

    severity: Literal["warning", "error"]
    code: str
    message: str
    source_id: str | None = None
    record_id: str | None = None

    @field_validator("code", "message")
    @classmethod
    def issue_text_is_non_empty(cls, value: str, info: Any) -> str:
        return _non_empty(value, info.field_name)


class AuditReport(ContractModel):
    """Compact aggregate suitable for ``audit.json``."""

    schema_version: Literal[1] = SCHEMA_VERSION
    issues: list[AuditIssue] = Field(default_factory=list)
    source_count: int = Field(default=0, ge=0)
    extraction_record_count: int = Field(default=0, ge=0)
    chunk_count: int = Field(default=0, ge=0)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    return value


def model_to_json(value: BaseModel | Mapping[str, Any] | Sequence[Any]) -> str:
    """Serialize a contract or JSON-compatible value deterministically."""

    return json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def model_to_json_line(value: BaseModel | Mapping[str, Any] | Sequence[Any]) -> str:
    """Serialize one JSONL record with a trailing newline."""

    return f"{model_to_json(value)}\n"


# Short aliases are useful to JSONL writers while retaining one implementation.
to_json = model_to_json
to_json_line = model_to_json_line

# Descriptive aliases keep downstream imports readable without creating
# competing model definitions.
SourceRegistry = SourceManifest
NormalizedRecord = ExtractionRecord
GuideImageRecord = ImageRecord
ImageCandidate = ImageRecord
AuditRecord = AuditIssue
Status = DocumentStatus


__all__ = [
    "AUTHORITY_RANKS",
    "AuditIssue",
    "AuditRecord",
    "AuditReport",
    "AuthorityLevel",
    "ChunkRecord",
    "ContentKind",
    "DocumentStatus",
    "DocumentType",
    "ExtractionMethod",
    "ExtractionRecord",
    "ImageRecord",
    "Language",
    "MediaType",
    "ReviewStatus",
    "SourceRegistry",
    "SourceManifest",
    "SourceRecord",
    "NormalizedRecord",
    "GuideImageRecord",
    "ImageCandidate",
    "Status",
    "WarningCode",
    "model_to_json",
    "model_to_json_line",
    "to_json",
    "to_json_line",
]
