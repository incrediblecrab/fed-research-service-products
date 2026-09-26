"""The Beige Book dataset: one row per section of every edition from May 1970 on.

An edition is a date that one of the Board's lists names (board.py). Its text comes from the Board's HTML where either list links it (October 30, 1996 on) and from the Minneapolis Fed's archive before that (minneapolis.py). Every edition has a row for the summary and each of the twelve districts, and May 18, 1983 one more for its special report. A section that no source serves as HTML has a row with null text, and null source and url unless a page holds a note that the report is not available, which they then name.

A unit is what one fetch covers: a Board edition, or a Minneapolis month, whose pages can hold two editions. Its version changes when a list or the sitemap says something about it changed, and the sync fetches it again then. It is also fetched again when its recheck is due, because the Board's pages can change after release: on September 25, 2026 the pages of January 14, 2026 answered with a Last-Modified date of February 26, 2026.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date

import pyarrow as pa

from . import board, minneapolis
from .http import PageMissing
from .sections import STANDARD, district_number, rank

REPO_ID = "incrediblecrab/federal-reserve-beige-book"
SCHEMA = pa.schema([
    ("id", pa.string()),
    ("edition", pa.string()),
    ("section", pa.string()),
    ("district", pa.int8()),
    ("text", pa.large_string()),
    ("source", pa.string()),
    ("url", pa.string()),
    ("pdf_url", pa.string()),
    ("source_modified", pa.string()),
    ("fetched_at", pa.string()),
])
COLUMNS = SCHEMA.names
# Some Board pages serve the Unicode replacement character (the bytes EF BF BD) where a character was lost before publication: "focusing inward" \ufffd "not spending" on the Boston page of March 5, 2003. The text keeps it as served, and the manifest names the rows that hold it.
REPLACEMENT_CHARACTER = "\ufffd"
# Days between rechecks of a unit whose version has not changed. The Board answers a conditional GET of an unchanged page with 304 and no body (measured September 25, 2026), so its rechecks are cheap; Minneapolis always answers 200, and its sitemap's lastmod already signals an edit, so its months are rechecked once a year as a backstop.
RECENT_DAYS = 365
RECENT_RECHECK_DAYS = 7
RECHECK_DAYS = 30
MINNEAPOLIS_RECHECK_DAYS = 365


def row_id(edition, section):
    return f"{edition}-{section}"


def partition_of(uid):
    """Rows and units are kept by year: 1971-06-02-summary and the unit 1971-06 go to 1971."""
    return uid[:4]


def normalize(row):
    out = {name: row.get(name) for name in COLUMNS}
    if out["district"] is not None:
        out["district"] = int(out["district"])
    return out


def row_weight(row):
    return len(row["text"] or "")


def comparable(row):
    """A row as the sync compares it: everything but when it was fetched."""
    out = normalize(row)
    del out["fetched_at"]
    return out


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


@dataclass
class Edition:
    date: str
    html_url: str | None = None
    pdf_url: str | None = None
    lists: tuple = ()

    @property
    def board_hosted(self):
        return self.html_url is not None


@dataclass
class Listing:
    """Everything the lists said in one reading. head is the landing page's part, which the probe reads alone."""

    editions: dict
    scheduled: list
    months: dict
    head: dict
    pages: int = 0


@dataclass
class Unit:
    """kind is "board" (id: the edition's date) or "minneapolis" (id: the month). codes: {section code: sitemap lastmod or None} for a Minneapolis month; others: the dates of editions of that month the Board hosts, whose pages there are skipped."""

    id: str
    kind: str
    editions: tuple
    version: str
    codes: dict = field(default_factory=dict)
    others: frozenset = frozenset()

    @property
    def partition(self):
        return partition_of(self.id)

    def recheck_days(self, today):
        if self.kind == "minneapolis":
            return MINNEAPOLIS_RECHECK_DAYS
        newest = max(date.fromisoformat(edition.date) for edition in self.editions)
        return RECENT_RECHECK_DAYS if (today - newest).days < RECENT_DAYS else RECHECK_DAYS


def head_of(published):
    """What the probe compares: the newest edition the landing page links, and how many it links."""
    return {"newest": max((item.date for item in published), default=None), "published": len(published)}


def assemble(published, scheduled, historical, months, head):
    """Editions from the Beige Book pages first, then the historical pages; for each link the first list that gives one wins. Raises board.ParseError for an HTML link of no known layout."""
    editions = {}
    for origin, items in (("beige-book", published), ("historical", historical)):
        for item in items:
            edition = editions.setdefault(item.date, Edition(item.date))
            edition.html_url = edition.html_url or item.html_url
            edition.pdf_url = edition.pdf_url or item.pdf_url
            if origin not in edition.lists:
                edition.lists += (origin,)
    for edition in editions.values():
        if edition.html_url:
            board.era_of(edition.html_url)
    upcoming = sorted({day for day in scheduled if day not in editions})
    return Listing(dict(sorted(editions.items())), upcoming, months, head)


def units_of(listing):
    """{partition: [Unit]}: one unit per edition the Board hosts, one per month of the others. A Minneapolis month fetches the thirteen standard sections whether or not the sitemap lists them, and any other section the sitemap lists."""
    out = {}
    by_month, hosted = {}, {}
    for edition in listing.editions.values():
        if edition.board_hosted:
            unit = Unit(edition.date, "board", (edition,), digest([edition.html_url, edition.pdf_url]))
            out.setdefault(unit.partition, []).append(unit)
            hosted.setdefault(edition.date[:7], set()).add(edition.date)
        else:
            by_month.setdefault(edition.date[:7], []).append(edition)
    standard = {minneapolis.SECTION_SLUG[section]: None for section in STANDARD}
    for month, editions in sorted(by_month.items()):
        codes = dict(sorted({**standard, **listing.months.get(month, {})}.items()))
        version = digest({"codes": codes, "editions": [[edition.date, edition.pdf_url] for edition in editions]})
        unit = Unit(month, "minneapolis", tuple(editions), version, codes, frozenset(hosted.get(month, ())))
        out.setdefault(unit.partition, []).append(unit)
    for units in out.values():
        units.sort(key=lambda unit: unit.id)
    return dict(sorted(out.items()))


def make_row(edition, section, text=None, source=None, url=None, modified=None):
    return {"id": row_id(edition.date, section), "edition": edition.date, "section": section, "district": district_number(section),
            "text": text, "source": source, "url": url, "pdf_url": edition.pdf_url, "source_modified": modified, "fetched_at": None}


class BeigeBookSource:
    """Reads the lists and fetches units through a Fetcher (http.py)."""

    def __init__(self, fetcher):
        self.fetcher = fetcher

    def head(self):
        published, _ = board.year_table(self.fetcher.page(board.LANDING), board.LANDING)
        return head_of(published)

    def listing(self, first_year=board.FIRST_YEAR):
        """Every list, read in full. Any page that does not answer 200 or does not parse raises, and so does a year page that names no edition, so a run never works from part of a list."""
        page = self.fetcher.page
        published, scheduled = board.year_table(page(board.LANDING), board.LANDING)
        if not published and not scheduled:
            raise board.ParseError(f"{board.LANDING} names no edition")
        head = head_of(published)
        pages = 2
        for url in board.year_pages(page(board.ARCHIVE)):
            more, later = board.year_table(page(url), url)
            if not more:
                raise board.ParseError(f"{url} links no edition")
            published += more
            scheduled += later
            pages += 1
        historical = []
        pages += 1
        for url in board.historical_years(page(board.HISTORICAL_INDEX), first_year=first_year):
            more = board.historical_year(page(url), url)
            if not more and int(board.HISTORICAL_PAGE.search(url).group(1)) >= board.FIRST_YEAR:
                raise board.ParseError(f"{url} links no Beige Book or Redbook")
            historical += more
            pages += 1
        months = minneapolis.parse_sitemap(page(minneapolis.SITEMAP))
        listing = assemble(published, scheduled, historical, months, head)
        listing.pages = pages + 1
        return listing

    def fetch(self, unit, stored):
        """The rows of the unit's editions. stored ({id: row}, the rows the dataset holds for them) lets a Board page be asked for only if it changed since its source_modified; an unchanged page's rows come back as stored."""
        if unit.kind == "board":
            return self._board(unit.editions[0], stored)
        return self._minneapolis(unit)

    def _board(self, edition, stored):
        era = board.era_of(edition.html_url)
        rows = []
        for sections, url in board.pages_of(era, edition.html_url):
            known = [stored.get(row_id(edition.date, section)) for section in sections]
            stamps = {(row["url"], row["source_modified"]) for row in known if row}
            headers = None
            if all(known) and len(stamps) == 1:
                ((was_url, modified),) = stamps
                if was_url == url and modified:
                    headers = {"If-Modified-Since": modified}
            response = self.fetcher.get(url, headers=headers)
            if response.status_code == 304 and headers:
                rows += [dict(row, pdf_url=edition.pdf_url) for row in known]
                continue
            if response.status_code != 200:
                raise PageMissing(f"HTTP {response.status_code} from {url}")
            texts = board.parse_page(era, response.content, sections, edition.date)
            modified = response.headers.get("Last-Modified")
            rows += [make_row(edition, section, texts[section], board.SOURCE, url, modified) for section in sections]
        return rows

    def _minneapolis(self, unit):
        by_date = {edition.date: edition for edition in unit.editions}
        found = {}
        for code, lastmod in sorted(unit.codes.items(), key=lambda item: rank(minneapolis.SLUGS.get(item[0], ""))):
            section = minneapolis.SLUGS.get(code)
            if section is None:
                raise minneapolis.ParseError(f"{unit.id}: the sitemap lists a section code {code!r} this module does not know")
            url = minneapolis.page_url(unit.id, code)
            response = self.fetcher.get(url)
            if response.status_code == 404 and lastmod is None:
                # Neither in the sitemap nor on the site: a section no source serves.
                continue
            if response.status_code != 200:
                raise PageMissing(f"HTTP {response.status_code} from {url}")
            page = minneapolis.parse_page(response.content, section)
            printed, listed = minneapolis.MISDATED.get((unit.id, code), (None, None))
            day = listed if page.date == printed else page.date
            if day in unit.others:
                continue
            if day not in by_date:
                raise minneapolis.ParseError(f"{url} is dated {page.date}; the lists name {sorted(by_date)} in {unit.id}")
            # A page with a note in place of the report gives a row without text that still names the page.
            found[(day, section)] = make_row(by_date[day], section, page.text, minneapolis.SOURCE, url, lastmod)
        rows = []
        for edition in unit.editions:
            extra = sorted((section for day, section in found if day == edition.date and section not in STANDARD), key=rank)
            for section in STANDARD + tuple(extra):
                rows.append(found.get((edition.date, section)) or make_row(edition, section))
        return rows

    def exists(self, row):
        """Whether a stored row's page still answers 200, asked before an edition no list names any more is removed."""
        return bool(row.get("url")) and self.fetcher.get(row["url"]).status_code == 200
