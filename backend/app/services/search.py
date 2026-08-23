from datetime import date, datetime
from typing import Any

from azure.core.credentials import AzureKeyCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.models import VectorizedQuery

from app.config import Settings
from app.errors import AppError
from app.services.models import SearchChunk
from app.services.scope import QueryScope

SELECT_FIELDS = [
    "id",
    "content",
    "content_type",
    "title",
    "source_id",
    "source_url",
    "page",
    "section",
    "published_date",
    "effective_from",
    "effective_to",
    "tax_year",
    "document_version",
    "workflow_id",
    "tags",
]


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

    async def search(
        self, question: str, vector: list[float], scope: QueryScope, tax_year: str | None
    ) -> list[SearchChunk]:
        filters: list[str] = []
        if scope == QueryScope.PORTAL:
            filters.append("content_type eq 'portal_guide'")
        elif scope == QueryScope.TAX:
            filters.append("content_type eq 'tax_document'")
        if tax_year:
            safe_year = tax_year.replace("'", "''")
            filters.append(f"(tax_year eq null or tax_year eq '{safe_year}')")

        query = VectorizedQuery(
            vector=vector,
            k_nearest_neighbors=self.settings.rag_top_k,
            fields="embedding",
        )
        try:
            results = await self.client.search(
                search_text=question,
                vector_queries=[query],
                filter=" and ".join(filters) or None,
                select=SELECT_FIELDS,
                top=self.settings.rag_top_k,
            )
            chunks = []
            async for result in results:
                score = float(result.get("@search.score", 0))
                if score < self.settings.rag_min_score:
                    continue
                chunks.append(
                    SearchChunk(
                        id=result["id"],
                        content=result["content"],
                        content_type=result["content_type"],
                        title=result["title"],
                        source_id=result["source_id"],
                        score=score,
                        source_url=result.get("source_url"),
                        page=result.get("page"),
                        section=result.get("section"),
                        published_date=_as_date(result.get("published_date")),
                        effective_from=_as_date(result.get("effective_from")),
                        effective_to=_as_date(result.get("effective_to")),
                        tax_year=result.get("tax_year"),
                        document_version=result.get("document_version"),
                        workflow_id=result.get("workflow_id"),
                        tags=result.get("tags") or [],
                    )
                )
            return chunks
        except AppError:
            raise
        except Exception as exc:
            raise AppError(
                "SEARCH_ERROR", "The knowledge base is temporarily unavailable.", 503
            ) from exc

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
