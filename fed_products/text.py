"""Plain text from HTML, the way a browser lays it out: whitespace inside a paragraph collapses to one space, block elements start new paragraphs.

Paragraphs are separated by a blank line and each paragraph is one line. A <br> is a single line break; two or more in a row, which a browser shows as an empty line, are a paragraph break (the Minneapolis page of September 11, 1996 separates its paragraphs that way). A blank line inside a text node is kept as a paragraph break: the older reports were typed text wrapped at about 70 columns, and where their HTML never marked the paragraphs up (the special report of May 18, 1983, for one), the blank lines are the only record of them. Table cells are separated by tabs and rows by line breaks; list items start with "- ".
"""

import copy
import re

from lxml import html as lxml_html

SOFT_HYPHEN = "\u00ad"
BLOCKS = {"address", "article", "aside", "blockquote", "center", "dd", "div", "dl", "dt", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "ul", "caption"}
SKIP = {"script", "style", "noscript", "template"}
# Private-use markers for breaks while the text is assembled; xml_safe() has already removed any control character a page could carry.
PARA, LINE, TAB, BR = "\x00", "\x01", "\x02", "\x03"
_BREAKS = re.compile(r"[ \x00-\x03]*[\x00-\x03][ \x00-\x03]*")
_BLANK_LINE = re.compile(r"[ \t\r\f\v\u00a0]*\n[ \t\r\f\v\u00a0]*\n\s*")
_SPACE = re.compile(r"[ \t\n\r\f\v\u00a0]+")
# What XML 1.0 does not allow: C0 controls other than tab, line feed and carriage return; surrogates; U+FFFE and U+FFFF. lxml refuses to set a text node that holds one.
_NOT_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]")


def decode(raw):
    """UTF-8, else Windows-1252: the 2011 to 2016 Board pages declare ISO-8859-1 and are not valid UTF-8."""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def xml_safe(text):
    """text with each character XML does not allow replaced by a space, so words either side stay apart."""
    return _NOT_XML.sub(" ", text)


def parse_html(source):
    """An lxml document from bytes or str."""
    text = xml_safe(source if isinstance(source, str) else decode(source))
    return lxml_html.document_fromstring(text)


def _tokens(node, out):
    tag = node.tag.lower() if isinstance(node.tag, str) else None
    if tag in SKIP:
        return
    if tag == "br":
        out.append(BR)
        return
    block = tag in BLOCKS
    if block:
        out.append(PARA)
    if tag == "li":
        out.append("- ")
    if tag is not None and node.text:
        out.append(node.text)
    for child in node:
        _tokens(child, out)
        if child.tail:
            out.append(child.tail)
    if tag in ("td", "th"):
        out.append(TAB)
    elif tag == "tr":
        out.append(LINE)
    if block:
        out.append(PARA)


def _resolve(match):
    """The one break a run of markers stands for: the strongest a block, a table row or a table cell makes; else a paragraph for two or more <br>, and a line for one."""
    run = match.group(0)
    for marker, text in ((PARA, "\n\n"), (LINE, "\n"), (TAB, "\t")):
        if marker in run:
            return text
    return "\n\n" if run.count(BR) > 1 else "\n"


def element_text(element):
    """The text of an element (its tail excluded), laid out as the module docstring says; None when there is none."""
    out = []
    tail, element.tail = element.tail, None
    try:
        _tokens(element, out)
    finally:
        element.tail = tail
    pieces = []
    for token in out:
        if token in (PARA, LINE, TAB, BR):
            pieces.append(token)
        else:
            pieces.append(_SPACE.sub(" ", _BLANK_LINE.sub(PARA, token.replace(SOFT_HYPHEN, ""))))
    text = _BREAKS.sub(_resolve, "".join(pieces))
    text = "\n".join(line.strip(" ") for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def html_text(source):
    """element_text of an HTML fragment given as a string."""
    if source is None or not source.strip():
        return None
    return element_text(lxml_html.fragment_fromstring(xml_safe(source), create_parent="div"))


def detached(element):
    """A deep copy, tail excluded, that can be pruned without changing the document."""
    out = copy.deepcopy(element)
    out.tail = None
    return out


def wrap(elements):
    """A new <div> holding deep copies of a run of sibling elements with their tails, which are text between them in the page."""
    div = lxml_html.Element("div")
    for element in elements:
        div.append(copy.deepcopy(element))
    return div
