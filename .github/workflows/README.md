# workflows

**Objective:** keep the dataset current with no personal device and no person, given a commit every 60 days. The workflow is scheduled at 00:00 and 12:00 UTC, and GitHub starts some runs late or [drops them](https://huggingface.co/datasets/incrediblecrab/federal-reserve-beige-book#how-it-stays-current).

**Inputs:** the job's OIDC token, which Hugging Face exchanges for a short-lived write token when the dataset lists this repository and workflow as a Trusted Publisher; otherwise the run fails. No secret is used.

**Files:**

- [`pipeline.yml`](pipeline.yml): `probe` reads the Board's landing page and the card's metadata. When needed, `run` syncs; `verify --live` and `squash` follow a commit. `continue` starts the next run when one ran out of budget mid-fetch. `inactivity` fails after 50 days without a commit, before GitHub disables every schedule here at 60. It makes no keepalive commit: GitHub [called](https://github.com/ddev/github-action-add-on-test/issues/46) that a Terms violation.

GitHub reports a failed scheduled run to whoever last changed its cron, by email if their settings allow. A dispatch with `args` runs only a bounded test. `.github/` has no README, which GitHub would show instead of the repository's.
