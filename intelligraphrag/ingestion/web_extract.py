"""BeautifulSoup HTML extraction for the web crawler.

Turns a fetched HTML page into the same record shape the regex path produced
(title / headings / date / linked_pdfs / content), but with two upgrades the
client asked for:

  * robust main-content extraction (drops nav/header/footer/script/style), and
  * HTML <table> -> markdown, emitted as ``[EXTRACTED TABLE]`` blocks so the
    existing chunker + parse_table() build structured table_data for the
    table store / SQL / deterministic cell-lookup lanes. A government table on
    a web page becomes cell-queryable exactly like a table parsed from a PDF.

BeautifulSoup is the primary path; if it is unavailable the module degrades to
a small regex extractor so ingestion never hard-fails. No domain hardcoding.
"""
from __future__ import annotations

import re
from typing import Dict, List
from urllib.parse import urljoin

try:                                  # primary path
    from bs4 import BeautifulSoup
    _BS4 = True
except Exception:                     # noqa: BLE001 — degrade, never hard-fail
    _BS4 = False

# Tags that never carry document content — removed before text extraction.
_NOISE_TAGS = ("script", "style", "noscript", "nav", "footer", "header",
               "aside", "form", "svg", "button", "iframe")
_PDF_HREF = re.compile(r"\.pdf(?:[?#]|$)", re.I)

# regex fallbacks (used only when BeautifulSoup is not importable)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H = re.compile(r"<h[1-3][^>]*>(.*?)</h[1-3]>", re.I | re.S)
_PDF = re.compile(r'href=["\']([^"\']+\.pdf[^"\']*)["\']', re.I)
_META_DATE = re.compile(
    r'<meta[^>]+(?:article:published_time|name=["\']date["\'])[^>]+'
    r'content=["\']([^"\']+)["\']', re.I)


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def html_table_to_markdown(table) -> str:
    """Render a BeautifulSoup <table> as a GitHub-flavoured markdown table with
    a header separator row, so parse_markdown_table() recognises the header and
    produces structured {columns, rows}. Ragged rows are padded to a common
    width; cells are whitespace-normalised."""
    rows: List[List[str]] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if not cells:
            continue
        rows.append([_clean(c.get_text(" ", strip=True)) for c in cells])
    rows = [r for r in rows if any(c for c in r)]
    if len(rows) < 2:                      # not a real table (or header only)
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    header, body = rows[0], rows[1:]
    out = ["| " + " | ".join(header) + " |",
           "| " + " | ".join("---" for _ in range(width)) + " |"]
    out += ["| " + " | ".join(r) + " |" for r in body]
    return "\n".join(out)


def _extract_bs4(html: str, url: str) -> Dict:
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:                      # noqa: BLE001 — lxml missing/broken
        soup = BeautifulSoup(html, "html.parser")

    title = _clean(soup.title.get_text()) if soup.title else ""

    # published date: standard meta tags, then a <time datetime=…>
    date = ""
    for attrs in ({"property": "article:published_time"}, {"name": "date"},
                  {"property": "og:updated_time"}, {"name": "dcterms.date"}):
        m = soup.find("meta", attrs=attrs)
        if m and m.get("content"):
            date = m["content"].strip()
            break
    if not date:
        t = soup.find("time")
        if t:
            date = (t.get("datetime") or _clean(t.get_text())).strip()

    # linked PDFs -> absolute URLs (deduped, order-preserving)
    pdfs = list(dict.fromkeys(
        urljoin(url, a["href"]) for a in soup.find_all("a", href=True)
        if _PDF_HREF.search(a["href"])))

    headings = [_clean(h.get_text(" ", strip=True))
                for h in soup.find_all(["h1", "h2", "h3"])]
    headings = [h for h in headings if h][:10]

    # tables FIRST (before we strip them from the text body)
    tables_md = [m for m in (html_table_to_markdown(t)
                             for t in soup.find_all("table")) if m]

    # main body text, with noise + tables removed so text isn't duplicated
    for tag in soup(list(_NOISE_TAGS) + ["table"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    body_text = main.get_text("\n", strip=True)

    parts: List[str] = []
    if title:
        parts.append(title)
    if body_text:
        parts.append(body_text)
    for md in tables_md:
        parts.append("[EXTRACTED TABLE]\n" + md)
    content = "\n\n".join(parts)

    return {"title": title, "headings": headings, "date": date[:40],
            "linked_pdfs": pdfs, "content": content, "n_tables": len(tables_md)}


def _extract_regex(html: str, url: str) -> Dict:
    """Minimal fallback when BeautifulSoup is unavailable (no table extraction)."""
    from .loaders import _html_to_text
    tm = _TITLE.search(html)
    title = _clean(re.sub("<[^>]+>", "", tm.group(1))) if tm else ""
    headings = [_clean(re.sub("<[^>]+>", "", h)) for h in _H.findall(html)]
    headings = [h for h in headings if h][:10]
    dm = _META_DATE.search(html)
    date = (dm.group(1).strip()[:40] if dm else "")
    pdfs = list(dict.fromkeys(urljoin(url, h) for h in _PDF.findall(html)))
    return {"title": title, "headings": headings, "date": date,
            "linked_pdfs": pdfs, "content": _html_to_text(html), "n_tables": 0}


def extract_content(html: str, url: str = "") -> Dict:
    """Extract {title, headings, date, linked_pdfs, content, n_tables} from HTML.
    Uses BeautifulSoup when available, else a regex fallback."""
    if not html:
        return {"title": "", "headings": [], "date": "", "linked_pdfs": [],
                "content": "", "n_tables": 0}
    return _extract_bs4(html, url) if _BS4 else _extract_regex(html, url)
