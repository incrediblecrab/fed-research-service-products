"""The CLI's contract with the workflow: exit codes, $GITHUB_OUTPUT keys, Trusted Publishing, and a workflow that calls only commands, options and outputs the CLI has. Also the workflow's inactivity job."""

import json
import os
import re
import subprocess
import time
from pathlib import Path

import httpx
import huggingface_hub
import pytest
import yaml
from huggingface_hub.errors import HfHubHTTPError

from fed_products import card, cli
from fed_products.beige_book import REPO_ID
from fed_products.http import QuotaExhausted, Unavailable
from fed_products.pipeline import Context
from fed_products.store import CARD, LocalStore
from conftest import ScriptedSource, local_store, run_once, scripted

WORKFLOWS = Path(__file__).parent.parent / ".github" / "workflows"
WORKFLOW = WORKFLOWS / "pipeline.yml"
# What each command writes to $GITHUB_OUTPUT. The probe and run tests check the commands against this, and the workflow test checks the workflow's if: expressions against it.
OUTPUTS = {"probe": {"needed"}, "run": {"commits", "more"}}
BOARD = ["2026-01-14", "2026-03-04"]


@pytest.fixture
def actions(tmp_path, monkeypatch):
    """A GitHub Actions environment with $GITHUB_OUTPUT as a file. HF_OIDC_RESOURCE starts unset and is restored afterwards, since the CLI sets it in os.environ."""
    output = tmp_path / "github_output"
    output.touch()
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("HF_OIDC_RESOURCE", "placeholder")
    monkeypatch.delenv("HF_OIDC_RESOURCE")
    return output


def outputs(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines())


def hub_error(status, message):
    return HfHubHTTPError(message, response=httpx.Response(status, request=httpx.Request("POST", "https://huggingface.co/oauth/token")))


def raising(error):
    def fail(*args, **kwargs):
        raise error
    return fail


def test_trusted_publishing_is_requested_only_in_actions_and_only_for_the_hub(actions, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(cli, "cmd_probe", lambda args: seen.append(os.environ.get("HF_OIDC_RESOURCE")) or 0)
    assert cli.main(["probe", "--repo", "someone/some-dataset"]) == 0
    monkeypatch.delenv("HF_OIDC_RESOURCE")
    cli.main(["probe"])
    monkeypatch.delenv("HF_OIDC_RESOURCE")
    cli.main(["probe", "--local", str(tmp_path)])
    monkeypatch.delenv("GITHUB_ACTIONS")
    cli.main(["probe", "--repo", "someone/some-dataset"])
    assert seen == ["datasets/someone/some-dataset", f"datasets/{REPO_ID}", None, None]


def test_run_without_a_trusted_publisher_fails_so_github_notifies_the_owner(actions, monkeypatch, capsys):
    monkeypatch.setattr(cli, "open_store", raising(hub_error(400, f"400 Client Error: Bad Request for url: https://huggingface.co/oauth/token ({cli.NO_PUBLISHER} for datasets/x/y)")))
    assert cli.main(["run"]) == 1
    assert outputs(actions) == {"commits": "0", "more": "false"}
    assert capsys.readouterr().out.startswith(f"::error::{cli.NO_PUBLISHER} for {REPO_ID}, so nothing was written.")


def test_run_with_any_other_hub_refusal_fails(actions, monkeypatch):
    monkeypatch.setattr(cli, "open_store", raising(hub_error(401, "401 Client Error: Unauthorized")))
    with pytest.raises(HfHubHTTPError):
        cli.main(["run"])
    assert outputs(actions) == {}


def test_run_passes_its_options_to_the_sync(actions, monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(cli, "sync", lambda ctx, source: seen.append((ctx, source)) or {"stopped": None, "finished": True, "commits": 0, "fetched": 0})
    assert cli.main(["run", "--local", str(tmp_path / "hub"), "--partitions", "1971, 2026,", "--max-units", "2", "--refetch", "--budget-minutes", "1"]) == 0
    ((ctx, source),) = seen
    assert ctx.only == {"1971", "2026"} and ctx.max_units == 2 and ctx.refetch and isinstance(source, cli.BeigeBookSource)
    assert 0 < ctx.deadline - time.monotonic() <= 60


@pytest.mark.parametrize("stopped, code", [(None, 0), ("budget", 0), ("deferred", 0), ("superseded", 0), ("Blocked: HTTP 403 from www.federalreserve.gov/x.htm", 1), ("RuntimeError: boom", 1)])
def test_run_exit_codes(actions, monkeypatch, tmp_path, stopped, code):
    monkeypatch.setattr(cli, "sync", lambda ctx, source: {"stopped": stopped, "finished": stopped is None, "commits": 3, "fetched": 1})
    assert cli.main(["run", "--local", str(tmp_path / "hub")]) == code
    assert outputs(actions)["commits"] == "3" and set(outputs(actions)) == OUTPUTS["run"]


# sync() leaves fetched out of a deferred run's record.
@pytest.mark.parametrize("stopped, fetched, more", [("budget", 4, "true"), ("budget", 0, "false"), (None, 4, "false"), ("superseded", 4, "false"), ("RuntimeError: boom", 4, "false"), ("deferred", None, "false")])
def test_only_a_run_that_ran_out_of_budget_while_fetching_asks_for_the_next_run(actions, monkeypatch, tmp_path, stopped, fetched, more):
    run = {"stopped": stopped, "finished": stopped is None, "commits": 1} | ({} if fetched is None else {"fetched": fetched})
    monkeypatch.setattr(cli, "sync", lambda ctx, source: run)
    cli.main(["run", "--local", str(tmp_path / "hub")])
    assert outputs(actions)["more"] == more


def test_more_follows_the_record_the_real_sync_returns(actions, monkeypatch, tmp_path):
    state = scripted(board=["2025-01-15", "2025-03-05", *BOARD], stop_after=2)
    monkeypatch.setattr(cli, "BeigeBookSource", lambda fetcher: ScriptedSource(state))
    monkeypatch.setattr(cli, "Context", lambda **kwargs: setattr(state, "ctx", Context(**kwargs)) or state.ctx)
    argv = ["run", "--local", str(tmp_path / "hub"), "--workdir", str(tmp_path)]
    assert cli.main(argv) == 0 and outputs(actions)["more"] == "true"
    state.stop_after = None
    actions.write_text("")
    assert cli.main(argv) == 0 and outputs(actions)["more"] == "false" and len(state.fetched) == 4


class FakeSource:
    """Stands in for BeigeBookSource(fetcher): head() answers the given head or raises the given error."""

    def __init__(self, head=None, error=None):
        self.value, self.error = head, error

    def __call__(self, fetcher):
        return self

    def head(self):
        if self.error:
            raise self.error
        return self.value


def test_probe_asks_for_a_sync_when_the_hub_has_no_manifest(actions, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "BeigeBookSource", FakeSource(head={"newest": "2026-09-02", "published": 6}))
    assert cli.main(["probe", "--local", str(tmp_path)]) == 0
    assert outputs(actions) == {"needed": "true"} and set(outputs(actions)) == OUTPUTS["probe"]
    assert '"reason": "no manifest yet"' in capsys.readouterr().out


def test_probe_decides_from_the_card_without_reading_the_manifest(actions, monkeypatch, tmp_path, capsys):
    state = scripted(board=list(BOARD))
    run_once(local_store(tmp_path), state, writer="github-actions")
    monkeypatch.setattr(cli, "BeigeBookSource", FakeSource(head=ScriptedSource(state).head()))
    monkeypatch.setattr(LocalStore, "read_manifest", lambda self: pytest.fail("planted: the probe read the manifest"))
    assert cli.main(["probe", "--local", str(tmp_path / "hub")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["needed"], out["reason"], out["state_from"]) == (False, "up to date", "card") and outputs(actions) == {"needed": "false"}


def test_probe_asks_for_a_sync_when_the_landing_page_names_a_new_edition(actions, monkeypatch, tmp_path, capsys):
    state = scripted(board=list(BOARD))
    run_once(local_store(tmp_path), state, writer="github-actions")
    state.board.append("2026-04-15")
    monkeypatch.setattr(cli, "BeigeBookSource", FakeSource(head=ScriptedSource(state).head()))
    assert cli.main(["probe", "--local", str(tmp_path / "hub")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["needed"] is True and out["reason"].startswith("landing page ") and outputs(actions) == {"needed": "true"}


@pytest.mark.parametrize("readme", ["---\nlicense: other\n---\n# a card from before the probe state\n", None])
def test_probe_falls_back_to_the_manifest_when_the_card_has_no_state(actions, monkeypatch, tmp_path, capsys, readme):
    state = scripted(board=list(BOARD))
    run_once(local_store(tmp_path), state, writer="github-actions")
    path = tmp_path / "hub" / CARD
    path.write_text(readme) if readme else path.unlink()
    state.board.append("2026-04-15")
    monkeypatch.setattr(cli, "BeigeBookSource", FakeSource(head=ScriptedSource(state).head()))
    assert cli.main(["probe", "--local", str(tmp_path / "hub")]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["needed"], out["state_from"]) == (True, "manifest") and out["reason"].startswith("landing page ")


@pytest.mark.parametrize("error", [Unavailable("HTTP 503 from www.federalreserve.gov/monetarypolicy/publications/beige-book-default.htm"), QuotaExhausted("429"), hub_error(503, "503 Server Error"),
                                   hub_error(429, "429 Too Many Requests"), httpx.ConnectError("refused")])
def test_a_transient_outage_skips_the_probe_quietly(actions, monkeypatch, tmp_path, error):
    monkeypatch.setattr(cli, "BeigeBookSource", FakeSource(error=error))
    assert cli.main(["probe", "--local", str(tmp_path)]) == 0
    assert outputs(actions) == {"needed": "false"}


@pytest.mark.parametrize("error", [RuntimeError("a bug"), hub_error(404, "404 Client Error: Repository Not Found")])
def test_any_other_probe_error_fails_the_job(actions, monkeypatch, tmp_path, error):
    monkeypatch.setattr(cli, "BeigeBookSource", FakeSource(error=error))
    with pytest.raises(type(error)):
        cli.main(["probe", "--local", str(tmp_path)])
    assert outputs(actions) == {}


@pytest.mark.parametrize("problems, code", [([], 0), (["2026: planted"], 1)])
def test_verify_exits_1_on_any_problem_and_reads_the_lists_only_when_live(monkeypatch, tmp_path, problems, code):
    import fed_products.verify

    seen = []
    monkeypatch.setattr(fed_products.verify, "verify", lambda store, source=None: seen.append(source) or {"problems": problems})
    assert cli.main(["verify", "--local", str(tmp_path)]) == code
    assert cli.main(["verify", "--local", str(tmp_path), "--live"]) == code
    assert seen[0] is None and isinstance(seen[1], cli.BeigeBookSource)


def test_card_rewrites_the_card_only_when_it_changed(tmp_path, capsys):
    run_once(local_store(tmp_path), scripted(board=list(BOARD)))
    path = tmp_path / "hub" / CARD
    assert cli.main(["card", "--local", str(tmp_path / "hub")]) == 0 and capsys.readouterr().out == "card unchanged\n"
    path.write_text(path.read_text().replace("# Federal Reserve Beige Book", "# An older card"))
    assert cli.main(["card", "--local", str(tmp_path / "hub")]) == 0 and capsys.readouterr().out == "card updated\n"
    assert "# Federal Reserve Beige Book" in path.read_text()


def test_squash_works_on_the_hub_only(capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["squash", "--local", "/tmp/x"])
    assert stopped.value.code == 2 and "squash works on the Hub only" in capsys.readouterr().err


@pytest.mark.parametrize("commits, squashed", [(cli.SQUASH_AFTER_COMMITS, False), (cli.SQUASH_AFTER_COMMITS + 1, True)])
def test_squash_only_past_the_threshold(monkeypatch, commits, squashed):
    calls = []

    class FakeHfApi:
        def __init__(self, token=None):
            pass

        def list_repo_commits(self, repo_id, repo_type):
            return [None] * commits

        def super_squash_history(self, repo_id, repo_type, commit_message):
            calls.append((repo_id, commit_message))

    monkeypatch.setattr(huggingface_hub, "HfApi", FakeHfApi)
    assert cli.main(["squash", "--repo", "x/y"]) == 0
    assert bool(calls) == squashed


def test_options_are_never_abbreviated(capsys):
    with pytest.raises(SystemExit):
        cli.main(["run", "--local", "/tmp/x", "--budget", "5"])
    assert "unrecognized arguments: --budget" in capsys.readouterr().err


def shell_argv(line, env):
    """The argv bash builds for one `python -m fed_products ...` line of a workflow step, with the step's env."""
    command = line.split("|")[0].strip().replace("python -m fed_products", "printf '%s\\0'", 1)
    done = subprocess.run(["bash", "-c", command], capture_output=True, text=True, env={"PATH": os.environ["PATH"], **env}, check=True)
    return done.stdout.split("\0")[:-1]


def load_workflow():
    workflow = yaml.safe_load(WORKFLOW.read_text())
    return workflow, workflow.get("on", workflow.get(True))  # PyYAML reads the key `on` as True


def test_the_only_workflow_is_the_one_checked_here():
    assert sorted(path.name for path in WORKFLOWS.iterdir() if path.suffix in (".yml", ".yaml")) == ["pipeline.yml"]


def test_the_workflow_calls_only_commands_options_and_outputs_the_cli_has(monkeypatch):
    workflow, triggers = load_workflow()
    jobs = workflow["jobs"]
    example = re.search(r"e\.g\. (.+?) \(", triggers["workflow_dispatch"]["inputs"]["args"]["description"]).group(1)
    parsed = []
    for command in ("run", "probe", "verify", "squash", "card"):
        monkeypatch.setattr(cli, f"cmd_{command}", lambda args: parsed.append(args) or 0)
    command_of = {}
    for step in jobs["sync"]["steps"]:
        for line in (step.get("run") or "").splitlines():
            if "python -m fed_products" not in line:
                continue
            envs = [{}]
            if "$EXTRA_ARGS" in line or "$BUDGET" in line:
                envs = [{}, {"BUDGET": "30", "EXTRA_ARGS": example}]
            for env in envs:
                argv = shell_argv(line, env)
                assert cli.main(argv) == 0, f"{step.get('name')}: {argv}"
                command_of[step.get("id")] = argv[0]
    assert {args.command for args in parsed} == {"run", "probe", "verify", "squash"}
    assert {args.repo for args in parsed if not args.local} == {REPO_ID}
    assert any(args.command == "verify" and args.live for args in parsed)
    smoke = next(args for args in parsed if args.command == "run" and args.local)
    assert smoke.budget_minutes == 30 and smoke.partitions and smoke.max_units
    assert all(args.budget_minutes < jobs["sync"]["timeout-minutes"] for args in parsed if args.command == "run"), "the budget must end the run before GitHub does"
    assert not re.search(r"secrets\.", WORKFLOW.read_text()), "the workflow needs no secret"

    expressions = " ".join([str(step.get("if", "")) for step in jobs["sync"]["steps"]] + list(jobs["sync"].get("outputs", {}).values()))
    referenced = re.findall(r"steps\.(\w+)\.outputs\.(\w+)", expressions)
    assert referenced
    for step_id, key in referenced:
        assert key in OUTPUTS[command_of[step_id]], f"steps.{step_id}.outputs.{key}: `{command_of[step_id]}` does not write {key}"

    needed = [(job_name, key) for job in jobs.values() for job_name, key in re.findall(r"needs\.(\w+)\.outputs\.(\w+)", str(job.get("if", "")))]
    assert needed
    for job_name, key in needed:
        assert key in jobs[job_name].get("outputs", {}), f"needs.{job_name}.outputs.{key}: job {job_name} declares no output {key}"
    # The job that starts runs starts this workflow, never after a bounded test, and holds no permission to touch the dataset; the job that parses downloads cannot start runs.
    starters = [job for job in jobs.values() if any("gh workflow run" in (step.get("run") or "") for step in job["steps"])]
    assert len(starters) == 1
    for job in starters:
        assert all(f"gh workflow run {WORKFLOW.name} " in step["run"] for step in job["steps"] if "gh workflow run" in (step.get("run") or ""))
        assert "!inputs.args" in job["if"] and job["permissions"] == {"actions": "write"}
    assert jobs["sync"]["permissions"] == {"contents": "read", "id-token": "write"}
    # No job may commit: GitHub called commits that keep a schedule enabled a violation of its Terms.
    assert workflow["permissions"] == {} and workflow["concurrency"]["group"] == WORKFLOW.stem
    for job_name, job in jobs.items():
        assert job.get("permissions", {}).get("contents") != "write", f"job {job_name} can push"
        assert not any(re.search(r"\bgit\s+(commit|push)\b", step.get("run") or "") for step in job["steps"]), f"job {job_name} commits"
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), f"job {job_name}: {step['uses']} is not pinned to a commit"


def test_the_schedule_is_what_the_code_and_card_say():
    _, triggers = load_workflow()
    assert [entry["cron"] for entry in triggers["schedule"]] == [card.SCHEDULE]
    text = card.render({})
    assert f"scheduled at 00:00 and 12:00 UTC (`{card.SCHEDULE}`)" in text and card.SCHEDULE == "0 0,12 * * *"
    assert not re.search(r"scheduled every|every hour at|minutes past|every 5 minutes|every 6 hours|once a week|weekly", text)


@pytest.mark.parametrize("idle_days, fails", [(49, False), (50, True)])
def test_inactivity_runs_on_every_schedule_and_fails_from_50_idle_days_without_committing(tmp_path, idle_days, fails):
    """The step run the way GitHub runs a step (bash -e), in a checkout made the way actions/checkout makes one, against a local origin. That no job commits is checked above."""
    workflow, _ = load_workflow()
    job = workflow["jobs"]["inactivity"]
    assert job["if"] == "github.event_name == 'schedule'"
    (step,) = [step for step in job["steps"] if "run" in step]
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}

    def git(*args, cwd, **extra):
        return subprocess.run(["git", *args], cwd=cwd, env={**env, **extra}, capture_output=True, text=True, check=True).stdout

    origin, seed, work = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "work"
    for path in (origin, seed, work):
        path.mkdir()
    git("init", "-q", "--bare", "-b", "main", cwd=origin)
    git("init", "-q", "-b", "main", cwd=seed)
    stamp = f"@{int(time.time()) - idle_days * 86400} +0000"
    who = {"GIT_AUTHOR_NAME": "a", "GIT_AUTHOR_EMAIL": "a@example.com", "GIT_COMMITTER_NAME": "a", "GIT_COMMITTER_EMAIL": "a@example.com"}
    for title in ("first change", "last change"):
        git("commit", "-q", "--allow-empty", "-m", title, cwd=seed, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp, **who)
    git("push", "-q", origin.as_uri(), "main", cwd=seed)
    # actions/checkout on a scheduled run: a shallow fetch, then a local main that tracks origin's.
    git("init", "-q", cwd=work)
    git("remote", "add", "origin", origin.as_uri(), cwd=work)
    git("fetch", "-q", "--depth=1", "origin", "+refs/heads/main:refs/remotes/origin/main", cwd=work)
    git("checkout", "-q", "--force", "-B", "main", "refs/remotes/origin/main", cwd=work)
    assert git("rev-parse", "--is-shallow-repository", cwd=work).strip() == "true"

    done = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=work, env={**env, "SCHEDULE": card.SCHEDULE}, capture_output=True, text=True)
    assert done.returncode == (1 if fails else 0), done.stdout + done.stderr
    assert f"schedule: {card.SCHEDULE}" in done.stdout
    assert f"last commit {idle_days} days ago" in done.stdout
    errors = [line for line in done.stdout.splitlines() if line.startswith("::error::")]
    assert bool(errors) == fails
    assert all(f"No commit in {idle_days} days" in line and "60 days" in line for line in errors)
    assert git("log", "--format=%s", "main", cwd=origin).splitlines() == ["last change", "first change"]
