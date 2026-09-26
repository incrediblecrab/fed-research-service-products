"""The dataset card: front matter the Hub's own parser accepts, numbers taken from the manifest, every column documented."""

import yaml
from huggingface_hub import DatasetCard

from fed_products import minneapolis
from fed_products.beige_book import COLUMNS
from fed_products.card import COLUMN_DOCS, render, size_category
from fed_products.pipeline import LISTING_HOURS, MAX_ATTEMPTS, PROBE_KEY, new_manifest, probe_state
from conftest import local_store, run_once, scripted

T1 = "2026-09-01T10:00:00Z"


def front_matter(card):
    assert card.startswith("---\n")
    head, separator, _ = card.removeprefix("---\n").partition("\n---\n")
    assert separator, "the front matter block is closed"
    meta = yaml.safe_load(head)
    assert DatasetCard(card).data.to_dict() == meta
    return meta


def beige(**overrides):
    """Two Board editions in each of 2025 and 2026; June 1971, a month with two editions; May 1983 with its special report."""
    return scripted(**{"board": ["2025-01-15", "2025-03-05", "2026-01-14", "2026-03-04"], "early": ["1971-06-02", "1971-06-23", "1983-05-18"], "specials": {"1983-05-18"}, **overrides})


def test_an_empty_manifest_renders_a_card_without_data_files():
    for manifest in (new_manifest(), None):
        card = render(manifest)
        meta = front_matter(card)
        assert "configs" not in meta and meta["license"] == "other" and meta["size_categories"] == ["n<1K"]
        assert "The first sync has not fetched an edition yet." in card and "| (none yet) | 0 | 0 | 0 | 0 | 0 |" in card and "- (no listing yet)" in card
        assert "- Every section has text." in card and "Last complete sync:" not in card
        assert "from the first, when the Board's pages call it the Redbook" in card
    assert PROBE_KEY not in front_matter(render(None))


def test_each_page_assigned_by_the_boards_pdf_is_named(monkeypatch):
    note = "Where a page prints a day that no list names, it is assigned to its month's edition only once the Board's PDF of that edition was read and holds the same report: August 15, 1973, every section, whose pages print August 10, 1973; October 27, 1987, richmond, whose page prints October 23, 1987."
    assert note in render(new_manifest())
    monkeypatch.setattr(minneapolis, "MISDATED", {})
    assert "Where a page prints a day" not in render(new_manifest())


def test_the_schedule_names_the_listing_age_at_which_the_probe_asks_for_a_sync(monkeypatch):
    assert f"or the last complete sync is {LISTING_HOURS} hours old, the job reads every list" in render(new_manifest())
    monkeypatch.setattr("fed_products.card.LISTING_HOURS", 7)
    assert "or the last complete sync is 7 hours old" in render(new_manifest())


def test_the_columns_say_a_row_without_text_can_name_a_page_that_holds_another_report():
    assert "holds another edition's report" in COLUMN_DOCS["source"] and "holds another edition's report" in COLUMN_DOCS["url"]


def test_a_synced_manifest_renders_its_numbers(tmp_path):
    store, state = local_store(tmp_path), beige(gaps={"1971-06-02-atlanta"}, notes={"1971-06-23-boston"}, scheduled=["2026-10-14", "2026-11-25"])
    run_once(store, state)
    card = render(store.read_manifest())
    meta = front_matter(card)
    assert meta["configs"] == [{"config_name": "default", "data_files": [{"split": "train", "path": "data/*.parquet"}]}]
    assert "from the first, June 2, 1971, when the Board's pages call it the Redbook" in card
    assert "**7 editions**, June 2, 1971 to March 4, 2026: 92 rows, 90 with text and 2 without (see Known gaps)." in card
    assert "the Board's lists named 7 editions, the newest March 4, 2026. The next edition on the Board's schedule: October 14, 2026." in card
    assert "Last complete sync:" in card
    for line in ("| 1971 | 2 | 26 | 24 | 0 | 24 |", "| 1983 | 1 | 14 | 14 | 0 | 14 |", "| 2025 | 2 | 26 | 26 | 26 | 0 |", "| 2026 | 2 | 26 | 26 | 26 | 0 |"):
        assert line in card
    assert "- January 15, 2025 to March 4, 2026, 4 editions: one page per section under /monetarypolicy/." in card
    gaps = card.partition("## Known gaps")[2].partition("## License")[0]
    assert "- 1 section has no text and no page. Each is from an edition that shared its month with another" in gaps and "  - June 2, 1971: atlanta\n" in gaps
    assert "- 1 section has no text because the Minneapolis page holds a note that the report is not available" in gaps and "  - June 23, 1971: boston\n" in gaps
    assert "the report; `url` names the page. The row keeps `pdf_url`" in gaps
    assert "Every section has text" not in gaps and "units failed" not in card and "unit failed" not in card


def test_gap_counts_agree_in_number(tmp_path):
    store, state = local_store(tmp_path), beige(gaps={"1971-06-02-atlanta", "1971-06-02-chicago", "1971-06-23-boston"})
    run_once(store, state)
    card = render(store.read_manifest())
    assert "- 3 sections have no text and no page." in card and "  - June 2, 1971: atlanta, chicago\n  - June 23, 1971: boston\n" in card
    assert "The rows keep `pdf_url`" in card and "The row keeps" not in card
    assert "holds a note" not in card


def test_sections_that_hold_the_replacement_character_are_counted_by_year(tmp_path):
    store, state = local_store(tmp_path), beige()
    run_once(store, state)
    assert "U+FFFD" not in render(store.read_manifest())
    texts = {"2025-01-15-boston": "inward \ufffd not", "2025-03-05-dallas": "a \ufffd b \ufffd c", "2026-01-14-summary": "x \ufffd y"}
    (tmp_path / "again").mkdir()
    store, state = local_store(tmp_path / "again"), beige(texts=texts)
    run_once(store, state)
    manifest = store.read_manifest()
    assert manifest["partitions"]["2025"]["replacement_character"] == ["2025-01-15-boston", "2025-03-05-dallas"] and manifest["partitions"]["1971"]["replacement_character"] == []
    gaps = render(manifest).partition("## Known gaps")[2].partition("## License")[0]
    assert "- 3 sections hold U+FFFD, the Unicode replacement character, where the page itself serves it" in gaps and "Sections by year: 2025 (2), 2026 (1)." in gaps


def test_a_partial_backfill_shows_what_is_stored_and_what_the_lists_name(tmp_path):
    store, state = local_store(tmp_path), beige(stop_after=2)
    run_once(store, state)
    card = render(store.read_manifest())
    assert "**2 editions**, January 14, 2026 to March 4, 2026: 26 rows, 26 with text and 0 without" in card
    assert "| 2026 | 2 | 26 | 26 | 26 | 0 |" in card and "| 2025 |" not in card
    assert "the Board's lists named 7 editions" in card and "Last complete sync:" not in card


def test_units_that_keep_failing_are_named_in_number(tmp_path):
    store, state = local_store(tmp_path), beige(fail={"1983-05"})
    manifest = None
    for _ in range(MAX_ATTEMPTS):
        run_once(store, state)
        manifest = store.read_manifest()
        for failure in manifest["failures"].values():
            failure["at"] = T1
        store.stage_manifest(manifest)
        store.commit("age the failures")
    card = render(store.read_manifest())
    assert f"1 unit failed {MAX_ATTEMPTS} times and is retried every 24 hours; its error is in `manifest.json`: 1983-05." in card
    manifest = store.read_manifest()
    manifest["failures"] = {f"19{n:02d}-01": {"attempts": MAX_ATTEMPTS, "at": T1, "partition": f"19{n:02d}"} for n in range(70, 82)}
    card = render(manifest)
    assert f"12 units failed {MAX_ATTEMPTS} times and are retried every 24 hours; their errors are in `manifest.json`: 1970-01, " in card and "1979-01 and 2 more." in card


def test_every_column_is_documented_in_the_schema_table():
    card = render(new_manifest())
    assert set(COLUMN_DOCS) == set(COLUMNS)
    for column in COLUMNS:
        assert f"| `{column}` |" in card


def test_the_size_category_follows_the_rows():
    assert [size_category(n) for n in (0, 999, 1_000, 6_371, 10_000, 100_000)] == ["n<1K", "n<1K", "1K<n<10K", "1K<n<10K", "10K<n<100K", "100K<n<1M"]


def test_the_front_matter_carries_the_probe_state_and_no_error_text(tmp_path):
    store, state = local_store(tmp_path), beige(stop_after=2)
    run_once(store, state)
    manifest = store.read_manifest()
    # No listing is published before a run brings every year up to date, so the probe keeps asking for runs.
    assert front_matter(render(manifest))[PROBE_KEY] == probe_state(manifest) and probe_state(manifest)["listing"] is None
    reason = "HTTPStatusError: Client error '404 Not Found' for url \"https://x/y?a=b\": #1\n---\nkey: value"
    manifest["runs"].append({"stopped": reason, "ended": T1})
    meta = front_matter(render(manifest))
    assert meta[PROBE_KEY]["failed_runs"] == [{"stopped": "HTTPStatusError", "ended": T1}] and "key" not in meta
    assert reason not in render(manifest).partition("\n---\n")[0]
