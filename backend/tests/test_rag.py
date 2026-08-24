from datetime import date

from app.config import Settings
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
