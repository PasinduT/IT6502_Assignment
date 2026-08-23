from datetime import date

from app.services.models import SearchChunk
from app.services.rag import build_context, map_citations
from app.services.scope import QueryScope, classify_scope, extract_tax_year


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


def test_scope_classification():
    assert classify_scope("What is VAT in Sri Lanka?") == QueryScope.TAX
    assert classify_scope("Where do I click in the tax portal?") == QueryScope.MIXED
    assert classify_scope("Write a poem") == QueryScope.OUT_OF_SCOPE


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
