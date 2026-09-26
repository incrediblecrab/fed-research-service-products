"""The Federal Reserve Bank of Minneapolis's Beige Book archive on www.minneapolisfed.org: every edition from May 1970, one page per section.

The Board hosts no edition before October 30, 1996, so those editions come from here. The site's sitemap (robots.txt allows it) lists each page as https://minneapolisfed.org/beige-book-reports/YYYY/YYYY-MM-xx, xx one of the section codes in SLUGS, with a lastmod. A page is a Next.js page whose __NEXT_DATA__ JSON holds the report as HTML; a missing page answers 404 with the site's landing page template.

Pages are addressed by month, but two months held two editions each (June 2 and June 23, 1971; January 2 and January 29, 1980), and each address serves one of them. So a page belongs to the edition whose date its report prints, never to its month: on September 25, 2026, 9 of the 13 June 1971 addresses served June 2 and the other 4 June 23, and 5 of the 13 January 1980 addresses served January 2 and the other 8 January 29. The sections neither address serves are in no source's HTML.

Some pages hold a note in place of the report ("The January 12, 1971 Boston report is not available."); such a section has no text, and its row names the page.
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit

from lxml import etree

from .sections import DISTRICTS, names
from .text import html_text, parse_html

SITEMAP = "https://www.minneapolisfed.org/sitemap.xml"
SOURCE = "minneapolisfed.org"
TEMPLATE = "BeigeBookPage"
SLUGS = {"su": "summary", "bo": "boston", "ny": "new-york", "ph": "philadelphia", "cl": "cleveland", "ri": "richmond", "at": "atlanta", "ch": "chicago", "sl": "st-louis", "mi": "minneapolis", "kc": "kansas-city", "da": "dallas", "sf": "san-francisco", "sr": "special-report"}
SECTION_SLUG = {section: slug for slug, section in SLUGS.items()}
PAGE = re.compile(r"/beige-book-reports/(\d{4})/((\d{4})-\d{2})-([a-z]+)")
NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
# The report's text opens with its date on a line of its own: <p> <strong>May 18, 1983</strong></p>, sometimes inside a <div> (November 10, 1971) or with a space before the comma (January 12 , 1971).
DATELINE = re.compile(r"([A-Z][a-z]+) (\d{1,2}) ?, (\d{4})(?:\n|$)")
# A note in place of the report is one short line.
UNAVAILABLE = re.compile(r"[^\n]{0,150}\bnot available\b[^\n]{0,50}")


class ParseError(ValueError):
    """A page does not have the structure this module reads; nothing is guessed."""


@dataclass(frozen=True)
class Page:
    date: str
    text: str | None
    title: str


def page_url(month, slug):
    return f"https://www.minneapolisfed.org/beige-book-reports/{month[:4]}/{month}-{slug}"


def parse_sitemap(raw):
    """{month: {code: lastmod}} for every Beige Book section page the sitemap lists as YYYY-MM-code. A page listed twice (every June 1971 and January 1980 page, measured September 25, 2026) counts once, with its latest lastmod. lastmod is kept as written; it has no time zone."""
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    root = etree.fromstring(raw, parser)
    months = {}
    for url in root.iter(f"{NS}url"):
        loc = (url.findtext(f"{NS}loc") or "").strip()
        match = PAGE.fullmatch(urlsplit(loc).path)
        if not match or match.group(1) != match.group(3):
            continue
        lastmod = (url.findtext(f"{NS}lastmod") or "").strip() or None
        codes = months.setdefault(match.group(2), {})
        code = match.group(4)
        codes[code] = max(filter(None, (codes.get(code), lastmod)), default=None)
    if not months:
        raise ParseError(f"{SITEMAP} lists no Beige Book page")
    return months


def _route(raw):
    doc = parse_html(raw)
    scripts = doc.xpath("//script[@id='__NEXT_DATA__']")
    if len(scripts) != 1:
        raise ParseError("no __NEXT_DATA__")
    data = json.loads(scripts[0].text or "null")
    try:
        return data["props"]["pageProps"]["layoutData"]["sitecore"]["route"] or {}
    except (KeyError, TypeError):
        raise ParseError("__NEXT_DATA__ has no route") from None


def _field(fields, name):
    value = fields.get(name)
    return value.get("value") if isinstance(value, dict) else None


def parse_page(raw, section):
    """The page's report: its date (ISO), its text without the opening dateline (the edition column holds it), and its title. The text is None when the page holds a note that the report is not available. ParseError when the page is not a Beige Book page, has no dateline or text, or names another district."""
    route = _route(raw)
    if route.get("templateName") != TEMPLATE:
        raise ParseError(f"template {route.get('templateName')!r}, not {TEMPLATE}")
    fields = route.get("fields") or {}
    report, title = _field(fields, "Report") or "", _field(fields, "Title") or ""
    text = html_text(report) or ""
    match = DATELINE.match(text)
    if not match:
        raise ParseError(f"the report does not open with a date on a line of its own: {text[:60]!r}")
    try:
        day = datetime.strptime(" ".join(match.groups()), "%B %d %Y").date()
    except ValueError:
        raise ParseError(f"the report opens with {match.group(0).strip()!r}, which is not a date") from None
    named = _field(fields, "District") or title
    if section in DISTRICTS and not names(section, named):
        raise ParseError(f"the page names {named!r}, not {section}")
    text = text[match.end():].strip()
    if not text:
        raise ParseError("the report has no text after its date")
    return Page(day.isoformat(), None if UNAVAILABLE.fullmatch(text) else text, title)
