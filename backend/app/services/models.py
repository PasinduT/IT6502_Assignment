from dataclasses import dataclass, field
from datetime import date


@dataclass(slots=True)
class SearchChunk:
    id: str
    content: str
    content_type: str
    title: str
    source_id: str
    score: float
    source_url: str | None = None
    blob_path: str | None = None
    page: int | None = None
    page_end: int | None = None
    section: str | None = None
    sheet: str | None = None
    cell_range: str | None = None
    published_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    tax_year: str | None = None
    document_version: str | None = None
    workflow_id: str | None = None
    authority_level: str | None = None
    authority_rank: int | None = None
    tax_types: list[str] = field(default_factory=list)
    taxpayer_types: list[str] = field(default_factory=list)
    language: str | None = None
    status: str | None = None
    supersedes: list[str] = field(default_factory=list)
    form_code: str | None = None
    tags: list[str] = field(default_factory=list)
    source_hash: str | None = None
    chunk_hash: str | None = None
    image_id: str | None = None
    image_url: str | None = None
    image_alt_text: str | None = None
    image_caption: str | None = None
