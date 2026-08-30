# Data, indices, and guide setup

This document contains the operational instructions for turning the reviewed IRD source
registry into grounded chatbot evidence. It is intentionally separate from the application
README. The commands below are local and read-only unless a command explicitly includes an
upload, publish, or cloud mutation flag.

## Search index name and schema lifecycle

The application uses the existing `tax-assistant` name. The current ingestion schema includes
stable locators, authority and status metadata, effective dates, tax years, workflow and image
fields, and the vector field used by the retrieval code.

The one-time migration from the earlier index schema has already been completed: the live,
currently empty `tax-assistant` index has the current 34-field schema and 768-dimensional vector
field. Do not delete it during normal setup. A push to `main` runs
`create_search_index.py --upload`; that operation is idempotent and does not delete indexed
documents.

Azure AI Search cannot change an existing field's type in place. If a future code change alters
field types, treat it as a reviewed migration: verify the target index and its document count,
choose an explicit backup/rollback plan, and only then recreate that exact index. To use another
index name for a separate environment, set `AZURE_SEARCH_INDEX`; explicit environment values
always take precedence over the `tax-assistant` default.

## Prerequisites and Git boundaries

Install Python 3.11+, `uv`, and the project dependencies. From `backend/`, run:

```bash
uv sync --dev
```

PDF OCR requires the native Tesseract executable on the local machine; the Python
`pytesseract` wrapper is a development dependency installed by `uv sync --dev`. Verify both
before a full corpus build:

```bash
tesseract --version
cd backend
uv run python -c "import pytesseract; print(pytesseract.get_tesseract_version())"
```

Tesseract is an operating-system executable and cannot be supplied by `pyproject.toml` alone.
If the executable is missing, install it with the platform's package manager and rerun the
preflight. Do not replace local PDF OCR with Gemini vision.

The Git boundary is deliberate:

- Commit reviewable metadata such as `data/metadata/sources.yaml`,
  `data/metadata/sources.example.yaml`, and `data/metadata/guide-images.yaml`.
- Do not commit downloaded source documents, screenshots, rendered images, extraction output,
  chunks, audit reports, embedding caches, or publish reports. They belong under ignored
  `data/raw/` and `data/processed/` paths.
- Do not commit `.env`, API keys, Terraform variables, state, or provider credentials.
- Metadata may contain official URLs, checksums, and local paths, but it must not contain the
  actual source bytes or tax-content extracts.

Confirm the boundary before committing:

```bash
git ls-files data
git status --short -- data/raw data/processed
```

The first command should list metadata templates/registries only; generated and downloaded data
should not appear as tracked files.

## Registry validation and no-key corpus build

Only sources with `jurisdiction: LK`, an allowed IRD host, a valid checksum, an eligible media
type, and `review_status: approved` can enter the production corpus. Third-party research pages
are not authoritative evidence. Run these commands from `backend/` after `uv sync --dev`:

```bash
uv run python ../scripts/ingestion/registry.py \
  --manifest ../data/metadata/sources.yaml \
  --json-summary

uv run python ../scripts/build_corpus.py \
  --manifest ../data/metadata/sources.yaml \
  --output ../data/processed/corpus
```

`build_corpus.py` performs local PDF/HTML/XLSM extraction, normalization, structural chunking,
image-candidate rendering, and audit generation. It does not call Gemini, Azure AI Search, or
any other cloud service. It writes ignored artifacts including `extraction.jsonl`,
`chunks.jsonl`, `audit.json`, `source-summary.json`, `corpus-scope.json`, and
`image-candidates.json`.

Useful recovery and diagnostic flags (all match the script's `--help`) are:

```bash
# Rebuild only selected sources; repeat --source-id for more than one source.
uv run python ../scripts/build_corpus.py \
  --manifest ../data/metadata/sources.yaml \
  --output ../data/processed/corpus \
  --source-id ird-iit-return-and-schedules-guide \
  --force

# Diagnostic only: record OCR-required pages as errors instead of invoking OCR.
uv run python ../scripts/build_corpus.py \
  --manifest ../data/metadata/sources.yaml \
  --output ../data/processed/corpus \
  --skip-ocr

# Omit guide-page candidate rendering for a corpus-only diagnostic.
uv run python ../scripts/build_corpus.py \
  --manifest ../data/metadata/sources.yaml \
  --output ../data/processed/corpus \
  --skip-images
```

Use `--skip-ocr` only to diagnose the remaining work. A full build should run with native
Tesseract available. Review `audit.json`, source errors, extraction methods, chunk counts, and
the estimated embedding size before requesting any cloud operation.

## Current ingestion status and release gates

The latest complete local build processed 162 approved sources into 2,135 chunks with no audit
errors. Its `corpus-scope.json` reports a complete build. All 2,135 embeddings are present in the
ignored local cache. The Consolidated Stamp Duty Act PDF opens with an empty password and extracts
successfully, so no replacement PDF is required. Its official source is
[SDActNo.43\[E\]1982ConsolFinal.pdf](https://www.ird.gov.lk/en/publications/Acts_Stamp%20Duty/SDActNo.43%5BE%5D1982ConsolFinal.pdf).

The live `tax-assistant` index now contains all 2,135 chunks. End-to-end checks have verified
grounded Stamp Duty answers, a structured VAT filing guide, the year-specific 2024/2025 IIT
guide, citation mapping, out-of-scope refusal, and the public `/api/chat` response contract.

The remaining release gates are:

- Review the nine draft guide-image metadata entries if guide images are desired. IIT filling-guide
  images are intentionally not selected. Draft images are not indexed or published.
- Commit the reviewed code and metadata, then push to `main` so the deployment workflow applies
  Terraform. No local Terraform apply is required.

The successful build still contains extraction warnings that should remain visible during review,
including OCR use and possible table-layout loss. They are not audit errors, but answers should be
checked against their cited pages before the corpus is treated as release-ready. Do not use
`--delete-stale` without a complete matching `corpus-scope.json`.

## Index schema and creation

Index creation is separate from corpus extraction. The default operation of
`create_search_index.py` is a local dry run; `--dry-run` makes that intent explicit:

```bash
cd backend
uv run python ../scripts/create_search_index.py \
  --dry-run \
  --index tax-assistant \
  --dimensions 768
```

The dry run does not contact Azure and does not require cloud credentials. After the schema,
embedding model, dimensions, and approved corpus have been reviewed, create the index with the
explicit mutation flag. This requires `AZURE_SEARCH_ENDPOINT` and `AZURE_SEARCH_KEY`:

```bash
uv run python ../scripts/create_search_index.py \
  --upload \
  --index tax-assistant \
  --dimensions 768
```

Never pass `--upload` merely to test a command. The deployment workflow also runs the index
creation step after Terraform provisions Azure. A push to `main` is the intended Terraform
execution path for this project; running Terraform locally is optional and is not required for
this data workflow. Do not run a local `terraform apply` here.

## Embeddings, upload, evaluation, and promotion

First perform the no-key estimate. Keep provider values empty in the environment (and do not
print or paste secrets):

```bash
cd backend
GEMINI_API_KEY= AZURE_SEARCH_ENDPOINT= AZURE_SEARCH_KEY= \
  uv run python ../scripts/ingest_corpus.py \
    --chunks ../data/processed/corpus/chunks.jsonl \
    --cache ../data/processed/corpus/embedding-cache.sqlite3 \
    --dry-run \
    --report ../data/processed/corpus/ingestion-report.json
```

This validates chunk records and reports counts, source/type breakdown, estimated embedding
characters, cache hits/misses, and missing cloud configuration. It does not call Gemini or
upload to Search when those values are empty.

After explicit approval, set `GEMINI_API_KEY`, `AZURE_SEARCH_ENDPOINT`, `AZURE_SEARCH_KEY`,
`GEMINI_EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, and `AZURE_SEARCH_INDEX=tax-assistant`,
then upload with the separate explicit flag:

```bash
uv run python ../scripts/ingest_corpus.py \
  --chunks ../data/processed/corpus/chunks.jsonl \
  --cache ../data/processed/corpus/embedding-cache.sqlite3 \
  --manifest ../data/metadata/sources.yaml \
  --upload \
  --report ../data/processed/corpus/ingestion-report.json
```

Embeddings use the same model and dimensions as runtime query embeddings. The SQLite cache is
keyed by model, dimensions, and embedding-text hash, so successful unchanged chunks are reused.
Stable chunk IDs make failed upload batches safe to retry. Do not use `--delete-stale` until the
complete replacement corpus has been verified and `corpus-scope.json` proves that it matches the
manifest:

```bash
uv run python ../scripts/ingest_corpus.py \
  --chunks ../data/processed/corpus/chunks.jsonl \
  --cache ../data/processed/corpus/embedding-cache.sqlite3 \
  --manifest ../data/metadata/sources.yaml \
  --upload \
  --delete-stale \
  --report ../data/processed/corpus/ingestion-report.json
```

`--delete-stale` is destructive within the verified source scope and requires `--upload`, a
matching manifest, and a complete corpus build. Never use it with a partial source selection.

Evaluate retrieval against [evaluation/rag_questions.json](evaluation/rag_questions.json),
including year-sensitive, portal, foreign-tax, unsupported, and prompt-injection questions.
Fill expected source IDs only after the real corpus is approved. Check answer groundedness,
source markers, effective dates, authority ranking, refusal behavior, and guide-step citations
before switching the backend.

The deployed backend uses `AZURE_SEARCH_INDEX=tax-assistant`. Evaluate retrieval and citations
after uploading the approved corpus before treating the deployment as release-ready.

## Guide pages and public images

Screenshots are development inputs. Sanitize names, TINs, credentials, tokens, account details,
and other confidential data before inspection. Convert each workflow to the structure in
[data/sample/portal-guide.example.yaml](data/sample/portal-guide.example.yaml), manually verify
every step, and set `review_status: approved`. Draft or non-LK guides are rejected; image files
and image paths are not indexed as ordinary source evidence.

To create image candidates, put explicit positive page numbers in the source registry's
`render_pages` field and rebuild the corpus. Review the rendered pages and record only approved
public image metadata in `data/metadata/guide-images.yaml`. The image manifest is intentionally
separate from source approval.

Run the no-mutation image validation with a public HTTPS media origin:

```bash
uv run python ../scripts/publish_guide_images.py \
  --manifest ../data/metadata/guide-images.yaml \
  --directory ../data/processed/corpus/rendered-images \
  --report ../data/processed/corpus/guide-image-publish-report.json \
  --base-url https://media.example.invalid \
  --dry-run
```

The dry run validates approved entries, hashes, dimensions, WebP MIME type, target paths, and
immutable cache headers. Replace the example origin with the provisioned public guide-media
origin before release. Publishing requires the explicit `--publish` flag and either
`AZURE_GUIDE_MEDIA_CONNECTION_STRING` or both `AZURE_GUIDE_MEDIA_ACCOUNT_URL` and
`AZURE_GUIDE_MEDIA_ACCOUNT_KEY`:

```bash
uv run python ../scripts/publish_guide_images.py \
  --manifest ../data/metadata/guide-images.yaml \
  --directory ../data/processed/corpus/rendered-images \
  --report ../data/processed/corpus/guide-image-publish-report.json \
  --base-url https://media.example.invalid \
  --publish
```

The public guide-media container is separate from the private source-document container. Do not
publish raw screenshots or unreviewed pages.

## Incremental updates and rollback

When an approved source changes, review the replacement snapshot first, then update its checksum
and metadata. Rebuild only the affected source with repeatable `--source-id` and `--force`
options, review the audit, and rerun the no-key ingestion estimate. Extraction and chunk outputs
are written atomically. Unchanged chunks reuse the local embedding cache; failed upload batches
can be retried using stable IDs.

Runtime rollback is configuration-only: restore the prior `AZURE_SEARCH_INDEX` value and
restart/redeploy the backend. Do not delete raw sources or clear the embedding cache. Image
rollback restores the previous approved image manifest or removes image associations from new
responses; content-hashed public assets may remain cached.

Every cloud-facing operation has an explicit mutation flag:

- `create_search_index.py --upload` creates or updates a Search index.
- `ingest_corpus.py --upload` requests embeddings and uploads Search documents.
- `ingest_corpus.py --delete-stale` additionally permits verified scoped stale deletion.
- `publish_guide_images.py --publish` uploads approved public images.

Without those flags, the supported checks are local validation/dry runs. Keep credentials out of
the repository and command output, and obtain approval immediately before each cloud mutation.
