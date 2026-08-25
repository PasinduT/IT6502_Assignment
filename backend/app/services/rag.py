import re
from collections.abc import Sequence

from app.config import Settings
from app.errors import AppError
from app.prompts.system_prompt import MODEL_KNOWLEDGE_SYSTEM_PROMPT, SYSTEM_PROMPT
from app.schemas import ChatMessage, ChatResponse, Citation
from app.services.embeddings import EmbeddingService
from app.services.gemini import GeminiService
from app.services.models import SearchChunk
from app.services.query import extract_tax_year
from app.services.search import SearchService

SOURCE_MARKER = re.compile(r"\[SOURCE_(\d+)]")


def build_context(chunks: Sequence[SearchChunk]) -> tuple[str, dict[str, SearchChunk]]:
    blocks: list[str] = []
    mapping: dict[str, SearchChunk] = {}
    seen: set[tuple[str, str]] = set()
    for chunk in chunks:
        dedupe_key = (chunk.source_id, chunk.content.strip())
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        marker = str(len(mapping) + 1)
        mapping[marker] = chunk
        metadata = [f"Title: {chunk.title}", f"Type: {chunk.content_type}"]
        if chunk.section:
            metadata.append(f"Section: {chunk.section}")
        if chunk.page is not None:
            metadata.append(f"Page: {chunk.page}")
        if chunk.tax_year:
            metadata.append(f"Tax year: {chunk.tax_year}")
        if chunk.effective_from:
            metadata.append(f"Effective from: {chunk.effective_from.isoformat()}")
        blocks.append(f"[SOURCE_{marker}]\n" + "\n".join(metadata) + f"\nContent: {chunk.content}")
    return "\n\n".join(blocks), mapping


def build_user_prompt(messages: Sequence[ChatMessage], context: str) -> str:
    history = "\n".join(f"{item.role.upper()}: {item.content}" for item in messages)
    return (
        f"RETRIEVED EVIDENCE:\n{context or '(none retrieved)'}\n\n"
        f"CONVERSATION:\n{history}\n\n"
        "Answer the latest user question."
    )


def build_model_knowledge_prompt(messages: Sequence[ChatMessage]) -> str:
    history = "\n".join(f"{item.role.upper()}: {item.content}" for item in messages)
    return (
        "RETRIEVAL STATUS: The application's knowledge base is not configured. "
        "Use model knowledge according to the system instructions.\n\n"
        f"CONVERSATION:\n{history}\n\n"
        "Answer the latest user question."
    )


def map_citations(answer: str, mapping: dict[str, SearchChunk]) -> list[Citation]:
    citation_ids = list(dict.fromkeys(SOURCE_MARKER.findall(answer)))
    citations: list[Citation] = []
    for marker in citation_ids:
        chunk = mapping.get(marker)
        if not chunk:
            continue
        citations.append(
            Citation(
                id=marker,
                title=chunk.title,
                document_type=chunk.content_type,
                section=chunk.section,
                page=chunk.page,
                published_date=chunk.published_date,
                effective_from=chunk.effective_from,
                tax_year=chunk.tax_year,
                url=chunk.source_url,
            )
        )
    return citations


class RagService:
    def __init__(
        self,
        settings: Settings,
        embeddings: EmbeddingService,
        search: SearchService,
        gemini: GeminiService,
    ) -> None:
        self.settings = settings
        self.embeddings = embeddings
        self.search = search
        self.gemini = gemini

    async def answer(self, messages: list[ChatMessage]) -> ChatResponse:
        question = messages[-1].content
        if not self.settings.gemini_configured:
            raise AppError(
                "SERVICE_NOT_CONFIGURED",
                "The assistant has not been connected to Gemini yet.",
                503,
            )
        recent_messages = messages[-self.settings.max_history_messages :]

        if self.settings.search_configured:
            vector = await self.embeddings.embed_query(question)
            chunks = await self.search.search(question, vector, extract_tax_year(question))
            context, mapping = build_context(chunks)
            system_prompt = SYSTEM_PROMPT
            user_prompt = build_user_prompt(recent_messages, context)
        else:
            mapping = {}
            system_prompt = MODEL_KNOWLEDGE_SYSTEM_PROMPT
            user_prompt = build_model_knowledge_prompt(recent_messages)

        answer = await self.gemini.generate(system_prompt, user_prompt)
        return ChatResponse(answer=answer, citations=map_citations(answer, mapping))
