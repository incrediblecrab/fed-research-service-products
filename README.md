# fed-research-service-products

This repository builds and updates [federal-reserve-beige-book](https://huggingface.co/datasets/incrediblecrab/federal-reserve-beige-book), a public Hugging Face dataset of every edition of the Federal Reserve's Beige Book from May 1970 on. Each edition has one row for the national summary and one for each of the twelve districts, with text. The dataset card shows how much it holds and which sections no source serves.

**Objective:** keep the dataset equal to the Board's lists of editions with no person, personal device or local copy. The workflow runs on GitHub Actions at 00:00 and 12:00 UTC and writes through Hugging Face Trusted Publishing, so no token is stored. GitHub disables schedules after 60 days without repository activity, such as a commit.

**Inputs:** the Board's [Beige Book pages](https://www.federalreserve.gov/monetarypolicy/publications/beige-book-default.htm) and FOMC historical pages (HTML from October 30, 1996 on), and the Minneapolis Fed's [Beige Book archive](https://www.minneapolisfed.org/region-and-community/regional-economic-indicators/beige-book-archive) for earlier editions. No key is needed.

**Files:**

- [`fed_products/`](fed_products/README.md): the pipeline package
- [`tests/`](tests/README.md): offline tests
- [`.github/workflows/`](.github/workflows/README.md): the schedule
- `pyproject.toml`: pinned dependencies
- `LICENSE`: MIT, for the code

**Try it:** `pip install .`, then `python -m fed_products run --local /tmp/out --partitions 2026 --max-units 1`.
