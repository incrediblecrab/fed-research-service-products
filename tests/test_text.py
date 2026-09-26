"""Text layout: what element_text and html_text make of the markup the Board and Minneapolis pages use."""

import pytest
from lxml import html as lxml_html

from fed_products.minneapolis import page_url, parse_page
from fed_products.text import decode, detached, element_text, html_text, parse_html, wrap, xml_safe
from conftest import recorded


@pytest.mark.parametrize("markup, text", [
    ("<p>Consumer   spending\n  rose\tslightly.</p>", "Consumer spending rose slightly."),
    ("<p>One.</p><p>Two.</p>", "One.\n\nTwo."),
    ("<div><h4>Manufacturing</h4>Output rose.</div>", "Manufacturing\n\nOutput rose."),
    ("<p>Line one<br>line two<br/>line three</p>", "Line one\nline two\nline three"),
    ("<p>Line one <br> <br> line two</p>", "Line one\n\nline two"),
    ("<p>Line one<br>&nbsp;<br/><br>line two</p>", "Line one\n\nline two"),
    ("<p>Ends with a break<br></p><p>Next</p>", "Ends with a break\n\nNext"),
    ("<table><tr><td>a<br></td><td>b</td></tr><tr><td>c</td><td>d<br></td></tr></table>", "a\tb\nc\td"),
    ("<table><tr><td>a<br><br></td><td>b</td></tr></table>", "a\tb"),
    ("<strong>Federal Reserve Bank</strong><br/>Summary", "Federal Reserve Bank\nSummary"),
    ("<table><tr><th>District</th><th>Change</th></tr><tr><td>Boston</td><td>up</td></tr></table>", "District\tChange\nBoston\tup"),
    ("<ul><li>Wages rose.</li><li>Prices <em>held</em>.</li></ul>", "- Wages rose.\n\n- Prices held."),
    ("<p>Kept</p><script>var x = 1;</script><style>p {}</style><noscript>no</noscript><p>too</p>", "Kept\n\ntoo"),
    ("<p>eco\u00adnomic\u00a0activity</p>", "economic activity"),
    ("<p>An <a href='#'>inline</a> link, <b>bold</b> and <i>italic</i>.</p>", "An inline link, bold and italic."),
    ("<p>\n\n\n</p><p>Only this.</p><p> </p>", "Only this."),
])
def test_layout(markup, text):
    assert html_text(markup) == text


def test_a_blank_line_in_typed_text_is_a_paragraph_break_and_a_single_newline_is_a_space():
    typed = "<pre>Retail sales were\nsteady in most areas.\n\n   Manufacturing activity\nwas mixed.</pre>"
    assert html_text(typed) == "Retail sales were steady in most areas.\n\nManufacturing activity was mixed."
    assert html_text("<div>First paragraph\n   \n  second paragraph</div>") == "First paragraph\n\nsecond paragraph"


def test_no_text_is_none():
    assert html_text(None) is None and html_text("  \n ") is None and html_text("<p> </p><br/>") is None
    assert element_text(lxml_html.fragment_fromstring("<div><img src='x.png'/></div>")) is None


def test_element_text_leaves_out_the_tail_and_keeps_it_in_the_document():
    doc = lxml_html.fragment_fromstring("<div><p id='a'>Inside.</p>After the paragraph.</div>")
    p = doc.get_element_by_id("a")
    assert element_text(p) == "Inside." and p.tail == "After the paragraph."
    assert element_text(doc) == "Inside.\n\nAfter the paragraph."


def test_characters_xml_does_not_allow_become_spaces():
    assert xml_safe("a\x00b\x08c\x0bd\x1fe\ufffef") == "a b c d e f" and xml_safe("tab\tline\nreturn\r") == "tab\tline\nreturn\r"
    doc = parse_html(b"<html><body><p>Loans\x0cgrew</p></body></html>")
    assert element_text(doc.body) == "Loans grew"


def test_pages_that_are_not_utf8_are_read_as_windows_1252():
    assert decode("caf\u00e9 \u201cquoted\u201d".encode()) == "caf\u00e9 \u201cquoted\u201d"
    assert decode(b"\x93quoted\x94 \x96 caf\xe9") == "\u201cquoted\u201d \u2013 caf\u00e9"


def test_detached_and_wrap_copy_instead_of_moving():
    doc = lxml_html.fragment_fromstring("<div><h3>Title</h3>between<p>Body.</p>after</div>")
    h3, p = doc[0], doc[1]
    copy = detached(h3)
    copy.text = "changed"
    assert copy.tail is None and h3.text == "Title" and h3.tail == "between"
    run = wrap([h3, p])
    assert element_text(run) == "Title\n\nbetween\n\nBody.\n\nafter" and len(doc) == 2 and doc[0] is h3


def test_a_run_of_line_breaks_is_a_paragraph_break_as_the_browser_shows_it():
    """The Minneapolis page of September 11, 1996 separates its paragraphs with two <br> and no <p>."""
    report = parse_page(recorded(page_url("1996-09", "su")), "summary")
    assert report.date == "1996-09-11"
    assert "is reported to be generally good and expanding moderately.\n\nWages and Prices\nReports " in report.text
