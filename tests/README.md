# tests

Offline tests: `pip install '.[test]'`, then `python -m pytest -q`. No network or key.

**Objective:** pin down the behavior an unattended pipeline depends on. Each parser and gate fix was also tested by planting the defect it removed: a named test fails.

**Inputs:** real pages in [`fixtures/`](fixtures/README.md), and scripted fakes.

**Files:**

- `conftest.py`: a fetcher that serves recorded pages by URL, and a scripted Beige Book source.
- `test_board.py`: the Board's lists, each of its four page layouts, footnotes and their links back to the text, and a page that holds another edition's report.
- `test_minneapolis.py`: the sitemap, report pages and their datelines.
- `test_source.py`: every list read in full, the units they make, the rows a fetch returns, gaps, notes, page dates, conditional rechecks.
- `test_text.py`: text layout.
- `test_pipeline.py`: the sync loop: first sync, idle runs, changes, rechecks, removals, retries, the suspect-listing guard, resumption, refetch, the writer lease, the probe's decision, and when a run republishes the listing.
- `test_http.py`: pacing, challenges, rate limits, outages.
- `test_store.py`: Parquet round trips, the commit fence, the probe state read from the card.
- `test_verify.py`: each planted data defect is named; exit 1.
- `test_card.py`: the card's front matter, numbers and columns.
- `test_cli.py`: exit codes, `$GITHUB_OUTPUT`, Trusted Publishing, and the workflow's commands, options, outputs, schedule and inactivity job.
