# fixtures

Real pages, recorded September 25, 2026 and gzipped, one per URL, named `<host><path>.gz` with each `/` written as `~`. Every one is read by a test.

**Objective:** test parsing against what the sites actually serve, not markup written from memory.

**Inputs:** a GET of the URL each name spells.

**Terms:** these are the sites' pages, not code, so the repository's MIT license does not cover them. The [dataset card](https://huggingface.co/datasets/incrediblecrab/federal-reserve-beige-book) quotes each site's terms.

**Files:**

- www.federalreserve.gov, 100 pages: the Beige Book landing page, archive index and year pages 1996 to 2025; the FOMC historical index and year pages 1968 to 2020; and editions in each of the four layouts: October 30, 1996 and December 1, 2010 (a page per section, three of each), January 12, 2011 (one page), January 18, March 1 and October 18, 2017 and January 15, 2020 (one page with an anchor per district), and January 14, 2026 (a page per section, three).
- www.minneapolisfed.org, 31 pages: the sitemap; every section page of June 1971 and January 1980, the two months with two editions; the summary and special report of May 1983; and the summaries of July and September 1996.
