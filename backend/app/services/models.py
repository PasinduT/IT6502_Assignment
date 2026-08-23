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
    page: int | None = None
    section: str | None = None
    published_date: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    tax_year: str | None = None
    document_version: str | None = None
    workflow_id: str | None = None
    tags: list[str] = field(default_factory=list)
