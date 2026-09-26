# fixtures

Real pages, recorded September 25, 2026 (one, the Kansas City page of March 6, 2024, on September 26) and gzipped, one per URL, named `<host><path>.gz` with each `/` written as `~`. Every one is read by a test. The Board's pages are kept as served. Of the Minneapolis Fed's, the sitemap keeps only its 6,357 `/beige-book-reports/` entries, each with its `loc` and `lastmod`, and 30 of the 32 pages under `/beige-book-reports/` keep only what the parser reads: the `__NEXT_DATA__` script cut to the route's template name and whichever of its `Report`, `Title` and `District` fields it has, values unchanged, in a minimal HTML page. The August 1973 summary and October 1987 Richmond are kept whole.

**Objective:** test parsing against what the sites actually serve, not markup written from memory.

**Inputs:** a GET of the URL each name spells.

**Terms:** the repository's MIT license covers code, not these pages; the [dataset card](https://huggingface.co/datasets/incrediblecrab/federal-reserve-beige-book) quotes each site's terms.

**Files:**

- www.federalreserve.gov, 104 pages: the Beige Book landing page, archive index and year pages 1996 to 2025; the FOMC historical index and year pages 1968 to 2020; and editions in each layout: October 30, 1996 and December 1, 2010 (a page per section, three of each), January 12, 2011 (one page), January 18, March 1 and October 18, 2017, January 15, 2020 and July 13, 2022 (one page with an anchor per district; July 2022 ends with a note the summary links), January 14, 2026 (a page per section, three) and Kansas City's page of March 6, 2024, whose footnote links back to the text; and the summary pages of January 22, 1997, which holds the October 29, 1997 report, and of October 29, 1997.
- www.minneapolisfed.org, 33 pages: the sitemap; every section page of June 1971 and January 1980, months with two editions; the summary and special report of May 1983; the summary of September 1996, and what the July 1996 summary's address serves, the site's "Page not found" page with HTTP 404, since there was no July 1996 edition; and two pages printing a day no list names, the August 1973 summary and October 1987 Richmond.
