"""The sections of a Beige Book edition: the national summary and the twelve Federal Reserve districts, in the order the reports print them, plus the one special report (May 18, 1983)."""

import re

SUMMARY = "summary"
DISTRICTS = ("boston", "new-york", "philadelphia", "cleveland", "richmond", "atlanta", "chicago", "st-louis", "minneapolis", "kansas-city", "dallas", "san-francisco")
STANDARD = (SUMMARY,) + DISTRICTS
SPECIAL_REPORT = "special-report"
ORDER = STANDARD + (SPECIAL_REPORT,)


def district_number(section):
    """1 for Boston through 12 for San Francisco, the Federal Reserve's own numbering; None for the summary and the special report."""
    return DISTRICTS.index(section) + 1 if section in DISTRICTS else None


def name_key(text):
    """Letters only, lower case, so "St. Louis", "st-louis" and "stlouis" compare equal."""
    return re.sub(r"[^a-z]", "", text.lower())


def names(section, text):
    """Whether a heading or title names the section: "Federal Reserve Bank of St. Louis" names st-louis, "National Summary" names summary."""
    return name_key(section) in name_key(text or "")


def rank(section):
    return ORDER.index(section) if section in ORDER else len(ORDER)
