"""The dataset's source: every list read in full from pages recorded September 25, 2026, the units they make, and the rows a unit's fetch returns."""

import copy
import json

import pytest

from conftest import FakeFetcher, recorded
from fed_products import board, minneapolis
from fed_products.beige_book import BeigeBookSource, Edition, Unit, digest, units_of
from fed_products.board import ParseError
from fed_products.http import PageMissing
from fed_products.sections import STANDARD

MP = "https://www.federalreserve.gov/monetarypolicy/"
ERA3 = f"{MP}beigebook202001.htm"
MODIFIED = "Wed, 15 Jan 2020 19:00:10 GMT"


@pytest.fixture(scope="module")
def listing():
    return BeigeBookSource(FakeFetcher()).listing()


def test_the_lists_of_september_25_2026_name_490_editions(listing):
    editions = listing.editions
    assert (len(editions), min(editions), max(editions)) == (490, "1970-05-20", "2026-09-02")
    assert sum(edition.board_hosted for edition in editions.values()) == 240
    assert all(edition.board_hosted == (day >= "1996-10-30") for day, edition in editions.items())
    assert listing.scheduled == ["2026-10-14", "2026-11-25"]
    assert listing.head == {"newest": "2026-09-02", "published": 6}
    assert listing.pages == 85
    assert editions["2003-09-03"].lists == ("historical",), "only the historical page names it"
    assert editions["2003-09-03"].html_url == "https://www.federalreserve.gov/fomc/beigebook/2003/20030903/default.htm"
    assert editions["2006-09-06"].lists == ("beige-book",), "the historical page's link had an empty target"
    assert all(edition.pdf_url for day, edition in editions.items() if day < "1996-10-30"), "every edition before the Board's HTML has a PDF"
    assert [day for day in editions if day[:7] in ("1971-06", "1980-01")] == ["1971-06-02", "1971-06-23", "1980-01-02", "1980-01-29"]


def test_units_are_board_editions_and_minneapolis_months(listing):
    units = [unit for group in units_of(listing).values() for unit in group]
    board_units = [unit for unit in units if unit.kind == "board"]
    months = {unit.id: unit for unit in units if unit.kind == "minneapolis"}
    assert (len(board_units), len(months)) == (240, 248)
    assert [edition.date for edition in months["1971-06"].editions] == ["1971-06-02", "1971-06-23"]
    assert set(months["1983-05"].codes) == set(minneapolis.SLUGS)
    assert all(len(unit.codes) == 13 for month, unit in months.items() if month != "1983-05")
    assert sum(len(unit.editions) for unit in units) == 490
    assert units_of(listing) == units_of(BeigeBookSource(FakeFetcher()).listing()), "the same lists give the same units and versions"


def test_a_unit_version_changes_with_its_lists_and_its_sitemap_entries(listing):
    before = {unit.id: unit.version for group in units_of(listing).values() for unit in group}
    changed = copy.deepcopy(listing)
    changed.editions["2020-01-15"].pdf_url = "https://www.federalreserve.gov/monetarypolicy/files/moved.pdf"
    changed.months["1983-05"]["bo"] = "2027-01-01 00:00:00"
    changed.months["2020-01"]["bo"] = "2027-01-01 00:00:00"
    after = {unit.id: unit.version for group in units_of(changed).values() for unit in group}
    assert {uid for uid in before if before[uid] != after[uid]} == {"2020-01-15", "1983-05"}, "a Board edition does not depend on the sitemap"


@pytest.mark.parametrize("url, body, error", [
    (f"{MP}beigebook2003.htm", recorded(f"{MP}beigebook2003.htm").replace(b"HTML", b"XXXX").replace(b"PDF", b"XXX"), ParseError),
    (f"{MP}fomchistorical1971.htm", b"<html><body><th id='year'>1971</th></body></html>", ParseError),
    (f"{MP}fomchistorical1985.htm", 404, PageMissing),
    (minneapolis.SITEMAP, 404, PageMissing),
    (board.LANDING, recorded(board.LANDING).replace(b"beigebook202608-summary.htm", b"beigebook202608-overview.htm"), ParseError),
])
def test_a_list_that_does_not_answer_or_parse_stops_the_listing(url, body, error):
    with pytest.raises(error):
        BeigeBookSource(FakeFetcher({url: body})).listing()


def test_a_year_page_that_links_no_edition_stops_the_listing():
    url = f"{MP}beigebook2010.htm"
    empty = b"<html><body><table><tr><th id='year'>2010</th></tr></table></body></html>"
    with pytest.raises(ParseError, match="links no edition"):
        BeigeBookSource(FakeFetcher({url: empty})).listing()


def test_the_head_is_the_landing_page_alone():
    fetcher = FakeFetcher()
    assert BeigeBookSource(fetcher).head() == {"newest": "2026-09-02", "published": 6}
    assert fetcher.urls() == [board.LANDING]


def month_unit(listing, month):
    return next(unit for unit in units_of(listing)[month[:4]] if unit.id == month)


def test_a_month_with_two_editions_gives_each_its_pages_and_the_rest_are_gaps(listing):
    rows = BeigeBookSource(FakeFetcher()).fetch(month_unit(listing, "1971-06"), {})
    assert len(rows) == 26 and len({row["id"] for row in rows}) == 26
    gaps = sorted(row["id"] for row in rows if row["text"] is None)
    assert gaps == ["1971-06-02-atlanta", "1971-06-02-chicago", "1971-06-02-kansas-city", "1971-06-02-richmond"] + [f"1971-06-23-{s}" for s in ("boston", "cleveland", "dallas", "minneapolis", "new-york", "philadelphia", "san-francisco", "st-louis", "summary")]
    boston = next(row for row in rows if row["id"] == "1971-06-02-boston")
    assert boston["url"] == "https://www.minneapolisfed.org/beige-book-reports/1971/1971-06-bo" and boston["source"] == "minneapolisfed.org"
    assert boston["source_modified"] == "2019-09-06 11:00:58" and boston["pdf_url"].endswith("redbook19710602.pdf")
    assert boston["text"].startswith("First District directors continue to express guard")
    assert all(row["source"] is None and row["url"] is None for row in rows if row["text"] is None)


def mpls_unit(codes, editions=("1975-03-19",), others=()):
    return Unit(editions[0][:7], "minneapolis", tuple(Edition(day, None, f"https://www.federalreserve.gov/x{day}.pdf") for day in editions), "v", codes, frozenset(others))


def mpls_page(day, district="Boston", report=None):
    report = report or f"<p><strong>{board.spoken(day)}</strong></p><p>Business activity in {district} rose.</p>"
    route = {"templateName": "BeigeBookPage", "fields": {"Report": {"value": report}, "Title": {"value": f"{district}: 1975"}, "District": {"value": district}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps({"props": {"pageProps": {"layoutData": {"sitecore": {"route": route}}}}})}</script>'.encode()


def all_pages(day, month):
    return {minneapolis.page_url(month, minneapolis.SECTION_SLUG[s]): mpls_page(day, "National Summary" if s == "summary" else s.replace("-", " ").title()) for s in STANDARD}


def test_a_404_is_a_gap_only_for_a_page_the_sitemap_does_not_list():
    routes = all_pages("1975-03-19", "1975-03")
    routes[minneapolis.page_url("1975-03", "bo")] = 404
    codes = {minneapolis.SECTION_SLUG[s]: None for s in STANDARD}
    rows = BeigeBookSource(FakeFetcher(routes, record=False)).fetch(mpls_unit(codes), {})
    assert [row["id"] for row in rows if row["text"] is None] == ["1975-03-19-boston"]
    with pytest.raises(PageMissing):
        BeigeBookSource(FakeFetcher(routes, record=False)).fetch(mpls_unit(dict(codes, bo="2019-09-06 11:00:00")), {})


def test_a_note_that_the_report_is_not_available_is_a_row_without_text_that_names_its_page():
    routes = all_pages("1971-01-12", "1971-01")
    note = minneapolis.page_url("1971-01", "bo")
    routes[note] = mpls_page("1971-01-12", report="<p><strong>January 12, 1971</strong></p><p>The January 12, 1971 Boston report is not available.</p>")
    rows = BeigeBookSource(FakeFetcher(routes, record=False)).fetch(mpls_unit({minneapolis.SECTION_SLUG[s]: None for s in STANDARD}, ("1971-01-12",)), {})
    gaps = [row for row in rows if row["text"] is None]
    assert [(row["id"], row["url"], row["source"]) for row in gaps] == [("1971-01-12-boston", note, "minneapolisfed.org")]


def test_a_page_dated_with_an_edition_outside_the_unit_raises_unless_the_board_hosts_it():
    routes = all_pages("1975-03-19", "1975-03")
    routes[minneapolis.page_url("1975-03", "bo")] = mpls_page("1975-03-26")
    codes = {minneapolis.SECTION_SLUG[s]: None for s in STANDARD}
    with pytest.raises(minneapolis.ParseError, match="dated 1975-03-26"):
        BeigeBookSource(FakeFetcher(routes, record=False)).fetch(mpls_unit(codes), {})
    rows = BeigeBookSource(FakeFetcher(routes, record=False)).fetch(mpls_unit(codes, others=("1975-03-26",)), {})
    assert [row["id"] for row in rows if row["text"] is None] == ["1975-03-19-boston"]


@pytest.mark.parametrize("code, edition, printed, opening", [
    ("ri", "1987-10-27", "1987-10-23", "Overview\n\nGrowth remains the predominant trend in the Fifth District. Manufacturing is leading the way"),
    ("su", "1973-08-15", "1973-08-10", "The overall impression conveyed by the District Banks' August Red Book reports is that business activity continues at a high level"),
])
def test_a_page_that_prints_a_day_no_list_names_is_assigned_only_where_misdated_names_it(monkeypatch, code, edition, printed, opening):
    unit = mpls_unit({code: "2019-09-06 11:00:00"}, (edition,))
    rows = BeigeBookSource(FakeFetcher()).fetch(unit, {})
    row = next(row for row in rows if row["section"] == minneapolis.SLUGS[code])
    assert (row["id"], row["url"]) == (f"{edition}-{minneapolis.SLUGS[code]}", minneapolis.page_url(edition[:7], code))
    assert row["text"].startswith(opening)
    monkeypatch.setattr(minneapolis, "MISDATED", {})
    with pytest.raises(minneapolis.ParseError, match=f"dated {printed}"):
        BeigeBookSource(FakeFetcher()).fetch(unit, {})


def test_every_page_of_august_1973_is_misdated():
    assert {code for month, code in minneapolis.MISDATED if month == "1973-08"} == {minneapolis.SECTION_SLUG[section] for section in STANDARD}


def test_a_section_code_the_module_does_not_know_raises():
    codes = dict({minneapolis.SECTION_SLUG[s]: None for s in STANDARD}, xx="2019-09-06 11:00:00")
    with pytest.raises(minneapolis.ParseError, match="'xx'"):
        BeigeBookSource(FakeFetcher(all_pages("1975-03-19", "1975-03"), record=False)).fetch(mpls_unit(codes), {})


def era3_unit(pdf_url=None):
    edition = Edition("2020-01-15", ERA3, pdf_url, ("beige-book",))
    return Unit("2020-01-15", "board", (edition,), digest([ERA3, pdf_url]))


def test_a_board_edition_on_one_page_and_its_conditional_recheck():
    fetcher = FakeFetcher({ERA3: lambda url, headers: (304, b"") if headers.get("If-Modified-Since") == MODIFIED else (200, recorded(ERA3), {"Last-Modified": MODIFIED})})
    source = BeigeBookSource(fetcher)
    rows = source.fetch(era3_unit(), {})
    assert [row["section"] for row in rows] == list(STANDARD)
    assert all(row["url"] == ERA3 and row["source"] == "federalreserve.gov" and row["source_modified"] == MODIFIED for row in rows)
    assert fetcher.requests[-1] == (ERA3, {})
    stored = {row["id"]: dict(row, fetched_at="2026-09-25T00:00:00Z") for row in rows}
    again = source.fetch(era3_unit("https://www.federalreserve.gov/monetarypolicy/files/BeigeBook_20200115.pdf"), stored)
    assert fetcher.requests[-1] == (ERA3, {"If-Modified-Since": MODIFIED})
    assert [row["text"] for row in again] == [row["text"] for row in rows], "an unchanged page's rows come back as stored"
    assert {row["pdf_url"] for row in again} == {"https://www.federalreserve.gov/monetarypolicy/files/BeigeBook_20200115.pdf"}, "with the lists' current PDF"
    assert {row["fetched_at"] for row in again} == {"2026-09-25T00:00:00Z"}


def test_no_conditional_request_without_one_stamp_for_every_stored_row_of_the_page():
    fetcher = FakeFetcher({ERA3: (200, recorded(ERA3), {"Last-Modified": MODIFIED})})
    source = BeigeBookSource(fetcher)
    rows = {row["id"]: row for row in source.fetch(era3_unit(), {})}
    partial = dict(rows)
    del partial["2020-01-15-boston"]
    mixed = dict(rows, **{"2020-01-15-boston": dict(rows["2020-01-15-boston"], source_modified="Thu, 16 Jan 2020 10:00:00 GMT")})
    moved = {uid: dict(row, url=f"{MP}beigebook202001-old.htm") for uid, row in rows.items()}
    for stored in (partial, mixed, moved):
        source.fetch(era3_unit(), stored)
        assert fetcher.requests[-1] == (ERA3, {})


def test_a_missing_section_page_of_a_board_edition_raises():
    url = f"{MP}beigebook202601-summary.htm"
    edition = Edition("2026-01-14", url, None, ("beige-book",))
    with pytest.raises(PageMissing):
        BeigeBookSource(FakeFetcher()).fetch(Unit("2026-01-14", "board", (edition,), "v"), {})


def test_exists_asks_only_for_a_row_with_a_page():
    fetcher = FakeFetcher({ERA3: b"<html></html>"}, record=False)
    source = BeigeBookSource(fetcher)
    assert source.exists({"url": ERA3}) and not source.exists({"url": f"{MP}gone.htm"}) and not source.exists({"url": None})
    assert fetcher.urls() == [ERA3, f"{MP}gone.htm"]
