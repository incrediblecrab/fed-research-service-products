"""The sync loop against a scripted source and a local store."""

import json
from datetime import date, datetime, timedelta, timezone

import pytest
from huggingface_hub import DatasetCard

from fed_products import board, pipeline
from fed_products.beige_book import MINNEAPOLIS_RECHECK_DAYS, RECENT_RECHECK_DAYS, RECHECK_DAYS, Edition, Unit
from fed_products.card import render
from fed_products.http import Blocked
from fed_products.pipeline import MAX_ATTEMPTS, PROBE_KEY, Partition, decide, order, probe_state
from fed_products.sections import STANDARD
from fed_products.store import CARD, MANIFEST, LocalStore, Superseded
from fed_products.verify import verify
from conftest import ScriptedSource, board_url, local_store, run_once, scripted

TODAY = date(2026, 9, 25)
BOARD = ["2025-01-15", "2025-03-05", "2026-01-14", "2026-03-04"]
EARLY = ["1971-06-02", "1971-06-23", "1983-05-18"]
ALL_UNITS = ["2026-03-04", "2026-01-14", "2025-03-05", "2025-01-15", "1983-05", "1971-06"]
NEW_PDF = "https://www.federalreserve.gov/monetarypolicy/files/BeigeBook_20250305.pdf"


def beige(**overrides):
    """Four Board editions over two years, as of TODAY two under a year old and two older; June 1971, a month with two editions, one of whose sections no source serves; and May 1983 with its special report."""
    return scripted(**{"board": list(BOARD), "early": list(EARLY), "specials": {"1983-05-18"}, "gaps": {"1971-06-02-atlanta"}, **overrides})


def decide_both(manifest, head, writer):
    """decide() from probe_state(manifest), and from the copy of it in the front matter of the card rendered from the manifest, which the probe reads from the Hub; the two must agree."""
    direct = decide(probe_state(manifest), head, writer)
    carried = DatasetCard(render(manifest)).data.to_dict().get(PROBE_KEY)
    assert decide(carried, head, writer) == direct
    return direct


def stored(store, key):
    return {row["id"]: row for row in store.read_partition(key)}


def of(rows, day):
    return {uid: row for uid, row in rows.items() if row["edition"] == day}


def shift_clock(monkeypatch, hours):
    """Moves the pipeline's own clock (checks, retries and the stamps it writes), not the lease's."""
    monkeypatch.setattr(pipeline, "utcnow", lambda: (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(pipeline.STAMP))


def stamp(minutes_ago):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime(pipeline.STAMP)


def parquet_uploads(commits):
    return [path for commit in commits for path in commit["files"] if path.endswith(".parquet")]


def test_first_sync_stores_every_edition_and_publishes_the_listing(tmp_path):
    store, state = local_store(tmp_path), beige()
    run = run_once(store, state, today=TODAY)
    assert run["finished"] and run["stopped"] is None and run["fetched"] == 6
    assert state.fetched == ALL_UNITS, "newest year first, newest unit first"
    assert all(seen == {} for seen in state.stored_seen.values())
    m = store.read_manifest()
    entries = m["partitions"]
    assert {key: entry["complete"] for key, entry in entries.items()} == {"1971": True, "1983": True, "2025": True, "2026": True}
    assert sum(entry["rows"] for entry in entries.values()) == 4 * 13 + 2 * 13 + 14
    assert entries["1971"]["gaps"] == ["1971-06-02-atlanta"] and entries["1971"]["unavailable"] == [] and entries["1971"]["editions"] == 2
    assert entries["1971"]["sources"] == {"minneapolisfed.org": 25} and entries["2026"]["sources"] == {"federalreserve.gov": 26}
    assert (entries["1983"]["first"], entries["1983"]["last"], entries["1983"]["rows"]) == ("1983-05-18", "1983-05-18", 14)
    listing = m["listing"]
    assert listing["head"] == {"newest": "2026-03-04", "published": 2} and listing["editions"] == 7 and listing["board_hosted"] == 4
    assert (listing["first"], listing["newest"]) == ("1971-06-02", "2026-03-04")
    assert listing["partitions"] == {"1971": 2, "1983": 1, "2025": 2, "2026": 2}
    assert listing["eras"] == {"4": {"first": "2025-01-15", "last": "2026-03-04", "editions": 4}}
    special = stored(store, "1983")["1983-05-18-special-report"]
    assert special["text"] == "special-report of 1983-05-18" and special["source"] == "minneapolisfed.org" and special["district"] is None and special["fetched_at"]
    assert special["pdf_url"] == "https://www.federalreserve.gov/monetarypolicy/files/fomc19830518redbook.pdf"
    assert stored(store, "2026")["2026-01-14-boston"]["district"] == 1
    assert m["writer"]["by"] == "local" and m["runs"][-1]["fetched"] == 6
    assert decide_both(m, ScriptedSource(state).head(), "local") == (False, "up to date")
    assert verify(store)["problems"] == []


def test_every_commit_carries_the_manifest_and_the_card_rendered_from_it(tmp_path):
    store, state = local_store(tmp_path), beige()
    run_once(store, state, today=TODAY)
    assert store.commits and all({MANIFEST, CARD} <= set(commit["files"]) for commit in store.commits)
    assert store.read_text(CARD) == render(store.read_manifest())


def test_idle_rerun_fetches_and_commits_nothing(tmp_path):
    store, state = local_store(tmp_path), beige()
    run_once(store, state, today=TODAY)
    commits, fetched = len(store.commits), len(state.fetched)
    run = run_once(store, state, today=TODAY)
    assert run["finished"] and run["commits"] == 0
    assert len(store.commits) == commits and len(state.fetched) == fetched


@pytest.mark.parametrize("hours_old, republished", [(pipeline.LISTING_HOURS - 1.5, False), (pipeline.LISTING_HOURS - 0.5, True)])
def test_a_run_republishes_the_listing_from_an_hour_before_it_falls_due(tmp_path, hours_old, republished):
    store, state = local_store(tmp_path), beige()
    run_once(store, state, today=TODAY)
    m = store.read_manifest()
    m["listing"]["at"] = stamp(hours_old * 60)
    (store.root / MANIFEST).write_text(json.dumps(m))
    commits = len(store.commits)
    run = run_once(store, state, today=TODAY)
    assert run["finished"] and (len(store.commits) > commits) == republished
    assert (store.read_manifest()["listing"]["at"] != m["listing"]["at"]) == republished


def test_a_run_that_fetches_claims_the_lease_before_its_first_fetch(tmp_path):
    fetched_at_commit = []

    class Recording(LocalStore):
        def commit(self, message):
            fetched_at_commit.append((message, len(state.fetched)))
            return super().commit(message)

    store = Recording(tmp_path / "hub", workdir=tmp_path, card=render)
    state = beige()
    run_once(store, state, today=TODAY)
    state.board.append("2026-04-15")
    before = len(state.fetched)
    run = run_once(store, state, today=TODAY)
    claims = [(message, n) for message, n in fetched_at_commit if "takes the writer lease" in message]
    assert claims[-1][1] == before, "the claim commit happens before the run fetches anything"
    assert run["commits"] == 2, "a run that fetches commits twice: the claim, then the result"


def test_a_scheduled_edition_once_published_is_noticed_by_the_probe_and_fetched_alone(tmp_path):
    store, state = local_store(tmp_path), beige(scheduled=["2026-04-15", "2026-06-03"])
    run_once(store, state, today=TODAY)
    m = store.read_manifest()
    assert m["listing"]["scheduled"] == ["2026-04-15", "2026-06-03"]
    state.scheduled.remove("2026-04-15")
    state.board.append("2026-04-15")
    head = ScriptedSource(state).head()
    assert head == {"newest": "2026-04-15", "published": 3}
    assert decide_both(m, head, "local") == (True, "landing page {'newest': '2026-03-04', 'published': 2} -> {'newest': '2026-04-15', 'published': 3}")
    state.fetched.clear()
    run = run_once(store, state, today=TODAY)
    assert state.fetched == ["2026-04-15"] and run["fetched"] == 1
    m = store.read_manifest()
    assert m["listing"]["scheduled"] == ["2026-06-03"] and m["listing"]["head"] == head and m["partitions"]["2026"]["editions"] == 3
    assert len(of(stored(store, "2026"), "2026-04-15")) == 13
    assert decide_both(m, head, "local") == (False, "up to date")
    assert verify(store)["problems"] == []


def test_a_changed_link_refetches_its_edition_and_rewrites_only_it(tmp_path):
    store, state = local_store(tmp_path), beige()
    run_once(store, state, today=TODAY)
    before = stored(store, "2025")
    commits = len(store.commits)
    state.fetched.clear()
    state.pdfs["2025-03-05"] = NEW_PDF
    run = run_once(store, state, today=TODAY)
    assert state.fetched == ["2025-03-05"] and run["fetched"] == 1 and run["unchanged"] == 0
    rows = stored(store, "2025")
    assert of(rows, "2025-01-15") == of(before, "2025-01-15")
    assert {row["pdf_url"] for row in of(rows, "2025-03-05").values()} == {NEW_PDF}
    assert parquet_uploads(store.commits[commits:]) == ["data/2025.parquet"]
    assert verify(store)["problems"] == []


def test_a_changed_sitemap_entry_refetches_its_month_and_a_fetch_that_finds_nothing_new_rewrites_nothing(tmp_path, monkeypatch):
    store, state = local_store(tmp_path), beige(months={"1983-05": {"ph": "2024-11-01"}})
    run_once(store, state, today=TODAY)
    before, sha = stored(store, "1983"), store.read_manifest()["partitions"]["1983"]["sha256"]
    commits = len(store.commits)
    shift_clock(monkeypatch, 1)
    state.fetched.clear()
    state.months["1983-05"] = {"ph": "2025-03-13"}
    run = run_once(store, state, today=TODAY)
    assert state.fetched == ["1983-05"] and run["fetched"] == 1 and run["unchanged"] == 1
    assert run["commits"] == 2 and parquet_uploads(store.commits[commits:]) == [], "the claim and the result, without the partition"
    assert stored(store, "1983") == before, "the rows keep their fetched_at"
    assert store.read_manifest()["partitions"]["1983"]["sha256"] == sha
    assert verify(store)["problems"] == []


def test_rechecks_fall_due_by_age_and_one_that_finds_nothing_new_rewrites_nothing(tmp_path, monkeypatch):
    store, state = local_store(tmp_path), beige()
    run_once(store, state, today=TODAY)
    keys = ("1971", "1983", "2025", "2026")
    before = {key: stored(store, key) for key in keys}
    commits = len(store.commits)

    def after(days):
        shift_clock(monkeypatch, days * 24)
        state.fetched.clear()
        return run_once(store, state, today=TODAY)

    run = after(RECENT_RECHECK_DAYS + 1)
    assert state.fetched == ["2026-03-04", "2026-01-14"] and run["rechecked"] == 2 and run["unchanged"] == 2, "only the editions under a year old"
    assert set(state.stored_seen["2026-01-14"]) == {f"2026-01-14-{section}" for section in STANDARD}, "a recheck is given the stored rows, for its conditional requests"
    run = after(RECHECK_DAYS + 1)
    assert state.fetched == ["2026-03-04", "2026-01-14", "2025-03-05", "2025-01-15"], "then the older Board editions"
    run = after(MINNEAPOLIS_RECHECK_DAYS + 1)
    assert state.fetched == ALL_UNITS and run["rechecked"] == 6, "then the Minneapolis months"
    assert {key: stored(store, key) for key in keys} == before, "rows found unchanged keep their fetched_at"
    assert parquet_uploads(store.commits[commits:]) == []
    assert verify(store)["problems"] == []


def test_an_edited_page_is_rewritten_at_its_recheck(tmp_path, monkeypatch):
    store, state = local_store(tmp_path), beige()
    run_once(store, state, today=TODAY)
    before = stored(store, "2026")
    commits = len(store.commits)
    state.revisions["2026-01-14"] = 1
    state.fetched.clear()
    run_once(store, state, today=TODAY)
    assert state.fetched == [], "an edit that changes no list waits for the recheck"
    shift_clock(monkeypatch, (RECENT_RECHECK_DAYS + 1) * 24)
    run = run_once(store, state, today=TODAY)
    assert run["rechecked"] == 2 and run["unchanged"] == 1
    rows = stored(store, "2026")
    summary = rows["2026-01-14-summary"]
    assert (summary["text"], summary["source_modified"]) == ("summary of 2026-01-14, revision 1", "modified 1")
    assert summary["fetched_at"] > before["2026-01-14-summary"]["fetched_at"]
    assert of(rows, "2026-03-04") == of(before, "2026-03-04")
    assert parquet_uploads(store.commits[commits:]) == ["data/2026.parquet"]
    assert verify(store)["problems"] == []


def test_rechecks_beyond_the_cap_wait_for_a_later_run_the_oldest_check_first(tmp_path, monkeypatch):
    store, state = local_store(tmp_path), beige()
    run_once(store, state, today=TODAY)
    m = store.read_manifest()
    m["partitions"]["2026"]["units"]["2026-01-14"]["checked"] = stamp(24 * 60)
    store.stage_manifest(m)
    store.commit("an older check")
    shift_clock(monkeypatch, (RECENT_RECHECK_DAYS + 1) * 24)
    state.fetched.clear()
    run = run_once(store, state, today=TODAY, rechecks=1)
    assert state.fetched == ["2026-01-14"] and run["rechecked"] == 1 and run["rechecks_deferred"] == 1
    assert store.read_manifest()["partitions"]["2026"]["complete"]
    state.fetched.clear()
    run = run_once(store, state, today=TODAY, rechecks=1)
    assert state.fetched == ["2026-03-04"] and run["rechecks_deferred"] == 0


def test_a_recheck_that_fails_keeps_the_stored_rows_and_is_tried_again(tmp_path, monkeypatch):
    store, state = local_store(tmp_path), beige()
    run_once(store, state, today=TODAY)
    before = stored(store, "2026")
    shift_clock(monkeypatch, (RECENT_RECHECK_DAYS + 1) * 24)
    state.fail = {"2026-01-14"}
    run = run_once(store, state, today=TODAY)
    assert run["failed"] == 1 and stored(store, "2026") == before
    m = store.read_manifest()
    assert m["partitions"]["2026"]["complete"] and m["failures"]["2026-01-14"]["attempts"] == 1
    assert verify(store)["problems"] == []
    state.fail.clear()
    state.fetched.clear()
    run_once(store, state, today=TODAY)
    assert state.fetched == ["2026-01-14"] and store.read_manifest()["failures"] == {}


def test_an_edition_no_list_names_is_removed_once_its_page_is_gone(tmp_path):
    store, state = local_store(tmp_path), beige()
    run_once(store, state, today=TODAY)
    state.fetched.clear()
    state.board.remove("2025-01-15")
    run = run_once(store, state, today=TODAY)
    assert run["removed"] == 1 and state.fetched == [] and state.exists_asked == ["2025-01-15"]
    assert {row["edition"] for row in stored(store, "2025").values()} == {"2025-03-05"}
    entry = store.read_manifest()["partitions"]["2025"]
    assert entry["editions"] == entry["listed"] == 1 and entry["complete"] and entry["unlisted"] == []
    assert verify(store)["problems"] == []


def test_an_edition_no_list_names_is_kept_while_its_page_answers(tmp_path):
    store, state = local_store(tmp_path), beige()
    run_once(store, state, today=TODAY)
    state.board.remove("2025-01-15")
    state.exists = {"2025-01-15"}
    run = run_once(store, state, today=TODAY)
    assert run["removed"] == 0 and run["unlisted_kept"] == 1
    assert {row["edition"] for row in stored(store, "2025").values()} == {"2025-01-15", "2025-03-05"}
    entry = store.read_manifest()["partitions"]["2025"]
    assert entry["unlisted"] == ["2025-01-15"] and entry["editions"] == 2 and entry["listed"] == 1 and entry["complete"]
    assert verify(store)["problems"] == []
    commits = len(store.commits)
    rerun = run_once(store, state, today=TODAY)
    assert rerun["commits"] == 0 and len(store.commits) == commits, "a kept edition does not make every run write"


def test_a_year_whose_every_edition_is_gone_is_emptied(tmp_path):
    store, state = local_store(tmp_path), beige()
    run_once(store, state, today=TODAY)
    state.early.remove("1983-05-18")
    run = run_once(store, state, today=TODAY)
    assert run["removed"] == 1 and stored(store, "1983") == {}
    m = store.read_manifest()
    assert "1983" not in m["listing"]["partitions"] and m["partitions"]["1983"]["rows"] == 0
    assert verify(store)["problems"] == []


def test_a_listing_that_lacks_most_of_a_year_removes_nothing(tmp_path):
    days = ["2024-01-17", "2024-03-06", "2024-04-17", "2024-05-29", "2024-07-17", "2024-09-04", "2024-10-23", "2024-11-27"]
    store, state = local_store(tmp_path), beige(board=BOARD + days)
    run_once(store, state, today=TODAY)
    state.board = BOARD + days[:3]
    run = run_once(store, state, today=TODAY)
    assert run["suspect_listings"] == 1 and run["removed"] == 0 and state.exists_asked == []
    assert len({row["edition"] for row in stored(store, "2024").values()}) == 8


def test_a_note_that_a_report_is_not_available_is_a_row_without_text_the_manifest_and_card_name(tmp_path):
    store, state = local_store(tmp_path), beige(early=EARLY + ["1971-01-12"], notes={"1971-01-12-boston"})
    run_once(store, state, today=TODAY)
    entry = store.read_manifest()["partitions"]["1971"]
    assert entry["gaps"] == ["1971-01-12-boston", "1971-06-02-atlanta"] and entry["unavailable"] == ["1971-01-12-boston"]
    row = stored(store, "1971")["1971-01-12-boston"]
    assert row["text"] is None and row["source"] == "minneapolisfed.org" and row["url"] == "https://www.minneapolisfed.org/beige-book-reports/1971/1971-01-bo"
    assert verify(store)["problems"] == [], "a note is possible in a month with one edition"
    card = store.read_text(CARD)
    assert "- 1 section has no text because the Minneapolis page holds a note that the report is not available" in card
    assert "  - January 12, 1971: boston" in card and "  - June 2, 1971: atlanta" in card


def test_a_board_page_that_holds_another_editions_report_is_a_row_without_text_the_card_names(tmp_path, monkeypatch):
    monkeypatch.setattr(board, "ANOTHER_REPORT", {("2026-01-14", "summary"): "2025-03-05"})
    store, state = local_store(tmp_path), beige(notes={"2026-01-14-summary"}, pdfs={"2026-01-14": "https://www.federalreserve.gov/monetarypolicy/files/BeigeBook_20260114.pdf"})
    run_once(store, state, today=TODAY)
    entry = store.read_manifest()["partitions"]["2026"]
    assert entry["gaps"] == ["2026-01-14-summary"] and entry["unavailable"] == [], "only a Minneapolis page holds a note that the report is not available"
    row = stored(store, "2026")["2026-01-14-summary"]
    assert row["text"] is None and row["source"] == "federalreserve.gov" and row["url"] == board_url("2026-01-14")
    assert verify(store)["problems"] == []
    gaps = store.read_text(CARD).partition("## Known gaps")[2].partition("## License")[0]
    assert "- 1 section has no text because the page holds another edition's report under this edition's heading" in gaps
    assert "  - January 14, 2026: summary, whose page holds the summary of March 5, 2025\n" in gaps
    assert "- 1 section has no text and no page." in gaps and "holds a note" not in gaps


def test_failures_are_retried_then_exhausted_then_retried_daily(tmp_path, monkeypatch):
    store, state = local_store(tmp_path), beige(fail={"2025-03-05"})
    for attempt in range(1, MAX_ATTEMPTS + 1):
        run_once(store, state, today=TODAY)
        m = store.read_manifest()
        assert m["failures"]["2025-03-05"]["attempts"] == attempt
        assert m["partitions"]["2025"]["complete"] == (attempt == MAX_ATTEMPTS), "a unit with attempts left keeps its year open"
    entry = m["partitions"]["2025"]
    assert entry["editions"] == 1 and entry["failed"] == ["2025-03-05"] and entry["listed"] == 2
    assert verify(store)["problems"] == []
    state.fetched.clear()
    run_once(store, state, today=TODAY)
    assert state.fetched == [], "an exhausted unit is left alone until its retry is due"
    shift_clock(monkeypatch, pipeline.RETRY_AFTER_HOURS + 1)
    run_once(store, state, today=TODAY)
    assert state.fetched == ["2025-03-05"] and store.read_manifest()["failures"]["2025-03-05"]["attempts"] == MAX_ATTEMPTS + 1
    monkeypatch.undo()
    state.fetched.clear()
    state.pdfs["2025-03-05"] = NEW_PDF
    run_once(store, state, today=TODAY)
    assert state.fetched == ["2025-03-05"] and store.read_manifest()["failures"]["2025-03-05"]["attempts"] == 1, "a changed unit starts its attempts over"
    state.fail.clear()
    run_once(store, state, today=TODAY)
    m = store.read_manifest()
    assert m["failures"] == {} and m["partitions"]["2025"]["editions"] == 2 and m["partitions"]["2025"]["failed"] == []


def test_a_fatal_error_keeps_the_fetched_work_and_backs_the_probe_off(tmp_path):
    store, state = local_store(tmp_path), beige(fatal={"2025-01-15": Blocked("bot challenge at www.federalreserve.gov")})
    run = run_once(store, state, today=TODAY)
    assert run["stopped"].startswith("Blocked") and not run["finished"]
    assert set(of(stored(store, "2025"), "2025-03-05")) and not of(stored(store, "2025"), "2025-01-15")
    assert len(stored(store, "2026")) == 26, "work done before the stop is committed"
    m = store.read_manifest()
    assert m["failures"] == {}, "a fatal error is not held against the unit"
    assert m["runs"][-1]["stopped"].startswith("Blocked") and m["listing"] is None
    needed, reason = decide_both(m, ScriptedSource(state).head(), "local")
    assert not needed and reason.startswith("backing off 15 minutes")


def test_a_run_cut_by_the_budget_resumes_without_refetching(tmp_path):
    store, state = local_store(tmp_path), beige(stop_after=2)
    run = run_once(store, state, today=TODAY)
    assert run["stopped"] == "budget" and state.fetched == ["2026-03-04", "2026-01-14"]
    m = store.read_manifest()
    assert set(m["partitions"]) == {"2026"} and m["listing"] is None, "only a finished run publishes the listing"
    assert decide_both(m, ScriptedSource(state).head(), "local")[0]
    state.stop_after = None
    state.fetched.clear()
    run = run_once(store, state, today=TODAY)
    assert run["finished"] and state.fetched == ALL_UNITS[2:]
    assert verify(store)["problems"] == []


def test_only_and_max_units_bound_a_smoke_run(tmp_path):
    store, state = local_store(tmp_path), beige()
    run = run_once(store, state, today=TODAY, only=frozenset({"2025"}), max_units=1)
    assert state.fetched == ["2025-03-05"] and run["finished"]
    m = store.read_manifest()
    assert set(m["partitions"]) == {"2025"} and not m["partitions"]["2025"]["complete"] and m["listing"] is None


def test_refetch_fetches_every_unit_again_and_keeps_the_rows_that_did_not_change(tmp_path, monkeypatch):
    store, state = local_store(tmp_path), beige()
    run_once(store, state, today=TODAY)
    before = {key: stored(store, key) for key in ("1971", "2026")}
    shift_clock(monkeypatch, 1)
    state.fetched.clear()
    state.stored_seen.clear()
    run = run_once(store, state, today=TODAY, refetch=True)
    assert state.fetched == ALL_UNITS and run["unchanged"] == 6 and run["rechecked"] == 0
    assert state.stored_seen and all(seen == {} for seen in state.stored_seen.values()), "a refetch makes no conditional request, which would return the old parser's rows"
    assert {key: stored(store, key) for key in before} == before


@pytest.mark.parametrize("by, minutes_ago, deferred", [("github-actions", 5, True), ("github-actions", 60, False), ("local", 5, False)])
def test_another_writers_fresh_lease_defers_the_run(tmp_path, by, minutes_ago, deferred):
    store, state = local_store(tmp_path), beige()
    run_once(store, state, today=TODAY)
    m = store.read_manifest()
    m["writer"] = {"by": by, "at": stamp(minutes_ago)}
    store.stage_manifest(m)
    store.commit("someone else wrote")
    commits = len(store.commits)
    state.board.append("2026-04-15")
    state.fetched.clear()
    run = run_once(store, state, today=TODAY)
    assert (run["stopped"] == "deferred") == deferred
    assert (state.fetched == []) == deferred and (len(store.commits) == commits) == deferred


@pytest.mark.parametrize("refused, fetched", [(1, []), (2, ALL_UNITS)])
def test_a_superseded_store_stops_the_run_and_commits_nothing_more(tmp_path, refused, fetched):
    """Another writer's commit lands first: at the claim, as when two runs start together, the run stops before it fetches; later, its result is refused."""
    class Refusing(LocalStore):
        calls = 0

        def commit(self, message):
            self.calls += 1
            if self.calls == refused:
                raise Superseded("another writer committed")
            return super().commit(message)

    store = Refusing(tmp_path / "hub", workdir=tmp_path, card=render)
    state = beige()
    run = run_once(store, state, today=TODAY)
    assert run["stopped"] == "superseded" and not run["finished"] and state.fetched == fetched
    assert len(store.commits) == refused - 1 and store.list_files("data/") == []


def test_order_puts_changed_complete_partitions_before_the_backfill():
    def year(key):
        return Partition(key, {key: Unit(key, "board", (Edition(key),), "v1")})

    partitions = {key: year(key) for key in ("2020", "2021", "2022", "2023", "2024", "2025")}
    manifest = {"failures": {"2020": {"partition": "2020", "attempts": MAX_ATTEMPTS, "at": stamp(25 * 60)}}, "partitions": {
        "2020": {"complete": True, "fingerprint": partitions["2020"].fingerprint},
        "2021": {"complete": True, "fingerprint": "stale"},
        "2024": {"complete": True, "fingerprint": partitions["2024"].fingerprint},
        "2025": {"complete": False},
    }}
    assert [p.key for p in order(manifest, partitions)] == ["2021", "2020", "2025", "2023", "2022", "2024"]


def test_decide():
    head = {"newest": "2026-09-02", "published": 6}
    listing = {"head": head, "at": stamp(10), "partitions": {"2026": 6}}
    done = {"partitions": {"2026": {"complete": True}}, "listing": listing, "runs": [{"stopped": None, "ended": stamp(10)}], "writer": {"by": "local", "at": stamp(10)}}
    assert decide_both(None, head, "local") == (True, "no manifest yet")
    assert decide_both(done, head, "local") == (False, "up to date")
    assert decide_both(done, head, "github-actions")[1].startswith("deferred")
    assert decide_both(dict(done, writer={"by": "local", "at": stamp(50)}), head, "github-actions") == (False, "up to date")
    assert decide_both(dict(done, listing=None), head, "local") == (True, "never listed")
    assert decide_both(dict(done, partitions={"2026": {"complete": False}}), head, "local") == (True, "1 partitions incomplete: 2026")
    assert decide_both(done, dict(head, newest="2026-10-14", published=7), "local") == (True, "landing page {'newest': '2026-09-02', 'published': 6} -> {'newest': '2026-10-14', 'published': 7}")
    assert decide_both(done, dict(head, published=5), "local")[0], "a link that goes away is a change too"
    assert decide_both(dict(done, listing=dict(listing, at=stamp(pipeline.LISTING_HOURS * 60 + 1))), head, "local")[1].startswith("last full listing")
    assert decide_both(dict(done, listing=dict(listing, at=stamp(pipeline.LISTING_HOURS * 60 - 1))), head, "local") == (False, "up to date")
    failed = [{"stopped": "Blocked: x", "ended": stamp(5)}]
    assert decide_both(dict(done, runs=failed), head, "local")[1] == "backing off 15 minutes after a failed run: Blocked"
    assert decide_both(dict(done, runs=failed * 3), head, "local")[1].startswith("backing off 60 minutes")
    assert decide_both(dict(done, runs=failed * 2 + [{"stopped": "budget", "ended": stamp(5)}]), head, "local") == (False, "up to date")
    assert decide_both(dict(done, runs=[{"stopped": "budget", "ended": stamp(9)}] + failed), head, "local")[1].startswith("backing off 15 minutes")
    assert decide_both(dict(done, runs=[{"stopped": "Blocked: x", "ended": stamp(20)}]), head, "local") == (False, "up to date")
