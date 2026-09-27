"""The store: Parquet round trips with the dataset's schema, and the Hub commit fence against a fake Hub API."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pyarrow.parquet as pq
import pytest
from huggingface_hub.errors import HfHubHTTPError

from fed_products import store as store_module
from fed_products.beige_book import SCHEMA, Edition, make_row, partition_of
from fed_products.cli import read_probe_state
from fed_products.pipeline import PROBE_KEY, new_manifest, probe_state
from fed_products.store import HubStore, Superseded, git_blob_sha1, partition_path, read_parquet, write_parquet


def http_error(status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://huggingface.co/api/datasets/x/y/commit/main"))
    return HfHubHTTPError(f"{status} from the fake Hub", response=response)


class FakeApi:
    """The parts of HfApi that HubStore.commit uses. A commit on a parent that is not the head answers 412, as the Hub did when measured."""

    token = False

    def __init__(self):
        self.head, self.files, self.commits, self.attempts = "c0", {}, [], 0
        self.lose_next_response = False
        self.card_data = None

    def dataset_info(self, repo_id):
        return SimpleNamespace(sha=self.head, card_data=self.card_data)

    def create_commit(self, repo_id, operations, commit_message, repo_type, parent_commit):
        self.attempts += 1
        if parent_commit != self.head:
            raise http_error(412)
        for operation in operations:
            self.files[operation.path_in_repo] = Path(operation.path_or_fileobj).read_bytes()
        self.commits.append(commit_message)
        self.head = f"c{len(self.commits)}"
        if self.lose_next_response:
            self.lose_next_response = False
            raise http_error(502)
        return SimpleNamespace(oid=self.head)

    def get_paths_info(self, repo_id, paths, repo_type, revision):
        return [SimpleNamespace(path=path, blob_id=git_blob_sha1(self.files[path]), lfs=None) for path in paths if path in self.files]


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module.time, "sleep", lambda seconds: None)
    api = FakeApi()
    return HubStore("x/y", workdir=tmp_path, api=api, card=lambda manifest: f"card for {manifest}"), api


class UnreachableApi(FakeApi):
    def dataset_info(self, repo_id):
        raise httpx.ConnectError("planted: the Hub cannot be reached")


def test_a_hub_that_cannot_be_reached_leaves_no_scratch_directory(tmp_path):
    with pytest.raises(httpx.ConnectError):
        HubStore("x/y", workdir=tmp_path, api=UnreachableApi())
    assert list(tmp_path.iterdir()) == []


def test_a_commit_on_a_stale_parent_is_superseded_and_the_store_writes_nothing_more(hub):
    store, api = hub
    store.stage_manifest({"n": 1})
    assert store.commit("first") == "c1" and set(api.files) == {"manifest.json", "README.md"}
    assert api.files["README.md"] == b"card for {'n': 1}", "the card is rendered from the manifest staged with it"
    api.head = "someone-else"
    store.stage_manifest({"n": 2})
    with pytest.raises(Superseded):
        store.commit("second")
    attempts = api.attempts
    store.stage_manifest({"n": 3})
    with pytest.raises(Superseded):
        store.commit("third")
    assert api.attempts == attempts, "a superseded store does not ask the Hub again"
    assert api.commits == ["first"]


def test_a_retried_commit_that_had_landed_is_adopted_not_superseded(hub):
    store, api = hub
    api.lose_next_response = True
    store.stage_manifest({"n": 1})
    assert store.commit("first") == "c1"
    assert api.commits == ["first"] and store.revision == "c1" and store.superseded is None
    store.stage_manifest({"n": 2})
    assert store.commit("second") == "c2"


def test_a_412_whose_head_holds_a_different_manifest_is_superseded(hub):
    store, api = hub
    store.stage_manifest({"n": 1})
    real_create = api.create_commit

    def other_writer_first(*args, **kwargs):
        # Another writer's manifest lands between this store's attempts.
        api.create_commit = real_create
        api.files["manifest.json"] = b'{"n": "theirs"}\n'
        api.head = "theirs"
        raise http_error(502)

    api.create_commit = other_writer_first
    with pytest.raises(Superseded):
        store.commit("first")


def test_nothing_staged_is_no_commit(hub):
    store, api = hub
    assert store.commit("empty") is None and api.attempts == 0


def test_a_readme_check_answered_with_a_busy_page_is_retried(hub):
    """huggingface_hub parses the validate-yaml body before it checks the status, so a busy Hub raises JSONDecodeError before anything is uploaded; the Fed scheduled run of September 27, 2026 stopped on one."""
    store, api = hub
    real_create = api.create_commit

    def busy_first(*args, **kwargs):
        api.create_commit = real_create
        raise json.JSONDecodeError("Expecting value", "", 0)

    api.create_commit = busy_first
    store.stage_manifest({"n": 1})
    assert store.commit("first") == "c1" and api.commits == ["first"]


def test_a_dropped_connection_whose_commit_landed_is_adopted(hub):
    store, api = hub
    real_create = api.create_commit

    def lands_then_drops(*args, **kwargs):
        api.create_commit = real_create
        real_create(*args, **kwargs)
        raise httpx.ReadTimeout("planted: the response never arrived")

    api.create_commit = lands_then_drops
    store.stage_manifest({"n": 1})
    assert store.commit("first") == "c1"
    assert api.commits == ["first"] and store.superseded is None, "the retry adopted the landed commit rather than committing twice"


def test_invalid_card_metadata_is_raised_at_once(hub):
    store, api = hub

    def invalid(*args, **kwargs):
        api.attempts += 1
        raise ValueError("Invalid metadata in README.md.")

    api.create_commit = invalid
    store.stage_manifest({"n": 1})
    with pytest.raises(ValueError, match="Invalid metadata"):
        store.commit("first")
    assert api.attempts == 1, "JSONDecodeError is a ValueError, but a card the Hub rejects is not retried"


def test_git_blob_sha1_matches_git():
    assert git_blob_sha1(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert git_blob_sha1(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


def test_the_probe_reads_the_hub_cards_state_without_downloading_a_file(tmp_path, monkeypatch):
    state = probe_state(dict(new_manifest(), writer={"by": "local", "at": "2026-09-25T21:13:28Z"}))
    downloads = []
    monkeypatch.setattr(HubStore, "_download", lambda self, repo_path: downloads.append(repo_path))
    api = FakeApi()
    api.card_data = SimpleNamespace(to_dict=lambda: {"license": "other", PROBE_KEY: state})
    assert read_probe_state(HubStore("x/y", workdir=tmp_path, api=api)) == (state, "card") and downloads == []
    # A card rendered before it carried the state, or carrying another version of it: the probe falls back to the manifest, a file download.
    for data in ({"license": "other"}, {PROBE_KEY: dict(state, version=state["version"] + 1)}):
        downloads.clear()
        api.card_data = SimpleNamespace(to_dict=lambda: data)
        assert read_probe_state(HubStore("x/y", workdir=tmp_path, api=api)) == (None, "manifest") and downloads == ["manifest.json"]


@pytest.mark.parametrize("uid, key", [("1971-06-02-summary", "1971"), ("1971-06", "1971"), ("2026-09-02", "2026"), ("1983-05-18-special-report", "1983")])
def test_rows_and_units_go_to_their_year(uid, key):
    assert partition_of(uid) == key and partition_path(key) == f"data/{key}.parquet"


def test_rows_round_trip_sorted_by_id_in_the_dataset_schema(tmp_path):
    early = Edition("1971-01-12", pdf_url="https://www.federalreserve.gov/monetarypolicy/files/fomc19710112redbook.pdf")
    note = "https://www.minneapolisfed.org/beige-book-reports/1971/1971-01-bo"
    rows = [
        make_row(early, "summary", "Economic activity was flat.", "minneapolisfed.org", "https://www.minneapolisfed.org/beige-book-reports/1971/1971-01-su", "2025-03-13"),
        make_row(early, "boston", None, "minneapolisfed.org", note, None),
        make_row(Edition("1971-06-02"), "atlanta"),
    ]
    path = tmp_path / "1971.parquet"
    stats = write_parquet(rows, path)
    assert stats["rows"] == 3 and stats["text_rows"] == 1 and len(stats["sha256"]) == 64
    back = read_parquet(path)
    assert [row["id"] for row in back] == ["1971-01-12-boston", "1971-01-12-summary", "1971-06-02-atlanta"]
    assert back[0] == dict(rows[1], district=1) and back[2]["district"] == 6 and back[1]["district"] is None
    assert pq.read_schema(path).equals(SCHEMA, check_metadata=False)
    empty = write_parquet([], tmp_path / "empty.parquet")
    assert empty["rows"] == 0 and read_parquet(tmp_path / "empty.parquet") == []
    # The datasets library reads a file in batches the size of its first row group, and fails on one of 0 rows.
    assert pq.ParquetFile(tmp_path / "empty.parquet").metadata.num_row_groups == 0


def test_the_same_rows_write_the_same_bytes(tmp_path):
    rows = [make_row(Edition("2026-09-02", "https://www.federalreserve.gov/monetarypolicy/beigebook202608-summary.htm"), "summary", "Text.", "federalreserve.gov", "https://www.federalreserve.gov/monetarypolicy/beigebook202608-summary.htm", "Wed, 02 Sep 2026 18:00:00 GMT")]
    assert write_parquet(rows, tmp_path / "a.parquet")["sha256"] == write_parquet(list(reversed(rows)), tmp_path / "b.parquet")["sha256"]
