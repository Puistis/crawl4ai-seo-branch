# Crawler Bug Fixes Summary — 2026-02-21

All 23 items from `CRAWLER_CROSS_REFERENCE_REPORT.md` addressed across 4 tiers.

## Files Modified

| File | Changes |
|------|---------|
| `crawl4ai/seo_audit/models.py` | Added fields: `LinkStats` (javascript/empty/sponsored/ugc/internal_nofollow counts), `HreflangCheck` (validation_errors), `PageAuditResult` (content_hash, is_soft_404, iframe_count, rel_next, rel_prev), `RobotsTxtCheck` (sitemap_refs, broken_sitemap_refs, conflicting_rules, crawl_delay_directives) |
| `crawl4ai/seo_audit/checks.py` | Updated `check_links` to capture javascript:/empty hrefs + sponsored/ugc/internal nofollow. Expanded `_STOP_WORDS` and raised keyword stuffing thresholds. Extended `_validate_structured_data` for Article, FAQPage, Product, LocalBusiness, Event, Recipe, Person. Added ISO 639-1 language code validation to `check_hreflang`. Added `_compute_content_hash`, `_detect_soft_404`, `_count_iframes`, `_check_pagination` to `audit_page`. |
| `crawl4ai/seo_audit/site_checks.py` | Added 16 new site-level check functions. Updated `_check_skipped_headings` for template dedup. Updated `_check_javascript_hrefs` to use new LinkStats counts. Wired all into `run_site_checks`. |
| `crawl4ai/seo_audit/domain_checks.py` | Enhanced `check_robots_txt` to parse sitemap refs, validate them via HEAD requests, detect conflicting Allow/Disallow rules, and track crawl-delay directives. |

## Tier 1 — Critical (Detection gaps)

| # | Issue | Fix |
|---|-------|-----|
| T1 | Duplicate title detection (M2, M3) | Already implemented in `_check_duplicate_titles` — verified |
| T2 | Duplicate meta description (M9, M10) | Already implemented in `_check_duplicate_descriptions` — verified |
| T3 | Broken internal link detection (L1) | Already implemented via `broken_internal_urls` pipeline — verified |

## Tier 2 — High (Feature gaps)

| # | Issue | Fix |
|---|-------|-----|
| T4 | Canonical URL validation (M13, X5) | New `_check_canonical_validation` — flags canonicals pointing to 404s or cross-page canonicals |
| T5 | Duplicate body content (C7, C8) | New `_compute_content_hash` (SHA-256 of normalized body) + `_check_duplicate_content` |
| T6 | Noindex-in-sitemap (S5, M17) | Already implemented — verified |
| T7 | Soft 404 detection (X1) | New `_detect_soft_404` (pattern match on title/H1) + `_check_soft_404` site-level |
| T8 | JSON-LD syntax validation (SD2) | Already works + new `_check_structured_data_issues` site-level aggregation |

## Tier 3 — Medium (False positives & enrichment)

| # | Issue | Fix |
|---|-------|-----|
| T9 | Keyword density false positives | Expanded `_STOP_WORDS` to ~130 words, raised threshold to 4%/15 occurrences, min 3-char words, strip nav/footer |
| T10 | Template heading dedup | Updated `_check_skipped_headings` — if same skip on >50% pages, reported as template issue |
| T11 | Link attribute analysis | `check_links` now captures javascript:/empty/sponsored/ugc/internal nofollow. Three new site checks. |
| T12 | Robots.txt deep analysis | `check_robots_txt` now validates sitemap refs, detects conflicting rules, tracks crawl-delay. `_check_robots_deep_analysis` surfaces as site issues. |
| T13 | Schema field validation | `_validate_structured_data` expanded for Article, FAQPage, Product, LocalBusiness, Event, Recipe, Person |
| T14 | Hreflang validation | ISO 639-1 code validation in `check_hreflang` + `_check_hreflang_issues` site-level |
| T15 | Image dimensions surfacing | New `_check_images_missing_dimensions` site-level issue |
| T16 | Default max_pages to 200 | Already set to 200 in `server.py` line 906 |

## Tier 4 — Low (Nice-to-have)

| # | Issue | Fix |
|---|-------|-----|
| T17 | Pagination analysis | New `_check_pagination` per-page + `_check_pagination_issues` site-level |
| T18 | IFrame content analysis | New `_count_iframes` per-page + `_check_iframe_content` site-level |
| T19 | Trailing slash normalization | New `_check_trailing_slash_inconsistency` |
| T20 | Long URL detection | New `_check_long_urls` (>100 chars) |
| T21 | Orphan page discovery via sitemap | New `_check_orphan_pages_via_sitemap` — cross-references sitemap URLs vs link graph |
| T22 | Redirect chain full-hop tracking | Already implemented — verified |
| T23 | Empty alt vs missing alt | New `_check_empty_alt_text` — distinguishes decorative (alt="") from missing |

## New Site Issue Types

These are the new `issue_type` values that will appear in `site_issues`:

- `canonical_target_missing` — WARNING
- `cross_page_canonical` — INFO
- `duplicate_content` — WARNING
- `soft_404` — WARNING
- `internal_nofollow` — INFO
- `sponsored_ugc_links` — INFO
- `images_missing_dimensions` — WARNING
- `images_empty_alt` — INFO
- `long_urls` — INFO
- `trailing_slash_inconsistency` — INFO
- `pagination_detected` — INFO
- `iframe_content` — INFO
- `sitemap_orphan_pages` — INFO
- `structured_data_issues` — WARNING/INFO
- `hreflang_issues` — WARNING
- `robots_broken_sitemap_ref` — WARNING
- `robots_conflicting_rules` — WARNING
- `robots_crawl_delay` — INFO

## SonarQube Notes

Cognitive complexity warnings are pre-existing and non-blocking. The main affected functions:
- `run_site_checks` (101) — orchestrator function, inherently complex
- `check_robots_txt` (76) — parser function
- `_validate_structured_data` (114) — validation dispatch
- `check_links` (30) — link categorization
- `check_hreflang` (18) — validation logic

## Deployment

To deploy these changes, follow the standard deploy steps (copy crawl4ai → docker context → npm run deploy). No D1 schema changes required — all new data flows through existing `audit_json` and `site_issues` columns.
