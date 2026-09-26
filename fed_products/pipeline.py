"""The sync loop.

A run reads every list (beige_book.BeigeBookSource.listing): the Board's Beige Book pages, its FOMC historical pages and the Minneapolis sitemap, 85 pages on September 25, 2026, every one of which must answer and parse. The editions they name are grouped into units, a Board edition or a Minneapolis month (beige_book.units_of), and the units into partitions, a year each. For each partition the run loads what the Hub holds, fetches the units that are new, whose version changed or whose recheck is due, removes editions no list names any more, and stages the partition with the manifest. A fetch that returns what is stored keeps the stored rows, and a partition in which nothing changed is not staged again. Staged work is committed every checkpoint_seconds and at the end, so a run that dies loses at most one interval. The Parquet files hold the rows; the manifest records, per unit, the version it was fetched at and when it was last checked.

One writer at a time. The manifest names the last writer and when it wrote (the lease). A run defers while another writer's lease is fresh, and commits before it works on its first partition, which claims the lease. Every commit names its parent (store.HubStore.commit), so when two writers race, the second commit is refused and that run stops (Superseded) before fetching.
"""

import copy
import hashlib
import logging
import os
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from .beige_book import REPLACEMENT_CHARACTER, comparable, unavailable, units_of
from .board import LANDING, era_of
from .http import Blocked, QuotaExhausted
from .sections import rank
from .store import Superseded

log = logging.getLogger("fed_products")

MANIFEST_VERSION = 1
MAX_ATTEMPTS = 3
# A unit that failed MAX_ATTEMPTS times is left alone this long, then tried again.
RETRY_AFTER_HOURS = 24
# The probe asks for a sync once the last full listing is this old, so every list is reread even when the landing page has not changed.
LISTING_HOURS = 24
LEASE_MINUTES = 45
RUNS_KEPT = 20
# At most this many units are rechecked in one run, in partition order and oldest check first within a partition, so the rechecks that fall due together after a backfill spread over several runs instead of one long one.
RECHECKS_PER_RUN = 60
# A listing that would remove more than this share of a stored partition's editions is treated as an upstream glitch, not a deletion.
MAX_REMOVED_SHARE = 0.5
MIN_REMOVED_GUARD = 2
# Stop the run rather than record a failure against the unit: these say the source or the Hub is refusing, not that one unit is bad.
FATAL = (Blocked, QuotaExhausted, Superseded)
# Runs that stopped for these reasons did their job; any other stop is a failed run, and the probe backs off after it.
CLEAN_STOPS = (None, "budget")
STAMP = "%Y-%m-%dT%H:%M:%SZ"
# The card's front-matter key for probe_state(), and the shape it has; a card with another version is ignored and the probe reads the manifest.
PROBE_KEY = "fed_products_probe"
PROBE_STATE_VERSION = 1


def utcnow():
    return datetime.now(timezone.utc).strftime(STAMP)


def later(stamp, hours):
    return (datetime.strptime(stamp, STAMP) + timedelta(hours=hours)).strftime(STAMP)


def age_hours(stamp):
    """Hours since one of this pipeline's own utcnow() stamps; infinite for a missing one."""
    if not stamp:
        return float("inf")
    then = datetime.strptime(stamp, STAMP).replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds() / 3600


def writer_identity():
    return "github-actions" if os.environ.get("GITHUB_ACTIONS") == "true" else "local"


@dataclass
class Partition:
    key: str
    units: dict

    @property
    def fingerprint(self):
        digest = hashlib.sha256()
        for uid in sorted(self.units):
            digest.update(f"{uid}\t{self.units[uid].version}\n".encode())
        return digest.hexdigest()

    @property
    def editions(self):
        return {edition.date: edition for unit in self.units.values() for edition in unit.editions}


@dataclass
class Context:
    """deadline is a time.monotonic() value. only (partition keys) and max_units (units fetched per partition) bound a smoke run; a capped partition stays incomplete and resumes on the next run. refetch fetches every unit of each partition the run reaches, as after a parser change. today dates the rechecks (default: today in UTC)."""

    store: object
    deadline: float
    checkpoint_seconds: float = 600.0
    only: frozenset | None = None
    max_units: int | None = None
    refetch: bool = False
    rechecks: int = RECHECKS_PER_RUN
    writer: str = field(default_factory=writer_identity)
    today: date = field(default_factory=lambda: datetime.now(timezone.utc).date())
    stats: dict = field(default_factory=lambda: defaultdict(int))
    pending: list = field(default_factory=list)
    last_commit: float = field(default_factory=time.monotonic)
    claimed: bool = False

    def out_of_time(self):
        return time.monotonic() > self.deadline


def new_manifest():
    return {"version": MANIFEST_VERSION, "source": LANDING, "partitions": {}, "failures": {}, "listing": None, "runs": [], "writer": None, "updated_at": None}


def other_writer(manifest, writer):
    """The lease of a different writer that wrote less than LEASE_MINUTES ago, else None."""
    lease = (manifest or {}).get("writer") or {}
    if lease.get("by") and lease["by"] != writer and age_hours(lease.get("at")) * 60 < LEASE_MINUTES:
        return lease
    return None


def backoff_minutes(runs):
    """0 after a clean run; after n failed runs in a row, 15 minutes doubled n-1 times, at most a day."""
    failed = 0
    for run in reversed(runs or []):
        if run.get("stopped") in CLEAN_STOPS:
            break
        failed += 1
    return min(24 * 60, 15 * 2 ** (failed - 1)) if failed else 0


def probe_state(manifest):
    """What decide() reads from the manifest. The card carries it in its front matter, so the probe gets it with the repo's metadata, an API call, instead of downloading manifest.json, which the Hub counts as a download. Failed runs keep only their exception's name, so no error text reaches the card's YAML."""
    if not manifest:
        return None
    failed = []
    for run in reversed(manifest.get("runs") or []):
        if run.get("stopped") in CLEAN_STOPS:
            break
        failed.insert(0, {"stopped": re.match(r"\w*", run.get("stopped") or "").group(0) or "failed", "ended": run.get("ended")})
    listing = manifest.get("listing")
    stored = manifest.get("partitions") or {}
    return {
        "version": PROBE_STATE_VERSION,
        "writer": manifest.get("writer"),
        "failed_runs": failed,
        "listing": {key: listing.get(key) for key in ("head", "at")} if listing else None,
        "incomplete": sorted(key for key in (listing or {}).get("partitions") or {} if not (stored.get(key) or {}).get("complete")),
    }


def decide(state, head, writer):
    """Whether a full sync is needed, from probe_state() (as the card carries it, or computed from the manifest) and the landing page's head (beige_book.head_of). Returns (needed, reason). A daily full sync also runs the rechecks that are due."""
    if not state:
        return True, "no manifest yet"
    holder = other_writer(state, writer)
    if holder:
        return False, f"deferred: {holder['by']} holds the lease (wrote at {holder['at']})"
    runs = state["failed_runs"]
    wait = backoff_minutes(runs)
    if wait and age_hours(runs[-1].get("ended")) * 60 < wait:
        return False, f"backing off {wait} minutes after a failed run: {runs[-1].get('stopped')}"
    listing = state["listing"]
    if not listing:
        return True, "never listed"
    incomplete = state["incomplete"]
    if incomplete:
        return True, f"{len(incomplete)} partitions incomplete: {', '.join(incomplete[:5])}"
    if head != listing.get("head"):
        return True, f"landing page {listing.get('head')} -> {head}"
    if age_hours(listing.get("at")) >= LISTING_HOURS:
        return True, f"last full listing at {listing.get('at')}"
    return False, "up to date"


def recheck_due(unit, state, now, today):
    return later(state["checked"], unit.recheck_days(today) * 24) <= now


def retry_due(manifest, key, now):
    return any(f.get("partition") == key and f["attempts"] >= MAX_ATTEMPTS and later(f["at"], RETRY_AFTER_HOURS) <= now for f in manifest["failures"].values())


def rechecks_due(entry, partition, now, today):
    states = entry.get("units") or {}
    return [uid for uid, unit in partition.units.items() if uid in states and states[uid]["version"] == unit.version and recheck_due(unit, states[uid], now, today)]


def order(manifest, partitions):
    """Changed complete partitions first (a new edition reaches the Hub before a backfill resumes), then incomplete ones, then the rest; newest year first within each."""
    now = utcnow()

    def position(partition):
        entry = manifest["partitions"].get(partition.key) or {}
        if not entry.get("complete"):
            return 1
        return 0 if entry.get("fingerprint") != partition.fingerprint or retry_due(manifest, partition.key, now) else 2

    return sorted(sorted(partitions.values(), key=lambda p: p.key, reverse=True), key=position)


def flush(ctx, manifest, message=None):
    """Stamp the lease and commit everything staged, the manifest and card included."""
    manifest["writer"] = {"by": ctx.writer, "at": utcnow()}
    ctx.store.stage_manifest(manifest)
    text = message or "; ".join(ctx.pending) or "manifest"
    if ctx.store.commit(text if len(text) <= 500 else text[:497] + "..."):
        ctx.stats["commits"] += 1
    ctx.pending = []
    ctx.claimed = True
    ctx.last_commit = time.monotonic()


def listing_record(listing, partitions, at):
    eras = defaultdict(list)
    for edition in listing.editions.values():
        if edition.board_hosted:
            eras[str(era_of(edition.html_url))].append(edition.date)
    return {"head": listing.head, "editions": len(listing.editions), "board_hosted": sum(len(days) for days in eras.values()),
            "first": min(listing.editions, default=None), "newest": max(listing.editions, default=None), "scheduled": listing.scheduled,
            "eras": {era: {"first": min(days), "last": max(days), "editions": len(days)} for era, days in sorted(eras.items())},
            "pages": listing.pages, "at": at, "partitions": {key: len(partitions[key].editions) for key in sorted(partitions)}}


def sync(ctx, source):
    """One run. source reads every list (listing), fetches a unit (fetch) and confirms a stored row's page still answers (exists); see beige_book.BeigeBookSource. Returns the run record."""
    started = utcnow()
    base = ctx.store.read_manifest() or new_manifest()
    holder = other_writer(base, ctx.writer)
    if holder:
        log.info("deferring to %s, which wrote at %s", holder["by"], holder["at"])
        return {"started": started, "ended": utcnow(), "writer": ctx.writer, "finished": False, "stopped": "deferred", "holder": holder, "commits": 0}
    manifest = copy.deepcopy(base)
    manifest.setdefault("partitions", {})
    manifest.setdefault("failures", {})
    manifest["version"] = MANIFEST_VERSION
    finished, reason, record = True, None, None
    try:
        listing = source.listing()
        partitions = {key: Partition(key, {unit.id: unit for unit in units}) for key, units in units_of(listing).items()}
        record = listing_record(listing, partitions, started)
        # What this run saw, for the card; the probe reads only the published listing.
        manifest["seen"] = {key: record[key] for key in ("editions", "board_hosted", "first", "newest", "scheduled", "eras", "at")}
        for key in manifest["partitions"]:
            partitions.setdefault(key, Partition(key, {}))
        for partition in order(manifest, partitions):
            if ctx.only is not None and partition.key not in ctx.only:
                continue
            if ctx.out_of_time() or not sync_partition(ctx, source, manifest, partition):
                finished, reason = False, "budget"
                break
    except Superseded as error:
        finished, reason = False, "superseded"
        log.warning("stopped: %s", error)
    except FATAL as error:
        finished, reason = False, f"{type(error).__name__}: {error}"[:300]
        log.warning("stopped: %s", reason)
    except Exception as error:  # noqa: BLE001 - recorded in the run record; the probe backs off
        finished, reason = False, f"{type(error).__name__}: {error}"[:300]
        log.exception("run failed")
    run = {"started": started, "ended": utcnow(), "writer": ctx.writer, "finished": finished, "stopped": reason}
    run.update({key: ctx.stats[key] for key in ("fetched", "unchanged", "rechecked", "rechecks_deferred", "failed", "removed", "unlisted_kept", "suspect_listings")})
    if reason == "superseded":
        return dict(run, commits=ctx.stats["commits"])
    runs = base.get("runs") or []
    # The listing is what the probe compares against, so it is published only by a run that brought every partition up to date with it; until then the probe keeps asking for a run. A run that finishes within an hour of the listing falling due republishes it, so the next probe does not ask for another full sync.
    changed = False
    if finished and ctx.only is None and record:
        old = base.get("listing") or {}
        changed = any(record[key] != old.get(key) for key in ("head", "editions", "scheduled", "partitions")) or age_hours(old.get("at")) > LISTING_HOURS - 1
        manifest["listing"] = record
    if ctx.store.staged or ctx.stats["commits"] or changed or reason not in CLEAN_STOPS or not runs or age_hours(runs[-1].get("ended")) > 23:
        manifest["runs"] = runs[-(RUNS_KEPT - 1):] + [dict(run, commits=ctx.stats["commits"] + 1)]
        try:
            flush(ctx, manifest)
        except Superseded as error:
            run.update(finished=False, stopped="superseded")
            log.warning("stopped: %s", error)
        except Exception as error:  # noqa: BLE001 - the Hub refused the final commit
            run.update(finished=False, stopped=f"{type(error).__name__}: {error}"[:300])
            log.exception("final commit failed")
    return dict(run, commits=ctx.stats["commits"])


def same_rows(old, new):
    """Whether a fetch returned exactly the stored rows, apart from when they were fetched."""
    return set(old) == set(new) and all(comparable(old[uid]) == comparable(new[uid]) for uid in new)


def sync_partition(ctx, source, manifest, partition):
    """Bring one partition up to date. Returns False when the budget ran out first."""
    entry = manifest["partitions"].get(partition.key) or {}
    failures = manifest["failures"]
    now = utcnow()
    due = rechecks_due(entry, partition, now, ctx.today)
    if entry.get("complete") and entry.get("fingerprint") == partition.fingerprint and not ctx.refetch and not retry_due(manifest, partition.key, now) and not due:
        return True
    if not ctx.claimed:
        flush(ctx, manifest, f"{ctx.writer} takes the writer lease")
    stored = {row["id"]: row for row in ctx.store.read_partition(partition.key)} if entry else {}
    by_edition = defaultdict(dict)
    for uid, row in stored.items():
        by_edition[row["edition"]][uid] = row
    listed = partition.editions
    missing = sorted(day for day in by_edition if day not in listed)
    if len(missing) > max(MIN_REMOVED_GUARD, MAX_REMOVED_SHARE * len(by_edition)):
        log.warning("%s: the lists lack %d of %d stored editions; skipped as a suspect listing", partition.key, len(missing), len(by_edition))
        ctx.stats["suspect_listings"] += 1
        return True
    counts = {"fetched": 0, "unchanged": 0, "rechecked": 0, "rechecks_deferred": 0, "failed": 0, "removed": 0, "unlisted_kept": 0}
    kept = []
    for day in missing:
        rows = sorted(by_edition[day].values(), key=lambda row: rank(row["section"]))
        served = next((row for row in rows if row.get("url")), None)
        if served and source.exists(served):
            # A page that still answers stays as stored, and verify --live names the edition, so a list that dropped it by mistake does not delete it.
            kept.append(day)
            counts["unlisted_kept"] += 1
        else:
            for uid in by_edition.pop(day):
                del stored[uid]
            counts["removed"] += 1
    for uid in [uid for uid, failure in failures.items() if failure.get("partition") == partition.key and uid not in partition.units]:
        del failures[uid]
    states = {uid: state for uid, state in (entry.get("units") or {}).items() if uid in partition.units}

    todo, rechecks = [], []
    for uid in sorted(partition.units, reverse=True):
        unit, state, failure = partition.units[uid], states.get(uid), failures.get(uid)
        present = all(edition.date in by_edition for edition in unit.editions)
        if not ctx.refetch and state and present and state["version"] == unit.version:
            if recheck_due(unit, state, now, ctx.today):
                rechecks.append(unit)
            continue
        if not ctx.refetch and failure and failure.get("version") == unit.version and failure["attempts"] >= MAX_ATTEMPTS and later(failure["at"], RETRY_AFTER_HOURS) > now:
            continue
        todo.append(unit)
    rechecks.sort(key=lambda unit: states[unit.id]["checked"])
    allowed = max(0, ctx.rechecks - ctx.stats["rechecked"])
    counts["rechecks_deferred"] = max(0, len(rechecks) - allowed)
    rechecks = rechecks[:allowed]
    work = todo if ctx.max_units is None else todo[:ctx.max_units]
    if not work and not rechecks and not counts["removed"] and entry.get("complete") and entry.get("fingerprint") == partition.fingerprint and kept == entry.get("unlisted", []):
        _count(ctx, counts)
        return True

    done, finished = set(), True
    for unit in work + rechecks:
        if ctx.out_of_time():
            finished = False
            break
        old = {uid: row for edition in unit.editions for uid, row in by_edition.get(edition.date, {}).items()}
        try:
            # A refetch is for a parser change, so it asks for every page whole: a conditional request would get 304 and the rows the old parser wrote.
            rows = source.fetch(unit, {} if ctx.refetch else old)
        except FATAL:
            _write(ctx, manifest, partition, stored, states, todo, done, counts, kept, final=False)
            raise
        except Exception as error:  # noqa: BLE001 - recorded per unit, retried on later runs
            previous = failures.get(unit.id) or {}
            attempts = previous.get("attempts", 0) + 1 if previous.get("version") == unit.version else 1
            failures[unit.id] = {"partition": partition.key, "version": unit.version, "attempts": attempts, "error": f"{type(error).__name__}: {error}"[:300], "at": utcnow()}
            counts["failed"] += 1
            log.info("%s failed (attempt %d): %s", unit.id, attempts, error)
        else:
            new = {row["id"]: row for row in rows}
            if same_rows(old, new):
                counts["unchanged"] += 1
            else:
                stamp = utcnow()
                for uid in old:
                    del stored[uid]
                for edition in unit.editions:
                    by_edition.pop(edition.date, None)
                for uid, row in new.items():
                    # A row the fetch returned unchanged keeps the fetched_at of the fetch that wrote it.
                    kept_row = old.get(uid)
                    row = kept_row if kept_row and comparable(kept_row) == comparable(row) else dict(row, fetched_at=stamp)
                    stored[uid] = row
                    by_edition[row["edition"]][uid] = row
            states[unit.id] = {"version": unit.version, "checked": utcnow()}
            failures.pop(unit.id, None)
            counts["fetched"] += 1
            if unit in rechecks:
                counts["rechecked"] += 1
        done.add(unit.id)
        if time.monotonic() - ctx.last_commit >= ctx.checkpoint_seconds:
            _write(ctx, manifest, partition, stored, states, todo, done, counts, kept, final=False)
    _write(ctx, manifest, partition, stored, states, todo, done, counts, kept, final=finished)
    return finished


def _count(ctx, counts):
    for key, value in counts.items():
        ctx.stats[key] += value
        counts[key] = 0


def _write(ctx, manifest, partition, stored, states, todo, done, counts, kept, final):
    failures = manifest["failures"]
    still_open = [unit for unit in todo if unit.id not in done or (unit.id in failures and failures[unit.id]["attempts"] < MAX_ATTEMPTS)]
    complete = final and not still_open
    rows = list(stored.values())
    entry = manifest["partitions"].setdefault(partition.key, {})
    # Otherwise the file on the Hub, or the one an earlier call staged, already holds these rows.
    if counts["fetched"] > counts["unchanged"] or counts["removed"] or not entry.get("sha256"):
        entry.update(ctx.store.stage_partition(partition.key, rows))
    days = sorted({row["edition"] for row in rows})
    entry.update({
        "editions": len(days),
        "first": days[0] if days else None,
        "last": days[-1] if days else None,
        "listed": len(partition.editions),
        "units": {uid: states[uid] for uid in sorted(states)},
        "failed": sorted(uid for uid, f in failures.items() if f.get("partition") == partition.key and f["attempts"] >= MAX_ATTEMPTS),
        "gaps": sorted(row["id"] for row in rows if row["text"] is None),
        "unavailable": sorted(row["id"] for row in rows if unavailable(row)),
        "sources": dict(sorted(Counter(row["source"] or "none" for row in rows if row["text"] is not None).items())),
        "replacement_character": sorted(row["id"] for row in rows if row["text"] and REPLACEMENT_CHARACTER in row["text"]),
        "unlisted": list(kept),
        "complete": complete,
        "fingerprint": partition.fingerprint if complete else None,
        "updated_at": utcnow(),
    })
    manifest["updated_at"] = entry["updated_at"]
    note = f"{partition.key}: {counts['fetched']} units fetched, {counts['failed']} failed, {counts['removed']} editions removed, {entry['rows']} rows" + ("" if complete else " (partial)")
    note += "".join(f", {counts[key]} {key.replace('_', ' ')}" for key in ("unchanged", "rechecked", "unlisted_kept") if counts[key])
    ctx.pending.append(note)
    log.info(note)
    _count(ctx, counts)
    if time.monotonic() - ctx.last_commit >= ctx.checkpoint_seconds:
        flush(ctx, manifest)
