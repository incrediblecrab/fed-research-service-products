"""The Minneapolis Fed's archive: the sitemap and the report pages, from pages recorded September 25, 2026."""

import json

import pytest

from conftest import recorded
from fed_products import minneapolis
from fed_products.minneapolis import ParseError, page_url, parse_page, parse_sitemap
from fed_products.sections import STANDARD


def page(month, code):
    return recorded(page_url(month, code))


def reported(report, district="Boston", title="Boston: June 1971", template="BeigeBookPage"):
    """A page in the site's shape holding the given report HTML."""
    route = {"templateName": template, "fields": {"Report": {"value": report}, "Title": {"value": title}, "District": {"value": district}}}
    data = {"props": {"pageProps": {"layoutData": {"sitecore": {"route": route}}}}}
    return f'<html><body><script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script></body></html>'.encode()


def test_the_sitemap_lists_every_month_with_its_section_codes():
    months = parse_sitemap(recorded(minneapolis.SITEMAP))
    assert (len(months), min(months), max(months)) == (487, "1970-05", "2026-09")
    early = {month: codes for month, codes in months.items() if month < "1996-10"}
    assert len(early) == 248
    standard = {minneapolis.SECTION_SLUG[section] for section in STANDARD}
    assert all(set(codes) == standard for month, codes in early.items() if month != "1983-05")
    assert set(months["1983-05"]) == standard | {"sr"}
    assert months["1983-05"]["ph"] == "2025-03-13 02:32:28", "lastmod is kept as written"
    assert "1996-07" not in months, "no edition in July 1996, no pages"


def test_a_page_the_sitemap_lists_twice_counts_once_with_its_latest_lastmod():
    raw = recorded(minneapolis.SITEMAP).decode()
    entry = "<url><loc>https://minneapolisfed.org/beige-book-reports/1971/1971-06-su</loc><lastmod>2019-09-06 11:01:16</lastmod></url>"
    assert raw.count(entry) == 2
    twice = raw.replace(entry, entry.replace("2019-09-06 11:01:16", "2020-01-01 00:00:00"), 1)
    assert parse_sitemap(twice.encode())["1971-06"]["su"] == "2020-01-01 00:00:00"


def test_a_sitemap_without_beige_book_pages_raises():
    with pytest.raises(ParseError):
        parse_sitemap(b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://minneapolisfed.org/</loc></url></urlset>')


def test_a_page_belongs_to_the_edition_its_report_is_dated_with_not_to_its_month():
    dates = {code: parse_page(page("1971-06", code), minneapolis.SLUGS[code]).date for code in minneapolis.SLUGS if code != "sr"}
    assert sorted(code for code, day in dates.items() if day == "1971-06-02") == ["bo", "cl", "da", "mi", "ny", "ph", "sf", "sl", "su"]
    assert sorted(code for code, day in dates.items() if day == "1971-06-23") == ["at", "ch", "kc", "ri"]
    dates = {code: parse_page(page("1980-01", code), minneapolis.SLUGS[code]).date for code in minneapolis.SLUGS if code != "sr"}
    assert sorted(code for code, day in dates.items() if day == "1980-01-02") == ["at", "da", "ph", "sl", "su"]
    assert len([day for day in dates.values() if day == "1980-01-29"]) == 8


def test_the_text_leaves_out_the_dateline_and_keeps_the_rest():
    report = parse_page(page("1983-05", "su"), "summary")
    assert report.date == "1983-05-18" and report.title == "National Summary: May 1983"
    assert report.text.startswith("Preface\n\nThis edition of Redbook contains a special")
    assert "May 18, 1983" not in report.text[:40]
    special = parse_page(page("1983-05", "sr"), "special-report")
    assert special.date == "1983-05-18" and special.text.startswith("National Summary This summary is organized around")


@pytest.mark.parametrize("report, day", [
    # Forms the first live run met: a <div> around the report (November 10, 1971), a bare <strong> before a <div> (June 23, 1971), a space before the comma (January 12, 1971).
    ('<div>\r\n  <p> <strong>November 10, 1971</strong>      </p>\r\n <p>Business activity rose.</p></div>', "1971-11-10"),
    ('<strong>June 23, 1971</strong> \r\n<div>\r\n\r\n\r\n<p>Business activity rose.</p></div>', "1971-06-23"),
    ('<p><strong>January 12 , 1971</strong></p>\r\n\r\n  <p>Business activity rose.</p>', "1971-01-12"),
])
def test_datelines_in_the_forms_the_archive_uses_are_read(report, day):
    parsed = parse_page(reported(report), "boston")
    assert (parsed.date, parsed.text) == (day, "Business activity rose.")


@pytest.mark.parametrize("report, message", [
    ("<p>Business activity rose in May 20, 1970.</p>", "does not open with a date"),
    ("<p><strong>May 20, 1970</strong> Business activity rose.</p>", "does not open with a date"),
    ("<p><strong>May 20, 1970</strong></p>", "no text after its date"),
    ("<p><strong>Mya 20, 1970</strong></p><p>Business activity rose.</p>", "not a date"),
])
def test_a_report_without_a_dateline_of_its_own_or_without_text_raises(report, message):
    with pytest.raises(ParseError, match=message):
        parse_page(reported(report), "boston")


def test_a_page_that_names_another_district_raises():
    with pytest.raises(ParseError, match="names"):
        parse_page(reported("<p><strong>June 2, 1971</strong></p><p>Text.</p>", district="Chicago", title="Chicago: June 1971"), "boston")


def test_the_missing_page_template_raises():
    # 1996-07-su answered 404 with the site's landing page; there was no July 1996 edition.
    with pytest.raises(ParseError, match="LandingPage"):
        parse_page(page("1996-07", "su"), "summary")
    with pytest.raises(ParseError, match="__NEXT_DATA__"):
        parse_page(b"<html><body>maintenance</body></html>", "summary")
