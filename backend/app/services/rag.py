"""Grounded answer orchestration and marker-to-provenance mapping."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

from app.config import Settings
from app.errors import AppError
from app.prompts.system_prompt import MODEL_KNOWLEDGE_SYSTEM_PROMPT, SYSTEM_PROMPT
from app.schemas import ChatMessage, ChatResponse, Citation, Guide, GuideImage, GuideStep
from app.services.embeddings import EmbeddingService
from app.services.gemini import (
    GeminiService,
    GeneratedAnswer,
    parse_generated_answer,
)
from app.services.models import SearchChunk
from app.services.query import QueryContext, analyze_query
from app.services.search import SearchResults, SearchService

SOURCE_MARKER = re.compile(r"\[SOURCE_([1-9][0-9]*)\]")
_SOURCE_GROUP = re.compile(r"\[(?:\s*SOURCE_[1-9][0-9]*\s*,)+\s*SOURCE_[1-9][0-9]*\s*\]")
_SOURCE_TOKEN = re.compile(r"\[SOURCE_([^\]\s]*)\]")
_IMAGE_TOKEN = re.compile(r"\[IMAGE_[^\]\s]*\]")
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


def _metadata(chunk: SearchChunk) -> list[str]:
    metadata = [f"Title: {chunk.title}", f"Type: {chunk.content_type}"]
    if chunk.section:
        metadata.append(f"Section: {chunk.section}")
    if chunk.page is not None:
        page = str(chunk.page) if chunk.page_end is None else f"{chunk.page}-{chunk.page_end}"
        metadata.append(f"Page: {page}")
    if chunk.sheet:
        metadata.append(f"Sheet: {chunk.sheet}")
    if chunk.cell_range:
        metadata.append(f"Cell range: {chunk.cell_range}")
    if chunk.tax_year:
        metadata.append(f"Tax year: {chunk.tax_year}")
    if chunk.effective_from:
        metadata.append(f"Effective from: {chunk.effective_from.isoformat()}")
    if chunk.authority_level:
        metadata.append(f"Authority: {chunk.authority_level}")
    if chunk.status:
        metadata.append(f"Status: {chunk.status}")
    return metadata


def _image_associated_with_evidence(image: SearchChunk, evidence: Sequence[SearchChunk]) -> bool:
    """Return whether an image belongs to one of the retrieved evidence records."""

    if (
        image.source_id
        and image.page is not None
        and any(
            image.source_id == chunk.source_id and image.page == chunk.page for chunk in evidence
        )
    ):
        return True
    workflow_id = (image.workflow_id or "").strip()
    return bool(
        workflow_id and any(workflow_id == (chunk.workflow_id or "").strip() for chunk in evidence)
    )


def build_context(
    chunks: Sequence[SearchChunk], images: Sequence[SearchChunk] | None = None
) -> (
    tuple[str, dict[str, SearchChunk]] | tuple[str, dict[str, SearchChunk], dict[str, SearchChunk]]
):
    """Build model context with independent source and image marker namespaces.

    The two-item return form is retained for callers that only supplied evidence. New RAG
    calls pass ``images`` and receive the image mapping as the third item.
    """

    blocks: list[str] = []
    source_mapping: dict[str, SearchChunk] = {}
    image_mapping: dict[str, SearchChunk] = {}
    seen_sources: set[tuple[str, str]] = set()

    for chunk in chunks:
        if chunk.content_type == "guide_image":
            continue
        dedupe_key = (chunk.source_id, chunk.content.strip())
        if dedupe_key in seen_sources:
            continue
        seen_sources.add(dedupe_key)
        marker = str(len(source_mapping) + 1)
        source_mapping[marker] = chunk
        blocks.append(
            f"[SOURCE_{marker}]\n" + "\n".join(_metadata(chunk)) + f"\nContent: {chunk.content}"
        )

    for chunk in images or ():
        # SearchService indexes only reviewed images, but retain this defensive check at the
        # model boundary so a malformed/legacy result cannot become an image attachment.
        if (
            chunk.content_type != "guide_image"
            or not chunk.image_id
            or not chunk.image_alt_text
            or (chunk.status or "").lower() in {"excluded", "superseded", "historical"}
            or not _image_associated_with_evidence(chunk, chunks)
        ):
            continue
        marker = str(len(image_mapping) + 1)
        image_mapping[marker] = chunk
        image_metadata = _metadata(chunk)
        image_metadata.append(f"Image ID: {chunk.image_id}")
        if chunk.image_alt_text:
            image_metadata.append(f"Reviewed alt text: {chunk.image_alt_text}")
        if chunk.image_caption:
            image_metadata.append(f"Reviewed caption: {chunk.image_caption}")
        # Deliberately omit image_url: URLs are backend-owned output fields, never model input.
        blocks.append(f"[IMAGE_{marker}]\n" + "\n".join(image_metadata))

    context = "\n\n".join(blocks)
    if images is None:
        return context, source_mapping
    return context, source_mapping, image_mapping


def build_user_prompt(messages: Sequence[ChatMessage], context: str) -> str:
    history = "\n".join(f"{item.role.upper()}: {item.content}" for item in messages)
    return (
        "RETRIEVED CONTEXT (SOURCE and IMAGE blocks are separate):\n"
        f"{context or '(none retrieved)'}\n\n"
        f"CONVERSATION:\n{history}\n\n"
        "Answer the latest user question using the required JSON response shape."
    )


def build_model_knowledge_prompt(messages: Sequence[ChatMessage]) -> str:
    history = "\n".join(f"{item.role.upper()}: {item.content}" for item in messages)
    return (
        "RETRIEVAL STATUS: The application's knowledge base is not configured. "
        "Use model knowledge according to the system instructions.\n\n"
        f"CONVERSATION:\n{history}\n\n"
        "Answer the latest user question using the required JSON response shape."
    )


def _clean_answer(answer: str, source_mapping: dict[str, SearchChunk]) -> str:
    """Keep only exact source markers that were supplied in context."""

    def replace_source(match: re.Match[str]) -> str:
        marker = match.group(1)
        return match.group(0) if _source_chunk(source_mapping, marker) else ""

    # Expand only bracketed lists made entirely from canonical source markers. The broad token
    # pattern below then validates each marker independently against the supplied mapping.
    def expand_source_group(match: re.Match[str]) -> str:
        markers = re.findall(r"SOURCE_([1-9][0-9]*)", match.group(0))
        return " ".join(f"[SOURCE_{marker}]" for marker in markers)

    normalized = _SOURCE_GROUP.sub(expand_source_group, answer)
    # The broad token pattern also catches malformed IDs; only canonical supplied markers
    # survive the callback.
    cleaned = _SOURCE_TOKEN.sub(replace_source, normalized)
    # Images are represented only by the structured guide field. This prevents a model URL
    # or an invented IMAGE marker from reaching the public response as Markdown.
    cleaned = _IMAGE_TOKEN.sub("", cleaned)
    return _MARKDOWN_IMAGE.sub("", cleaned).strip()


def _source_chunk(mapping: dict[str, SearchChunk], marker: str) -> SearchChunk | None:
    return mapping.get(marker) or mapping.get(f"SOURCE_{marker}")


def map_citations(
    answer: str,
    mapping: dict[str, SearchChunk],
    *,
    guide: Guide | None = None,
) -> list[Citation]:
    """Map answer and guide-step source markers to backend-owned citations.

    Guide citation IDs are included here as well as answer markers so a source marker that
    appears only in a step instruction still resolves in the top-level citation list.
    """

    citation_ids = list(dict.fromkeys(SOURCE_MARKER.findall(answer)))
    if guide is not None:
        for step in guide.steps:
            for marker in step.citation_ids:
                number = _source_number(marker)
                if number and number not in citation_ids:
                    citation_ids.append(number)
    citations: list[Citation] = []
    for marker in citation_ids:
        chunk = _source_chunk(mapping, marker)
        if not chunk:
            continue
        citations.append(
            Citation(
                id=marker,
                title=chunk.title,
                document_type=chunk.content_type,
                section=chunk.section,
                page=chunk.page,
                page_end=chunk.page_end,
                sheet=chunk.sheet,
                cell_range=chunk.cell_range,
                authority_level=chunk.authority_level,
                status=chunk.status,
                source_id=chunk.source_id,
                published_date=chunk.published_date,
                effective_from=chunk.effective_from,
                tax_year=chunk.tax_year,
                url=chunk.source_url,
            )
        )
    return citations


def _marker_number(value: str, prefix: str) -> str | None:
    match = re.fullmatch(rf"{prefix}([1-9][0-9]*)", value.strip().strip("[]"))
    return match.group(1) if match else None


def _source_number(value: str) -> str | None:
    normalized = value.strip().strip("[]")
    if re.fullmatch(r"[1-9][0-9]*", normalized):
        return normalized
    return _marker_number(value, "SOURCE_")


def _step_citation_ids(
    markers: Sequence[str], source_mapping: dict[str, SearchChunk], instruction: str = ""
) -> list[str]:
    result: list[str] = []
    all_markers = list(markers) + [
        f"SOURCE_{number}" for number in SOURCE_MARKER.findall(instruction)
    ]
    for marker in all_markers:
        number = _source_number(marker)
        if number and _source_chunk(source_mapping, number) and number not in result:
            result.append(number)
    return result


def _is_trusted_media_url(image_url: str, trusted_media_base_url: str) -> bool:
    """Accept only URLs under the explicitly configured guide-media origin and path."""

    if not image_url or not trusted_media_base_url:
        return False
    image = urlsplit(image_url)
    trusted = urlsplit(trusted_media_base_url)
    if (
        image.scheme != trusted.scheme
        or image.netloc != trusted.netloc
        or image.username
        or image.password
        or not image.hostname
    ):
        return False
    base_path = trusted.path.rstrip("/")
    if not base_path:
        return True
    return image.path == base_path or image.path.startswith(f"{base_path}/")


def _image_for_step(
    image_id: str | None,
    image_mapping: dict[str, SearchChunk],
    trusted_media_base_url: str = "",
) -> GuideImage | None:
    if not image_id:
        return None
    marker = _marker_number(image_id, "IMAGE_")
    chunk = image_mapping.get(marker) if marker else None
    if chunk is None:
        # The internal contract uses IMAGE_n, but accepting the backend-supplied image ID is
        # useful for clients migrating from the earlier guide contract. It still must resolve
        # to an image retrieved in this request.
        chunk = next((item for item in image_mapping.values() if item.image_id == image_id), None)
    if (
        not chunk
        or chunk.content_type != "guide_image"
        or not chunk.image_id
        or not chunk.image_url
        or not chunk.image_alt_text
        or (chunk.status or "").lower() in {"excluded", "superseded", "historical"}
        or not _is_trusted_media_url(chunk.image_url, trusted_media_base_url)
    ):
        return None
    return GuideImage(
        id=chunk.image_id,
        url=chunk.image_url,
        alt=chunk.image_alt_text,
        caption=chunk.image_caption,
        source_id=chunk.source_id,
        page=chunk.page,
    )


def _build_guide(
    generated: Any,
    source_mapping: dict[str, SearchChunk],
    image_mapping: dict[str, SearchChunk],
    trusted_media_base_url: str = "",
) -> Guide | None:
    if generated is None:
        return None
    steps: list[GuideStep] = []
    for number, step in enumerate(generated.steps, start=1):
        instruction = _clean_answer(step.instruction, source_mapping)
        steps.append(
            GuideStep(
                number=number,
                title=step.title.strip(),
                instruction=instruction,
                image=_image_for_step(step.image_id, image_mapping, trusted_media_base_url),
                citation_ids=_step_citation_ids(step.citation_markers, source_mapping, instruction),
            )
        )
    if not steps:
        return None
    return Guide(title=(generated.title or "Guide").strip() or "Guide", steps=steps)


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
        source_mapping: dict[str, SearchChunk] = {}
        image_mapping: dict[str, SearchChunk] = {}

        if self.settings.search_configured:
            query_context: QueryContext = analyze_query(question, recent_messages)
            vector = await self.embeddings.embed_query(query_context.retrieval_text)
            retrieved = await self.search.retrieve(query_context, vector)
            if isinstance(retrieved, SearchResults):
                evidence, images = retrieved.evidence, retrieved.images
            else:
                # Keep migration compatibility with a legacy evidence-only test double.
                evidence, images = retrieved, []
            context, source_mapping, image_mapping = build_context(evidence, images)
            system_prompt = SYSTEM_PROMPT
            user_prompt = build_user_prompt(recent_messages, context)
        else:
            system_prompt = MODEL_KNOWLEDGE_SYSTEM_PROMPT
            user_prompt = build_model_knowledge_prompt(recent_messages)

        generated: GeneratedAnswer | str = await self.gemini.generate(system_prompt, user_prompt)
        # Production GeminiService returns GeneratedAnswer; accepting a JSON string here
        # keeps provider doubles and older adapters on the same strict parsing path.
        if isinstance(generated, str):
            generated = parse_generated_answer(generated)
        else:
            generated = parse_generated_answer(generated)
        answer = _clean_answer(generated.answer, source_mapping)
        guide = _build_guide(
            generated.guide,
            source_mapping,
            image_mapping,
            self.settings.guide_media_base_url,
        )
        return ChatResponse(
            answer=answer,
            citations=map_citations(answer, source_mapping, guide=guide),
            guide=guide,
        )
