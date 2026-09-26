# fed_products

The pipeline package, run as `python -m fed_products {run,probe,card,verify,squash}`.

**Objective:** keep the dataset's Parquet partitions, one a year, equal to the editions the Board's lists name, with one writer at a time and little local disk.

**Inputs:** the Board's Beige Book and FOMC historical pages, the Minneapolis Fed's sitemap and report pages, and the manifest on the Hub.

**Files:**

- `cli.py`, `__main__.py`: the commands. `probe` decides whether a sync is needed, `run` syncs within a time budget, `verify` checks the Hub against the manifest (with `--live`, also against every list), `card` re-renders the card, `squash` shortens Hub history.
- `pipeline.py`: the sync loop, the writer lease, the probe's decision.
- `beige_book.py`: the schema, the rows, the units a fetch covers, and the source that reads every list.
- `board.py`: the Board's lists and its four page layouts.
- `minneapolis.py`: the Minneapolis archive's sitemap and pages.
- `sections.py`: the sections and their order.
- `text.py`: HTML to plain text, laid out as a browser shows it.
- `http.py`: per-host pacing, bounded retries, bot-challenge detection.
- `store.py`: Parquet partitions, and the Hub store's parent-commit fence.
- `card.py`: the dataset card, rendered from the manifest.
- `verify.py`: the publication check.
