"""The Board of Governors' Beige Book pages on www.federalreserve.gov: the lists of editions, and the text of each section.

Two lists name the editions. The Beige Book pages (the current year on the landing page, earlier years from the archive, 1996 on) link each published edition's HTML and PDF. The FOMC historical materials (one page a year; the index listed 1936 to 2020 on September 25, 2026) link the Redbook, as the Beige Book was called until 1983, or Beige Book of each meeting that had one, from May 1970 on. Neither list is complete on its own: on September 25, 2026 the 2003 page of the first omitted September 3, 2003, which the Board hosts, and the 2006 page of the second had four Beige Book links with empty targets.

The Board hosts HTML from October 30, 1996, in four layouts that the HTML link's form identifies (era_of). An edition's pages are never derived from its date: the edition of September 2, 2026 is beigebook202608-summary.htm. A page that holds another edition's report under its own heading is named in ANOTHER_REPORT, and its section has no text.
"""

import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import urljoin, urlsplit, urlunsplit

from .sections import DISTRICTS, STANDARD, SUMMARY, name_key, names
from .text import detached, element_text, parse_html, wrap

BASE = "https://www.federalreserve.gov"
LANDING = f"{BASE}/monetarypolicy/publications/beige-book-default.htm"
ARCHIVE = f"{BASE}/monetarypolicy/beige-book-archive.htm"
HISTORICAL_INDEX = f"{BASE}/monetarypolicy/fomc_historical_year.htm"
SOURCE = "federalreserve.gov"
# The Redbook began in May 1970: the historical pages for 1968 and 1969 link none (checked September 25, 2026). verify --live reads every year the index lists, so an earlier one would show up there.
FIRST_YEAR = 1970
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")

YEAR_PAGE = re.compile(r"/monetarypolicy/beigebook(\d{4})\.htm$")
HISTORICAL_PAGE = re.compile(r"/monetarypolicy/fomchistorical(\d{4})\.htm$")
# The Beige Book or Redbook PDF of the historical pages and the Beige Book pages: fomc19710112redbook19710112.pdf, fomc19970205beige19970122.pdf, fullreport20120111.pdf, BeigeBook_20170118.pdf.
BOOK_PDF = re.compile(r"(?:redbook|beige|beigebook_?|fullreport)(\d{8})\.pdf$", re.I)
ERAS = (
    (1, re.compile(r"/fomc/beigebook/\d{4}/(\d{8})/default\.htm$")),
    (2, re.compile(r"/monetarypolicy/beigebook/beigebook\d{6,8}\.htm$")),
    (3, re.compile(r"/monetarypolicy/beigebook\d{6,8}\.htm$")),
    (4, re.compile(r"/monetarypolicy/beigebook\d{6,8}-summary\.htm$")),
)
NAV_LINKS = ("#top", "#pagetop")
# A footnote's link back to where the text cites it: <a class="return" href="#f1r">Return to text</a> (Kansas City, March 6, 2024), and after the summary's note of July 13, 2022.
BACKLINK = "Return to text"
# The summary's note on who prepared it: "Prepared at the Federal Reserve Bank of Atlanta and based on information collected before October 20, 1997".
COLLECTED = re.compile(rf"based\s+on\s+information\s+collected\s+(?:on\s+or\s+)?before\s+({'|'.join(MONTHS)})\s+(\d{{1,2}}),\s+(\d{{4}})")
COLLECTED_DAYS = 31
# Pages that hold another edition's report under their own edition's heading: {(edition, section): the edition whose report it is}. The section is left without text while the page's note says its information was collected for that other edition; a note that fits neither edition raises. Read September 25, 2026.
ANOTHER_REPORT = {
    # /fomc/beigebook/1997/19970122/default.htm, headed January 22, 1997, holds the summary of October 29, 1997 word for word, "based on information collected before October 20, 1997", and so does the Minneapolis archive's page of January 1997. The Board's PDF of January 22, 1997 (fomc19970205beige19970122.pdf) holds that edition's own summary, prepared at San Francisco "based on information collected before January 13, 1997".
    ("1997-01-22", "summary"): "1997-10-29",
}


class ParseError(ValueError):
    """A page does not have the structure this module reads; nothing is guessed."""


@dataclass(frozen=True)
class Listed:
    """One edition as one list shows it."""

    date: str
    html_url: str | None
    pdf_url: str | None


def canonical(url, base):
    """Absolute https URL on www.federalreserve.gov without a fragment; the lists mix relative links, absolute ones and a few http:// ones."""
    parts = urlsplit(urljoin(base, url.strip()))
    host = parts.hostname or ""
    if host in ("federalreserve.gov", "www.federalreserve.gov"):
        return urlunsplit(("https", "www.federalreserve.gov", parts.path, parts.query, ""))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def spoken(iso):
    """2026-09-02 as the pages print it: September 2, 2026."""
    day = date.fromisoformat(iso)
    return f"{MONTHS[day.month - 1]} {day.day}, {day.year}"


def iso_of(digits):
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"


def era_of(url):
    """1 to 4 from the form of an edition's HTML link; ParseError for any other link."""
    path = urlsplit(url).path
    for era, pattern in ERAS:
        if pattern.fullmatch(path):
            return era
    raise ParseError(f"not a Beige Book HTML link: {url}")


def _links(doc, base, pattern):
    found = {}
    for anchor in doc.xpath("//a[@href]"):
        url = canonical(anchor.get("href"), base)
        match = pattern.search(urlsplit(url).path)
        if match:
            found[int(match.group(1))] = url
    return found


def year_pages(raw, url=ARCHIVE):
    """The archive's links to its year pages, oldest first."""
    found = _links(parse_html(raw), url, YEAR_PAGE)
    if not found:
        raise ParseError(f"{url} links no year page")
    return [found[year] for year in sorted(found)]


def historical_years(raw, url=HISTORICAL_INDEX, first_year=FIRST_YEAR):
    """The historical index's links to its year pages from first_year (None: all), oldest first."""
    found = _links(parse_html(raw), url, HISTORICAL_PAGE)
    if not found:
        raise ParseError(f"{url} links no year page")
    return [found[year] for year in sorted(found) if first_year is None or year >= first_year]


def _text(element):
    return re.sub(r"\s+", " ", element.text_content()).strip()


def year_table(raw, url):
    """(published, scheduled) from a Beige Book year page or the landing page: Listed per edition with a link, and the dates of rows without one (editions not yet released). Rows are "January 16 | HTML | PDF" in two cells before 2024 and "January 14: HTML | PDF" in one from 2024."""
    doc = parse_html(raw)
    heads = doc.xpath("//th[@id='year']")
    if len(heads) != 1 or not re.fullmatch(r"\d{4}", _text(heads[0])):
        raise ParseError(f"{url}: expected one th#year holding a year, found {len(heads)}")
    year = int(_text(heads[0]))
    published, scheduled = [], []
    for row in heads[0].xpath("ancestor::table[1]//tr[td]"):
        label = _text(row.xpath("./td")[0]).split(":")[0].strip()
        match = re.fullmatch(r"([A-Z][a-z]+) (\d{1,2})", label)
        if not match or match.group(1) not in MONTHS:
            raise ParseError(f"{url}: row label {label!r} is not a month and day")
        iso = date(year, MONTHS.index(match.group(1)) + 1, int(match.group(2))).isoformat()
        links = {}
        for anchor in row.xpath(".//a[@href]"):
            kind = _text(anchor).upper()[:4]
            if kind in ("HTML", "PDF") and kind not in links and anchor.get("href").strip():
                links[kind] = canonical(anchor.get("href"), url)
        if not row.xpath(".//a[@href]"):
            scheduled.append(iso)
        elif links:
            published.append(Listed(iso, links.get("HTML"), links.get("PDF")))
        else:
            raise ParseError(f"{url}: the row for {iso} has links, none of them HTML or PDF")
    return published, scheduled


def historical_year(raw, url):
    """Listed per Beige Book or Redbook on one FOMC historical materials page. A meeting's links sit in one paragraph ("Beige Book: PDF | HTML"); the date comes from the PDF's name or the 1996 to 2010 HTML address, since the later HTML addresses give only the month."""
    doc = parse_html(raw)
    groups = {}
    for anchor in doc.xpath("//a[@href]"):
        href = anchor.get("href").strip()
        if not href:
            continue
        link = canonical(href, url)
        path = urlsplit(link).path
        pdf = BOOK_PDF.search(path)
        try:
            era = era_of(link)
        except ParseError:
            era = None
        if not pdf and era is None:
            continue
        holder = anchor.getparent()
        while holder is not None and holder.tag not in ("p", "li", "td", "div"):
            holder = holder.getparent()
        group = groups.setdefault(id(holder), {"dates": set(), "html": None, "pdf": None})
        if pdf:
            group["dates"].add(iso_of(pdf.group(1)))
            group["pdf"] = group["pdf"] or link
        else:
            group["html"] = group["html"] or link
            if era == 1:
                group["dates"].add(iso_of(ERAS[0][1].search(path).group(1)))
    out = {}
    for group in groups.values():
        if len(group["dates"]) != 1:
            if group["dates"]:
                raise ParseError(f"{url}: one meeting's Beige Book links name dates {sorted(group['dates'])}")
            continue
        (iso,) = group["dates"]
        known = out.get(iso)
        out[iso] = Listed(iso, (known and known.html_url) or group["html"], (known and known.pdf_url) or group["pdf"])
    return [out[iso] for iso in sorted(out)]


def pages_of(era, html_url):
    """[(sections, page URL)] for an edition: one page per section in eras 1 and 4, one page for all of them in eras 2 and 3."""
    if era == 1:
        folder = html_url.rsplit("/", 1)[0]
        return [((SUMMARY,), f"{folder}/default.htm")] + [((district,), f"{folder}/{n}.htm") for n, district in enumerate(DISTRICTS, 1)]
    if era == 4:
        stem = html_url.removesuffix("-summary.htm")
        return [((section,), f"{stem}-{section}.htm") for section in STANDARD]
    return [(STANDARD, html_url)]


def _drop_nav(container):
    """Removes "Return to top" links: the table holding one (era 1), else its paragraph (era 2). A footnote's "Return to text" link goes alone, so the footnote stays."""
    for anchor in container.xpath(".//a[@href]"):
        href = anchor.get("href").strip()
        if href.startswith("#") and _text(anchor) == BACKLINK:
            anchor.drop_tree()
            continue
        if href not in NAV_LINKS:
            continue
        blocks = anchor.xpath("ancestor::table[1]") or anchor.xpath("ancestor::p[1]")
        # A second link in a block already dropped finds that block detached.
        if blocks and blocks[0] is not container and blocks[0].getparent() is not None:
            blocks[0].drop_tree()


def _require(text, what):
    if not text:
        raise ParseError(f"{what}: no text")
    return text


def _page_date(doc, iso, what):
    """The page prints the edition's date, with or without a leading zero on the day (March 01, 2017)."""
    day = date.fromisoformat(iso)
    if not re.search(rf"\b{MONTHS[day.month - 1]} 0?{day.day}, {day.year}\b", _text(doc)):
        raise ParseError(f"{what}: the page does not print {spoken(iso)}")


def _era1(doc, section, iso):
    title = doc.findtext(".//title") or ""
    if not names(section, title):
        raise ParseError(f"era 1 {section}: the title {title!r} names another section")
    _page_date(doc, iso, f"era 1 {section}")
    anchors = doc.xpath("//a[@name='content']")
    cells = anchors[0].xpath("ancestor::td[1]") if len(anchors) == 1 else []
    if not cells:
        raise ParseError(f"era 1 {section}: no content cell")
    cell = detached(cells[0])
    _drop_nav(cell)
    return _require(element_text(cell), f"era 1 {section}")


def _era2(doc, sections, iso):
    _page_date(doc, iso, "era 2")
    out = {}
    for section in sections:
        found = doc.xpath(f"//div[@id='div_{section.replace('-', '_')}']")
        if len(found) != 1:
            raise ParseError(f"era 2 {section}: {len(found)} div_{section.replace('-', '_')}")
        div = detached(found[0])
        heads = div.xpath("./h2")
        if not heads or not names(section, _text(heads[0])):
            raise ParseError(f"era 2 {section}: the heading does not name the section")
        heads[0].drop_tree()
        _drop_nav(div)
        out[section] = _require(element_text(div), f"era 2 {section}")
    return out


def _era3(doc, sections, iso):
    _page_date(doc, iso, "era 3")
    articles = doc.xpath("//div[@id='article']")
    if len(articles) != 1:
        raise ParseError(f"era 3: {len(articles)} div#article")
    children = list(articles[0])
    starts = {}
    for index, child in enumerate(children):
        if child.tag != "p":
            continue
        # Anchors are named "newyork" in some editions and "new_york" in others (January 18, 2017).
        names_here = {name_key(value) for anchor in child.xpath(".//a[@id or @name]") for value in (anchor.get("id"), anchor.get("name")) if value}
        for district in DISTRICTS:
            if name_key(district) in names_here:
                starts[district] = index
    if list(starts) != list(DISTRICTS) or sorted(starts.values()) != list(starts.values()):
        raise ParseError(f"era 3: district anchors {list(starts)}, expected the twelve in order")
    # A rule after the last district opens the page's notes, which belong to the section that links them, not to the last district: on July 13, 2022, a correction to the summary's "prepared at" note.
    rules = [index for index, child in enumerate(children) if index > starts[DISTRICTS[-1]] and child.tag == "hr"]
    last = rules[0] if rules else len(children)
    bounds = [(SUMMARY, 0, starts[DISTRICTS[0]])] + [(district, starts[district], starts[following] if following else last) for district, following in zip(DISTRICTS, DISTRICTS[1:] + (None,))]
    notes = {}
    for child in children[last + 1:]:
        if not isinstance(child.tag, str) or not _text(child):
            continue
        links = {f"#{value}" for anchor in child.iter("a") for value in (anchor.get("name"), anchor.get("id")) if value}
        owners = [section for section, begin, stop in bounds if any((anchor.get("href") or "").strip() in links for element in children[begin:stop] if isinstance(element.tag, str) for anchor in element.iter("a"))]
        if len(owners) != 1:
            raise ParseError(f"era 3: a note after the last district is linked from {owners or 'no section'}: {_text(child)[:60]!r}")
        notes.setdefault(owners[0], []).append(child)
    out = {}
    for section, begin, end in bounds:
        # Copies with their tails, so text between elements stays; drop_tree() below keeps a dropped element's tail too.
        run = wrap(children[begin:end] + notes.get(section, []))
        if section == SUMMARY:
            # Before the summary: an empty float; in 2017 the heading "National Summary", an h4 or (October 18) an h3; the "prepared at" panel (kept, as every layout keeps it); and the table of contents, a list of links within the page.
            for child in list(run):
                links = child.xpath(".//a")
                if "pull-right" in (child.get("class") or "").split() or (child.tag == "ul" and links and all((a.get("href") or "").startswith("#") for a in links)):
                    child.drop_tree()
            first = next((child for child in run if isinstance(child.tag, str)), None)
            if first is not None and first.tag in ("h3", "h4") and names(SUMMARY, _text(first)):
                first.drop_tree()
        else:
            anchor, head = ([child for child in run if isinstance(child.tag, str)] + [None, None])[:2]
            if head is None or head.tag != "h4" or not names(section, _text(head)):
                raise ParseError(f"era 3 {section}: the section does not start with its heading")
            anchor.drop_tree()
            head.drop_tree()
        _drop_nav(run)
        if section in sections:
            out[section] = _require(element_text(run), f"era 3 {section}")
    return out


def _era4(doc, section):
    articles = doc.xpath("//div[@id='article']")
    if len(articles) != 1:
        raise ParseError(f"era 4 {section}: {len(articles)} div#article")
    article = detached(articles[0])
    for float_ in article.xpath("./div[contains(concat(' ', @class, ' '), ' pull-right ')]"):
        float_.drop_tree()
    heads = article.xpath("./h3")
    if not heads or not names(section, _text(heads[0])):
        raise ParseError(f"era 4 {section}: the heading does not name the section")
    heads[0].drop_tree()
    _drop_nav(article)
    return _require(element_text(article), f"era 4 {section}")


def _collected(text):
    """The day a report's note says its information was collected by, or None."""
    match = COLLECTED.search(text)
    return date(int(match.group(3)), MONTHS.index(match.group(1)) + 1, int(match.group(2))) if match else None


def _collected_for(day, iso):
    return day is not None and 0 <= (date.fromisoformat(iso) - day).days <= COLLECTED_DAYS


def _withhold(texts, iso):
    """Leaves out the text of each section ANOTHER_REPORT names while the page still holds that other edition's report."""
    for section, text in texts.items():
        other = ANOTHER_REPORT.get((iso, section))
        if other is None:
            continue
        day = _collected(text)
        if _collected_for(day, other):
            texts[section] = None
        elif not _collected_for(day, iso):
            raise ParseError(f"{section} of {spoken(iso)}: ANOTHER_REPORT says the page holds the report of {spoken(other)}, but its note names {spoken(day.isoformat()) if day else 'no day'}")
    return texts


def parse_page(era, raw, sections, iso):
    """{section: text} for the sections a page of an edition dated iso holds. The section's own heading is left out (the section column names it); everything else in the section's container is kept, subheadings, the "prepared at" note and the closing "for more information" line included. Raises ParseError unless every section is found with text. A section ANOTHER_REPORT names comes back as None while its page holds the other edition's report."""
    doc = parse_html(raw)
    if era == 1:
        texts = {sections[0]: _era1(doc, sections[0], iso)}
    elif era == 2:
        texts = _era2(doc, sections, iso)
    elif era == 3:
        texts = _era3(doc, sections, iso)
    elif era == 4:
        texts = {sections[0]: _era4(doc, sections[0])}
    else:
        raise ParseError(f"no era {era}")
    return _withhold(texts, iso)
