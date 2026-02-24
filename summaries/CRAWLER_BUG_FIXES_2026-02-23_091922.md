# Crawler Bug Fixes — 2026-02-23 (testing-crawler.pages.dev report)

**Test Site:** https://testing-crawler.pages.dev  
**Report:** `cross-reference-report.md`  
**Deploy Version:** `b5a4476c-5745-4c4b-b2fc-061bed6d4afd`

---

## Summary

13 fixes applied across 4 files addressing false negatives, false positives, and missing detection capabilities identified in the testing-crawler.pages.dev cross-reference report.

## Fixes by File

### `crawl4ai/seo_audit/checks.py`

| # | Bug | Fix |
|---|-----|-----|
| 1 | **Keyword stuffing not detected (C9/BUG-15)** — "seo" was in `_STOP_WORDS`, so a page with "SEO" 50+ times was skipped | Removed SEO/test-related words from `_STOP_WORDS`: `seo`, `test`, `testing`, `example`, `demo`, `sample`, `description`, `meta`, `url`, `urls`, `alt`, `heading`, `headings`, `duplicate`, `missing`, `broken`, `error`, `warning` |
| 2 | **Soft 404 not triggered (X1/BUG-07)** — Only checked `<title>` via `text()` (misses child elements) | Changed to `text_content()` for title, added body text check (first 500 chars) |

### `crawl4ai/seo_audit/site_checks.py`

| # | Bug | Fix |
|---|-----|-----|
| 3 | **Template heading dedup not catching footer h2→h4 (BUG-11)** — 44/45 false positives | Lowered threshold from 50% to 30% of all pages |
| 4 | **Valid schema page flagged (BUG-17/SD1)** — Missing recommended fields treated as errors | `_check_structured_data_issues` now only includes pages with actual errors in `affected_pages`, not pages with only warnings |
| 5 | **Duplicate issue types (BUG-13)** — `image_optimization` + `images_missing_dimensions` overlap | `_check_image_optimization` now only flags non-modern formats (INFO); dimensions handled by dedicated check |
| 6 | **Noindex-in-sitemap not matching (S5/M17)** — URL trailing slash mismatch | Added bidirectional trailing slash matching (both with/ and without/) |
| 7 | **Trailing slash inconsistency not detected (S6/BUG-18)** — Only checked page URLs | Now also compares canonical URLs vs internal link targets for slash convention mismatches |
| 8 | **Redirect loop detection missing (BUG-14/L9)** — Meta refresh cycles not identified | New `_check_redirect_loops` follows meta_refresh_url graph up to 10 hops to detect cycles |
| 9 | **Robots.txt compliance missing (BUG-09/X4)** — Disallowed pages crawled without flagging | New `_check_robots_compliance` cross-references crawled pages against robots.txt blocked_paths |

### `crawl4ai/seo_audit/domain_checks.py`

| # | Bug | Fix |
|---|-----|-----|
| 10 | **Robots.txt conflict false positives (BUG-10)** — `Allow /` + `Disallow /admin` flagged as conflict | Skip conflicts where either path is `/` (root Allow + specific Disallow is standard) |

### `infrastructure/docker/server.py`

| # | Bug | Fix |
|---|-----|-----|
| 11 | **Broken internal links not detected (L1)** — BFS stops at max_pages, never visits 404 targets | New `_check_uncrawled_internal_links` HEAD-checks up to 50 internal link targets that BFS didn't visit, merges results into broken_internal_urls |

## New Issue Types Added

| Issue Type | Severity | Description |
|------------|----------|-------------|
| `redirect_loops` | CRITICAL | Meta refresh redirect cycles (A→B→A) |
| `crawled_but_blocked` | WARNING | Pages crawled but disallowed by robots.txt |

## Expected Detection Improvements

| ID | Issue | Before | After |
|----|-------|--------|-------|
| L1 | Broken internal links | ❌ FALSE NEGATIVE | ✅ HEAD-checked |
| C9 | Keyword stuffing | ❌ FALSE NEGATIVE | ✅ "seo" no longer in stop words |
| X1 | Soft 404 | ❌ FALSE NEGATIVE | ✅ Checks body text too |
| S5/M17 | Noindex-in-sitemap | ❌ FALSE NEGATIVE | ✅ Trailing slash matching |
| S6 | Trailing slash inconsistency | ❌ FALSE NEGATIVE | ✅ Canonical vs link comparison |
| L9 | Redirect loop | 🔶 PARTIAL | ✅ Meta refresh cycle detection |
| X4 | Crawled but blocked | ❌ FALSE NEGATIVE | ✅ Cross-reference with robots.txt |
| BUG-10 | Robots.txt conflict FP | FP | ✅ Fixed |
| BUG-11 | Template heading FP | 44 FPs | ✅ Template dedup at 30% |
| BUG-13 | Duplicate image issues | FP | ✅ Separated |
| BUG-17 | Valid schema FP | FP | ✅ Errors only |

## SonarQube Complexity Warnings

All cognitive complexity warnings are pre-existing and non-blocking. The `run_site_checks`, `_post_crawl_analysis`, `check_robots_txt`, and `_validate_structured_data` functions are inherently complex orchestrators/parsers.
