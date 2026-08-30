"""Build and validate the approved Inland Revenue Department source registry.

The registry is intentionally generated from the immutable downloader manifests under
``data/raw``.  No network access is performed here: a build only promotes files that are
already present locally and whose bytes match the manifest checksum.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

try:  # Running as ``python -m scripts.ingestion.registry``.
    from .models import AUTHORITY_RANKS, SourceManifest, SourceRecord
except ImportError:  # Running directly from the backend ingestion command.
    from models import AUTHORITY_RANKS, SourceManifest, SourceRecord  # type: ignore


ALLOWED_HOSTS = ["www.ird.gov.lk", "ird.gov.lk", "eservices.ird.gov.lk"]
MANIFEST_NAMES = (
    "data/raw/research-sources/manifest.json",
    "data/raw/research-sources/operational/manifest.json",
    "data/raw/research-sources-2/indexes/manifest.json",
    "data/raw/research-sources-2/documents/manifest.json",
)
HTML_ERROR_URL = (
    "https://www.ird.gov.lk/en/Downloads/Schedules_WHT_2021/WHT_Schedule_04_Amendment_wef_2025.xlsm"
)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class RegistryValidationError(ValueError):
    """Raised when YAML/model validation fails before file validation can run."""

    def __init__(self, message: str, *, summary: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.summary = summary or {"valid": False, "errors": [message], "warnings": []}


def _repo_root(path: Path | None = None) -> Path:
    candidate = (path or Path.cwd()).resolve()
    for root in (candidate, *candidate.parents):
        if (root / ".git").exists():
            return root
    return candidate


def _normalise_yaml_dates(value: Any) -> Any:
    """Convert SafeLoader's implicit date objects to explicit ISO strings."""

    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _normalise_yaml_dates(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise_yaml_dates(item) for item in value]
    return value


def read_yaml(path: str | Path) -> dict[str, Any]:
    """Read a registry YAML document and normalize implicit YAML dates."""

    registry_path = Path(path)
    with registry_path.open(encoding="utf-8") as stream:
        value = yaml.safe_load(stream) or {}
    if not isinstance(value, dict):
        raise RegistryValidationError("registry YAML must contain a mapping at the top level")
    return _normalise_yaml_dates(value)


def _manifest_model(value: SourceManifest | Mapping[str, Any] | str | Path) -> SourceManifest:
    if isinstance(value, SourceManifest):
        return value
    raw = read_yaml(value) if isinstance(value, (str, Path)) else dict(value)
    try:
        return SourceManifest.model_validate(_normalise_yaml_dates(raw))
    except Exception as exc:  # Pydantic's error includes the offending field paths.
        raise RegistryValidationError(f"invalid source registry: {exc}") from exc


def load_registry(path: str | Path = "data/metadata/sources.yaml") -> SourceManifest:
    """Load and model-validate a source registry."""

    return _manifest_model(path)


def _resolve_local_file(source: SourceRecord, manifest_path: Path, repository_root: Path) -> Path:
    local = (manifest_path.parent / source.local_file).resolve()
    try:
        local.relative_to(repository_root.resolve())
    except ValueError as exc:
        raise ValueError(
            f"local_file escapes repository: {source.id}: {source.local_file}"
        ) from exc
    return local


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _signature(path: Path) -> str:
    with path.open("rb") as stream:
        head = stream.read(4096)
    if head.startswith(b"%PDF"):
        return "pdf"
    if head.startswith(b"PK"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "[Content_Types].xml" in names and "xl/workbook.xml" in names:
                    return "xlsm"
        except (OSError, zipfile.BadZipFile):
            pass
    stripped = head.lstrip().lower()
    if stripped.startswith((b"<!doctype", b"<html", b"<?xml", b"<head", b"<body")):
        return "html"
    return "unknown"


def _pdf_page_count(path: Path) -> int | None:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path), strict=False).pages)
    except Exception:
        return None


def validate_registry(
    value: SourceManifest | Mapping[str, Any] | str | Path = "data/metadata/sources.yaml",
    *,
    repository_root: str | Path | None = None,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate a registry's model and local-file invariants.

    The returned summary is deliberately JSON-compatible so it can be written directly to
    CI logs or ``audit.json``.  Model errors are reported in the summary rather than hidden
    behind a traceback; callers can use ``summary["valid"]`` as the process result.
    """

    errors: list[str] = []
    warnings: list[str] = []
    try:
        registry = _manifest_model(value)
    except RegistryValidationError as exc:
        return {**exc.summary, "source_count": 0, "excluded_count": 0, "duplicate_hashes": []}

    if manifest_path is not None:
        registry_file = Path(manifest_path).resolve()
    elif isinstance(value, (str, Path)):
        registry_file = Path(value).resolve()
    else:
        registry_file = Path("data/metadata/sources.yaml").resolve()
    root = Path(repository_root).resolve() if repository_root else _repo_root(registry_file.parent)

    hash_groups: dict[str, list[str]] = defaultdict(list)
    for source in registry.sources:
        hash_groups[source.sha256].append(source.id)
        try:
            local = _resolve_local_file(source, registry_file, root)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not local.exists():
            if source.status.value == "excluded":
                warnings.append(f"excluded source has no local snapshot: {source.id}")
            else:
                errors.append(
                    f"missing local_file for approved source: {source.id}: {source.local_file}"
                )
            continue
        actual_hash = _sha256(local)
        if actual_hash != source.sha256:
            errors.append(
                f"checksum mismatch for {source.id}: expected {source.sha256}, got {actual_hash}"
            )
        signature = _signature(local)
        expected = {
            "application/pdf": "pdf",
            "text/html": "html",
            "application/vnd.ms-excel.sheet.macroEnabled.12": "xlsm",
        }[source.media_type.value]
        if signature != expected:
            errors.append(
                "media type disagrees with file signature for "
                f"{source.id}: {source.media_type.value} / {signature}"
            )
        if source.media_type.value.endswith("macroEnabled.12") and signature == "html":
            errors.append(f"XLSM URL returned an HTML error page: {source.id}")
        if source.render_pages:
            pages = _pdf_page_count(local) if signature == "pdf" else None
            if pages is not None and any(page > pages for page in source.render_pages):
                errors.append(f"render_pages outside PDF page count for {source.id}: {pages}")

    duplicate_hashes = [ids for ids in hash_groups.values() if len(ids) > 1]
    for ids in duplicate_hashes:
        warnings.append(f"duplicate checksum retained in source IDs: {', '.join(ids)}")
    excluded_count = sum(source.status.value == "excluded" for source in registry.sources)
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "source_count": len(registry.sources),
        "approved_count": sum(
            source.review_status.value == "approved" for source in registry.sources
        ),
        "excluded_count": excluded_count,
        "duplicate_hashes": duplicate_hashes,
    }


def _slug(value: str, *, limit: int = 82) -> str:
    value = _SLUG_RE.sub("-", value.lower()).strip("-")
    value = value[:limit].rstrip("-")
    return value if len(value) >= 3 else (value + "-source")[:limit]


def _url_title(url: str, title_map: Mapping[str, str]) -> str:
    if url in title_map:
        return title_map[url]
    parsed = urlsplit(url)
    bits = [unquote(bit) for bit in parsed.path.rstrip("/").split("/") if bit]
    value = bits[-1] if bits else parsed.hostname or "IRD source"
    value = re.sub(r"\.(pdf|xlsm|aspx?|html?)$", "", value, flags=re.IGNORECASE)
    value = re.sub(r"[_-]+", " ", value).strip()
    return value or "IRD official webpage"


def _title_map(repository_root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in (
        repository_root / "data/raw/research-sources-2/resolved-sources.md",
        repository_root / "data/raw/research-sources/discovered-operational-sources.md",
    ):
        if not path.exists():
            continue
        for title, url in re.findall(
            r"- \[([^]]+)\]\((https://[^)]+)\)", path.read_text(encoding="utf-8")
        ):
            result[url] = title
    return result


def _infer_year(text: str) -> str | None:
    match = re.search(r"(20\d{2})[_-](20\d{2})", text)
    if match:
        return f"{match.group(1)}/{match.group(2)}"
    match = re.search(r"(?<!\d)(\d{2})[_-](\d{2})(?!\d)", text)
    if match and int(match.group(2)) == (int(match.group(1)) + 1) % 100:
        return f"20{int(match.group(1)):02d}/20{int(match.group(2)):02d}"
    # Compact YA labels (2526, 2425, ...) are not ordinary four-digit years
    # such as the ``2021`` directory used by several IRD schedules.
    for match in re.finditer(r"(?<!\d)(\d{4})(?!\d)", text):
        compact = int(match.group(1))
        if compact < 1900 or compact > 2099:
            first, second = compact // 100, compact % 100
            if 10 <= first <= 99 and second == (first + 1) % 100:
                return f"20{first:02d}/20{second:02d}"
    return None


def _metadata(url: str, media_type: str, title: str) -> dict[str, Any]:
    text = f"{url} {title}".lower()
    path = urlsplit(url).path.lower()
    if "acts_" in path or "_acts/" in path or "/acts/" in path:
        document_type, authority = "act", "legislation"
    elif "gazette" in path:
        document_type, authority = "gazette", "gazette"
    elif "circular" in path:
        document_type, authority = "circular", "official_circular"
    elif "latest%20news" in text or "latest news" in text or "/pn_" in text:
        document_type, authority = "public_notice", "official_notice"
    elif "tax_calendar" in text or "tax calendar" in text:
        document_type, authority = "tax_calendar", "official_calendar"
    elif media_type == "application/vnd.ms-excel.sheet.macroEnabled.12":
        document_type, authority = "spreadsheet_template", "official_form"
    elif "eservices" in path and media_type == "application/pdf":
        document_type, authority = "portal_guide", "official_guide"
    elif media_type == "text/html":
        document_type, authority = "official_webpage", "official_webpage"
    elif any(token in text for token in ("guide", "guideline", "quick guide", "tax table")):
        document_type, authority = "return_guide", "official_guide"
    elif "/forms_" in path or "/asmt_" in path or "/downloads/it_" in path or "return" in text:
        document_type, authority = "return_form", "official_form"
    else:
        document_type, authority = "official_webpage", "official_webpage"
    language = (
        "si"
        if "/si/" in path or re.search(r"_[s]\.(?:pdf|html?)$", path)
        else "ta"
        if "/ta/" in path
        else "en"
    )
    tax_types: list[str] = []
    for pattern, tax_type in (
        (r"apit", "apit"),
        (r"wht", "wht"),
        (r"vat", "vat"),
        (r"sscl", "sscl"),
        (r"stamp|sd[_-]", "stamp_duty"),
        (r"(?<![a-z])cit(?![a-z])", "corporate_income_tax"),
        (r"(?<![a-z])pit(?![a-z])", "partnership_income_tax"),
        (r"(?<![a-z])iit(?![a-z])", "individual_income_tax"),
        (r"income", "income_tax"),
    ):
        if re.search(pattern, text) and tax_type not in tax_types:
            tax_types.append(tax_type)
    taxpayer_types = []
    if "individual" in text or "iit" in text:
        taxpayer_types.append("individual")
    if "corporate" in text or "cit" in text:
        taxpayer_types.append("company")
    if "partnership" in text or "pit" in text:
        taxpayer_types.append("partnership")
    year = _infer_year(text)
    old_portal = document_type == "portal_guide" and year is not None and year < "2025/2026"
    status = "historical" if old_portal else "current"
    version = None
    version_match = re.search(r"\b(v\s*[0-9]+(?:[._-][0-9]+)*)\b", text, flags=re.IGNORECASE)
    if version_match:
        version = re.sub(r"\s+", "", version_match.group(1)).lower()
    form_code = None
    form_match = re.search(
        r"(?<![A-Z])((?:ASMT_[A-Z]+_\d{3}|APIT_T\d+|SSCL_\d+|CLR_\d{3}_[A-Z]|RFN_[A-Z]+_\d+))(?![A-Z0-9])",
        text.upper(),
    )
    if form_match:
        form_code = form_match.group(1)
    return {
        "document_type": document_type,
        "authority_level": authority,
        "authority_rank": AUTHORITY_RANKS[authority],
        "language": language,
        "tax_year": year,
        "document_version": version,
        "form_code": form_code,
        "tax_types": tax_types,
        "taxpayer_types": taxpayer_types,
        "status": status,
    }


def _source_id(url: str, title: str, used: set[str], sha256: str) -> str:
    base = _slug("ird-" + title)
    candidate = base
    if candidate in used:
        candidate = _slug(f"{base}-{sha256[:8]}")
    serial = 2
    while candidate in used:
        candidate = _slug(f"{base}-{serial}")
        serial += 1
    used.add(candidate)
    return candidate


def _find_error_snapshot(repository_root: Path, url: str) -> Path | None:
    key = _slug(Path(unquote(urlsplit(url).path)).stem, limit=70)
    candidates = sorted(repository_root.glob("data/raw/**/*.html"))
    return next(
        (candidate for candidate in candidates if key in _slug(candidate.stem, limit=100)), None
    )


def build_registry(
    manifest_paths: Iterable[str | Path] | None = None,
    *,
    repository_root: str | Path | None = None,
) -> SourceManifest:
    """Merge downloader manifests into a deterministic approved IRD registry."""

    root = Path(repository_root).resolve() if repository_root else _repo_root()
    if isinstance(manifest_paths, (str, Path)):
        manifest_paths = [manifest_paths]
    paths = [root / item for item in (manifest_paths or MANIFEST_NAMES)]
    title_map = _title_map(root)
    records: list[dict[str, Any]] = []
    by_hash: dict[str, dict[str, Any]] = {}
    used_ids: set[str] = set()
    for manifest_path in paths:
        if not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for item in manifest.get("sources", []):
            url = item.get("url")
            host = urlsplit(url or "").hostname
            if host not in ALLOWED_HOSTS:
                continue
            failed_xlsm = url == HTML_ERROR_URL and item.get("status") != "downloaded"
            if item.get("status") != "downloaded" and not failed_xlsm:
                continue
            source_file = (
                manifest_path.parent / item["file"]
                if item.get("file")
                else _find_error_snapshot(root, url)
            )
            if source_file is None or not source_file.exists():
                continue
            source_hash = _sha256(source_file)
            title = _url_title(url, title_map)
            if failed_xlsm:
                # Preserve the source's advertised workbook classification while
                # recording the captured bytes as HTML for signature validation.
                metadata = _metadata(
                    url,
                    "application/vnd.ms-excel.sheet.macroEnabled.12",
                    title,
                )
                record = {
                    "id": _source_id(url, title, used_ids, source_hash),
                    "title": title,
                    "source_url": url,
                    "local_file": Path(
                        os.path.relpath(source_file, root / "data/metadata")
                    ).as_posix(),
                    "media_type": "text/html",
                    **metadata,
                    "jurisdiction": "LK",
                    "status": "excluded",
                    "review_status": "rejected",
                    "sha256": source_hash,
                    "notes": (
                        "The URL is advertised as XLSM, but the captured HTTP 200 response "
                        "is an HTML error page."
                    ),
                    "exclusion_reason": "Expected XLSM download returned an HTML error page.",
                }
                records.append(record)
                continue
            if not _HASH_RE.fullmatch(source_hash):
                continue
            if source_hash in by_hash:
                canonical = by_hash[source_hash]
                if url != canonical["source_url"] and url not in canonical.setdefault(
                    "aliases", []
                ):
                    canonical["aliases"].append(url)
                continue
            media_type = item.get("content_type", "").split(";", 1)[0]
            if media_type not in {
                "application/pdf",
                "text/html",
                "application/vnd.ms-excel.sheet.macroEnabled.12",
            }:
                continue
            metadata = _metadata(url, media_type, title)
            local_file = Path(os.path.relpath(source_file, root / "data/metadata")).as_posix()
            record = {
                "id": _source_id(url, title, used_ids, source_hash),
                "title": title,
                "source_url": url,
                "local_file": local_file,
                "media_type": media_type,
                **metadata,
                "jurisdiction": "LK",
                "review_status": "approved",
                "sha256": source_hash,
            }
            if metadata["language"] != "en":
                record["status"] = "excluded"
                record["exclusion_reason"] = (
                    "Non-English source retained in the registry but excluded from the "
                    "English-first MVP corpus."
                )
            if item.get("final_url") and item["final_url"] != url:
                record["final_url"] = item["final_url"]
            by_hash[source_hash] = record
            records.append(record)
    return SourceManifest.model_validate(
        {"schema_version": 1, "allowed_hosts": ALLOWED_HOSTS, "sources": records}
    )


def write_registry(registry: SourceManifest, path: str | Path) -> None:
    """Write stable, reviewable YAML using explicit ISO date strings."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "allowed_hosts": list(ALLOWED_HOSTS),
        "sources": [
            source.model_dump(mode="json", exclude_defaults=True) for source in registry.sources
        ],
    }
    output.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", default="data/metadata/sources.yaml", help="registry YAML path"
    )
    parser.add_argument(
        "--build", action="store_true", help="build registry from local downloader manifests"
    )
    parser.add_argument(
        "--output", default="data/metadata/sources.yaml", help="output YAML for --build"
    )
    parser.add_argument(
        "--json-summary", action="store_true", help="print a machine-readable validation summary"
    )
    args = parser.parse_args(argv)
    if args.build:
        write_registry(build_registry(), args.output)
    manifest = Path(args.manifest)
    if not manifest.exists() and (Path("..") / manifest).exists():
        manifest = Path("..") / manifest
    if args.build and args.manifest == "data/metadata/sources.yaml":
        output = Path(args.output)
        if output.exists():
            manifest = output
    summary = validate_registry(manifest)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["valid"] else 1


if __name__ == "__main__":
    sys.exit(main())
