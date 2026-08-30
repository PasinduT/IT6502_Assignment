"""Azure AI Search retrieval and deterministic result selection.

Provider scores are ordering hints only. Applicability, status, authority, and source
diversity are resolved locally so migration-era records behave predictably.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery

from app.config import Settings
from app.errors import AppError
from app.services.models import SearchChunk
from app.services.query import QueryContext, analyze_query

SELECT_FIELDS = [
    "id",
    "content",
    "content_type",
    "title",
    "source_id",
    "source_url",
    "blob_path",
    "page",
    "page_end",
    "section",
    "sheet",
    "cell_range",
    "published_date",
    "effective_from",
    "effective_to",
    "tax_year",
    "document_version",
    "workflow_id",
    "authority_level",
    "authority_rank",
    "tax_types",
    "taxpayer_types",
    "language",
    "status",
    "supersedes",
    "form_code",
    "tags",
    "source_hash",
    "chunk_hash",
    "image_id",
    "image_url",
    "image_alt_text",
    "image_caption",
]


@dataclass(slots=True)
class SearchResults:
    """Separate text evidence and image candidates for RAG orchestration."""

    evidence: list[SearchChunk]
    images: list[SearchChunk]

    @property
    def image_candidates(self) -> list[SearchChunk]:
        return self.images


RetrievalResults = SearchResults


@dataclass(slots=True)
class _RankedChunk:
    chunk: SearchChunk
    original_rank: int


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [item for item in value if isinstance(item, str)]
    return []


def _chunk(result: dict[str, Any], score: float) -> SearchChunk:
    """Map a Search result with defaults so old index records remain readable."""

    return SearchChunk(
        id=str(result.get("id", "")),
        content=str(result.get("content", "")),
        content_type=str(result.get("content_type", "document")),
        title=str(result.get("title", "")),
        source_id=str(result.get("source_id", "")),
        score=score,
        source_url=result.get("source_url"),
        blob_path=result.get("blob_path"),
        page=_as_int(result.get("page")),
        page_end=_as_int(result.get("page_end")),
        section=result.get("section"),
        sheet=result.get("sheet"),
        cell_range=result.get("cell_range"),
        published_date=_as_date(result.get("published_date")),
        effective_from=_as_date(result.get("effective_from")),
        effective_to=_as_date(result.get("effective_to")),
        tax_year=result.get("tax_year"),
        document_version=result.get("document_version"),
        workflow_id=result.get("workflow_id"),
        authority_level=result.get("authority_level"),
        authority_rank=_as_int(result.get("authority_rank")),
        tax_types=_as_list(result.get("tax_types")),
        taxpayer_types=_as_list(result.get("taxpayer_types")),
        language=result.get("language"),
        status=result.get("status"),
        supersedes=_as_list(result.get("supersedes")),
        form_code=result.get("form_code"),
        tags=_as_list(result.get("tags")),
        source_hash=result.get("source_hash"),
        chunk_hash=result.get("chunk_hash"),
        image_id=result.get("image_id"),
        image_url=result.get("image_url"),
        image_alt_text=result.get("image_alt_text"),
        image_caption=result.get("image_caption"),
    )


def _period_score(chunk: SearchChunk, context: QueryContext) -> int:
    if not context.tax_year:
        return 1 if (chunk.status or "").lower() == "current" else 0
    if chunk.tax_year == context.tax_year:
        return 3
    try:
        year = int(context.tax_year[:4])
    except (TypeError, ValueError):
        return 1
    if (
        chunk.effective_from
        and chunk.effective_from.year <= year
        and (chunk.effective_to is None or chunk.effective_to.year >= year)
    ):
        return 2
    if chunk.tax_year is None and chunk.effective_from is None:
        return 1
    return 0


def _status_score(chunk: SearchChunk, historical: bool) -> int:
    status = (chunk.status or "unknown").lower()
    if historical:
        return {"historical": 3, "superseded": 2, "current": 1, "unknown": 0}.get(status, 0)
    return {"current": 3, "unknown": 2, "historical": 1, "superseded": 0}.get(status, 0)


def _is_legal_query(context: QueryContext) -> bool:
    text = context.retrieval_text.lower()
    return re.search(r"\b(?:law|legal|act|gazette|section|regulation|rate)\b", text) is not None


def _authority_score(chunk: SearchChunk, context: QueryContext) -> int:
    level = (chunk.authority_level or "").lower()
    if _is_legal_query(context):
        preferred = {
            "legislation": 8,
            "gazette": 7,
            "official_circular": 6,
            "official_notice": 5,
            "official_calendar": 3,
            "official_form": 2,
            "official_guide": 1,
            "official_webpage": 0,
        }
    elif context.procedural_intent:
        preferred = {
            "official_form": 8,
            "official_guide": 7,
            "official_notice": 6,
            "legislation": 5,
            "gazette": 4,
            "official_circular": 4,
            "official_calendar": 3,
            "official_webpage": 2,
        }
    else:
        preferred = {
            "legislation": 8,
            "gazette": 7,
            "official_circular": 6,
            "official_notice": 5,
            "official_form": 4,
            "official_guide": 3,
            "official_calendar": 2,
            "official_webpage": 1,
        }
    return preferred.get(level, min(max(chunk.authority_rank or 0, 0) // 10, 8))


def _tax_match_score(chunk: SearchChunk, context: QueryContext) -> int:
    if not context.tax_types:
        return 0
    candidate = {item.casefold() for item in chunk.tax_types}
    requested = {item.casefold() for item in context.tax_types}
    if not candidate:
        return 1  # Unknown metadata remains a candidate.
    return 2 if candidate & requested else 0


def _sort_key(
    item: _RankedChunk, context: QueryContext
) -> tuple[int, int, int, int, int, int, str]:
    chunk = item.chunk
    # Numeric priorities sort descending; original rank and stable ID sort ascending.
    return (
        -_period_score(chunk, context),
        -(1 if context.tax_year and chunk.tax_year == context.tax_year else 0),
        -_status_score(chunk, context.historical_intent),
        -_tax_match_score(chunk, context),
        -_authority_score(chunk, context),
        item.original_rank,
        chunk.id,
    )


def rerank_chunks(
    chunks: Sequence[SearchChunk], context: QueryContext, *, limit: int = 8, max_per_source: int = 2
) -> list[SearchChunk]:
    """Apply period/status/tax/authority ordering and first-pass source diversity."""

    ranked = [_RankedChunk(chunk, rank) for rank, chunk in enumerate(chunks)]
    ranked = [item for item in ranked if (item.chunk.status or "").lower() != "excluded"]
    ranked.sort(key=lambda item: _sort_key(item, context))
    # Current questions use superseded material only as a fallback when no replacement exists.
    if not context.historical_intent:
        non_superseded = [
            item for item in ranked if (item.chunk.status or "").lower() != "superseded"
        ]
        if non_superseded:
            ranked = non_superseded
    selected: list[SearchChunk] = []
    counts: dict[str, int] = {}
    for item in ranked:
        count = counts.get(item.chunk.source_id, 0)
        if count >= max_per_source:
            continue
        selected.append(item.chunk)
        counts[item.chunk.source_id] = count + 1
        if len(selected) >= limit:
            break
    return selected


def rerank_image_candidates(
    chunks: Sequence[SearchChunk],
    context: QueryContext,
    evidence: Sequence[SearchChunk],
    *,
    limit: int = 4,
) -> list[SearchChunk]:
    """Select only guide images associated with the retrieved evidence.

    Relevance ranking can prefer an applicable page or workflow, but it must never turn an
    unrelated current image into model context merely because the image search returned it.
    """

    evidence_keys = {
        (chunk.source_id, chunk.page)
        for chunk in evidence
        if chunk.source_id and chunk.page is not None
    }
    evidence_workflows = {
        chunk.workflow_id.strip()
        for chunk in evidence
        if chunk.workflow_id and chunk.workflow_id.strip()
    }
    ranked = [_RankedChunk(chunk, rank) for rank, chunk in enumerate(chunks)]
    ranked = [
        item
        for item in ranked
        if item.chunk.content_type == "guide_image"
        and (item.chunk.status or "").lower() not in {"excluded", "superseded", "historical"}
        and (not context.tax_year or _period_score(item.chunk, context) > 0)
        and (
            (
                item.chunk.source_id
                and item.chunk.page is not None
                and (item.chunk.source_id, item.chunk.page) in evidence_keys
            )
            or (item.chunk.workflow_id and item.chunk.workflow_id.strip() in evidence_workflows)
        )
    ]
    ranked.sort(
        key=lambda item: (
            -(
                2
                if (item.chunk.source_id, item.chunk.page) in evidence_keys
                else 1
                if item.chunk.workflow_id and item.chunk.workflow_id in evidence_workflows
                else 0
            ),
            -_period_score(item.chunk, context),
            -_tax_match_score(item.chunk, context),
            -_authority_score(item.chunk, context),
            item.original_rank,
            item.chunk.id,
        )
    )
    return [item.chunk for item in ranked[:limit]]


class SearchService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._client: SearchClient | None = None

    @property
    def client(self) -> SearchClient:
        if self._client is None:
            self._client = SearchClient(
                endpoint=self.settings.azure_search_endpoint,
                index_name=self.settings.azure_search_index,
                credential=AzureKeyCredential(self.settings.azure_search_key),
            )
        return self._client

    @staticmethod
    def _filter(context: QueryContext, *, images: bool) -> str:
        filters = [
            "content_type eq 'guide_image'" if images else "content_type ne 'guide_image'",
            "(language eq 'en' or language eq null)",
            "(status ne 'excluded' or status eq null)",
        ]
        if context.tax_year:
            safe_year = context.tax_year.replace("'", "''")
            filters.append(f"(tax_year eq null or tax_year eq '{safe_year}')")
        return " and ".join(filters)

    async def _run_search(
        self, context: QueryContext, vector: list[float], *, images: bool
    ) -> list[SearchChunk]:
        top = self.settings.rag_image_top_k if images else self.settings.rag_initial_top
        vector_k = max(top, self.settings.rag_vector_k)
        query = VectorizedQuery(vector=vector, k_nearest_neighbors=vector_k, fields="embedding")
        results = await self.client.search(
            search_text=context.retrieval_text,
            vector_queries=[query],
            filter=self._filter(context, images=images),
            select=SELECT_FIELDS,
            top=top,
        )
        chunks: list[SearchChunk] = []
        async for result in results:
            score = float(result.get("@search.score", 0))
            if score < self.settings.rag_min_score:
                continue
            chunks.append(_chunk(result, score))
        return chunks

    async def retrieve(self, context: QueryContext | str, vector: list[float]) -> SearchResults:
        """Run evidence search and an optional image search with the same vector."""

        if isinstance(context, str):
            context = analyze_query(context)
        try:
            evidence_raw = await self._run_search(context, vector, images=False)
            evidence = rerank_chunks(evidence_raw, context, limit=self.settings.rag_max_evidence)
            images: list[SearchChunk] = []
            if context.procedural_intent and evidence:
                image_raw = await self._run_search(context, vector, images=True)
                images = rerank_image_candidates(
                    image_raw, context, evidence, limit=self.settings.rag_max_images
                )
            return SearchResults(evidence=evidence, images=images)
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "SEARCH_ERROR", "The knowledge base is temporarily unavailable.", 503
            ) from exc

    async def search(
        self, question: str, vector: list[float], tax_year: str | None = None
    ) -> list[SearchChunk]:
        """Legacy evidence-only interface retained for existing RAG callers."""

        context = analyze_query(question)
        if tax_year and context.tax_year is None:
            context = QueryContext(
                tax_year=tax_year,
                tax_types=context.tax_types,
                taxpayer_types=context.taxpayer_types,
                procedural_intent=context.procedural_intent,
                historical_intent=context.historical_intent,
                retrieval_text=context.retrieval_text,
            )
        return (await self.retrieve(context, vector)).evidence

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
