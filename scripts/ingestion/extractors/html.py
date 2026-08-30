"""Extract visible content from downloaded official IRD HTML snapshots.

This module deliberately accepts paths (or a :class:`SourceRecord` whose
``local_file`` points at a path) and never performs a network request.  The
output is the same locator-aware ``ExtractionRecord`` used by the other
ingestion workers.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Iterator
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, NavigableString, Tag

try:  # Running as ``python -m scripts.ingestion.extractors.html``.
    from ..models import ExtractionMethod, ExtractionRecord, SourceRecord, WarningCode
except ImportError:  # Running the file from the ingestion directory.
    from models import ExtractionMethod, ExtractionRecord, SourceRecord, WarningCode  # type: ignore


_OFFICIAL_HOST_SUFFIX = ".ird.gov.lk"
_IRD_MAIN_SELECTORS = (
    '[data-name="ContentPlaceHolderMain"]',
    "#DeltaPlaceHolderMain",
    ".ms-rtestate-field",
    ".content_common",
    ".container-body",
)
_REMOVED_TAGS = {
    "aside",
    "button",
    "canvas",
    "footer",
    "form",  # The source pages use an ASP form around the whole page; handled specially below.
    "iframe",
    "input",
    "menu",
    "nav",
    "noscript",
    "option",
    "script",
    "select",
    "style",
    "svg",
    "textarea",
}
_SEMANTIC_TAGS = {
    "blockquote",
    "dl",
    "ol",
    "p",
    "pre",
    "table",
    "ul",
    "figure",
}
_BOILERPLATE_MARKERS = re.compile(
    r"(?:^|[-_\s])(?:banner|breadcrumb|cookie|feedback|footer|header|leftnav|leftpanel|logo|"
    r"menu|modal|nav|navigation|quicklaunch|ribbon|search|share|sidebar|social|suitebar|"
    r"topnavigation|updated)(?:$|[-_\s])",
    re.IGNORECASE,
)
_HIDDEN_STYLE = re.compile(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", re.IGNORECASE)
_HIDDEN_CLASS = re.compile(
    r"(?:^|[-_\s])(?:d-none|hidden|visually-hidden)(?:$|[-_\s])", re.IGNORECASE
)
_HEADING_TAGS = {f"h{level}" for level in range(1, 7)}


def _clean_text(value: str) -> str:
    """Apply deterministic whitespace and Unicode cleanup to visible text."""

    value = unicodedata.normalize("NFKC", value).replace("\u00ad", "")
    # IRD snapshots contain zero-width markers around some headings and links;
    # they carry no visible meaning and otherwise create false non-empty rows.
    value = "".join(char for char in value if unicodedata.category(char) != "Cf")
    value = value.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    value = re.sub(r"[ \t\f\v]+", " ", value)
    value = re.sub(r"\n[ \t]+", "\n", value)
    return value.strip()


def _is_official_url(source_url: str) -> bool:
    parsed = urlsplit(source_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed.scheme.lower() == "https" and (
        host == "ird.gov.lk" or host.endswith(_OFFICIAL_HOST_SUFFIX)
    )


def _meaningful_link(href: str | None, source_url: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if (
        not href
        or href.startswith("#")
        or href.lower().startswith(("javascript:", "data:", "mailto:", "tel:"))
    ):
        return None
    resolved = urljoin(source_url, href)
    parsed = urlsplit(resolved)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        return None
    return resolved


def _plain_text(node: Tag | NavigableString) -> str:
    if isinstance(node, NavigableString):
        return _clean_text(str(node))
    return _clean_text(node.get_text(" ", strip=True))


def _inline_text(node: Tag | NavigableString, source_url: str) -> str:
    """Serialize inline markup, retaining meaningful links as Markdown links."""

    if isinstance(node, NavigableString):
        return _clean_text(str(node))
    if node.name == "br":
        return "\n"
    if node.name == "img":
        return _clean_text(node.get("alt", ""))
    if node.name == "a":
        label = _clean_text(" ".join(_inline_text(child, source_url) for child in node.children))
        target = _meaningful_link(node.get("href"), source_url)
        if target and label:
            return f"[{label}]({target})"
        return label
    return " ".join(part for child in node.children if (part := _inline_text(child, source_url)))


def _direct_cells(row: Tag, source_url: str) -> list[str]:
    cells = [child for child in row.find_all(["th", "td"], recursive=False)]
    if not cells:
        cells = row.find_all(["th", "td"])
    return [_clean_text(_inline_text(cell, source_url)) for cell in cells]


def _serialize_table(table: Tag, source_url: str) -> tuple[str, list[str]]:
    rows = table.find_all("tr")
    if not rows:
        return "", []
    values = [_direct_cells(row, source_url) for row in rows]
    values = [row for row in values if any(row)]
    if not values:
        return "", []

    header_rows = [row for row in rows if row.find(["th"], recursive=False) is not None]
    raw_headers = _direct_cells(header_rows[0], source_url) if header_rows else []
    # ``ExtractionRecord`` requires non-empty header labels.  Blank header
    # cells remain represented in row serialization but are omitted here.
    headers = [header for header in raw_headers if header]
    lines = []
    caption = table.find("caption")
    if caption:
        caption_text = _clean_text(_inline_text(caption, source_url))
        if caption_text:
            lines.append(f"Table: {caption_text}")
    if headers:
        lines.append("Headers: " + " | ".join(headers))
    start = 1 if headers and values and values[0] == raw_headers else 0
    for row in values[start:]:
        lines.append("Row: " + " | ".join(row))
    return "\n".join(lines), headers


def _serialize_list(node: Tag, source_url: str, level: int = 0) -> str:
    lines: list[str] = []
    ordered = node.name == "ol"
    items = node.find_all("li", recursive=False)
    for index, item in enumerate(items, start=1):
        nested = [child for child in item.find_all(["ul", "ol"], recursive=False)]
        parts = [
            _inline_text(child, source_url)
            for child in item.children
            if not (isinstance(child, Tag) and child.name in {"ul", "ol"})
        ]
        text = _clean_text(" ".join(part for part in parts if part))
        prefix = f"{index}." if ordered else "-"
        if text:
            lines.append(f"{'  ' * level}{prefix} {text}")
        for child in nested:
            nested_text = _serialize_list(child, source_url, level + 1)
            if nested_text:
                lines.extend(nested_text.splitlines())
    return "\n".join(lines)


def _serialize_definition_list(node: Tag, source_url: str) -> str:
    lines: list[str] = []
    current_term = ""
    for child in node.find_all(["dt", "dd"], recursive=False):
        value = _clean_text(_inline_text(child, source_url))
        if not value:
            continue
        if child.name == "dt":
            current_term = value
        elif current_term:
            lines.append(f"{current_term}: {value}")
            current_term = ""
        else:
            lines.append(value)
    return "\n".join(lines)


def _serialize_block(node: Tag, source_url: str) -> tuple[str, str, list[str]]:
    if node.name in _HEADING_TAGS:
        return _clean_text(_inline_text(node, source_url)), "heading", []
    if node.name in {"ul", "ol"}:
        return _serialize_list(node, source_url), "list", []
    if node.name == "dl":
        return _serialize_definition_list(node, source_url), "list", []
    if node.name == "table":
        content, headers = _serialize_table(node, source_url)
        return content, "table", headers
    content = _clean_text(_inline_text(node, source_url))
    return content, "paragraph", []


def _has_semantic_descendant(node: Tag) -> bool:
    return any(
        child.name in _SEMANTIC_TAGS or child.name in _HEADING_TAGS for child in node.find_all()
    )


def _is_layout_table(table: Tag) -> bool:
    """Identify SharePoint layout tables that wrap a real content table."""

    marker = f"{table.get('id', '')} {' '.join(table.get('class', []))}".lower()
    if any(value in marker for value in ("webpart", "ms-core", "ms-table", "layout")):
        return True
    if table.find("th") is not None:
        return False
    if table.find("table") is not None:
        return True
    # SharePoint's unclassed grid wrappers contain layout ``div`` elements in
    # their cells, whereas IRD content tables use classes such as ``in-table``.
    if any(value in marker for value in ("in-table", "e-table", "d-table")):
        return False
    return any(cell.find(["div", "section", "ul", "ol", "p"]) for cell in table.find_all("td"))


def _iter_blocks(node: Tag) -> Iterator[Tag]:
    """Yield semantic blocks while flattening layout-only container elements."""

    direct_text: list[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            text = _clean_text(str(child))
            if text:
                direct_text.append(text)
            continue
        if not isinstance(child, Tag):
            continue
        if child.name in _HEADING_TAGS:
            yield child
        elif child.name == "table" and _is_layout_table(child):
            yield from _iter_blocks(child)
        elif child.name in _SEMANTIC_TAGS:
            yield child
        elif _has_semantic_descendant(child):
            yield from _iter_blocks(child)
        elif _clean_text(_inline_text(child, "https://www.ird.gov.lk/")):
            # A card or link-only layout element with no semantic descendants is
            # still visible source content and must not be discarded.
            yield child
    if direct_text:
        # Preserve text directly under a layout container without re-serializing
        # all descendants (which would duplicate semantic blocks).
        synthetic = BeautifulSoup("<p></p>", "lxml").p
        if synthetic is not None:
            synthetic.append(" ".join(direct_text))
            yield synthetic


def _remove_boilerplate(root: Tag) -> None:
    for element in list(root.find_all(True)):
        # Decomposing a layout ancestor also invalidates descendants already
        # present in this snapshot of the traversal.
        if element.attrs is None:
            continue
        name = (element.name or "").lower()
        classes = " ".join(element.get("class", []))
        marker_text = f"{element.get('id', '')} {classes}"
        style = element.get("style", "")
        hidden = (
            element.get("aria-hidden", "").lower() == "true"
            or _HIDDEN_STYLE.search(style) is not None
            or "ms-hide" in classes.lower()
            or _HIDDEN_CLASS.search(classes) is not None
            or _BOILERPLATE_MARKERS.search(marker_text) is not None
        )
        if name in _REMOVED_TAGS and name != "form":
            element.decompose()
        elif name == "form":
            # IRD's ASP form wraps the real page. Remove controls from it but
            # keep the form's visible source content.
            continue
        elif hidden:
            element.decompose()
        else:
            for attribute in list(element.attrs):
                if attribute.lower().startswith("on"):
                    del element.attrs[attribute]
            if name in {"a", "img"} and element.get("href", "").lower().startswith("javascript:"):
                element.attrs.pop("href", None)


def _select_main(soup: BeautifulSoup) -> tuple[Tag, list[WarningCode]]:
    main = soup.find("main")
    if isinstance(main, Tag):
        return main, []
    role_main = soup.find(attrs={"role": re.compile(r"^main$", re.IGNORECASE)})
    if isinstance(role_main, Tag):
        return role_main, []
    for selector in _IRD_MAIN_SELECTORS:
        candidate = soup.select_one(selector)
        if candidate is not None:
            return candidate, []
    body = soup.body or soup
    return body, [WarningCode.HTML_MAIN_FALLBACK]


def _document_title(soup: BeautifulSoup, source_url: str) -> str:
    """Return a stable page-level locator from the snapshot itself.

    HTML records do not have PDF page numbers or spreadsheet ranges.  The
    document title is therefore their natural page-level locator.  A small
    number of snapshots may omit ``<title>``; in that case retain the exact
    canonical URL rather than inventing a section name.  The URL is already
    validated as an official HTTPS URL by :func:`_records_from_html`.
    """

    title = soup.find("title")
    if isinstance(title, Tag):
        value = _clean_text(title.get_text(" ", strip=True))
        if value:
            return value
    return source_url


def _resolve_local_file(
    source: SourceRecord, path: str | Path | None, base_dir: str | Path | None
) -> Path:
    if path is not None:
        candidate = Path(path)
        if candidate.is_file():
            return candidate
        raise FileNotFoundError(f"HTML snapshot not found: {candidate}")
    local = Path(source.local_file)
    candidates = [local]
    if base_dir is not None:
        candidates.insert(0, Path(base_dir) / local)
    candidates.extend((Path("data/metadata") / local, Path.cwd() / local))
    # Registry paths are relative to ``data/metadata/sources.yaml``.  Workers
    # are commonly invoked from ``backend/``, so also search repository
    # ancestors without ever constructing a URL or fetching a missing file.
    for directory in (Path.cwd(), *Path.cwd().parents):
        candidates.append(directory / "data" / "metadata" / local)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"HTML snapshot not found for {source.id}: {source.local_file}")


def _records_from_html(
    payload: bytes,
    *,
    source_id: str,
    source_url: str,
    warnings: Iterable[WarningCode] = (),
) -> list[ExtractionRecord]:
    if not _is_official_url(source_url):
        raise ValueError("HTML extraction only accepts HTTPS official IRD source URLs")
    soup = BeautifulSoup(payload, "lxml")
    page_title = _document_title(soup, source_url)
    root, selection_warnings = _select_main(soup)
    _remove_boilerplate(root)
    common_warnings = list(dict.fromkeys([*warnings, *selection_warnings]))
    records: list[ExtractionRecord] = []
    title_path: list[str] = []
    for block in _iter_blocks(root):
        content, kind, headers = _serialize_block(block, source_url)
        content = re.sub(r"\n{3,}", "\n\n", content).strip()
        if not content:
            continue
        if kind == "heading":
            level = int(block.name[1])
            title_path = title_path[: level - 1]
            title_path.append(_plain_text(block))
            block_title_path = list(title_path)
        else:
            # A page without semantic headings still needs a citable locator.
            # Use the extracted page title (or exact source URL fallback) as a
            # page-level title path and section; do not manufacture page numbers
            # or section labels for HTML content.
            block_title_path = list(title_path) or [page_title]
        ordinal = len(records) + 1
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        records.append(
            ExtractionRecord(
                record_id=f"{source_id}-html-{ordinal}",
                source_id=source_id,
                content_kind=kind,
                ordinal=ordinal,
                title_path=block_title_path,
                content=content,
                section=block_title_path[-1] if block_title_path else None,
                table_headers=headers,
                extraction_method=ExtractionMethod.HTML_DOM,
                warnings=common_warnings,
                content_hash=content_hash,
            )
        )
    return records


def extract_html(
    source: SourceRecord | str | Path,
    path: str | Path | None = None,
    *,
    source_id: str | None = None,
    source_url: str | None = None,
    base_dir: str | Path | None = None,
) -> list[ExtractionRecord]:
    """Extract records from a local HTML snapshot.

    ``source`` may be a validated ``SourceRecord`` or a snapshot path.  When
    passing a path directly, ``source_id`` and ``source_url`` are required.
    ``path`` is an optional explicit override useful when a registry path is
    relative to a manifest outside the current working directory.
    """

    if isinstance(source, SourceRecord):
        record_source_id = source.id
        record_source_url = str(source.source_url)
        snapshot = _resolve_local_file(source, path, base_dir)
    else:
        snapshot = Path(source)
        if not snapshot.is_file():
            raise FileNotFoundError(f"HTML snapshot not found: {snapshot}")
        if not source_id or not source_url:
            raise ValueError("source_id and source_url are required when source is a path")
        record_source_id = source_id
        record_source_url = source_url
    return _records_from_html(
        snapshot.read_bytes(),
        source_id=record_source_id,
        source_url=record_source_url,
    )


def extract_html_file(
    path: str | Path,
    *,
    source_id: str,
    source_url: str,
) -> list[ExtractionRecord]:
    """Explicit path-oriented alias for callers that do not load a registry."""

    return extract_html(path, source_id=source_id, source_url=source_url)


extract = extract_html

__all__ = ["extract", "extract_html", "extract_html_file"]
