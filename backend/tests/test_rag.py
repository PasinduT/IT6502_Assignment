from datetime import date

from app.config import Settings
from app.errors import AppError
from app.schemas import ChatMessage
from app.services.models import SearchChunk
from app.services.query import extract_tax_year
from app.services.rag import RagService, build_context, map_citations


def chunk(**overrides):
    values = dict(
        id="chunk-a",
        content="VAT evidence",
        content_type="tax_document",
        title="VAT Act",
        source_id="vat-act",
        score=0.9,
        section="4",
        page=12,
        effective_from=date(2025, 4, 1),
        source_url="https://example.gov.lk/vat",
    )
    values.update(overrides)
    return SearchChunk(**values)


def test_tax_year_extraction():
    assert extract_tax_year("For 2025/26, what applies?") == "2025/2026"
    assert extract_tax_year("Rules in 2024") == "2024"


def test_context_deduplicates_and_assigns_stable_markers():
    context, mapping = build_context([chunk(), chunk(id="chunk-b")])
    assert len(mapping) == 1
    assert "[SOURCE_1]" in context


def test_citations_include_only_known_referenced_markers():
    _, mapping = build_context([chunk()])
    citations = map_citations("Claim [SOURCE_1], bad [SOURCE_99].", mapping)
    assert len(citations) == 1
    assert citations[0].title == "VAT Act"
    assert citations[0].page == 12


async def test_model_determines_scope_even_without_retrieved_evidence():
    class FakeEmbeddings:
        async def embed_query(self, question):
            assert question == "Write a poem"
            return [0.1]

    class FakeSearch:
        async def search(self, question, vector, tax_year):
            assert (question, vector, tax_year) == ("Write a poem", [0.1], None)
            return []

    class FakeGemini:
        async def generate(self, system_prompt, prompt):
            assert "Determine from the conversation" in system_prompt
            assert "RETRIEVED EVIDENCE:\n(none retrieved)" in prompt
            return "I can only help with Sri Lankan tax questions."

    settings = Settings(
        gemini_api_key="test-key",
        azure_search_endpoint="https://example.search.windows.net",
        azure_search_key="test-key",
    )
    service = RagService(settings, FakeEmbeddings(), FakeSearch(), FakeGemini())

    response = await service.answer([ChatMessage(role="user", content="Write a poem")])

    assert response.answer == "I can only help with Sri Lankan tax questions."


async def test_answers_with_model_knowledge_when_search_is_not_configured():
    class UnusedEmbeddings:
        async def embed_query(self, _):
            raise AssertionError("embedding should not run without Search configuration")

    class UnusedSearch:
        async def search(self, *_):
            raise AssertionError("search should not run without Search configuration")

    class FakeGemini:
        async def generate(self, system_prompt, prompt):
            assert "operating without" in system_prompt
            assert "Do not emit [SOURCE_n] citations" in system_prompt
            assert "knowledge base is not configured" in prompt
            return "VAT is an indirect tax."

    settings = Settings(
        gemini_api_key="test-key", azure_search_endpoint="", azure_search_key=""
    )
    service = RagService(settings, UnusedEmbeddings(), UnusedSearch(), FakeGemini())

    response = await service.answer(
        [ChatMessage(role="user", content="What is VAT in Sri Lanka?")]
    )

    assert response.answer == "VAT is an indirect tax."
    assert response.citations == []


async def test_requires_gemini_even_when_search_is_not_configured():
    class UnusedProvider:
        pass

    service = RagService(
        Settings(gemini_api_key=""),
        UnusedProvider(),
        UnusedProvider(),
        UnusedProvider(),
    )

    try:
        await service.answer([ChatMessage(role="user", content="What is VAT?")])
    except AppError as exc:
        assert exc.code == "SERVICE_NOT_CONFIGURED"
        assert "Gemini" in exc.message
    else:
        raise AssertionError("missing Gemini configuration should fail")
