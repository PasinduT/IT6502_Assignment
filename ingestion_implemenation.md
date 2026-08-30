# Sri Lanka Tax Assistant Ingestion Implementation Plan

Status: planning and worker-execution specification only. No ingestion, indexing,
infrastructure, or runtime changes are implemented by this document.

## 1. Objective

Build a reliable ingestion and retrieval pipeline that enables the chatbot to:

- answer Sri Lankan tax questions using approved official evidence;
- explain tax terminology and form fields in plain language;
- respect taxable periods, effective dates, amendments, and superseded material;
- create situation-specific procedural guides from retrieved evidence;
- attach precise citations to tax claims and guide steps; and
- display relevant, approved public images in guides without generating images.

The first implementation will use the official IRD material already downloaded. A browser
capture application and general screenshot-ingestion workflow are deferred.

## 2. Current corpus

After hash-based deduplication across the downloaded research collections, the currently
available official IRD corpus contains approximately:

- 147 unique official files;
- 104 PDFs;
- 22 HTML pages;
- 21 XLSM templates;
- 1,835 PDF pages;
- 85 PDF pages with no directly extractable text; and
- 2.45 million directly extractable PDF characters.

The existing PDF text would produce roughly 2,100 chunks with the current character-based
splitter. The final number will change after structural chunking, OCR, HTML extraction, and
spreadsheet extraction.

Third-party websites, Reddit pages, and YouTube pages may remain useful for research, but
they will not be indexed as authoritative chatbot evidence. Only approved official IRD
sources will enter the production index.

## 3. Scope

### Included

- Official IRD PDFs, HTML pages, and XLSM templates already downloaded.
- Local PDF text extraction with selective OCR fallback.
- HTML main-content and table extraction.
- Safe structural extraction from XLSM files without executing macros.
- Source approval, deduplication, checksums, and version metadata.
- Section-, page-, table-, sheet-, and cell-aware chunking.
- Incremental Gemini embeddings with a local cache.
- A version-aware Azure AI Search index.
- Retrieval changes for authority, effective date, tax year, and document status.
- Precise citations in chatbot answers.
- Public guide images produced from selected approved guide pages.
- Structured guide responses and a dedicated frontend guide renderer.
- Dry-run reports and manual retrieval/evaluation checks.

### Deferred

- Browser automation and authenticated portal capture.
- A general-purpose screenshot upload/ingestion application.
- Generated images.
- Runtime user uploads.
- Automatic web crawling or unattended publication of newly discovered documents.
- Sinhala and Tamil ingestion unless explicitly added to the first release scope.
- A new automated test suite unless separately requested.

## 4. Design principles

1. **Official evidence only:** tax facts and procedures must come from approved official
   sources.
2. **Local extraction first:** PDF parsing, OCR, HTML parsing, spreadsheet inspection,
   chunking, and metadata validation should not require Gemini.
3. **No guessed metadata:** unknown dates, status, and relationships remain null or require
   review.
4. **Version awareness:** a newer guide does not automatically outrank legislation, and an
   older source must not be presented as current without qualification.
5. **Stable citations:** every searchable chunk retains an exact source and locator.
6. **Incremental cost:** unchanged chunks are never embedded twice.
7. **Images are selected, not generated:** the model may reference only approved image IDs
   supplied by retrieval.
8. **Review before publication:** extraction can be automatic, but production eligibility
   is explicit.

## 5. Target architecture

The pipeline will have two related outputs: searchable evidence and public guide images.

```text
Downloaded official sources
        |
        v
Approved source registry
        |
        +------------------------------+
        |                              |
        v                              v
PDF / HTML / XLSM extraction     Selected guide-page rendering
        |                              |
        v                              v
Normalized document records      Approved public image assets
        |                              |
        +---------------+--------------+
                        |
                        v
              Structure-aware chunks
                        |
                        v
          Cached Gemini document embeddings
                        |
                        v
             Versioned Azure Search index
                        |
                        v
             Retrieval and deterministic reranking
                        |
                        v
             Gemini grounded answer generation
                        |
                        v
        Answer + structured guide + citations
```

## 6. Phase 1: Approved source registry

Create a production source manifest derived from the download manifests and the resolved
official source list. Raw files remain ignored by Git; the committed registry contains
metadata and official URLs, not downloaded binaries.

Each source record should include:

```yaml
id: ird-iit-guide-2025-2026
title: Guide to Individual Income Tax Return 2025/2026
source_url: https://www.ird.gov.lk/...
local_file: ../raw/...
media_type: application/pdf
document_type: return_guide
authority_level: official_guide
authority_rank: 40
tax_types:
  - IIT
taxpayer_types:
  - individual
tax_year: 2025/2026
published_date: null
effective_from: 2025-04-01
effective_to: 2026-03-31
status: current
supersedes: null
language: en
jurisdiction: LK
sha256: "..."
review_status: approved
tags:
  - return
  - filing
```

Required controlled values will include:

- document type: `act`, `gazette`, `circular`, `public_notice`, `tax_calendar`,
  `return_form`, `return_guide`, `portal_guide`, `spreadsheet_template`, or
  `official_webpage`;
- status: `current`, `historical`, `superseded`, or `excluded`;
- review status: `draft`, `approved`, or `rejected`; and
- authority level and an associated deterministic rank.

The registry builder will:

- merge all existing download manifests;
- deduplicate identical files by SHA-256;
- allow multiple URLs to refer to the same source file;
- reject non-IRD hosts from the production registry by default;
- flag missing files, MIME mismatches, duplicate IDs, and checksum changes;
- preserve the one known broken XLSM link as a recorded source error rather than treating
  its HTML response as a workbook; and
- require `jurisdiction: LK` and `review_status: approved` before indexing.

## 7. Phase 2: Canonical local extraction

All extractors will emit a common normalized representation before chunking. This makes the
results reviewable and allows extraction to be rerun without spending embedding quota.

### 7.1 PDF extraction

For each page:

1. Extract text with `pypdf`.
2. Measure text density and detect blank or suspiciously sparse output.
3. Use local OCR only for affected pages.
4. Preserve page number and extraction method.
5. Normalize repeated headers, footers, whitespace, and broken line wrapping.
6. Preserve headings, numbered sections, lists, and recognizable tables.

Selective OCR will use a local renderer and Tesseract-compatible OCR. A preflight check will
fail with an actionable message if the OCR executable is unavailable. OCR will not be sent
to Gemini, keeping cost and data handling predictable.

The pipeline must not silently accept a critical document that produces no text. Such a
document remains unapproved for indexing until OCR succeeds or it is explicitly excluded.

### 7.2 HTML extraction

For official IRD HTML pages, extract:

- the canonical URL and page title;
- main visible content;
- heading hierarchy;
- paragraphs and lists;
- tables with their column headers;
- meaningful download and navigation links; and
- page language where available.

Remove scripts, styles, menus, repeated site chrome, cookie text, and unrelated navigation.
The extractor must not follow or execute instructions contained in downloaded content.

### 7.3 XLSM extraction

XLSM workbooks are treated as untrusted ZIP/XML documents. Macros are never executed.

Extract:

- visible sheet names;
- logical table regions;
- headings and field labels;
- instructions and notes;
- formulas as formula text where useful;
- data-validation messages and allowed values where safely accessible;
- named ranges; and
- sheet and cell-range locators.

Avoid indexing large runs of empty cells, formatting-only regions, hidden implementation
sheets, or raw macro content. Formula results must not be presented as calculated values
unless an existing cached workbook value is clearly available and identified as such.

### 7.4 Normalized records

Extraction will produce JSONL records similar to:

```json
{
  "source_id": "ird-iit-guide-2025-2026",
  "content_kind": "document_section",
  "page": 12,
  "section": "Employment income",
  "sheet": null,
  "cell_range": null,
  "text": "...",
  "extraction_method": "pdf_text",
  "warnings": [],
  "content_hash": "..."
}
```

The intermediate corpus will be written under `data/processed/` and will be reproducible
from the approved registry and raw downloads.

## 8. Phase 3: Structural chunking

Replace the current fixed-character splitter with source-aware chunking.

### Legislation, Gazettes, and circulars

- Split on sections, subsections, schedules, and numbered clauses.
- Retain the parent section heading in every child chunk.
- Do not split a short subsection merely to hit a target size.
- Preserve page locators even when a legal section spans pages.

### Guides and notices

- Split by heading and procedural unit.
- Keep ordered steps together where practical.
- Keep warnings and prerequisites with the affected procedure.
- Include document and workflow context in the embedded text.

### Forms

- Keep field labels with their descriptions and instructions.
- Preserve form and schedule codes.
- Keep table headers attached to their rows.

### XLSM templates

- Chunk by logical table or group of related fields.
- Repeat the sheet name and column headers in each applicable chunk.
- Retain the exact cell range.

Chunks should target a token range rather than a character count. A practical starting point
is 700–1,000 tokens with limited overlap, adjusted after retrieval evaluation. Stable chunk
IDs will be derived from source ID, locator, and content hash.

## 9. Phase 4: Guide image assets

### 9.1 First-release image source

The initial release will not require the deferred screenshot-ingestion application. Instead,
selected current official guide PDFs can supply images.

For approved guide documents:

- identify pages that contain useful portal or form guidance;
- render the complete page to a web-friendly image;
- convert it to WebP;
- strip unnecessary metadata;
- create a content-hashed filename;
- record width, height, source, page, effective dates, and workflow association; and
- require explicit approval before publishing.

Rendering selected pages is preferred over blindly extracting every PDF image object because
a PDF screenshot may be split into multiple objects and mixed with logos or decorative
elements.

### 9.2 Image registry

Each image asset will have a record such as:

```yaml
image_id: vat-return-step-period
public_url: https://media.example.lk/guides/vat-return-step-period.<hash>.webp
title: Select the VAT return period
alt_text: IRD portal page showing the VAT taxable-period selector
caption: Choose the applicable taxable period before continuing.
source_id: ird-vat-quick-guide-2025
source_url: https://www.ird.gov.lk/...
page: 7
workflow_id: file-vat-return
tax_types:
  - VAT
effective_from: 2025-01-01
effective_to: null
status: current
review_status: approved
```

The text fields and workflow metadata are searchable. The image binary is not embedded.

### 9.3 Public storage

Public images should not share the existing private raw-document container. Provision a
separate public media storage account or equivalent public asset origin with:

- anonymous read-only access;
- no anonymous write or listing requirement;
- content-hashed immutable filenames;
- `Cache-Control: public, max-age=31536000, immutable`;
- correct image MIME types;
- optional CDN or Front Door later; and
- a clear separation from private source retention.

Because every published guide image is intentionally public, signed URLs are not needed.
Expiring signed URLs would break stored conversations and cached guide responses.

### 9.4 Future standalone screenshots

When a cleaned standalone screenshot is later supplied with only an image and source URL,
it will need searchable context. A future lightweight registration step can use local OCR
and, when needed, one Gemini multimodal call to propose a caption, UI labels, workflow,
and tags. Publication will still require approval. This is separate from the deferred
browser-capture application.

## 10. Phase 5: Embeddings and incremental indexing

Gemini will be used only after extraction, review, and chunking are complete.

The embedding stage will:

- embed only approved chunks;
- use the same embedding model and dimensions as runtime query embeddings;
- cache vectors by model, dimensions, and content hash;
- batch requests;
- apply bounded retries and quota-aware backoff;
- checkpoint completed batches;
- resume without re-embedding successful chunks;
- report exact input counts before making API calls; and
- fail rather than uploading records with missing or incorrectly sized vectors.

A local embedding cache under ignored processed data will make future runs incremental.

### Planned Azure Search fields

In addition to the existing fields, the new index should include:

- `authority_level`;
- `authority_rank`;
- `tax_types`;
- `taxpayer_types`;
- `language`;
- `status`;
- `supersedes`;
- `form_code`;
- `sheet`;
- `cell_range`;
- `source_hash`;
- `chunk_hash`;
- `image_id`;
- `image_url`;
- `image_alt_text`; and
- `image_caption`.

Where Azure field types allow, applicable categorical fields will be filterable and
facetable. Titles, content, section names, tags, and form terminology remain searchable.

### Safe index rollout

Keep the existing `tax-assistant` index name. For the initial migration, first verify that the
old index is empty, recreate that exact index with the new schema, upload the complete approved
corpus, verify document and chunk counts, and run retrieval checks before treating the backend as
ready. Normal deployments use idempotent schema creation and do not delete the index.

For a future incompatible schema change, use a reviewed migration with an explicit backup and
rollback plan. Do not introduce a second index name unless a later deployment specifically needs
and approves a blue/green rollout.

## 11. Phase 6: Retrieval improvements

The current runtime performs hybrid search with an optional tax-year filter. It needs
additional deterministic relevance controls.

### Query context

- Use the latest question plus a bounded amount of recent user context for ambiguous
  follow-ups.
- Detect tax years and common taxable-period formats.
- Detect tax-type and taxpayer-type vocabulary using a maintained alias map.
- Expand common jargon deterministically, for example APIT, AIT, WHT, SET, SSCL, TIN, and
  RAMIS.
- Do not add a separate Gemini query-rewrite call for every message.

### Candidate selection

1. Retrieve a larger hybrid candidate set.
2. Remove sources that are not applicable to the requested tax year when applicability is
   known.
3. Prefer current sources unless the user explicitly asks about a historical year.
4. Apply deterministic authority and effective-date boosts.
5. Preserve lower-level official guidance when the question is procedural.
6. Diversify results so a single long source does not occupy the whole context.
7. Include image records only when they are associated with retrieved applicable evidence
   or the same supported workflow.

Raw Azure hybrid scores should not be treated as universally calibrated probabilities. The
existing minimum score must be calibrated against the real corpus rather than accepted as a
production threshold without evaluation.

### Conflict handling

The runtime context will expose status, authority, effective dates, and superseding sources
to Gemini. The prompt will require it to:

- use the rule applicable to the requested period;
- distinguish an Act from administrative guidance;
- disclose material conflicts;
- identify historical guidance as historical; and
- state that evidence is insufficient instead of combining incompatible versions.

## 12. Phase 7: Answers, citations, and structured guides

### Citation behavior

Every retrieved block will have a model-visible source marker. The backend will map only
known markers into citation objects and discard invented markers.

Citations should include:

- official document title;
- document type;
- source URL;
- page and section;
- sheet and cell range where applicable;
- published and effective dates;
- tax year; and
- document status.

The prompt will require citations for material tax facts and procedural steps. It will also
prohibit invented rates, deadlines, form fields, sections, and URLs.

### Structured guide response

Extend the chat response with an optional guide object rather than relying on arbitrary
Markdown images:

```json
{
  "answer": "Here is how to complete the relevant part of your return.",
  "guide": {
    "title": "Report employment and rental income",
    "steps": [
      {
        "number": 1,
        "title": "Open the income section",
        "instruction": "Select the applicable income categories.",
        "image_id": "iit-income-categories",
        "citation_ids": ["1", "2"]
      }
    ]
  },
  "citations": []
}
```

Gemini may select only retrieved image IDs. The backend will replace valid IDs with trusted
image metadata and public URLs. Unknown image IDs and arbitrary model-produced URLs will be
discarded.

The guide field remains null for ordinary questions that do not benefit from a procedure.

### Frontend rendering

Add a dedicated guide component that supports:

- numbered steps;
- step titles and instructions;
- an optional image below the relevant step;
- captions and accessible alt text;
- responsive sizing;
- click-to-expand images;
- citations associated with individual steps; and
- graceful rendering when a step has no image.

The existing Markdown answer remains available for summaries and non-guide responses.

## 13. Phase 8: Validation and release gates

### Extraction gate

The dry-run report must show:

- every approved source found and checksum verified;
- duplicate sources resolved;
- MIME mismatches rejected;
- zero-text and OCR pages accounted for;
- malformed documents reported;
- no macro execution;
- normalized-record counts by source and type; and
- chunks with missing citations or metadata.

Representative PDFs, OCR pages, HTML tables, and XLSM field regions will be manually
inspected before embedding.

### Retrieval gate

Use the existing evaluation material and manual smoke questions covering:

- IIT, CIT, VAT, SSCL, APIT, WHT/AIT, and stamp duty;
- current and historical tax years;
- deadlines and tax calendar questions;
- form-field jargon;
- refunds and clearances;
- procedural guides;
- questions with insufficient evidence;
- foreign-tax and unrelated questions;
- prompt injection inside retrieved content;
- source conflicts; and
- relevant versus irrelevant guide images.

Expected source IDs should be populated for the approved corpus. No new automated test suite
will be introduced unless explicitly requested.

### Release gate

Before the backend switches to the new index:

- extraction errors must be resolved or explicitly excluded;
- no current critical scanned document may remain silently empty;
- citations must open the correct official source;
- current questions must not default to superseded documents;
- image URLs must be public and stable;
- all returned image IDs must exist and be approved;
- backend linting must pass;
- the frontend production build must pass if guide rendering is implemented; and
- a final sample of grounded responses must be reviewed.

## 14. Cost and credential strategy

No higher-usage Gemini API key is needed to begin implementation.

The following stages require no Gemini key:

- registry generation and validation;
- PDF, HTML, and XLSM extraction;
- local OCR;
- structural chunking;
- guide-page rendering;
- public-image metadata generation from surrounding PDF text; and
- dry-run reporting.

A Gemini key is required for:

- final document embedding;
- runtime query embeddings;
- grounded response generation; and
- optional future vision analysis for standalone screenshots.

Before the first embedding request, the pipeline will report the exact approved chunk count
and estimated input size. Embedding caching and checkpointing will prevent repeated charges.
If the current key reaches a quota limit, ingestion can stop and resume safely after a
higher-quota key is configured.

Credentials must remain in `backend/.env` or deployment secrets and must never be written to
manifests, processed records, logs, or frontend code. Azure Search credentials are needed
only when creating and uploading the production index.

## 15. Planned repository changes

The implementation is expected to affect the following areas:

### Data and metadata

- Add a production source registry under `data/metadata/`.
- Add an image registry or generated approved-image manifest.
- Keep downloaded files and generated binary assets out of Git.
- Store normalized extraction and embedding cache data under ignored processed paths.

### Ingestion scripts

- Refactor shared ingestion configuration and upload behavior.
- Add source-registry validation and corpus building.
- Add PDF/OCR, HTML, and XLSM extractors.
- Add structural chunking and audit reporting.
- Add guide-page rendering and public-asset publication.
- Add embedding cache, resume support, stale-record handling, and dry-run modes.
- Update Search-index creation for the new schema.

### Backend

- Extend search result models and selected fields.
- Add date-, status-, authority-, and tax-type-aware retrieval.
- Include precise source and image evidence in the model context.
- Extend citation fields.
- Add an optional structured guide response.
- Validate all model-selected citation and image IDs.
- Update the grounded system prompt.

### Frontend

- Extend API and TypeScript response types.
- Add a structured guide renderer.
- Add accessible, responsive public-image display.
- Preserve the existing standard chat-message experience.

### Infrastructure and documentation

- Add separate public media storage while preserving private source storage.
- Add media-origin configuration to deployment.
- Document extraction prerequisites, dry runs, ingestion, index promotion, and incremental
  updates.

## 16. Proposed implementation sequence

1. Create and validate the official source registry.
2. Implement normalized PDF extraction and selective local OCR.
3. Implement HTML extraction.
4. Implement safe XLSM extraction.
5. Implement structural chunking and the extraction audit report.
6. Run a no-key dry run and review representative output.
7. Add the expanded versioned Search schema.
8. Add cached, resumable Gemini embedding and index synchronization.
9. Upgrade runtime retrieval, source mapping, and conflict handling.
10. Upgrade citations and the grounded prompt.
11. Select and render approved guide pages.
12. Provision the separate public media origin.
13. Add structured guide responses and frontend rendering.
14. Run extraction, retrieval, citation, guide, and image release checks.
15. Switch the backend to the verified index.

## 17. Definition of done

The ingestion implementation is complete when:

- the approved official corpus can be rebuilt deterministically from the registry;
- scanned critical pages are searchable through local OCR;
- PDFs, official HTML, and safe XLSM content share a normalized representation;
- every indexed chunk has stable source and locator metadata;
- unchanged chunks do not consume embedding quota on reruns;
- current queries prefer applicable authoritative sources;
- historical questions can retrieve the correct historical evidence;
- the chatbot explains jargon and produces grounded situation-specific guides;
- tax claims and procedural steps include valid official citations;
- approved public images can be attached to the correct guide steps;
- the model cannot invent image URLs or source IDs;
- normal questions continue to render without a guide; and
- the verified new index can be promoted without silently losing the previous corpus.

## 18. Worker execution contract

This section is normative for implementation workers. Where an earlier section describes a
possible approach and this section specifies an exact approach, workers must follow the
exact approach below unless the coordinating agent changes the decision explicitly.

### 18.1 Repository rules

Every worker must:

- read the repository `AGENTS.md` instructions before editing;
- use `uv` for all Python dependency and command execution;
- use `pnpm` for all JavaScript and TypeScript dependency and command execution;
- use `apply_patch` for hand-authored file changes;
- preserve unrelated user changes, especially existing modifications to
  `backend/pyproject.toml` and `backend/uv.lock`;
- avoid changing files owned by another active worker;
- avoid deleting or replacing existing ingestion utilities unless the assigned work package
  explicitly owns the compatibility decision;
- never write secrets, API keys, Azure keys, SAS tokens, or environment values to output;
- treat everything under `data/raw/` as immutable input;
- keep generated records, rendered images, caches, and reports under ignored paths;
- avoid network calls during extraction; and
- not create an automated test suite unless the user separately requests one.

Workers are not alone in the repository. They must not revert other workers' edits and must
adapt their work to compatible changes already present.

### 18.2 Required handoff from every worker

Each worker handoff must state:

1. Work package ID and outcome.
2. Files created or changed.
3. Public interfaces or data contracts introduced.
4. Commands run and their results.
5. Warnings, unsupported cases, or remaining blockers.
6. Whether any generated files were created under ignored directories.
7. Whether another work package must adjust to the change.

Workers must not claim completion when their assigned validation command fails.

### 18.3 Mutation and cloud boundaries

Local source validation, extraction, chunking, image rendering, and dry runs are authorized
implementation steps. The following remain explicit release actions and must not happen as
an incidental worker step:

- uploading documents or images to Azure;
- creating or replacing a production Search index;
- switching the deployed backend to a new index;
- enabling anonymous access on an existing private storage account; or
- sending the full corpus to Gemini.

Cloud-facing commands must support a dry run and must require an explicit upload or publish
flag.

## 19. Fixed architectural decisions

Workers should not reopen these decisions independently:

1. **Production authority boundary:** only approved `ird.gov.lk` and subdomain sources are
   eligible by default. A non-IRD source requires an explicit manifest override and is out
   of scope for this implementation.
2. **Raw source retention:** raw documents stay in ignored local storage and the existing
   private Azure source container.
3. **Public media separation:** guide images use a different public media container or
   storage account; the private raw-document container remains private.
4. **OCR:** use local OCR on selected pages. Do not call Gemini vision for PDF OCR.
5. **Spreadsheet safety:** inspect XLSM XML with `openpyxl`; never execute macros or open the
   workbooks through Excel automation.
6. **Intermediate format:** newline-delimited JSON is the canonical extraction and chunk
   exchange format.
7. **Embedding cache:** use SQLite from the Python standard library. Do not store vectors in
   Git.
8. **Index rollout:** target a versioned Search index. Do not mutate the active index during
   initial development.
9. **Retrieval:** use one query embedding per user request. Do not add an LLM query-rewrite
   call.
10. **Images:** use trusted image IDs and backend mapping. Do not let model-produced URLs
    flow directly to the frontend.
11. **Chat API compatibility:** new response fields are optional and additive.
12. **MVP language:** ingest English sources first. Other languages remain registered but
    excluded from the first production build unless explicitly approved.

## 20. Target code and data layout

The implementation should converge on this layout:

```text
scripts/
  build_corpus.py                 # local validate/extract/chunk orchestration
  ingest_corpus.py                # embedding cache and Search upload CLI
  publish_guide_images.py         # explicit public-image upload CLI
  create_search_index.py          # versioned Search schema
  ingestion/
    __init__.py
    models.py                     # canonical Pydantic models and enums
    registry.py                   # manifest load/validation/deduplication
    normalize.py                  # shared text cleanup only
    chunking.py                   # structure-aware chunk production
    audit.py                      # reports and error aggregation
    extractors/
      __init__.py
      pdf.py
      html.py
      xlsm.py
    images.py                     # candidate selection and page rendering
    embedding_cache.py            # SQLite cache
    search_upload.py              # embedding batches and index synchronization

data/
  metadata/
    sources.yaml                  # approved source registry
    guide-images.yaml             # approved public image metadata
    sources.example.yaml          # retained example
  processed/                      # ignored generated output
    corpus/
      extraction.jsonl
      chunks.jsonl
      image-candidates.json
      audit.json
      source-summary.json
      embedding-cache.sqlite3
      rendered-images/

backend/app/
  schemas.py
  prompts/system_prompt.py
  services/models.py
  services/query.py
  services/search.py
  services/rag.py
  services/gemini.py

frontend/src/
  types/chat.ts
  lib/api.ts
  components/Message.tsx
  components/Guide.tsx
```

Small helpers may be added inside these modules, but workers should not create competing
parallel model definitions or a second ingestion package.

The existing `scripts/ingest_tax_documents.py` and `scripts/ingest_portal_guides.py` should
remain functional until the new corpus pipeline is complete. They may later become thin
compatibility wrappers, but removal is not part of the first implementation.

## 21. Exact source-registry contract

### 21.1 Top-level shape

`data/metadata/sources.yaml` must use:

```yaml
schema_version: 1
allowed_hosts:
  - www.ird.gov.lk
  - ird.gov.lk
  - eservices.ird.gov.lk
sources: []
```

Unknown top-level fields and unknown source fields must be rejected. YAML dates must be
normalized to ISO `YYYY-MM-DD` strings by the loader rather than depending on implicit YAML
date objects downstream.

### 21.2 Source record

Required fields:

| Field | Type | Rule |
|---|---|---|
| `id` | string | Lowercase slug matching `^[a-z0-9][a-z0-9-]{2,99}$`; globally unique. |
| `title` | string | Non-empty official or reviewed title. |
| `source_url` | HTTPS URL | Host must be in `allowed_hosts`. |
| `local_file` | string | Relative to the manifest directory; resolved path must stay inside the repository. |
| `media_type` | enum | `application/pdf`, `text/html`, or XLSM MIME type. |
| `document_type` | enum | One of the controlled document types below. |
| `authority_level` | enum | One of the controlled authority values below. |
| `authority_rank` | integer | Must match the configured authority level. |
| `jurisdiction` | string | Must equal `LK`. |
| `language` | enum | `en`, `si`, or `ta`. |
| `status` | enum | `current`, `historical`, `superseded`, or `excluded`. |
| `review_status` | enum | `draft`, `approved`, or `rejected`. |
| `sha256` | string | Exactly 64 lowercase hexadecimal characters. |

Optional fields:

| Field | Type | Default/meaning |
|---|---|---|
| `final_url` | HTTPS URL | URL reached during download. |
| `published_date` | ISO date or null | Official publication date only. |
| `effective_from` | ISO date or null | First applicable date. |
| `effective_to` | ISO date or null | Last applicable date, inclusive. |
| `tax_year` | string or null | Canonical `YYYY/YYYY`, not `25/26`. |
| `document_version` | string or null | Official version label. |
| `supersedes` | list of source IDs | Empty list by default. |
| `superseded_by` | list of source IDs | Empty list by default. |
| `tax_types` | list of strings | Empty list means cross-tax or unknown. |
| `taxpayer_types` | list of strings | Empty list means general or unknown. |
| `form_code` | string or null | Exact official code where visible. |
| `workflow_ids` | list of slugs | Empty list by default. |
| `aliases` | list of strings | Search aliases and expanded acronyms. |
| `tags` | list of slugs | Additional deterministic labels. |
| `notes` | string or null | Reviewer notes; not indexed by default. |
| `render_pages` | list of positive integers | Explicit guide-image candidates. |
| `exclusion_reason` | string or null | Required when status is `excluded`. |

Controlled document types:

```text
act
gazette
circular
public_notice
tax_calendar
return_form
return_guide
portal_guide
spreadsheet_template
official_webpage
```

Controlled authority values and fixed ranks:

| Authority | Rank |
|---|---:|
| `legislation` | 100 |
| `gazette` | 90 |
| `official_circular` | 80 |
| `official_notice` | 70 |
| `official_calendar` | 60 |
| `official_form` | 50 |
| `official_guide` | 40 |
| `official_webpage` | 30 |

Authority rank is not a universal answer-quality score. It is used only after applicability
and query intent are considered. A procedural question can prefer an applicable official
guide over an Act while still presenting the Act as the legal authority.

### 21.3 Registry validation invariants

Validation must reject the registry when:

- two entries use the same ID;
- an approved non-excluded entry points to a missing file;
- the resolved local path escapes the repository;
- the actual checksum differs from `sha256`;
- media type disagrees with file signature;
- an XLSM URL downloaded an HTML error page;
- `effective_to` is earlier than `effective_from`;
- an entry supersedes itself;
- a superseding ID is unknown;
- `tax_year` is not canonical;
- an excluded source lacks `exclusion_reason`;
- an approved source is not from an allowed host; or
- a rendered page is outside the PDF page count.

Duplicate hashes with different source IDs are warnings unless the records claim different
legal versions. The registry builder should nominate one canonical record and retain other
URLs as aliases; it must not silently discard them.

## 22. Exact normalized extraction contract

Each line in `extraction.jsonl` must conform to this logical shape:

```json
{
  "schema_version": 1,
  "record_id": "ird-iit-guide-2025-2026-p12-s2",
  "source_id": "ird-iit-guide-2025-2026",
  "content_kind": "section",
  "ordinal": 17,
  "title_path": ["Guide", "Employment income"],
  "content": "Normalized visible text...",
  "page": 12,
  "page_end": 12,
  "section": "Employment income",
  "sheet": null,
  "cell_range": null,
  "table_headers": [],
  "extraction_method": "pdf_text",
  "warnings": [],
  "content_hash": "64-character SHA-256"
}
```

Rules:

- `record_id` is unique within the extraction output.
- `ordinal` is stable document order starting at one.
- `content` contains visible source content, not generated summaries.
- `title_path` is ordered outermost to innermost and may be empty.
- PDF records require `page`; multipage records use inclusive `page_end`.
- XLSM records require `sheet` and should use `cell_range` when a bounded region exists.
- `extraction_method` is one of `pdf_text`, `pdf_ocr`, `html_dom`, or `xlsm_xml`.
- Empty content records are not emitted. Empty pages are reported in `audit.json`.
- Warnings are controlled codes, not free-form stack traces.
- Output order is source-registry order, then document ordinal.
- JSON serialization uses UTF-8, one object per line, and stable key ordering where practical.

Controlled warning codes should initially include:

```text
OCR_USED
OCR_WEAKER_THAN_NATIVE
EMPTY_PAGE
SPARSE_PAGE
TABLE_LAYOUT_LOSS
MALFORMED_HEADING
HIDDEN_SHEET_SKIPPED
FORMULA_WITHOUT_CACHED_VALUE
DATA_VALIDATION_PARTIAL
HTML_MAIN_FALLBACK
```

## 23. Extractor implementation details

### 23.1 PDF thresholds and behavior

Use these initial deterministic rules:

1. Extract and normalize native text.
2. If normalized text has at least 80 non-whitespace characters, retain native text.
3. If it has fewer than 80 characters, render the page at 250 DPI and run English OCR.
4. If OCR output has at least 20 more non-whitespace characters than native output, use OCR
   and emit `OCR_USED`.
5. Otherwise retain the stronger native output and emit `OCR_WEAKER_THAN_NATIVE` when OCR
   was attempted.
6. If neither result reaches 20 non-whitespace characters, report `EMPTY_PAGE` and emit no
   content record for that page.

These are initial operational thresholds, not claims about OCR accuracy. The audit report
must make them visible so they can be adjusted after sampling.

PDF extraction must:

- catch failures per source and continue collecting audit results;
- never treat encrypted or malformed PDFs as successfully ingested without text;
- retain exact one-based page numbers;
- avoid merging text across pages during extraction;
- normalize soft hyphens and common ligatures;
- avoid aggressive dehyphenation when it could alter legal wording;
- retain lists and numbered clauses on separate lines; and
- report table-layout loss rather than inventing cell relationships.

The implementation dependency set should use the existing `pypdf` plus `PyMuPDF`, Pillow,
and `pytesseract`. Tesseract itself is a system prerequisite and must be checked by the CLI.

### 23.2 HTML behavior

Parse downloaded snapshots only. Do not fetch during extraction.

Use this main-content selection order:

1. `<main>`;
2. an element with `role="main"`;
3. IRD content containers identified during implementation;
4. `<body>` with an `HTML_MAIN_FALLBACK` warning.

Remove `script`, `style`, `noscript`, navigation, footer, form controls unrelated to source
content, and repeated whitespace. Preserve headings, paragraphs, ordered/unordered lists,
definition lists, and tables. Serialize tables as readable rows with headers repeated in the
record context.

Resolve relative links against `source_url`, retain only HTTPS links, and do not follow
them. JavaScript URLs and inline event handlers are ignored.

The implementation dependency set should use Beautiful Soup and `lxml`; it must not add a
headless browser to the ingestion runtime.

### 23.3 XLSM safety and behavior

Before opening a workbook:

- confirm ZIP signature and expected workbook members;
- reject path-traversal member names;
- reject an unreasonable uncompressed archive size rather than expanding indefinitely;
- do not load VBA content; and
- do not follow external workbook links.

Open with formulas visible rather than evaluated and with VBA preservation disabled. Inspect
only visible worksheets in the first release. Emit one or more records per logical non-empty
region, preserving sheet and cell range.

For each relevant cell capture, when present:

- coordinate;
- displayed value or formula text;
- nearby heading context;
- comment text;
- data-validation prompt and error message;
- explicit list-validation values; and
- number-format label only when it clarifies expected input.

Do not infer a formula result by running spreadsheet calculations. Do not index hidden
worksheets, VBA source, binary objects, or formatting-only cells.

The implementation dependency set should use `openpyxl` and `defusedxml`.

### 23.4 Shared normalization

`normalize.py` may perform only deterministic cleanup:

- Unicode normalization;
- soft-hyphen removal;
- line-ending normalization;
- repeated blank-line collapse;
- trimming trailing whitespace; and
- conservative joining of obvious wrapped prose lines.

It must not paraphrase, translate, summarize, correct tax terminology, or change numbers.

## 24. Exact chunk contract and algorithm

Each line in `chunks.jsonl` must contain:

```json
{
  "schema_version": 1,
  "id": "ird-iit-guide-2025-2026-<stable-digest>",
  "source_id": "ird-iit-guide-2025-2026",
  "content": "Source text shown to the answer model...",
  "embedding_text": "Title and retrieval context followed by source text...",
  "content_type": "return_guide",
  "title": "Guide to Individual Income Tax Return 2025/2026",
  "source_url": "https://www.ird.gov.lk/...",
  "page": 12,
  "page_end": 12,
  "section": "Employment income",
  "sheet": null,
  "cell_range": null,
  "published_date": null,
  "effective_from": "2025-04-01",
  "effective_to": "2026-03-31",
  "tax_year": "2025/2026",
  "document_version": null,
  "workflow_id": null,
  "authority_level": "official_guide",
  "authority_rank": 40,
  "tax_types": ["IIT"],
  "taxpayer_types": ["individual"],
  "language": "en",
  "status": "current",
  "supersedes": [],
  "form_code": null,
  "tags": ["return", "filing"],
  "source_hash": "...",
  "chunk_hash": "...",
  "image_id": null,
  "image_url": null,
  "image_alt_text": null,
  "image_caption": null
}
```

`embedding_text` is an offline-only field and does not need to be stored in Azure Search.
It should contain compact retrieval context in this order:

```text
Document: <title>
Document type: <document_type>
Tax type: <comma-separated tax types, when known>
Tax year: <tax year, when known>
Section: <section/title path, when known>
Form: <form code, when known>
Aliases: <reviewed aliases, when present>

<verbatim normalized source content>
```

### 24.1 Size rules

Gemini does not require a local tokenizer for this pipeline. Use a conservative local
estimate of `ceil(character_count / 4)` for planning and chunk limits.

- Preferred chunk size: 600–900 estimated tokens.
- Hard maximum: 1,050 estimated tokens.
- Minimum merge target: 250 estimated tokens.
- Overlap when a single structural block must be split: approximately 100 estimated tokens.
- Never add overlap across unrelated headings, pages, sheets, or sources.

### 24.2 Structural rules

1. Start from normalized records, never raw file text.
2. Keep a legal subsection or short guide step intact when below the hard maximum.
3. Merge adjacent small records only when source, page, section, content kind, and applicable
   table headers are compatible.
4. Split long prose on paragraph boundaries, then sentence boundaries, and only then use a
   hard character boundary.
5. Repeat table headers in each split table chunk.
6. Preserve original document order.
7. Compute `chunk_hash` from normalized `content` plus locator metadata.
8. Compute stable `id` from source ID, locator label, and the first 24 hexadecimal characters
   of `chunk_hash`.
9. Identical chunks in one source should be deduplicated by locator and hash; identical text
   at materially different legal locations must remain separately citable.

## 25. Image contract and selection rules

### 25.1 Image publication is manifest-driven

Workers must not automatically publish every visually rich PDF page. The source registry's
`render_pages` field creates candidates. `guide-images.yaml` is the publication approval
list.

The local image step has two modes:

- candidate mode: render `render_pages` and write local metadata with no public URL;
- publish mode: upload only `review_status: approved` image entries with explicit
  `--publish`.

### 25.2 Image processing defaults

- Render at sufficient resolution for readable portal labels, initially 1,600 pixels on the
  long edge.
- Preserve aspect ratio.
- Convert to WebP with a quality setting around 85.
- Strip EXIF and nonessential metadata.
- Do not crop automatically in the first release.
- Generate filename `<image-id>.<first-16-sha256>.webp`.
- Record binary SHA-256, byte size, width, and height.
- Reject images with missing alt text, source ID, page, or approval status.

Image descriptions must be derived from reviewed registry text or the corresponding page
content. The worker must not invent UI actions that are not visible in the source.

### 25.3 Guide-image search records

Each approved image becomes a Search document with:

- `content_type = guide_image`;
- searchable `content` built from title, caption, alt text, workflow, and reviewed page text;
- the source and page fields of its originating guide;
- `image_id`, `image_url`, `image_alt_text`, and `image_caption`;
- the same tax type, taxpayer type, status, and applicability metadata as the source unless
  explicitly narrowed; and
- its own embedding and stable chunk ID.

An image record is supporting evidence, not legal authority. The answer model must cite the
underlying document source for the procedural step.

## 26. Search-index schema specification

The versioned index should contain these fields:

| Field | Azure type | Searchable | Filterable | Sortable | Facetable |
|---|---|---:|---:|---:|---:|
| `id` | `Edm.String` | No | Yes | No | No |
| `content` | `Edm.String` | Yes | No | No | No |
| `content_type` | `Edm.String` | No | Yes | No | Yes |
| `title` | `Edm.String` | Yes | Yes | No | No |
| `source_id` | `Edm.String` | No | Yes | No | No |
| `source_url` | `Edm.String` | No | No | No | No |
| `blob_path` | `Edm.String` | No | No | No | No |
| `page` | `Edm.Int32` | No | Yes | No | No |
| `page_end` | `Edm.Int32` | No | Yes | No | No |
| `section` | `Edm.String` | Yes | Yes | No | No |
| `sheet` | `Edm.String` | Yes | Yes | No | No |
| `cell_range` | `Edm.String` | No | No | No | No |
| `published_date` | `Edm.DateTimeOffset` | No | Yes | Yes | No |
| `effective_from` | `Edm.DateTimeOffset` | No | Yes | Yes | No |
| `effective_to` | `Edm.DateTimeOffset` | No | Yes | Yes | No |
| `tax_year` | `Edm.String` | No | Yes | No | Yes |
| `document_version` | `Edm.String` | No | Yes | No | No |
| `workflow_id` | `Edm.String` | No | Yes | No | No |
| `authority_level` | `Edm.String` | No | Yes | No | Yes |
| `authority_rank` | `Edm.Int32` | No | Yes | Yes | No |
| `tax_types` | `Collection(Edm.String)` | Yes | Yes | No | Yes |
| `taxpayer_types` | `Collection(Edm.String)` | Yes | Yes | No | Yes |
| `language` | `Edm.String` | No | Yes | No | Yes |
| `status` | `Edm.String` | No | Yes | No | Yes |
| `supersedes` | `Collection(Edm.String)` | No | Yes | No | No |
| `form_code` | `Edm.String` | Yes | Yes | No | No |
| `tags` | `Collection(Edm.String)` | Yes | Yes | No | Yes |
| `source_hash` | `Edm.String` | No | Yes | No | No |
| `chunk_hash` | `Edm.String` | No | Yes | No | No |
| `image_id` | `Edm.String` | No | Yes | No | No |
| `image_url` | `Edm.String` | No | No | No | No |
| `image_alt_text` | `Edm.String` | Yes | No | No | No |
| `image_caption` | `Edm.String` | Yes | No | No | No |
| `embedding` | `Collection(Edm.Single)` | Vector | No | No | No |

The key field is `id`. The vector field uses the configured dimensions and the existing HNSW
profile unless evaluation demonstrates a reason to change it. Index creation must verify
that runtime and ingestion dimensions match.

Date values uploaded to Azure must be UTC midnight ISO timestamps. Null values remain null;
they must not be replaced with artificial dates.

## 27. Embedding-cache and upload specification

### 27.1 SQLite schema

The local cache should use one table:

```sql
CREATE TABLE embeddings (
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (model, dimensions, content_hash)
);
```

`content_hash` for caching is the SHA-256 of `embedding_text`, not the source `chunk_hash`.
Before use, validate that the decoded vector length exactly matches `dimensions` and that all
values are finite numbers. Invalid cache rows are ignored and replaced.

### 27.2 Batching and failure behavior

- Default embedding batch size: 16.
- Cache each successful batch before attempting Search upload.
- Retry only transient provider errors with bounded exponential backoff and jitter.
- Do not retry authentication, invalid-model, or dimensionality errors.
- Print progress as counts and source IDs, never full source text.
- On interruption, committed cache rows remain reusable.
- A failed embedding batch prevents those records from uploading but does not corrupt
  already cached vectors.

### 27.3 Index synchronization

The upload stage should:

1. Validate every chunk against the index contract.
2. Attach cached or newly generated vectors.
3. Upload in Azure batches within service limits.
4. Record success and failure by document ID.
5. Query existing index IDs for source IDs in the approved registry.
6. Compute stale IDs that no longer occur in the local chunk set.
7. Report stale IDs during dry run.
8. Delete stale IDs only when `--delete-stale` accompanies explicit `--upload`.

Deletion must never target IDs outside the source IDs managed by the supplied manifest.

## 28. CLI specification

### 28.1 Local corpus build

```bash
cd backend
uv run python ../scripts/build_corpus.py \
  --manifest ../data/metadata/sources.yaml \
  --output ../data/processed/corpus
```

This command performs validation, extraction, chunking, image-candidate rendering, and audit
generation. It makes no Gemini or Azure calls.

Supported flags:

- `--source-id <id>` may be repeated to restrict a debugging run;
- `--skip-ocr` records pages needing OCR as errors instead of silently skipping them;
- `--skip-images` omits candidate rendering;
- `--force` ignores reusable local extraction output for selected sources; and
- `--fail-on-warning` makes warnings produce a nonzero exit.

### 28.2 Index dry run and upload

```bash
cd backend
uv run python ../scripts/ingest_corpus.py \
  --chunks ../data/processed/corpus/chunks.jsonl \
  --cache ../data/processed/corpus/embedding-cache.sqlite3 \
  --dry-run
```

Dry run validates chunks and reports:

- chunk counts by type and source;
- estimated embedding input size;
- cache hits and misses;
- missing required configuration;
- stale-record estimates when Search is configured; and
- zero network mutations.

Actual embedding and upload require:

```bash
cd backend
uv run python ../scripts/ingest_corpus.py \
  --chunks ../data/processed/corpus/chunks.jsonl \
  --cache ../data/processed/corpus/embedding-cache.sqlite3 \
  --upload
```

`--delete-stale` is separate and optional. `--upload` must never be the default.

### 28.3 Image publishing

```bash
cd backend
uv run python ../scripts/publish_guide_images.py \
  --manifest ../data/metadata/guide-images.yaml \
  --directory ../data/processed/corpus/rendered-images \
  --dry-run
```

Actual public upload requires `--publish`. Dry run verifies approval, hashes, dimensions,
MIME types, target paths, and configuration without mutation.

### 28.4 Exit and reporting behavior

- Exit `0`: requested operation completed with no errors.
- Exit `1`: source, extraction, embedding, or upload errors occurred.
- Exit `2`: invalid arguments or invalid configuration.

All CLIs should print a compact summary and write detailed machine-readable reports. Expected
source problems belong in reports, not unhandled tracebacks. Unexpected programming errors
may retain tracebacks for debugging but must not print secrets or source bodies.

## 29. Runtime retrieval algorithm

### 29.1 Query analysis

Extend `backend/app/services/query.py` with deterministic helpers returning:

```python
QueryContext(
    tax_year: str | None,
    tax_types: list[str],
    taxpayer_types: list[str],
    procedural_intent: bool,
    historical_intent: bool,
    retrieval_text: str,
)
```

`retrieval_text` should use the latest user question plus, only when needed, the preceding
one or two user messages. It must not include assistant answers as factual evidence.

The alias map should be a reviewed constant and include at least:

```text
APIT -> Advance Personal Income Tax
AIT -> Advance Income Tax
WHT -> Withholding Tax
SET -> Statement of Estimated Tax Payable
SSCL -> Social Security Contribution Levy
TIN -> Taxpayer Identification Number
RAMIS -> Revenue Administration Management Information System
IIT -> Individual Income Tax
CIT -> Corporate Income Tax
PIT -> Partnership Income Tax
VAT -> Value Added Tax
```

Procedural intent is true when the user asks how to file, complete, register, pay, upload,
submit, amend, appeal, obtain, navigate, or identify a form field. The answer model still
decides whether a numbered guide is useful; this flag only controls retrieval strategy.

### 29.2 Evidence search

Run one hybrid search excluding `content_type = guide_image`:

- vector: the one query embedding;
- `search_text`: the retrieval text with deterministic alias expansions;
- initial top: 24;
- vector nearest neighbors: at least 24;
- language filter: English for the MVP;
- tax-year filter only when metadata is explicit; unknown-period sources remain candidates;
- exclude `status = excluded`; and
- exclude `status = superseded` for current questions unless no applicable replacement is
  present or the user asks historically.

Do not hard-filter by tax type when the source has no tax-type metadata. A cross-tax Act or
calendar may still be relevant.

### 29.3 Deterministic reranking

Because Azure hybrid scores are not calibrated probabilities, rerank using a stable tuple,
not arbitrary score arithmetic. Sort candidates by:

1. hard applicability to the requested period;
2. exact tax-year match;
3. current versus unknown versus historical/superseded status;
4. exact tax-type match;
5. query-appropriate authority category;
6. original Azure result rank; and
7. stable chunk ID as the final tie breaker.

For legal-rule questions, legislation and Gazettes are query-appropriate authorities. For
procedural questions, current official guides, forms, notices, and relevant legislation are
query-appropriate; guides must not be promoted as changing the law.

Select at most two chunks from one source on the first pass. Allow an additional adjacent
chunk only when needed to complete the same section. The initial context target is eight
evidence chunks.

### 29.4 Image search

Only when `procedural_intent` is true and applicable evidence was found, run a second search
using the same query vector with `content_type = guide_image`.

- Initial top: 8.
- Require `review_status` indirectly by indexing approved images only.
- Match known workflow IDs or tax types where available.
- Require source status and dates to be applicable.
- Prefer images whose source/page is already in the evidence set.
- Pass at most four image candidates to the answer model.

An image search result must never substitute for textual evidence.

## 30. Backend model and prompt contracts

### 30.1 Search models

Extend `SearchChunk` additively with:

```python
page_end: int | None
sheet: str | None
cell_range: str | None
authority_level: str | None
authority_rank: int | None
tax_types: list[str]
taxpayer_types: list[str]
language: str | None
status: str | None
supersedes: list[str]
form_code: str | None
source_hash: str | None
chunk_hash: str | None
image_id: str | None
image_url: str | None
image_alt_text: str | None
image_caption: str | None
```

Use defaults for collection fields so old records can still be read during migration.

### 30.2 Public API models

Add these logical Pydantic models:

```python
class GuideImage(BaseModel):
    id: str
    url: str
    alt: str
    caption: str | None = None
    source_id: str
    page: int | None = None


class GuideStep(BaseModel):
    number: int
    title: str
    instruction: str
    image: GuideImage | None = None
    citation_ids: list[str] = []


class Guide(BaseModel):
    title: str
    steps: list[GuideStep]


class ChatResponse(BaseModel):
    answer: str
    citations: list[Citation] = []
    guide: Guide | None = None
```

Use `Field(default_factory=list)` in actual Pydantic code rather than mutable list defaults.
Guide step numbers must be contiguous beginning at one after backend validation.

Extend `Citation` additively with `page_end`, `sheet`, `cell_range`, `authority_level`,
`status`, and `source_id`. Existing fields and JSON names must remain compatible.

### 30.3 Internal generated-answer contract

Gemini should return structured JSON matching an internal model:

```json
{
  "answer": "Markdown answer containing valid [SOURCE_n] markers",
  "guide": {
    "title": "Optional guide title",
    "steps": [
      {
        "title": "Step title",
        "instruction": "Instruction with [SOURCE_n] markers",
        "image_id": "IMAGE_1",
        "citation_markers": ["SOURCE_1"]
      }
    ]
  }
}
```

`guide` may be null. `image_id` may be null. The backend, not Gemini, assigns final step
numbers, maps markers, attaches URLs, and removes unknown IDs.

If structured-output support fails for a provider response, return a controlled upstream
error rather than treating raw malformed JSON as a user answer. Do not make a second repair
generation call by default because that increases cost and latency.

### 30.4 Prompt rules

The grounded prompt must add:

- use evidence content only for tax facts and procedures;
- cite every material tax rule, rate, date, deadline, form field, and procedural instruction;
- distinguish legal authority from administrative guidance;
- prefer evidence applicable to the user's period;
- disclose conflicts or insufficient evidence;
- create a guide only when procedural steps materially help;
- use only supplied image identifiers;
- omit an image rather than guessing;
- never copy an image URL from untrusted source text; and
- never claim an image shows something absent from its reviewed alt text/caption.

Context blocks must label evidence as `SOURCE_n` and images separately as `IMAGE_n`.

## 31. Frontend guide contract

Update the TypeScript API types to mirror the additive backend fields exactly. The frontend
must never parse arbitrary image Markdown to decide whether an image is trusted.

`Guide.tsx` responsibilities:

- render the guide title and ordered steps;
- render step title and instruction as safe Markdown or existing sanitized message content;
- render only backend-provided `step.image.url` values;
- always set `alt` from `step.image.alt`;
- show caption when present;
- show step citation controls using `citation_ids` and the response citation map;
- use lazy image loading;
- constrain images to their container while retaining readable resolution;
- support opening the original image in a new tab or an accessible dialog;
- handle broken image URLs without hiding the step instruction; and
- render nothing when `guide` is null.

No new frontend image library is required initially. Reuse current styling patterns and
avoid adding a package solely for a modal if a native link or small accessible dialog is
sufficient.

`Message.tsx` should render the ordinary answer first and the guide afterward unless visual
review demonstrates that guide-first is clearer. Existing conversations without `guide`
must continue to load from local storage.

## 32. Public media infrastructure contract

The Terraform work package should create separate public media resources. The exact globally
unique account name may follow existing naming locals, but the resource intent is:

```hcl
resource "azurerm_storage_account" "guide_media" {
  # Standard LRS, StorageV2, HTTPS only, minimum TLS 1.2.
  # Public blob access is allowed only for this media account.
}

resource "azurerm_storage_container" "guide_images" {
  storage_account_id    = azurerm_storage_account.guide_media.id
  container_access_type = "blob"
}
```

Requirements:

- do not change the private source account to public;
- allow anonymous reads only at blob scope, not anonymous writes;
- expose the public base URL as a Terraform output and backend ingestion configuration;
- upload with explicit WebP content type and immutable cache-control metadata;
- keep storage account keys backend/deployment-only;
- do not issue or return signed URLs for approved public images; and
- run `terraform fmt -check -recursive` and `terraform validate` for the Terraform package.

If organizational Azure policy blocks public Blob containers, use a Static Web App public
asset origin or CDN-backed equivalent while keeping the same stable public URL contract.
Do not weaken the private storage account as a workaround.

## 33. Work-package dependency graph

```text
WP-00 contracts/dependencies
   |
   +--> WP-01 registry
          |
          +--> WP-02 PDF extractor ----+
          +--> WP-03 HTML extractor ---+--> WP-05 corpus builder/chunker/audit
          +--> WP-04 XLSM extractor ---+             |
          +--> WP-06 image renderer -----------------+
                                                       |
                                                       v
                                                WP-07 index/cache/upload
                                                       |
                                                       v
                                                WP-08 runtime retrieval
                                                       |
                                                       v
                                                WP-09 response/prompt
                                                       |
                                                       v
                                                WP-10 frontend guide

WP-06 image renderer --> WP-11 public media infrastructure/publisher
WP-05 + WP-07 + WP-08 + WP-09 + WP-10 + WP-11 --> WP-12 integration/release review
```

WP-02, WP-03, and WP-04 are safe parallel tasks after WP-01 contracts are stable. WP-10 may
begin against the fixed API contract after WP-09 models are agreed, but integration must wait
for WP-09. Shared dependency files are owned only by WP-00 to prevent lockfile conflicts.

## 34. Detailed worker work packages

### WP-00: Contracts and dependency foundation

**Owns:**

- `backend/pyproject.toml`
- `backend/uv.lock`
- `scripts/ingestion/__init__.py`
- `scripts/ingestion/models.py`

**Tasks:**

1. Reconcile existing user changes before editing dependency files.
2. Add only the agreed extraction dependencies: Beautiful Soup, `lxml`, `openpyxl`,
   `defusedxml`, PyMuPDF, Pillow, and `pytesseract`.
3. Define all controlled enums and Pydantic models for source, extraction, chunk, image, and
   audit contracts.
4. Provide deterministic JSON serialization helpers.
5. Avoid implementing extractor behavior.

**Acceptance:**

- `uv sync --dev` completes.
- From `backend/`, `uv run ruff check ../scripts/ingestion app` or equivalently scoped lint
  passes.
- Models reject the invalid states listed in this plan through ordinary validation paths.
- No new environment or package manager is introduced.

### WP-01: Registry generation and validation

**Depends on:** WP-00.

**Owns:**

- `scripts/ingestion/registry.py`
- `data/metadata/sources.yaml`
- updates to `data/metadata/sources.example.yaml`

**Tasks:**

1. Merge the three existing download manifests and resolved official-source lists.
2. Build stable IDs and a reviewable registry containing official IRD sources only.
3. Deduplicate by checksum while retaining URL aliases.
4. Populate metadata supported by source titles, URLs, and official categorization.
5. Leave uncertain dates/status relationships explicit; do not ask Gemini to infer them.
6. Mark outdated portal guides historical or superseded rather than current.
7. Implement all registry invariants and a compact validation summary.

**Acceptance:**

- Registry validation runs locally without provider credentials.
- Every approved record points to an existing checksum-matching file.
- The known broken XLSM HTML response is excluded with a reason.
- Nonofficial hosts do not enter the approved registry.
- Source counts and exclusions appear in a machine-readable summary.

### WP-02: PDF and selective OCR extractor

**Depends on:** WP-00 and source model from WP-01.

**Owns:**

- `scripts/ingestion/extractors/pdf.py`

**Tasks:**

1. Implement native page extraction.
2. Implement OCR preflight and threshold behavior exactly as section 23.1.
3. Preserve page locators, extraction method, order, and warnings.
4. Handle malformed/encrypted PDFs as source-level errors.
5. Avoid guide-image rendering; that belongs to WP-06.

**Acceptance:**

- Run against representative native-text, mixed, and zero-text PDFs in the downloaded
  corpus.
- Confirm OCR activates only on qualifying pages.
- Confirm no empty critical PDF is reported as successfully extracted.
- Lint passes for the owned module.

### WP-03: Official HTML extractor

**Depends on:** WP-00 and source model from WP-01.

**Owns:**

- `scripts/ingestion/extractors/html.py`

**Tasks:**

1. Parse local official IRD snapshots.
2. Implement main-content selection and boilerplate removal.
3. Preserve headings, lists, tables, and meaningful HTTPS links.
4. Emit normalized extraction records and controlled warnings.
5. Never perform a network fetch.

**Acceptance:**

- Inspect output for at least one tax page, one eServices page, and one index/table page.
- Navigation and scripts are absent from extracted content.
- Table headers remain understandable in serialized output.
- Lint passes for the owned module.

### WP-04: Safe XLSM extractor

**Depends on:** WP-00 and source model from WP-01.

**Owns:**

- `scripts/ingestion/extractors/xlsm.py`

**Tasks:**

1. Validate archive structure and size before workbook parsing.
2. Load without VBA execution or external-link resolution.
3. Extract visible sheet regions, labels, formulas, comments, and supported validation text.
4. Preserve sheet/cell locators.
5. Report hidden sheets and unsupported validation structures.

**Acceptance:**

- Run against APIT, WHT/AIT, and VAT templates.
- No macro or external workbook is executed or fetched.
- Output includes meaningful field labels and exact sheet/cell ranges.
- The broken HTML masquerading as XLSM is rejected before `openpyxl` parsing.
- Lint passes for the owned module.

### WP-05: Corpus builder, chunker, and audit

**Depends on:** WP-01 through WP-04.

**Owns:**

- `scripts/build_corpus.py`
- `scripts/ingestion/normalize.py`
- `scripts/ingestion/chunking.py`
- `scripts/ingestion/audit.py`

**Tasks:**

1. Orchestrate approved sources in stable registry order.
2. Route each MIME type to the correct extractor.
3. Write `extraction.jsonl` atomically.
4. Apply shared normalization and structural chunking.
5. Write `chunks.jsonl` atomically.
6. Aggregate source errors without hiding successful independent sources.
7. Produce `audit.json`, `source-summary.json`, and compact terminal output.
8. Implement CLI behavior from section 28.1.

Use temporary sibling files and rename only after successful completion so interrupted runs
do not leave a valid-looking partial JSONL file.

**Acceptance:**

- A no-key full-corpus build completes or exits nonzero with every failure identified.
- JSONL line counts match the audit report.
- Repeating the same build produces identical IDs and hashes.
- No chunk crosses a source boundary.
- Representative legal sections, guide steps, form fields, tables, and spreadsheet regions
  remain understandable.
- Lint passes for all owned files.

### WP-06: Guide-image candidates and registry

**Depends on:** WP-01 and WP-02 page access patterns.

**Owns:**

- `scripts/ingestion/images.py`
- `data/metadata/guide-images.yaml`
- image-candidate integration exposed to WP-05

**Tasks:**

1. Render only manifest-selected candidate pages.
2. Produce normalized WebP assets and deterministic hashes.
3. Create candidate metadata using source/page text.
4. Keep all candidates draft until explicitly approved.
5. Convert approved entries into guide-image chunk records.
6. Do not upload images.

**Acceptance:**

- Repeated rendering is deterministic enough to reuse unchanged hashes on the same runtime.
- Candidate output includes source/page provenance and dimensions.
- No image lacking approval can enter `chunks.jsonl` as `guide_image`.
- No arbitrary embedded PDF object is published automatically.
- Lint passes for the owned module.

### WP-07: Search schema, embedding cache, and upload

**Depends on:** WP-05 chunk contract.

**Owns:**

- `scripts/create_search_index.py`
- `scripts/ingest_corpus.py`
- `scripts/ingestion/embedding_cache.py`
- `scripts/ingestion/search_upload.py`
- compatible refactoring in `scripts/common.py`

**Tasks:**

1. Implement the exact versioned Search schema.
2. Implement SQLite vector caching and validation.
3. Implement dry-run estimation with no network mutation.
4. Implement batched resumable embedding.
5. Implement scoped Search upload and stale-ID reporting/deletion.
6. Keep embedding dimensions consistent with backend settings.
7. Preserve old ingestion scripts or their shared helper compatibility.

**Acceptance:**

- Dry run succeeds without making Gemini or Azure mutations.
- Cache-hit reruns issue no embedding request for unchanged content.
- Invalid vector sizes are rejected before upload.
- Stale deletion cannot affect a source outside the supplied chunk set/manifest scope.
- A temporary/versioned index can be created and populated when credentials are deliberately
  supplied.
- Lint passes for all owned files.

### WP-08: Runtime query analysis and retrieval

**Depends on:** WP-07 schema.

**Owns:**

- `backend/app/services/query.py`
- `backend/app/services/models.py`
- `backend/app/services/search.py`
- related search settings in `backend/app/config.py` and `.env.example`

**Tasks:**

1. Add the fixed query context and alias behavior.
2. Select every new Search field.
3. Implement evidence and optional image searches using one query vector.
4. Implement applicability/status/authority reranking and source diversity.
5. Keep old records readable during index migration.
6. Return evidence and image candidates separately to RAG orchestration.

**Acceptance:**

- Current-year questions demote superseded guidance.
- Historical questions can retrieve historical sources.
- Procedural questions retrieve current guides/forms plus applicable authority.
- Legal questions do not become dominated by low-authority image records.
- Follow-up retrieval uses bounded user context.
- Backend Ruff check passes.

### WP-09: Grounded structured response and citations

**Depends on:** WP-08.

**Owns:**

- `backend/app/schemas.py`
- `backend/app/services/rag.py`
- `backend/app/services/gemini.py`
- `backend/app/prompts/system_prompt.py`

**Tasks:**

1. Add optional guide and expanded citation models.
2. Add separated `SOURCE_n` and `IMAGE_n` context mapping.
3. Request the internal structured Gemini response.
4. Validate all citation and image markers.
5. Drop unknown IDs and never pass a model URL through directly.
6. Map approved image IDs to backend-owned public image fields.
7. Preserve non-guide answer behavior.
8. Return a controlled error for malformed structured model output.

**Acceptance:**

- A normal tax answer returns `guide: null` when appropriate.
- A procedural answer can return ordered steps with citations and optional images.
- Invented source and image IDs never reach the public response.
- An image cannot appear unless it was retrieved and approved.
- Existing request shape remains unchanged.
- Backend Ruff check passes.

### WP-10: Frontend guide rendering

**Depends on:** WP-09 public API contract.

**Owns:**

- `frontend/src/types/chat.ts`
- `frontend/src/lib/api.ts`
- `frontend/src/components/Guide.tsx`
- guide integration in `frontend/src/components/Message.tsx`
- minimal related styles in `frontend/src/styles.css`

**Tasks:**

1. Add exact optional guide and expanded citation types.
2. Render accessible ordered guide steps.
3. Render only structured backend image URLs.
4. Add lazy loading, captions, alt text, and broken-image fallback.
5. Link step citations to the existing citation presentation.
6. Keep old local-storage conversations compatible.

**Acceptance:**

- `pnpm build` passes from `frontend/`.
- Text-only answers render as before.
- Guides render correctly with zero, one, or several step images.
- A failed image does not remove its instruction.
- Images remain usable on narrow screens.

### WP-11: Public media infrastructure and publisher

**Depends on:** WP-06 image contract. Can proceed independently of runtime tasks.

**Owns:**

- a new or appropriately named Terraform media-storage file under `infra/terraform/`
- necessary Terraform variables and outputs
- `scripts/publish_guide_images.py`

**Tasks:**

1. Provision separate public media storage without modifying private source visibility.
2. Expose public base URL and deployment configuration.
3. Implement dry-run validation and explicit `--publish` upload.
4. Set content type and immutable cache headers.
5. Prevent unapproved images from uploading.

**Acceptance:**

- Terraform formatting and validation pass.
- Dry run makes no cloud mutations.
- Publishing requires an explicit flag and credentials.
- Uploaded image URLs are stable public URLs without signatures.
- Private source documents remain inaccessible anonymously.

### WP-12: Integration and release review

**Depends on:** all required work packages.

**Owns:**

- `README.md` ingestion/runtime documentation
- evaluation source-ID updates if approved by the coordinating agent
- integration fixes explicitly delegated after ownership is released

**Tasks:**

1. Run the complete no-key corpus build.
2. Review source and extraction audit failures.
3. Record the exact final chunk count and embedding estimate.
4. Obtain approval before full embedding/upload.
5. Populate a versioned index.
6. Run the manual evaluation and release gates.
7. Verify public guide assets and frontend behavior.
8. Document incremental updates, rollback, and credentials.

**Acceptance:**

- All release gates in section 13 are satisfied or explicitly documented as exclusions.
- The active index is switched only after verified results.
- Rollback consists of restoring the prior index configuration.
- Documentation commands match actual CLI behavior.

## 35. Parallel-work ownership rules

To keep Luna worker changes mergeable:

- WP-00 exclusively owns Python dependency files until it finishes.
- WP-01 exclusively owns `sources.yaml`.
- Extractor workers own only their extractor module and must not change shared models.
- WP-05 owns the orchestration CLI, normalizer, chunker, and audit format.
- WP-06 owns `guide-images.yaml`; WP-11 reads but does not rewrite it except for an explicitly
  delegated publication-state update.
- WP-07 owns Search-index creation and ingestion upload scripts.
- WP-08 owns runtime retrieval models; WP-09 may consume them but should coordinate any field
  change rather than editing WP-08 files concurrently.
- WP-09 owns public backend response models.
- WP-10 mirrors, but does not redefine, the backend API contract.
- WP-11 exclusively owns Terraform media resources.
- WP-12 should begin integration fixes only after the corresponding owner has completed or
  released the file.

When a worker discovers a required cross-owned change, it should report the exact interface
need to the coordinating agent rather than editing another worker's file opportunistically.

## 36. Manual review checkpoints

### Checkpoint A: Registry approval

Before full extraction, review:

- all `current` versus `historical` assignments;
- tax-year normalization;
- Act/amendment/consolidation relationships;
- circular supersession;
- official guide applicability;
- excluded nonofficial sources; and
- the list of guide pages proposed for rendering.

### Checkpoint B: Extraction approval

Before embedding, inspect at minimum:

- two legislation pages with section numbering;
- two native-text form/guide pages;
- all nine previously identified zero-text critical PDFs at least at the document level;
- representative OCR pages;
- one IRD HTML table and one narrative HTML page;
- one APIT, one WHT/AIT, and two different VAT workbook templates;
- one long table split across chunks; and
- every approved guide-image candidate.

### Checkpoint C: Cost approval

Before Gemini embedding, present:

- approved source count;
- extraction-record count;
- chunk count by content type;
- total estimated embedding tokens;
- cache hits and misses;
- estimated number of embedding batches; and
- the target Gemini model, dimensions, and Azure index name.

No additional/high-quota key should be requested until this report shows it is needed or the
existing key actually encounters quota limits.

### Checkpoint D: Index promotion

Before changing runtime configuration, present:

- uploaded versus failed record counts;
- stale-record action taken;
- representative retrieval results;
- temporal and authority conflict results;
- citation mapping results;
- guide/image results; and
- the rollback index name.

## 37. Edge cases workers must handle

### Sources and metadata

- The same file appears through more than one official URL.
- An official URL returns status 200 with an HTML error document.
- A consolidated Act and its latest amendment both apply.
- A document has a tax year in its title but no reliable effective date.
- A guide is official but visibly tied to an old portal or assessment year.
- A source changes bytes without changing its URL.

### PDFs

- An entire PDF is scanned.
- Only some pages are scanned.
- Extracted text exists but is nonsensical or extremely sparse.
- Page labels differ from physical one-based page indices.
- A table is visually clear but extracts in column-major order.
- A page contains only a signature, stamp, or decorative cover.

### HTML

- Main content is nested in legacy SharePoint markup.
- The snapshot includes repeated navigation in multiple languages.
- A table has merged cells or headers in several rows.
- Links are relative, encoded, duplicated, or JavaScript-based.

### XLSM

- The workbook contains macros, hidden sheets, named ranges, comments, validations, and
  external links.
- Formulas have no cached results.
- One template is an amendment of another.
- A workbook contains mostly formatting with sparse labels.
- The MIME extension is XLSM but the body is HTML.

### Retrieval

- The user says `2025/26`, `2025-26`, or `YA 2526`.
- The user asks a follow-up such as "what about partnerships?".
- Current and historical guides contain nearly identical wording.
- The most authoritative legal source is not the most useful procedural source.
- The question spans more than one tax type.
- No source contains enough evidence.

### Images and frontend

- An approved image later becomes unavailable.
- A guide step is useful but has no matching image.
- The model returns an image ID that was not supplied.
- An image is relevant to the workflow but obsolete for the requested year.
- An image has a very tall page aspect ratio.
- An old locally stored conversation has no `guide` field.

## 38. Observability and safe logging

Ingestion logs may contain:

- source ID;
- document type;
- page/sheet number;
- counts, hashes, durations, and warning codes;
- provider error category; and
- Azure document IDs.

Logs must not contain:

- full extracted paragraphs;
- API keys or credential-bearing connection strings;
- full Gemini request bodies;
- workbook personal data if unexpectedly present;
- signed URLs; or
- raw provider stack traces in user-facing output.

Machine-readable audit files may include reviewed source URLs and local relative paths, but
must not include environment variables or secret values.

## 39. Rollback and recovery

- Extraction recovery: rerun only failed `--source-id` values with `--force`.
- Embedding recovery: reuse valid SQLite cache rows and resume missing hashes.
- Upload recovery: rerun failed document batches; upserts are stable by chunk ID.
- Stale deletion recovery: do not delete stale records until the new complete set is verified.
- Runtime rollback: restore `AZURE_SEARCH_INDEX` to the prior index and redeploy/restart the
  backend configuration.
- Image rollback: because filenames are content-hashed, restore the previous image manifest
  or remove the image association from new responses; existing public assets may remain
  harmlessly cached.

No rollback should require deleting raw source documents or clearing the embedding cache.

## 40. Coordinator completion checklist

The coordinating agent should not declare the implementation complete until it can answer
yes to all applicable items:

- [ ] The approved registry validates with no unresolved errors.
- [ ] Nonofficial research pages are excluded from production evidence.
- [ ] Every approved file matches its recorded checksum and MIME type.
- [ ] Native PDF, OCR PDF, HTML, and XLSM extraction paths completed.
- [ ] Critical zero-text PDFs are searchable or explicitly excluded with a reason.
- [ ] Structural chunks retain exact source locators.
- [ ] The dry-run audit and embedding estimate were reviewed.
- [ ] Embeddings are cached and resumable.
- [ ] The versioned Search index contains the expected documents.
- [ ] Stale-record handling stayed inside the approved manifest scope.
- [ ] Retrieval respects period, status, authority, and procedural intent.
- [ ] The answer model uses structured output.
- [ ] Citations and image IDs are validated server-side.
- [ ] Public guide images are approved, stable, accessible, and unsigned.
- [ ] The private source-document container remains private.
- [ ] Text-only answers and older saved conversations still render.
- [ ] Structured guides render accessibly in the frontend.
- [ ] Backend Ruff validation passes.
- [ ] Frontend `pnpm build` passes.
- [ ] Terraform formatting and validation pass if infrastructure was changed.
- [ ] Manual evaluation includes current, historical, insufficient-evidence, conflict, and
      image-guide scenarios.
- [ ] The rollback index name and procedure are documented.
