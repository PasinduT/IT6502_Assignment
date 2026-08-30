"""Create the Azure AI Search index used by the corpus pipeline.

Index construction is deliberately kept separate from corpus ingestion. ``--dry-run``
constructs and validates the schema locally and never contacts Azure.
"""

from __future__ import annotations

import argparse
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

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPOSITORY_ROOT = _SCRIPT_DIR.parent
if str(_REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY_ROOT))

VECTOR_PROFILE = "default-vector-profile"


def _collection(
    name: str, *, searchable: bool = True, filterable: bool = True, facetable: bool = True
) -> SearchField:
    """Build a string collection without ``SearchableField`` coercion.

    ``SearchableField`` is a convenience model for scalar ``Edm.String`` fields
    and silently replaces a supplied collection type with that scalar type.
    SearchField preserves ``Collection(Edm.String)``, which is required for the
    list-valued metadata emitted by the chunk contract.
    """
    field_type = SearchFieldDataType.Collection(SearchFieldDataType.String)
    return SearchField(
        name=name,
        type=field_type,
        searchable=searchable,
        filterable=filterable,
        facetable=facetable,
    )


def build_index(name: str, dimensions: int) -> SearchIndex:
    """Build the exact corpus schema from the implementation plan."""

    if not name or not name.strip():
        raise ValueError("index name cannot be empty")
    if dimensions < 1:
        raise ValueError("embedding dimensions must be positive")

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
        SimpleField(name="page_end", type=SearchFieldDataType.Int32, filterable=True),
        SearchableField(name="section", type=SearchFieldDataType.String, filterable=True),
        SearchableField(name="sheet", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="cell_range", type=SearchFieldDataType.String),
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
        SimpleField(
            name="authority_level", type=SearchFieldDataType.String, filterable=True, facetable=True
        ),
        SimpleField(
            name="authority_rank", type=SearchFieldDataType.Int32, filterable=True, sortable=True
        ),
        _collection("tax_types"),
        _collection("taxpayer_types"),
        SimpleField(
            name="language", type=SearchFieldDataType.String, filterable=True, facetable=True
        ),
        SimpleField(
            name="status", type=SearchFieldDataType.String, filterable=True, facetable=True
        ),
        _collection("supersedes", searchable=False, facetable=False),
        SearchableField(name="form_code", type=SearchFieldDataType.String, filterable=True),
        _collection("tags"),
        SimpleField(name="source_hash", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="chunk_hash", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="image_id", type=SearchFieldDataType.String, filterable=True),
        SimpleField(name="image_url", type=SearchFieldDataType.String),
        SearchableField(name="image_alt_text", type=SearchFieldDataType.String),
        SearchableField(name="image_caption", type=SearchFieldDataType.String),
        SearchField(
            name="embedding",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=dimensions,
            vector_search_profile_name=VECTOR_PROFILE,
        ),
    ]
    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw-config")],
        profiles=[
            VectorSearchProfile(name=VECTOR_PROFILE, algorithm_configuration_name="hnsw-config")
        ],
    )
    return SearchIndex(name=name, fields=fields, vector_search=vector_search)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Create the tax-assistant Search index")
    parser.add_argument("--index", dest="index_name", help="Search index name")
    parser.add_argument("--dimensions", type=int, help="embedding vector dimensions")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="validate schema without Azure")
    mode.add_argument("--upload", action="store_true", help="create or update the Search index")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Import after configuring the repository path so both direct script execution and
    # package-style execution resolve the shared ingestion configuration.
    from scripts.common import IngestionConfig

    args = _parser().parse_args(argv)
    try:
        config = IngestionConfig.from_env(require_cloud=False)
    except (SystemExit, ValueError) as exc:
        print(f"Invalid configuration: {exc}", file=sys.stderr)
        return 2
    index_name = args.index_name or config.search_index
    dimensions = args.dimensions or config.embedding_dimensions
    try:
        index = build_index(index_name, dimensions)
    except (TypeError, ValueError) as exc:
        print(f"Invalid index configuration: {exc}", file=sys.stderr)
        return 2

    if not args.upload:
        print(
            f"Dry run: index '{index.name}' schema is valid ({dimensions} dimensions); no mutation"
        )
        return 0

    missing = [
        key
        for key, value in {
            "AZURE_SEARCH_ENDPOINT": config.search_endpoint,
            "AZURE_SEARCH_KEY": config.search_key,
        }.items()
        if not value
    ]
    if missing:
        print(f"Missing required configuration: {', '.join(missing)}", file=sys.stderr)
        return 2
    client = SearchIndexClient(config.search_endpoint, AzureKeyCredential(config.search_key))
    client.create_or_update_index(index)
    print(f"Search index '{index.name}' is ready ({dimensions} dimensions).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
