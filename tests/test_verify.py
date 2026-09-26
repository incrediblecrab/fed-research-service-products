"""verify: a synced store passes, each planted defect is named, and the command exits 1 when one is."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from fed_products import board
from fed_products.beige_book import SCHEMA, normalize, unavailable
from fed_products.pipeline import MAX_ATTEMPTS
from fed_products.store import partition_path, read_parquet, sha256_file, write_parquet
from fed_products.verify import verify
from conftest import ScriptedSource, local_store, run_once, scripted

REPO = Path(__file__).parent.parent
BOARD = ["2025-01-15", "2025-03-05", "2026-01-14", "2026-03-04"]
EARLY = ["1971-06-02", "1971-06-23", "1983-05-18"]
NOTE_URL = "https://www.minneapolisfed.org/beige-book-reports/1983/1983-05-bo"


def beige(**overrides):
    return scripted(**{"board": list(BOARD), "early": list(EARLY), "specials": {"1983-05-18"}, "gaps": {"1971-06-02-atlanta"}, "months": {"1983-05": {"sr": "2025-03-13"}}, **overrides})


@pytest.fixture
def synced(tmp_path):
    store, state = local_store(tmp_path), beige()
    run_once(store, state)
    return store, state


def edit_manifest(store, change):
    path = store.root / "manifest.json"
    manifest = json.loads(path.read_text())
    change(manifest)
    path.write_text(json.dumps(manifest))


def rewrite(store, key, change, match=True):
    """Rewrites a partition's rows through change(rows), unsorted and unchecked, and updates its manifest entry's row count and sha256 to match; with match, also what it records about the rows (editions, gaps, notes), so only the planted defect is left."""
    path = store.root / partition_path(key)
    rows = read_parquet(path)
    change(rows)
    pq.write_table(pa.Table.from_pylist([normalize(row) for row in rows], schema=SCHEMA), path)

    def update(manifest):
        entry = manifest["partitions"][key]
        entry.update(rows=len(rows), sha256=sha256_file(path))
        if match:
            entry.update(editions=len({row["edition"] for row in rows}), gaps=sorted(row["id"] for row in rows if row["text"] is None),
                         unavailable=sorted(row["id"] for row in rows if unavailable(row)),
                         replacement_character=sorted(row["id"] for row in rows if row["text"] and "\ufffd" in row["text"]))

    edit_manifest(store, update)


def where(uid, **values):
    """A change that sets the columns of one row."""
    return lambda rows: [row.update(values) for row in rows if row["id"] == uid]


def add(uid, **values):
    day, section = uid[:10], uid[11:]
    return lambda rows: rows.append(dict(next(row for row in rows if row["edition"] == day), id=uid, section=section, **values))


def drop(uid):
    return lambda rows: rows.remove(next(row for row in rows if row["id"] == uid))


PLANTS = {
    "missing file": (lambda store: (store.root / "data/1983.parquet").unlink(), "1983: data/1983.parquet is in the manifest but not in the repo"),
    "sha256": (lambda store: edit_manifest(store, lambda m: m["partitions"]["2026"].update(sha256="0" * 64)), "2026: data/2026.parquet has sha256"),
    "row count": (lambda store: edit_manifest(store, lambda m: m["partitions"]["2026"].update(rows=27)), "2026: 26 rows, the manifest says 27"),
    "extra file": (lambda store: write_parquet([{"id": "1990-01-10-summary"}], store.root / "data/1990.parquet"), "data/1990.parquet is not in the manifest"),
    "misplaced id": (lambda store: rewrite(store, "2026", where("2026-01-14-boston", id="2025-01-14-boston", edition="2025-01-14")), "2026: ids that belong elsewhere ['2025-01-14-boston']"),
    "id not edition-section": (lambda store: rewrite(store, "2026", where("2026-01-14-boston", id="2026-01-14-bos")), "2026: ids that are not edition-section ['2026-01-14-bos']"),
    "duplicate id": (lambda store: rewrite(store, "2026", add("2026-01-14-boston", text="again")), "2026: duplicate ids ['2026-01-14-boston']"),
    "unknown section": (lambda store: rewrite(store, "2026", add("2026-01-14-tenth", district=None)), "2026: unknown sections ['2026-01-14-tenth']"),
    "unexpected section": (lambda store: rewrite(store, "2026", add("2026-01-14-tenth", district=None)), "2026-01-14: sections missing [], unexpected ['tenth']"),
    "wrong district": (lambda store: rewrite(store, "2026", where("2026-01-14-boston", district=2)), "2026: wrong district numbers ['2026-01-14-boston']"),
    "source without url": (lambda store: rewrite(store, "2026", where("2026-01-14-boston", url=None)), "2026: rows with only one of source and url ['2026-01-14-boston']"),
    "text without url": (lambda store: rewrite(store, "2026", where("2026-01-14-boston", url=None, source=None)), "2026: rows with text but no url ['2026-01-14-boston']"),
    "textless board row": (lambda store: rewrite(store, "2026", where("2026-01-14-boston", text=None)), "2026: rows without text from a page not on minneapolisfed.org and not in board.ANOTHER_REPORT ['2026-01-14-boston']"),
    "gap without a pdf": (lambda store: rewrite(store, "1971", where("1971-06-02-atlanta", pdf_url=None)), "1971: rows without text or pdf_url ['1971-06-02-atlanta']"),
    "unknown source": (lambda store: rewrite(store, "2026", where("2026-01-14-boston", source="example.org")), "2026: unknown sources ['2026-01-14-boston']"),
    "blank text": (lambda store: rewrite(store, "2026", where("2026-01-14-boston", text=" \n")), "2026: blank text ['2026-01-14-boston']"),
    "gaps": (lambda store: rewrite(store, "1983", where("1983-05-18-boston", text=None, source=None, url=None), match=False), "1983: rows without text ['1983-05-18-boston'], the manifest says []"),
    "notes": (lambda store: rewrite(store, "1983", where("1983-05-18-boston", text=None, url=NOTE_URL), match=False), "1983: rows whose page says the report is not available ['1983-05-18-boston'], the manifest says []"),
    "replacement character": (lambda store: rewrite(store, "2026", where("2026-01-14-boston", text="inward \ufffd not"), match=False), "2026: rows holding U+FFFD ['2026-01-14-boston'], the manifest says []"),
    "edition count": (lambda store: edit_manifest(store, lambda m: m["partitions"]["2026"].update(editions=3)), "2026: 2 editions, the manifest says 3"),
    "complete count": (lambda store: edit_manifest(store, lambda m: m["partitions"]["2026"].update(listed=3)), "2026: complete, but 2 editions != 3 listed"),
    "missing section": (lambda store: rewrite(store, "2026", drop("2026-01-14-boston")), "2026-01-14: sections missing ['boston'], unexpected []"),
    "gap in a month with one edition": (lambda store: rewrite(store, "1983", where("1983-05-18-boston", text=None, source=None, url=None)), "1983-05-18: no page for ['boston'], in a month with one edition"),
    "mixed sources": (lambda store: rewrite(store, "2026", where("2026-01-14-boston", source="minneapolisfed.org")), "2026-01-14: some sections from federalreserve.gov, others not"),
    "same text": (lambda store: rewrite(store, "2026", where("2026-01-14-boston", text="new-york  of\n2026-01-14")), "1 texts held by more than one row: [['2026-01-14-boston', '2026-01-14-new-york']]"),
    "special report without text": (lambda store: rewrite(store, "1983", where("1983-05-18-special-report", text=None)), "1983-05-18: a special report without text"),
}


def test_a_synced_store_passes(synced):
    store, state = synced
    report = verify(store, ScriptedSource(state))
    assert report["problems"] == []
    assert (report["editions"], report["rows"], report["text_rows"], report["gaps"]) == (7, 92, 91, 1)
    assert report["partitions"] == 4 and report["complete"] == 4 and report["listed_at_last_full_sync"] == 7 and report["failed_units"] == []
    assert report["sources"] == {"federalreserve.gov": 52, "minneapolisfed.org": 39}
    live = report["live"]
    assert (live["listed"], live["board_hosted"], live["on_hub"], live["missing"], live["pending"], live["extra"]) == (7, 4, 7, [], [], [])
    assert (live["first"], live["newest"], live["sitemap_months_without_edition"]) == ("1971-06-02", "2026-03-04", [])


@pytest.mark.parametrize("plant", sorted(PLANTS))
def test_each_planted_defect_is_named(synced, plant):
    store, _ = synced
    change, expected = PLANTS[plant]
    change(store)
    problems = verify(store)["problems"]
    assert any(problem.startswith(expected) for problem in problems), problems


def test_the_rewrite_helper_alone_plants_nothing(synced):
    store, _ = synced
    rewrite(store, "2026", lambda rows: None)
    assert verify(store)["problems"] == []


def test_a_note_in_a_month_with_one_edition_passes(synced):
    store, _ = synced
    rewrite(store, "1983", where("1983-05-18-boston", text=None, url=NOTE_URL))
    assert verify(store)["problems"] == []


def test_a_board_page_named_in_another_report_passes_without_text(synced, monkeypatch):
    store, state = synced
    pdf = "https://www.federalreserve.gov/monetarypolicy/files/BeigeBook_20260114.pdf"
    rewrite(store, "2026", lambda rows: [row.update(pdf_url=pdf, text=None if row["section"] == "summary" else row["text"]) for row in rows if row["edition"] == "2026-01-14"])
    state.pdfs["2026-01-14"] = pdf
    assert verify(store)["problems"] == ["2026: rows without text from a page not on minneapolisfed.org and not in board.ANOTHER_REPORT ['2026-01-14-summary']"]
    monkeypatch.setattr(board, "ANOTHER_REPORT", {("2026-01-14", "summary"): "2025-03-05"})
    assert verify(store, ScriptedSource(state))["problems"] == []


def test_the_live_diff_names_editions_missing_from_a_complete_year_and_editions_no_list_names(synced):
    store, state = synced
    state.board += ["2026-04-15", "2027-01-13"]
    state.board.remove("2025-01-15")
    report = verify(store, ScriptedSource(state))
    assert report["live"]["missing"] == ["2026-04-15"] and report["live"]["pending"] == ["2027-01-13"] and report["live"]["extra"] == ["2025-01-15"]
    assert report["problems"] == ["1 editions the lists name are not on the Hub: ['2026-04-15']", "1 editions on the Hub are named by no list: ['2025-01-15']"]


def test_the_live_diff_names_links_that_changed(synced):
    store, state = synced
    state.pdfs["2025-03-05"] = "https://www.federalreserve.gov/monetarypolicy/files/BeigeBook_20250305.pdf"
    state.pdfs["1971-06-02"] = "https://www.federalreserve.gov/monetarypolicy/files/other.pdf"
    report = verify(store, ScriptedSource(state))
    assert report["problems"] == ["2 editions whose links differ from the lists: ['1971-06-02 pdf_url', '2025-03-05 pdf_url']"]


def test_the_live_diff_names_board_pages_that_moved_and_an_edition_that_moved_to_the_board(synced):
    store, state = synced
    rewrite(store, "2026", where("2026-01-14-boston", url="https://www.federalreserve.gov/monetarypolicy/beigebook20260114-boston-moved.htm"))
    rewrite(store, "1983", lambda rows: [row.update(source="federalreserve.gov") for row in rows])
    report = verify(store, ScriptedSource(state))
    assert "2 editions whose links differ from the lists: ['1983-05-18 source', '2026-01-14 pages']" in report["problems"]


def test_the_live_diff_holds_each_months_extra_sections_to_the_sitemap(synced):
    store, state = synced
    state.months["1971-06"] = {"sr": "2025-03-13"}
    del state.months["1983-05"]
    report = verify(store, ScriptedSource(state))
    assert report["problems"] == ["months whose extra sections differ from the sitemap: [\"1971-06: sitemap ['special-report'], Hub []\", \"1983-05: sitemap [], Hub ['special-report']\"]"]


def test_an_edition_that_keeps_failing_is_named_by_the_live_diff(tmp_path):
    store, state = local_store(tmp_path), beige(fail={"2025-03-05"})
    for _ in range(MAX_ATTEMPTS):
        run_once(store, state)
    report = verify(store, ScriptedSource(state))
    assert report["failed_units"] == ["2025-03-05"]
    assert report["problems"] == ["1 editions the lists name are not on the Hub: ['2025-03-05']"]


def test_an_incomplete_year_is_not_held_to_the_live_lists(tmp_path):
    store, state = local_store(tmp_path), beige(stop_after=1)
    run_once(store, state)
    report = verify(store, ScriptedSource(state))
    assert report["problems"] == [] and report["complete"] == 0 and report["editions"] == 1
    assert report["live"]["pending"] == ["1971-06-02", "1971-06-23", "1983-05-18", "2025-01-15", "2025-03-05", "2026-01-14"]


def test_no_manifest_is_a_problem(tmp_path):
    assert verify(local_store(tmp_path))["problems"] == ["no manifest.json"]


def run_cli(*args):
    env = {key: value for key, value in os.environ.items() if key not in ("GITHUB_ACTIONS", "GITHUB_OUTPUT", "HF_OIDC_RESOURCE")}
    return subprocess.run([sys.executable, "-m", "fed_products", *args], capture_output=True, text=True, cwd=REPO, env=env)


def test_the_verify_command_exits_1_on_a_planted_defect(synced):
    store, _ = synced
    clean = run_cli("verify", "--local", str(store.root))
    assert clean.returncode == 0, clean.stderr
    PLANTS["sha256"][0](store)
    planted = run_cli("verify", "--local", str(store.root))
    assert planted.returncode == 1 and '"2026: data/2026.parquet has sha256' in planted.stdout
