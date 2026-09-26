"""Renders the dataset card (README.md on the Hub) from the manifest alone, so it is staged with every manifest commit and never disagrees with it."""

import json
from collections import Counter, defaultdict

from . import board, minneapolis
from .beige_book import MINNEAPOLIS_RECHECK_DAYS, RECENT_DAYS, RECENT_RECHECK_DAYS, RECHECK_DAYS, REPO_ID, SCHEMA
from .pipeline import LEASE_MINUTES, MAX_ATTEMPTS, PROBE_KEY, RETRY_AFTER_HOURS, probe_state
from .sections import DISTRICTS, STANDARD, rank

GITHUB = "https://github.com/incrediblecrab/fed-research-service-products"
# The workflow's cron (.github/workflows/pipeline.yml); a test holds the two equal.
SCHEDULE = "0 0,12 * * *"
BOARD_TERMS = "https://www.federalreserve.gov/disclaimer.htm"
MINNEAPOLIS_TERMS = "https://www.minneapolisfed.org/site-information/disclaimer"
COLUMN_DOCS = {
    "id": "`{edition}-{section}`, for example 2026-09-02-boston",
    "edition": "The edition's date (YYYY-MM-DD) as the Board's lists give it",
    "section": f"`summary`, a district ({', '.join(f'`{d}`' for d in DISTRICTS)}) or `special-report`",
    "district": "Federal Reserve district number, 1 (Boston) to 12 (San Francisco); null for the summary and the special report",
    "text": "The section's text (see Text layout); null where no source has it as HTML (see Known gaps)",
    "source": f"`{board.SOURCE}` or `{minneapolis.SOURCE}`, the site the text was taken from; for a row without text, the site whose page says the report is not available, else null",
    "url": "The page the text was taken from; for a row without text, the page that says the report is not available, else null",
    "pdf_url": "The edition's PDF on federalreserve.gov, from the Beige Book pages or else the FOMC historical materials; null when neither links one",
    "source_modified": f"When the page last changed, as its site said at the fetch: the Last-Modified header ({board.SOURCE}) or the sitemap's lastmod, which has no time zone ({minneapolis.SOURCE})",
    "fetched_at": "When the fetch that wrote this row ran (UTC)",
}
ERAS = {
    "1": "one page per section under /fomc/beigebook/",
    "2": "one page per edition under /monetarypolicy/beigebook/",
    "3": "one page per edition under /monetarypolicy/",
    "4": "one page per section under /monetarypolicy/",
}


def size_category(rows):
    for limit, label in ((1_000, "n<1K"), (10_000, "1K<n<10K"), (100_000, "10K<n<100K")):
        if rows < limit:
            return label
    return "100K<n<1M"


def spoken(iso):
    return board.spoken(iso) if iso else "none"


def misdated_note():
    """One clause per edition: the sections whose pages print another day, and that day."""
    groups = defaultdict(list)
    for (month, code), (printed, edition) in minneapolis.MISDATED.items():
        groups[(edition, printed)].append(minneapolis.SLUGS[code])
    parts = []
    for (edition, printed), sections in sorted(groups.items()):
        which = "every section" if set(STANDARD) <= set(sections) else ", ".join(sorted(sections, key=rank))
        parts.append(f"{spoken(edition)}, {which}, whose {'pages print' if len(sections) > 1 else 'page prints'} {spoken(printed)}")
    if not parts:
        return ""
    return f" Where a page prints a day that no list names, it is assigned to its month's edition only once the Board's PDF of that edition was read and holds the same report: {'; '.join(parts)}."


def render(manifest):
    manifest = manifest or {}
    entries = manifest.get("partitions") or {}
    failures = manifest.get("failures") or {}
    listing = manifest.get("listing") or {}
    seen = manifest.get("seen") or listing
    editions = sum(entry.get("editions") or 0 for entry in entries.values())
    rows = sum(entry.get("rows") or 0 for entry in entries.values())
    with_text = sum(entry.get("text_rows") or 0 for entry in entries.values())
    gaps = sorted(uid for entry in entries.values() for uid in entry.get("gaps") or [])
    notes = {uid for entry in entries.values() for uid in entry.get("unavailable") or []}
    replaced = sorted(uid for entry in entries.values() for uid in entry.get("replacement_character") or [])
    exhausted = sorted(uid for uid, f in failures.items() if f["attempts"] >= MAX_ATTEMPTS)
    lines = ["---", "pretty_name: Federal Reserve Beige Book", "license: other", "license_name: federal-reserve-website-terms",
             f"license_link: https://huggingface.co/datasets/{REPO_ID}#license", "language:", "- en",
             "task_categories:", "- text-generation", "- summarization", "- text-classification",
             "tags:", "- economics", "- finance", "- federal-reserve", "- beige-book", "- monetary-policy", "- government", "- united-states",
             "size_categories:", f"- {size_category(rows)}"]
    if entries:
        lines += ["configs:", "- config_name: default", "  data_files:", "  - split: train", "    path: data/*.parquet"]
    state = probe_state(manifest)
    if state:
        lines.append(f"{PROBE_KEY}: {json.dumps(state, sort_keys=True)}")
    lines += ["---", "", "# Federal Reserve Beige Book", ""]
    lines += [
        f"Every edition of the Beige Book, the Federal Reserve's *Summary of Commentary on Current Economic Conditions by Federal Reserve District*, from the first{', ' + spoken(seen['first']) if seen.get('first') else ''}, when the Board's pages call it the Redbook, to the latest. One row per section: the national summary, each of the twelve districts, and the one special report (May 18, 1983). In the words of the Board's [Beige Book page]({board.LANDING}), the report \"is published eight times per year. Each Federal Reserve Bank gathers anecdotal information on current economic conditions in its District [...] An overall summary of the twelve district reports is prepared by a designated Federal Reserve Bank on a rotating basis.\"",
        "",
        f"Nothing here is edited by hand. The pipeline, its tests and its schedule are in [{GITHUB.removeprefix('https://')}]({GITHUB}), and this card is rendered from `manifest.json` in the same commit.",
        "",
        "## Status",
        "",
    ]
    if editions:
        first = min(entry["first"] for entry in entries.values() if entry.get("first"))
        last = max(entry["last"] for entry in entries.values() if entry.get("last"))
        lines.append(f"**{editions:,} editions**, {spoken(first)} to {spoken(last)}: {rows:,} rows, {with_text:,} with text and {len(gaps):,} without (see Known gaps).")
    else:
        lines.append("The first sync has not fetched an edition yet.")
    if seen.get("at"):
        upcoming = seen.get("scheduled") or []
        lines += ["", f"At {seen['at']} UTC the Board's lists named {seen.get('editions') or 0:,} editions, the newest {spoken(seen.get('newest'))}. The next edition on the Board's schedule: {spoken(upcoming[0]) if upcoming else 'none listed'}."]
    if listing.get("at"):
        lines += ["", f"Last complete sync: {listing['at']} UTC."]
    if exhausted:
        named = ", ".join(exhausted[:10]) + (f" and {len(exhausted) - 10} more" if len(exhausted) > 10 else "")
        one = len(exhausted) == 1
        lines += ["", f"{'1 unit' if one else f'{len(exhausted)} units'} failed {MAX_ATTEMPTS} times and {'is' if one else 'are'} retried every {RETRY_AFTER_HOURS} hours; {'its error is' if one else 'their errors are'} in `manifest.json`: {named}."]
    lines += ["", f"| Year | Editions | Rows | With text | Text from {board.SOURCE} | Text from {minneapolis.SOURCE} |", "|---|---:|---:|---:|---:|---:|"]
    for key in sorted(entries):
        entry = entries[key]
        sources = entry.get("sources") or {}
        lines.append(f"| {key} | {entry.get('editions') or 0} | {entry.get('rows') or 0:,} | {entry.get('text_rows') or 0:,} | {sources.get(board.SOURCE, 0):,} | {sources.get(minneapolis.SOURCE, 0):,} |")
    if not entries:
        lines.append("| (none yet) | 0 | 0 | 0 | 0 | 0 |")
    lines += [
        "",
        "## Use",
        "",
        "```python",
        "from datasets import load_dataset",
        f'beige = load_dataset("{REPO_ID}", split="train")',
        "```",
        "",
        "```sql",
        "-- DuckDB, straight from the Hub",
        f"SELECT edition, text FROM 'hf://datasets/{REPO_ID}/data/*.parquet' WHERE section = 'summary' ORDER BY edition DESC LIMIT 5;",
        "```",
        "",
        "## Files",
        "",
        "- `data/{year}.parquet`: one row per section of the year's editions, sorted by `id`.",
        "- `manifest.json`: per year, the row count, SHA-256, editions, rows without text, and each unit's version and last check; units that failed, with their errors; the last complete listing; the last 20 runs.",
        "",
        "## Schema",
        "",
        "| Column | Type | Description |",
        "|---|---|---|",
    ]
    for column in SCHEMA:
        lines.append(f"| `{column.name}` | {column.type} | {COLUMN_DOCS[column.name]} |")
    eras = seen.get("eras") or listing.get("eras") or {}
    lines += [
        "",
        "## Where the text comes from",
        "",
        "An edition is a date that one of the Board of Governors' two lists names: the Beige Book pages (the landing page and the archive's year pages, 1996 on) and the FOMC historical materials, a page a year, which link the Redbook or Beige Book of each meeting that had one (1970 to 2020 on September 25, 2026). Neither list is complete alone. On September 25, 2026 the Board's 2003 Beige Book page omitted September 3, 2003, which the Board hosts, and the 2006 historical page had four Beige Book links with empty targets. This dataset holds the union.",
        "",
        "The Board's HTML begins with October 30, 1996. Those editions are taken from it, in the layout each period used:",
        "",
    ]
    for era, span in sorted(eras.items()):
        lines.append(f"- {spoken(span['first'])} to {spoken(span['last'])}, {span['editions']} editions: {ERAS.get(era, 'another layout')}.")
    if not eras:
        lines.append("- (no listing yet)")
    lines += [
        "",
        "For earlier editions the Board has PDFs only, so their text is from the Federal Reserve Bank of Minneapolis's archive, which has the editions from 1970 on as HTML, one page per section. Its pages are addressed by month, and a page is assigned to the edition whose date it prints, never to its month." + misdated_note(),
        "",
        "## Text layout",
        "",
        "Each paragraph is one line, and paragraphs are separated by a blank line. A line break in the page is a single newline, and two or more in a row, which a browser shows as an empty line, are a paragraph break; table cells are separated by tabs, and list items start with \"- \". Subheadings stay as their own paragraphs, and so does the note on which Reserve Bank prepared the report, where the page prints it. Left out: each section's own title (\"Federal Reserve Bank of Boston\", \"National Summary\"), which `section` names; page navigation; and the date that opens each Minneapolis page, which `edition` holds. The older Minneapolis pages are typed text wrapped at about 70 columns; where their HTML marks no paragraphs, a blank line in the text is kept as a paragraph break.",
        "",
        "## How it stays current",
        "",
        f"A GitHub Actions job is scheduled at 00:00 and 12:00 UTC (`{SCHEDULE}`). It reads the Board's Beige Book landing page, one request, and compares the newest edition it links and the number it links with the last complete sync, which it reads from `{PROBE_KEY}` in this card's metadata, so the check downloads no file. When either changed, or the last complete sync is a day old, the job reads every list and the Minneapolis sitemap, fetches the editions that are new or whose links changed, and commits the changed years with this card. Then it checks the files on the Hub against the manifest and against a fresh reading of every list. A job that runs out of time while still fetching starts the next one itself.",
        "",
        f"The Board's pages can change after release: on September 25, 2026 the pages of January 14, 2026 answered with a Last-Modified date of February 26, 2026. So the job also rechecks editions it holds: every {RECENT_RECHECK_DAYS} days for editions under {RECENT_DAYS} days old and every {RECHECK_DAYS} days for older ones, with a conditional request that the Board answers without a body when the page has not changed, and each Minneapolis month every {MINNEAPOLIS_RECHECK_DAYS} days besides whenever its sitemap entries change. An edition no list names any more is removed only once its page is gone; until then the check after each sync reports it.",
        "",
        "GitHub starts scheduled jobs late, or drops them, when it is busy, and its documentation names the start of every hour as a busy time. So a new edition can take more than 12 hours to appear, and the delay is not fixed. A dropped job loses nothing, because the next one reads whatever changed.",
        "",
        f"The job writes with Hugging Face Trusted Publishing, so no write token is stored anywhere. One writer at a time: the manifest records who wrote last and when, a writer waits while another's record is under {LEASE_MINUTES} minutes old, and every commit names its parent commit, so a second writer's commit is refused instead of merged. `manifest.json` names each run's writer: `github-actions` for this job, `local` for the same pipeline run from a computer.",
        "",
        "## Known gaps",
        "",
    ]
    pageless = [uid for uid in gaps if uid not in notes]
    for group, intro in (
        (pageless, "no text and no page. Each is from an edition that shared its month with another, and the Minneapolis archive has one address per section per month, each serving one of the month's editions, so these sections are in no source's HTML"),
        (sorted(notes), "no text because the Minneapolis page holds a note that the report is not available in place of the report; `url` names the page"),
    ):
        if not group:
            continue
        by_edition = defaultdict(list)
        for uid in group:
            by_edition[uid[:10]].append(uid[11:])
        count = f"{len(group)} sections have" if len(group) != 1 else "1 section has"
        lines.append(f"- {count} {intro}. The rows keep `pdf_url`, the Board's PDF of the edition, which this dataset does not read:")
        for day, sections in sorted(by_edition.items()):
            lines.append(f"  - {spoken(day)}: {', '.join(sections)}")
    if not gaps:
        lines.append("- Every section has text.")
    if replaced:
        count = f"{len(replaced)} sections hold" if len(replaced) != 1 else "1 section holds"
        years = ", ".join(f"{year} ({n})" for year, n in sorted(Counter(uid[:4] for uid in replaced).items()))
        lines.append(f"- {count} U+FFFD, the Unicode replacement character, where the page itself serves it, most likely for a character lost when the page was made; the text keeps it as served. Sections by year: {years}.")
    lines += [
        f"- An edit to a page reaches this dataset at its next recheck, so up to {RECENT_RECHECK_DAYS} days late for editions under {RECENT_DAYS} days old, {RECHECK_DAYS} days for older Board editions, and {MINNEAPOLIS_RECHECK_DAYS} days for a Minneapolis page whose sitemap entry does not change.",
        "- Only the text: images and the PDFs' page layout are not kept.",
        "",
        "## License",
        "",
        f"- Rows whose `source` is `{board.SOURCE}`: the Board's [website terms]({BOARD_TERMS}) say \"Unless otherwise indicated, information on Board's website is in the public domain and may be copied and distributed without permission. Please cite to the Board as the source of the information.\"",
        f"- Rows whose `source` is `{minneapolis.SOURCE}`: the Federal Reserve Bank of Minneapolis's [terms]({MINNEAPOLIS_TERMS}) say \"The information provided on this site may be used for research and informational purposes only.\" Filter on `source` to keep only the Board's rows. The Board has a PDF of these editions too (`pdf_url`).",
        "- The code that builds the dataset is MIT-licensed. This dataset is not affiliated with or endorsed by the Federal Reserve.",
        "",
    ]
    return "\n".join(lines)
