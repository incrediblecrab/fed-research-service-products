"""Checks that the files on the Hub are what the manifest says and hold whole editions, and with a source (--live) that they hold exactly the editions the Board's lists name now."""

from collections import Counter, defaultdict

from . import board, minneapolis
from .beige_book import REPLACEMENT_CHARACTER, partition_of, row_id
from .sections import ORDER, SPECIAL_REPORT, STANDARD, district_number
from .store import partition_path

COLUMNS = ["id", "edition", "section", "district", "text", "source", "url", "pdf_url"]
SOURCES = (board.SOURCE, minneapolis.SOURCE)
SAMPLE = 10


def verify(store, source=None):
    """Returns a report; report["problems"] is empty when every check passed."""
    manifest = store.read_manifest()
    if not manifest:
        return {"problems": ["no manifest.json"]}
    problems = []
    entries = manifest.get("partitions") or {}
    files = set(store.list_files("data/"))
    expected = {key: entry.get("file") or partition_path(key) for key, entry in entries.items()}
    sha256s = store.file_sha256s(sorted(path for path in expected.values() if path in files))
    hub = defaultdict(dict)
    rows_total, text_rows, gaps, sources = 0, 0, [], Counter()
    for key, path in sorted(expected.items()):
        entry = entries[key]
        if path not in files:
            problems.append(f"{key}: {path} is in the manifest but not in the repo")
            continue
        if sha256s.get(path) != entry.get("sha256"):
            problems.append(f"{key}: {path} has sha256 {str(sha256s.get(path))[:12]}, the manifest says {str(entry.get('sha256'))[:12]}")
        columns = store.read_columns(path, COLUMNS)
        rows = [dict(zip(COLUMNS, values)) for values in zip(*(columns[name] for name in COLUMNS))]
        problems += check_partition(key, entry, rows)
        for row in rows:
            if row["edition"] and row["section"]:
                hub[row["edition"]][row["section"]] = row
        rows_total += len(rows)
        text_rows += sum(1 for row in rows if row["text"] is not None)
        gaps += [row["id"] for row in rows if row["text"] is None]
        sources.update(row["source"] or "none" for row in rows if row["text"] is not None)
    for path in sorted(files - set(expected.values())):
        problems.append(f"{path} is not in the manifest")
    problems += check_editions(hub)
    listing = manifest.get("listing") or {}
    report = {
        "editions": len(hub),
        "rows": rows_total,
        "text_rows": text_rows,
        "gaps": len(gaps),
        "listed_at_last_full_sync": listing.get("editions"),
        "partitions": len(entries),
        "complete": sum(1 for entry in entries.values() if entry.get("complete")),
        "failed_units": sorted(uid for entry in entries.values() for uid in entry.get("failed") or []),
        "sources": dict(sorted(sources.items())),
    }
    if source is not None:
        report["live"] = live_diff(manifest, hub, source, problems)
    report["problems"] = problems
    return report


def check_partition(key, entry, rows):
    problems = []
    ids = [row["id"] for row in rows]
    if len(ids) != entry.get("rows"):
        problems.append(f"{key}: {len(ids)} rows, the manifest says {entry.get('rows')}")
    duplicates = sorted(uid for uid, n in Counter(ids).items() if n > 1)
    if duplicates:
        problems.append(f"{key}: duplicate ids {duplicates[:SAMPLE]}")
    bad = defaultdict(list)
    for row in rows:
        uid = row["id"]
        if uid is None or uid != row_id(row["edition"], row["section"]):
            bad["ids that are not edition-section"].append(uid)
            continue
        if partition_of(uid) != key:
            bad["ids that belong elsewhere"].append(uid)
        if row["section"] not in ORDER:
            bad["unknown sections"].append(uid)
        elif row["district"] != district_number(row["section"]):
            bad["wrong district numbers"].append(uid)
        if (row["source"] is None) != (row["url"] is None):
            bad["rows with only one of source and url"].append(uid)
        if row["text"] is not None and row["url"] is None:
            bad["rows with text but no url"].append(uid)
        if row["text"] is None and row["url"] is not None and row["source"] != minneapolis.SOURCE:
            bad[f"rows without text from a page not on {minneapolis.SOURCE}"].append(uid)
        # The card tells readers of a row without text to use the edition's PDF.
        if row["text"] is None and not row["pdf_url"]:
            bad["rows without text or pdf_url"].append(uid)
        if row["source"] is not None and row["source"] not in SOURCES:
            bad["unknown sources"].append(uid)
        if row["text"] is not None and not row["text"].strip():
            bad["blank text"].append(uid)
    for what, uids in sorted(bad.items()):
        problems.append(f"{key}: {what} {sorted(map(str, uids))[:SAMPLE]}")
    gaps = sorted(row["id"] for row in rows if row["text"] is None)
    if gaps != sorted(entry.get("gaps") or []):
        problems.append(f"{key}: rows without text {gaps[:SAMPLE]}, the manifest says {sorted(entry.get('gaps') or [])[:SAMPLE]}")
    notes = sorted(row["id"] for row in rows if row["text"] is None and row["url"] is not None)
    if notes != sorted(entry.get("unavailable") or []):
        problems.append(f"{key}: rows whose page says the report is not available {notes[:SAMPLE]}, the manifest says {sorted(entry.get('unavailable') or [])[:SAMPLE]}")
    replaced = sorted(row["id"] for row in rows if row["text"] and REPLACEMENT_CHARACTER in row["text"])
    if replaced != sorted(entry.get("replacement_character") or []):
        problems.append(f"{key}: rows holding U+FFFD {replaced[:SAMPLE]}, the manifest says {sorted(entry.get('replacement_character') or [])[:SAMPLE]}")
    editions = {row["edition"] for row in rows}
    if len(editions) != entry.get("editions"):
        problems.append(f"{key}: {len(editions)} editions, the manifest says {entry.get('editions')}")
    if entry.get("complete") and not entry.get("failed") and not entry.get("unlisted") and len(editions) != entry.get("listed"):
        problems.append(f"{key}: complete, but {len(editions)} editions != {entry.get('listed')} listed")
    return problems


def check_editions(hub):
    """Each edition holds the thirteen standard sections and nothing else but a special report; a section without text and without a page is only possible in a month that held two editions, where each Minneapolis address serves one of them; an edition the Board serves has every section from the Board."""
    problems = []
    per_month = Counter(day[:7] for day in hub)
    for day, sections in sorted(hub.items()):
        missing = [section for section in STANDARD if section not in sections]
        extra = sorted(section for section in sections if section not in STANDARD and section != SPECIAL_REPORT)
        if missing or extra:
            problems.append(f"{day}: sections missing {missing}, unexpected {extra}")
        pageless = sorted(section for section, row in sections.items() if row["text"] is None and row["url"] is None)
        if pageless and per_month[day[:7]] < 2:
            problems.append(f"{day}: no page for {pageless}, in a month with one edition")
        from_board = {row["source"] == board.SOURCE for row in sections.values()}
        if True in from_board and from_board != {True}:
            problems.append(f"{day}: some sections from {board.SOURCE}, others not")
        special = sections.get(SPECIAL_REPORT)
        if special is not None and special["text"] is None:
            problems.append(f"{day}: a special report without text")
    return problems


def live_diff(manifest, hub, source, problems):
    """The editions every list names now against the editions on the Hub, exactly: any difference in a complete partition is a problem, including an edition whose unit keeps failing. Also each Board edition's page and PDF links, and, in complete partitions, each Minneapolis month's extra sections against its sitemap entries. The historical pages are read from the first year the index lists, so an edition before board.FIRST_YEAR would show up as missing."""
    listing = source.listing(first_year=None)
    live = listing.editions
    entries = manifest.get("partitions") or {}
    missing, pending, extra = [], [], sorted(day for day in hub if day not in live)
    for day in sorted(live):
        if day in hub:
            continue
        (missing if (entries.get(partition_of(day)) or {}).get("complete") else pending).append(day)
    if missing:
        problems.append(f"{len(missing)} editions the lists name are not on the Hub: {missing[:SAMPLE]}")
    if extra:
        problems.append(f"{len(extra)} editions on the Hub are named by no list: {extra[:SAMPLE]}")
    links = []
    for day in sorted(set(live) & set(hub)):
        edition, sections = live[day], hub[day]
        if any(row["pdf_url"] != edition.pdf_url for row in sections.values()):
            links.append(f"{day} pdf_url")
        if edition.board_hosted:
            pages = {section: url for group, url in board.pages_of(board.era_of(edition.html_url), edition.html_url) for section in group}
            if any(row["source"] != board.SOURCE or row["url"] != pages.get(section) for section, row in sections.items()):
                links.append(f"{day} pages")
        elif any(row["source"] == board.SOURCE for row in sections.values()):
            links.append(f"{day} source")
    if links:
        problems.append(f"{len(links)} editions whose links differ from the lists: {links[:SAMPLE]}")
    months = defaultdict(list)
    for day, edition in live.items():
        if not edition.board_hosted:
            months[day[:7]].append(day)
    specials = []
    for month, days in sorted(months.items()):
        if not (entries.get(partition_of(month)) or {}).get("complete"):
            continue
        listed = sorted(minneapolis.SLUGS.get(code, code) for code in listing.months.get(month, {}) if minneapolis.SLUGS.get(code) not in STANDARD)
        held = sorted(section for day in days for section in hub.get(day, {}) if section not in STANDARD)
        if listed != held:
            specials.append(f"{month}: sitemap {listed}, Hub {held}")
    if specials:
        problems.append(f"months whose extra sections differ from the sitemap: {specials[:SAMPLE]}")
    return {
        "listed": len(live),
        "board_hosted": sum(1 for edition in live.values() if edition.board_hosted),
        "on_hub": len(hub),
        "missing": missing,
        "pending": pending,
        "extra": extra,
        "first": min(live, default=None),
        "newest": max(live, default=None),
        "scheduled": listing.scheduled,
        "pages_read": listing.pages,
        "sitemap_months_without_edition": sorted(month for month in listing.months if not any(day.startswith(month) for day in live)),
    }
