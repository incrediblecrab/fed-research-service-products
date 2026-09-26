"""Shared fakes: a fetcher that serves recorded pages by URL, and a scripted Beige Book source for the sync loop."""

import gzip
import math
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx

from fed_products import board, minneapolis
from fed_products.beige_book import Edition, Listing, head_of, make_row
from fed_products.board import Listed
from fed_products.card import render
from fed_products.http import PageMissing
from fed_products.pipeline import Context, sync
from fed_products.sections import SPECIAL_REPORT, STANDARD
from fed_products.store import LocalStore

FIXTURES = Path(__file__).parent / "fixtures"


def recorded(url):
    """The recorded body of a page, from fixtures/<host><path>.gz with each / as ~; None when none was recorded."""
    parts = urlsplit(url)
    path = FIXTURES / f"{parts.hostname}{parts.path}.gz".replace("/", "~")
    return gzip.decompress(path.read_bytes()) if path.is_file() else None


class FakeFetcher:
    """get() answers from routes, else from the recorded pages, else 404. A route is bytes (200), an int (that status), a (status, bytes) or (status, bytes, headers) tuple, an exception to raise, or a callable of (url, headers) returning one of those."""

    def __init__(self, routes=None, record=True):
        self.routes = dict(routes or {})
        self.record = record
        self.requests = []

    def get(self, url, headers=None):
        self.requests.append((url, dict(headers or {})))
        value = self.routes.get(url)
        if callable(value):
            value = value(url, dict(headers or {}))
        if value is None and self.record:
            value = recorded(url)
        if value is None:
            value = 404
        if isinstance(value, Exception):
            raise value
        request = httpx.Request("GET", url)
        if isinstance(value, int):
            return httpx.Response(value, request=request)
        if isinstance(value, tuple):
            status, content, *rest = value
            return httpx.Response(status, content=content, headers=rest[0] if rest else None, request=request)
        return httpx.Response(200, content=value, request=request)

    def page(self, url):
        response = self.get(url)
        if response.status_code != 200:
            raise PageMissing(f"HTTP {response.status_code} from {url}")
        return response.content

    def urls(self):
        return [url for url, _ in self.requests]

    def close(self):
        pass


def board_url(day):
    """An era 4 address for a scripted Board edition."""
    return f"https://www.federalreserve.gov/monetarypolicy/beigebook{day.replace('-', '')}-summary.htm"


def scripted(**overrides):
    """State for ScriptedSource. board: dates of editions the Board hosts. early: dates of editions before October 1996, fetched by Minneapolis month. specials: dates with a special report. months: the sitemap's {month: {code: lastmod}}. pdfs: {date: PDF address}. revisions: {date: n}, a page edit that changes the text but not the lists. texts: {row id: text} in place of the default. gaps: row ids no source serves. notes: row ids whose page holds a note that the report is not available. fail: unit ids whose fetch fails. fatal: {unit id: exception}. exists: dates whose first page still answers. stop_after: use up the budget after that many fetches."""
    return SimpleNamespace(**{"board": [], "early": [], "specials": set(), "months": {}, "pdfs": {}, "revisions": {}, "texts": {}, "gaps": set(), "notes": set(), "fail": set(), "fatal": {},
                              "exists": set(), "scheduled": [], "stop_after": None, "fetched": [], "stored_seen": {}, "exists_asked": [], "ctx": None, **overrides})


class ScriptedSource:
    """What beige_book.BeigeBookSource gives the sync loop, from a scripted state."""

    def __init__(self, state):
        self.state = state

    def editions(self):
        out = {day: Edition(day, board_url(day), self.state.pdfs.get(day), ("beige-book",)) for day in self.state.board}
        out.update({day: Edition(day, None, self.state.pdfs.get(day, f"https://www.federalreserve.gov/monetarypolicy/files/fomc{day.replace('-', '')}redbook.pdf"), ("historical",)) for day in self.state.early})
        return dict(sorted(out.items()))

    def listing(self, first_year=None):
        editions = self.editions()
        published = [Listed(day, edition.html_url, edition.pdf_url) for day, edition in editions.items() if edition.board_hosted and day[:4] == max(editions)[:4]]
        return Listing(editions, list(self.state.scheduled), dict(self.state.months), head_of(published), pages=3)

    def head(self):
        return self.listing().head

    def exists(self, row):
        self.state.exists_asked.append(row["edition"])
        return row["edition"] in self.state.exists

    def fetch(self, unit, stored):
        self.state.fetched.append(unit.id)
        self.state.stored_seen[unit.id] = dict(stored)
        if self.state.stop_after and len(self.state.fetched) >= self.state.stop_after:
            self.state.ctx.deadline = 0
        if unit.id in self.state.fatal:
            raise self.state.fatal[unit.id]
        if unit.id in self.state.fail:
            raise RuntimeError(f"planted failure for {unit.id}")
        rows = []
        for edition in unit.editions:
            sections = STANDARD + ((SPECIAL_REPORT,) if edition.date in self.state.specials else ())
            revision = self.state.revisions.get(edition.date, 0)
            if edition.board_hosted:
                source, pages = board.SOURCE, {section: url for group, url in board.pages_of(board.era_of(edition.html_url), edition.html_url) for section in group}
            else:
                source, pages = minneapolis.SOURCE, {section: minneapolis.page_url(edition.date[:7], minneapolis.SECTION_SLUG[section]) for section in sections}
            for section in sections:
                uid = f"{edition.date}-{section}"
                if uid in self.state.gaps:
                    rows.append(make_row(edition, section))
                elif uid in self.state.notes:
                    rows.append(make_row(edition, section, None, source, pages[section], f"modified {revision}"))
                else:
                    text = self.state.texts.get(uid) or f"{section} of {edition.date}" + (f", revision {revision}" if revision else "")
                    rows.append(make_row(edition, section, text, source, pages[section], f"modified {revision}"))
        return rows


def local_store(tmp_path):
    return LocalStore(tmp_path / "hub", workdir=tmp_path, card=render)


def run_once(store, state, **context):
    context.setdefault("writer", "local")
    ctx = Context(store=store, deadline=math.inf, **context)
    state.ctx = ctx
    return sync(ctx, ScriptedSource(state))
