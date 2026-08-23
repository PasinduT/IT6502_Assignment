import sys
from pathlib import Path

from azure.core.credentials import AzureKeyCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import IngestionConfig  # noqa: E402


def build_index(name: str, dimensions: int) -> SearchIndex:
    fields = [
        SimpleField(name="id", type=SearchFieldDataType.String, key=True, filterable=True),
        SearchableField(name="content", type=SearchFieldDataType.String),
        SimpleField(
            name="content_type", type=SearchFieldDataType.String, filterable=True, facetable=True
        ),
        SearchableField(name="title", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="source_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="source_url", type=SearchFieldDataType.String),
        SimpleField(name="blob_path", type=SearchFieldDataType.String),
        SimpleField(name="page", type=SearchFieldDataType.Int32, filterable=True),
        SearchableField(name="section", type=SearchFieldDataType.String, filterable=True),
        SimpleField(
            name="published_date",
            type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
            sortable=True,
        ),
        SimpleField(
            name="effective_from",
            type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
            sortable=True,
        ),
        SimpleField(
            name="effective_to",
            type=SearchFieldDataType.DateTimeOffset,
            filterable=True,
            sortable=True,
        ),
        SimpleField(
            name="tax_year", type=SearchFieldDataType.String, filterable=True, facetable=True
        ),
        SimpleField(name="document_version", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="workflow_id", type=SearchFieldDataType.String, filterable=True),
        SearchableField(
            name="tags",
            type=SearchFieldDataType.Collection(SearchFieldDataType.String),
            filterable=True,
            facetable=True,
        ),
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name="default-vector-profile",
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw-config")],
        profiles=[
            VectorSearchProfile(
                name="default-vector-profile", algorithm_configuration_name="hnsw-config"
            )
        ],
    )
    return SearchIndex(name=name, fields=fields, vector_search=vector_search)


def main() -> None:
    config = IngestionConfig.from_env()
    client = SearchIndexClient(config.search_endpoint, AzureKeyCredential(config.search_key))
    client.create_or_update_index(build_index(config.search_index, config.embedding_dimensions))
    print(
        f"Search index '{config.search_index}' is ready ({config.embedding_dimensions} dimensions)."
    )


if __name__ == "__main__":
    main()
