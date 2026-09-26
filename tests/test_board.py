"""The Board's lists and pages, from pages recorded September 25, 2026."""

import pytest

from conftest import recorded
from fed_products import board
from fed_products.board import ParseError, era_of, historical_year, historical_years, pages_of, parse_page, year_pages, year_table
from fed_products.sections import DISTRICTS, STANDARD

MP = "https://www.federalreserve.gov/monetarypolicy/"
ERA1 = "https://www.federalreserve.gov/fomc/beigebook/1996/19961030/"
ERA1_LAST = "https://www.federalreserve.gov/fomc/beigebook/2010/20101201/"


def year(n):
    url = f"{MP}beigebook{n}.htm"
    return year_table(recorded(url), url)


def historical(n):
    url = f"{MP}fomchistorical{n}.htm"
    return historical_year(recorded(url), url)


def test_the_landing_page_lists_this_years_editions_and_the_scheduled_ones():
    published, scheduled = year_table(recorded(board.LANDING), board.LANDING)
    assert [item.date for item in published] == ["2026-01-14", "2026-03-04", "2026-04-15", "2026-06-03", "2026-07-15", "2026-09-02"]
    assert scheduled == ["2026-10-14", "2026-11-25"]
    assert published[-1].html_url == f"{MP}beigebook202608-summary.htm", "the September 2 edition's address names August"
    assert published[-1].pdf_url.endswith("/BeigeBook_20260902.pdf")


@pytest.mark.parametrize("n, count, first_html", [
    (1996, 2, "https://www.federalreserve.gov/fomc/beigebook/1996/19961030/default.htm"),
    (2011, 8, f"{MP}beigebook/beigebook201101.htm"),
    (2023, 8, f"{MP}beigebook202301.htm"),
    (2024, 8, f"{MP}beigebook202401-summary.htm"),
])
def test_year_pages_in_both_row_layouts(n, count, first_html):
    published, scheduled = year(n)
    assert len(published) == count and scheduled == [] and published[0].html_url == first_html
    assert {era_of(item.html_url) for item in published} == {era_of(first_html)}


def test_the_2003_page_omits_september_3_which_the_historical_page_links():
    assert "2003-09-03" not in [item.date for item in year(2003)[0]]
    listed = {item.date: item for item in historical(2003)}
    assert listed["2003-09-03"].html_url == "https://www.federalreserve.gov/fomc/beigebook/2003/20030903/default.htm"


def test_links_with_empty_targets_are_skipped():
    # The 2006 historical page's Beige Book links for four meetings had empty targets.
    assert [item.date for item in historical(2006)] == ["2006-01-18", "2006-03-15", "2006-04-26", "2006-06-14"]
    assert len(year(2006)[0]) == 8


def test_historical_pages_date_redbooks_by_their_pdf_and_hold_both_editions_of_a_month():
    days = [item.date for item in historical(1971)]
    assert "1971-06-02" in days and "1971-06-23" in days and len(days) == 13
    assert all(item.html_url is None and item.pdf_url for item in historical(1971))
    assert [item.date for item in historical(1980)][:2] == ["1980-01-02", "1980-01-29"]
    assert historical(1970)[0].date == "1970-05-20"
    assert historical(1968) == [] and historical(1969) == []
    both = {item.date: item for item in historical(1996)}
    assert both["1996-09-11"].html_url is None and both["1996-10-30"].html_url == f"{ERA1}default.htm"


def test_the_indexes_link_every_year():
    years = year_pages(recorded(board.ARCHIVE))
    assert (len(years), years[0], years[-1]) == (30, f"{MP}beigebook1996.htm", f"{MP}beigebook2025.htm")
    index = recorded(board.HISTORICAL_INDEX)
    assert (len(historical_years(index)), historical_years(index)[0]) == (51, f"{MP}fomchistorical1970.htm")
    everything = historical_years(index, first_year=None)
    assert (len(everything), everything[0], everything[-1]) == (85, f"{MP}fomchistorical1936.htm", f"{MP}fomchistorical2020.htm")


@pytest.mark.parametrize("url, era", [
    (f"{ERA1}default.htm", 1), (f"{MP}beigebook/beigebook201101.htm", 2), (f"{MP}beigebook202001.htm", 3),
    (f"{MP}beigebook20230531.htm", 3), (f"{MP}beigebook202608-summary.htm", 4),
])
def test_era_of(url, era):
    assert era_of(url) == era


@pytest.mark.parametrize("url", [f"{MP}beigebook2003.htm", f"{MP}beigebook202608-boston.htm", "https://www.federalreserve.gov/fomc/beigebook/1996/19961030/1.htm"])
def test_other_links_are_no_edition(url):
    with pytest.raises(ParseError):
        era_of(url)


def test_pages_of_each_era():
    assert pages_of(1, f"{ERA1}default.htm") == [(("summary",), f"{ERA1}default.htm")] + [((district,), f"{ERA1}{n}.htm") for n, district in enumerate(DISTRICTS, 1)]
    assert pages_of(4, f"{MP}beigebook202608-summary.htm")[12] == (("san-francisco",), f"{MP}beigebook202608-san-francisco.htm")
    assert pages_of(3, f"{MP}beigebook202001.htm") == [(STANDARD, f"{MP}beigebook202001.htm")]


@pytest.mark.parametrize("era, url, section, iso, opening, ending", [
    (1, f"{ERA1}default.htm", "summary", "1996-10-30", "Prepared at the Federal Reserve Bank of Minneapolis based on information collected before ", "construction materials rose."),
    (1, f"{ERA1}1.htm", "boston", "1996-10-30", "Economic expansion remains solid in New England.", "the labor force should begin to expand."),
    (1, f"{ERA1}12.htm", "san-francisco", "1996-10-30", "Most contacts from the 12th District reported strong economic growth", "despite sharp competition for loans."),
    (1, f"{ERA1_LAST}default.htm", "summary", "2010-12-01", "Prepared by the Federal Reserve Bank of Cleveland based on information collected on or before November 19, 2010.", "are putting downward pressure on natural gas prices."),
    (1, f"{ERA1_LAST}1.htm", "boston", "2010-12-01", "Business activity in the First District continues to expand gradually.", "until consumer confidence improves."),
    (1, f"{ERA1_LAST}12.htm", "san-francisco", "2010-12-01", "Economic activity in the Twelfth District continued to edge up", "ongoing struggles with credit quality for some banks."),
    (4, f"{MP}beigebook202601-summary.htm", "summary", "2026-01-14", "Overall Economic Activity\n\nOverall economic activity increased at a slight to modest pace", "not a commentary on the views of Federal Reserve officials."),
    (4, f"{MP}beigebook202601-boston.htm", "boston", "2026-01-14", "Summary of Economic Activity\n\nEconomic activity edged up further", "visit: https://www.bostonfed.org/in-the-region.aspx."),
    (4, f"{MP}beigebook202601-san-francisco.htm", "san-francisco", "2026-01-14", "Summary of Economic Activity\n\nEconomic activity in the Twelfth District expanded modestly", "san-francisco-fed-twelfth-district-beige-book/."),
])
def test_one_page_per_section(era, url, section, iso, opening, ending):
    text = parse_page(era, recorded(url), (section,), iso)[section]
    assert text.startswith(opening) and text.endswith(ending)
    assert "Return to top" not in text and "Last Update" not in text


@pytest.mark.parametrize("era, url, iso, openings", [
    (2, f"{MP}beigebook/beigebook201101.htm", "2011-01-12", {"summary": "Prepared at the Federal Reserve Bank of Boston", "boston": "Economic conditions continue to improve in the First District", "san-francisco": "The Twelfth District economy firmed further"}),
    (3, f"{MP}beigebook202001.htm", "2020-01-15", {"summary": "This report was prepared at the Federal Reserve Bank of New York", "boston": "Summary of Economic Activity\nEconomic activity continued to expand in the First District", "san-francisco": "Summary of Economic Activity\nEconomic activity in the Twelfth District"}),
    # Anchors named new_york, st_louis, kansas_city and san_francisco, and headed "National Summary" inside the article.
    (3, f"{MP}beigebook201701.htm", "2017-01-18", {"summary": "This report was prepared at the Federal Reserve Bank of Boston", "boston": "Summary of Economic Activity\nBusiness activity continued to expand in the First District", "new-york": "Summary of Economic Activity\nEconomic activity in the Second District has held steady", "san-francisco": "Summary of Economic Activity\nEconomic activity in the Twelfth District continued"}),
    # Dated "March 01, 2017", and headed "National Summary" inside the article.
    (3, f"{MP}beigebook201703.htm", "2017-03-01", {"summary": "This report was prepared at the Federal Reserve Bank of New York", "boston": "Summary of Economic Activity\nFirst District businesses contacted in early February", "new-york": "Summary of Economic Activity\nEconomic activity in the Second District has picked up", "san-francisco": "Summary of Economic Activity\nEconomic activity in the Twelfth District continued"}),
    # Headed "National Summary" in an h3, not an h4.
    (3, f"{MP}beigebook201710.htm", "2017-10-18", {"summary": "This report was prepared at the Federal Reserve Bank of Minneapolis", "boston": "Summary of Economic Activity\nMost business contacts in the First District", "new-york": "Summary of Economic Activity\nEconomic activity in the Second District", "san-francisco": "Summary of Economic Activity\nEconomic activity in the Twelfth District continued to expand at a moderate pace"}),
])
def test_one_page_per_edition(era, url, iso, openings):
    texts = parse_page(era, recorded(url), STANDARD, iso)
    assert list(texts) == list(STANDARD) and all(texts.values())
    for section, opening in openings.items():
        assert texts[section].startswith(opening)
    assert not any(texts[district].startswith(("Federal Reserve Bank", "First District", "Twelfth District")) for district in DISTRICTS), "the district heading is left out"
    assert "Return to top" not in "".join(texts.values())
    # Each district's text ends where the next begins: no section holds another's opening.
    assert openings["boston"] not in texts["summary"] and openings["san-francisco"] not in texts["dallas"]


def test_a_page_for_another_date_or_section_raises():
    with pytest.raises(ParseError, match="does not print"):
        parse_page(1, recorded(f"{ERA1}1.htm"), ("boston",), "1996-12-04")
    with pytest.raises(ParseError, match="names another section"):
        parse_page(1, recorded(f"{ERA1}1.htm"), ("new-york",), "1996-10-30")
    with pytest.raises(ParseError, match="heading does not name"):
        parse_page(4, recorded(f"{MP}beigebook202601-boston.htm"), ("new-york",), "2026-01-14")
    with pytest.raises(ParseError, match="does not print"):
        parse_page(3, recorded(f"{MP}beigebook202001.htm"), STANDARD, "2020-03-04")


def test_a_page_without_its_layout_raises():
    with pytest.raises(ParseError):
        parse_page(3, b"<html><body><div id='article'><p>moved</p></div></body></html>", STANDARD, "2020-01-15")
    with pytest.raises(ParseError):
        parse_page(2, recorded(f"{MP}beigebook202001.htm"), STANDARD, "2020-01-15")
    with pytest.raises(ParseError):
        year_table(b"<html><body><table><tr><td>January 14</td></tr></table></body></html>", board.LANDING)
