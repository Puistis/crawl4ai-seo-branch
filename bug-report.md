# Bug Report — SEO Crawler

**Test site:** `https://testing-crawler.pages.dev`
**Job ID:** `98542962-fe71-4097-93e5-2addaf1fbdf2`
**Crawl date:** 2026-02-21
**Reference:** SEO_TEST_DOCUMENTATION.md (complete ground-truth issue catalog)

---

## Summary Table

| # | Bug | Severity | Category |
|---|---|---|---|
| 1 | Crawler stops at depth 1 of deep page chain | Critical | Crawl coverage |
| 2 | Entire page categories never discovered | Critical | Crawl coverage |
| 3 | Empty H1 counted as valid H1 | High | Detection — headings |
| 4 | Multiple H1 tags not flagged as an issue | High | Detection — headings |
| 5 | Internal broken links not stored in `broken_links` | High | Detection — links |
| 6 | Duplicate titles not detected across pages | High | Detection — titles |
| 7 | Duplicate descriptions not detected across pages | High | Detection — meta |
| 8 | Title/description length warnings not surfaced as issues | Medium | Reporting |
| 9 | Thin content threshold miscalibrated — 92% false positive rate | Medium | Detection — content |
| 10 | Meta refresh redirect not flagged | Medium | Detection — technical |
| 11 | Multiple conflicting canonical tags not detected | Medium | Detection — canonical |
| 12 | Wrong canonical (pointing to 404) not validated | Medium | Detection — canonical |
| 13 | Missing `charset` declaration not detected | Medium | Detection — technical |
| 14 | Missing `lang` attribute not detected | Medium | Detection — technical |
| 15 | Noindex + in sitemap conflict not flagged | Medium | Detection — indexation |
| 16 | H1 length not checked | Medium | Detection — headings |
| 17 | Skipped heading levels not detected | Medium | Detection — headings |
| 18 | Too many links not flagged | Medium | Detection — links |
| 19 | JavaScript/empty hrefs not detected | Medium | Detection — links |
| 20 | Broken image src not flagged as issue | Medium | Detection — images |
| 21 | External broken links all stored as `status_code = 0` | Medium | Data quality |
| 22 | Redirect loop not detected | Medium | Detection — links |
| 23 | `deep_pages` issue flags homepage instead of deep pages | Medium | Detection — depth |
| 24 | `redirect_chains.chain_length` off-by-one; middle hop missing | Low–Medium | Data quality |
| 25 | Hidden text not detected | Low–Medium | Detection — content |
| 26 | Keyword stuffing not detected | Low–Medium | Detection — content |
| 27 | Lorem ipsum / placeholder content not detected | Low | Detection — content |
| 28 | `has_og_tags` field does not distinguish incomplete OG | Low | Data quality |
| 29 | Uppercase URL not flagged | Low | Detection — URLs |
| 30 | Redirect loop page missing canonical not flagged | Low | Detection — canonical |
| 31 | Page-level nofollow directive not flagged | Low | Detection — indexation |
| 32 | `mixed_content` column always 0 — appears unimplemented | Low | Detection — security |
| 33 | robots.txt analysis incomplete | Low | Detection — domain |

---

## Bug 1 — Crawler stops at depth 1 of deep page chain

**Severity:** Critical

**Observed:** Only `/deep/level1` was crawled. `/deep/level1/level2/` through `/deep/level1/level2/level3/level4/level5/buried-page` (5 more pages, up to 6 clicks from homepage) were never visited.

**Expected:** With `max_depth: 3`, the crawler should reach at least level 3 (`/deep/level1/level2/level3/`). Level 4 and 5 would be beyond max_depth, but levels 2 and 3 should be crawled.

**Likely cause:** The link-following logic may be resolving `/deep/level1` correctly but not parsing or queuing links found on that page. Could be a depth counter bug (treating level1 as already at max depth), or links within that page not being extracted.

---

## Bug 2 — Entire page categories never discovered

**Severity:** Critical

**Observed:** The following entire sections of the site were never crawled despite being linked from the homepage or from crawled pages:

- `/schema/*` — 4 pages
- `/hreflang/*` — 2 pages
- `/security/*` — 2 pages
- `/pagination/*` — 3 pages
- `/misc/*` — 5 pages
- `/orphan/*` — 3 pages (expected — these are orphaned by design)
- `/urls/very-long-url-*` — 1 page

**Expected:** All pages linked from the homepage or from any crawled page within `max_depth` should be queued and visited.

**Likely cause:** The homepage has 74 internal links. Some of these category pages may be failing URL extraction (e.g., normalisation issues, fragment-only links being dropped). Alternatively the `max_pages: 50` limit was hit early and remaining pages silently dropped. The crawler reported 49 pages crawled — very close to the 50-page cap. This is likely the primary cause: the crawler hit its page cap before discovering all sections.

**Recommendation:** Either raise the default `max_pages`, or when the cap is hit, explicitly warn which discovered-but-not-crawled URLs were dropped so the user knows coverage is incomplete.

---

## Bug 3 — Empty H1 counted as valid H1

**Severity:** High

**Observed:** `/content/empty-h1` has `<h1></h1>` (empty tag). The DB stores `h1_count = 1` and no issue is raised. The page passes the heading check.

**Expected:** An empty H1 is functionally identical to having no H1. `h1_count` should not increment for H1 elements with no text content (after trimming whitespace), and a `missing_h1` or `empty_h1` issue should be raised.

**Fix:** In the H1 extraction logic, filter out elements where `innerText.trim() === ''`.

---

## Bug 4 — Multiple H1 tags not flagged as an issue

**Severity:** High

**Observed:** `/content/multiple-h1` has `h1_count = 3` in the DB. No `multiple_h1` issue is raised in `site_issues`.

**Expected:** Pages with more than one H1 should be flagged as a warning. Multiple H1s dilute the primary heading signal.

**Fix:** Add a check: if `h1_count > 1`, raise a `multiple_h1` warning issue.

---

## Bug 5 — Internal broken links not stored in `broken_links` table

**Severity:** High

**Observed:** `/links/broken-internal` contains 8 links to non-existent internal pages (all 404). The page has `internal_links = 17` in `page_audits`. Querying `broken_links`:

```sql
SELECT * FROM broken_links WHERE job_id = '98542962-fe71-4097-93e5-2addaf1fbdf2';
-- Returns 3 rows, all link_type = 'external'
-- Zero rows with link_type = 'internal'
```

**Expected:** Internal links that resolve to 4xx/5xx should be recorded in `broken_links` with `link_type = 'internal'` and the actual `status_code`.

**Likely cause:** The internal link checker may not be persisting failed requests to `broken_links`, or the write path only fires for external link checks.

---

## Bug 6 — Duplicate titles not detected across pages

**Severity:** High

**Observed:** `/meta/duplicate-title-1` and `/meta/duplicate-title-2` both have `title = "Duplicate Title Test Page | SEO Test Site"`. Both are in `page_audits`. No `duplicate_title` entry appears in `site_issues`.

**Expected:** A cross-page scan after crawl completion should identify titles shared by 2+ URLs and raise a `duplicate_title` site-level issue.

**Fix:** Post-crawl aggregation query:
```sql
SELECT title, COUNT(*) as cnt, GROUP_CONCAT(url) as urls
FROM page_audits WHERE job_id = ? AND title != ''
GROUP BY title HAVING cnt > 1
```

---

## Bug 7 — Duplicate descriptions not detected across pages

**Severity:** High

**Observed:** `/meta/duplicate-description-1` and `/meta/duplicate-description-2` share an identical 118-character meta description. Both in DB. No `duplicate_description` in `site_issues`.

**Expected:** Same cross-page aggregation as Bug 6, applied to `meta_desc`.

---

## Bug 8 — Title/description length warnings not surfaced as site-level issues

**Severity:** Medium

**Observed:** The following pages have `title_status = 'warning'` or `meta_desc_status = 'warning'` in `page_audits` but are not represented by any entry in `site_issues`:

| Page | Condition |
|---|---|
| `/meta/long-title` | `title_length = 174`, `title_status = warning` |
| `/meta/short-title` | `title_length = 18`, `title_status = warning` |
| `/meta/long-description` | `meta_desc_length = 316`, `meta_desc_status = warning` |
| `/meta/short-description` | `meta_desc_length = 10`, `meta_desc_status = warning` |

**Expected:** Any page-level `*_status = 'warning'` should roll up into a corresponding `site_issues` record (e.g., `long_title`, `short_title`, `long_description`, `short_description`) so they appear in `get_issues` results.

**Fix:** After page audits complete, scan for warning statuses and write corresponding `site_issues` rows.

---

## Bug 9 — Thin content threshold miscalibrated (92% false positive rate)

**Severity:** Medium

**Observed:** 45/49 pages flagged as thin content. Pages incorrectly flagged include:

| Page | Word Count | Should Be Thin? |
|---|---|---|
| `/content/duplicate-1` | 244 | No |
| `/content/duplicate-2` | 242 | No |
| `/content/hidden-text` | 176 | Debatable |
| `/links/nofollow-links` | 148 | Debatable |
| `/links/too-many` | 866 | Definitely no |

Wait — `/links/too-many` (866 words) is **not** in the thin content list. Pages with 242–244 words **are**. The actual cut-off appears to be between 244 and 314 words (the lowest non-flagged page is `/good/perfect-seo` at 314 words).

**Expected:** Industry standard thin content threshold is 200–300 words. Pages with 240+ words should not be flagged.

**Likely cause:** The content score threshold is set too high (around 300 words). Additionally, the checker may be measuring a subset of page text (main content zone only, excluding nav/footer) which would explain the gap between stored `word_count` and what triggers the flag.

**Recommendation:** Lower threshold to 200 words, or clearly document what text zone is being measured.

---

## Bug 10 — Meta refresh redirect not flagged

**Severity:** Medium

**Observed:** `/meta/meta-refresh` uses `<meta http-equiv="refresh" content="5;url=/meta/meta-refresh-target">`. It was crawled. `has_canonical = 0` was noted (leading to a missing_canonical warning) but no `meta_refresh` issue was raised.

**Expected:** Meta refresh redirects are bad practice for SEO and should be flagged as a distinct issue type.

---

## Bug 11 — Multiple conflicting canonical tags not detected

**Severity:** Medium

**Observed:** `/meta/multiple-canonicals` has two `<link rel="canonical">` tags pointing to different URLs. The crawler stores `has_canonical = 1` (at least one exists) and raises no issue.

**Expected:** If more than one canonical tag is found, raise a `multiple_canonicals` warning. The value stored in `has_canonical` should reflect the presence of a single, unambiguous canonical — not just "at least one".

---

## Bug 12 — Canonical target not validated (wrong canonical undetected)

**Severity:** Medium

**Observed:** `/meta/wrong-canonical` has a canonical pointing to `https://testing-crawler.pages.dev/this-page-does-not-exist` (a 404). `has_canonical = 1` is stored, no issue raised.

**Expected:** Canonical targets should be validated — if the target URL returns 4xx, a `broken_canonical` issue should be raised.

---

## Bug 13 — Missing `charset` declaration not detected

**Severity:** Medium

**Observed:** `/meta/missing-charset` has no `<meta charset>` declaration. `has_charset` column does not exist in `page_audits`. No issue raised.

**Expected:** The absence of a charset declaration can cause encoding issues. Should be stored in the page audit and surfaced as at least an info-level issue.

**Fix:** Add `has_charset` boolean column to `page_audits`. Flag as info/warning when absent.

---

## Bug 14 — Missing `lang` attribute not detected

**Severity:** Medium

**Observed:** `/meta/missing-lang` has `<html>` with no `lang` attribute. No column for this exists in `page_audits`. No issue raised.

**Expected:** Missing `lang` is a WCAG accessibility issue and an SEO signal. Should be detected and flagged.

**Fix:** Add `has_lang` boolean column to `page_audits`. Flag pages where `has_lang = 0`.

---

## Bug 15 — Noindex page in sitemap conflict not flagged

**Severity:** Medium

**Observed:** `/meta/noindex-in-sitemap` has `is_indexable = 0` in `page_audits` (correctly detected). However, no `noindex_in_sitemap` site-level issue is raised.

**Expected:** When a page marked `noindex` also appears in the sitemap, this is a contradictory signal that should be flagged — search engines are being told both to skip and to index the page.

**Fix:** Post-crawl: cross-reference `is_indexable = 0` pages against sitemap URLs and raise a `noindex_in_sitemap` issue for matches.

---

## Bug 16 — H1 length not checked

**Severity:** Medium

**Observed:** `/content/long-h1` has an H1 exceeding 150 characters. `h1_count = 1` is stored correctly but no length check is performed.

**Expected:** H1 length should be validated similarly to title length. An excessively long H1 should produce a warning.

---

## Bug 17 — Skipped heading levels not detected

**Severity:** Medium

**Observed:** `/content/skipped-headings` goes H1 → H3 (skipping H2) → H6 (skipping H4, H5). Not detected.

**Expected:** Heading hierarchy violations should be detected and flagged as a warning.

---

## Bug 18 — Too many links not flagged

**Severity:** Medium

**Observed:** `/links/too-many` has `internal_links = 259` stored in `page_audits`. No `excessive_links` issue is raised.

**Expected:** Pages with more than ~150 links should be flagged. The data is already captured — the issue just isn't written.

---

## Bug 19 — JavaScript hrefs and empty hrefs not detected

**Severity:** Medium

**Observed:** `/links/javascript-href` contains `href="javascript:void(0)"`, `href="javascript:alert()"`, `href=""`, `href="#"`, and bare `<a>` tags with no href. The page was crawled but no issue was raised.

**Expected:** Non-crawlable and null href patterns should be detected and flagged. These links are invisible to search engines and can indicate content that is inaccessible to crawlers.

---

## Bug 20 — Broken image src not flagged as issue

**Severity:** Medium

**Observed:** `/images/broken-src` has 4 images with broken `src` URLs (non-existent local file, non-existent domain, deleted file, empty src). `images_total = 4` is stored. No `broken_image_src` issue is raised.

**Expected:** Images with broken src URLs should be flagged similarly to broken links.

---

## Bug 21 — External broken links all stored as `status_code = 0`

**Severity:** Medium

**Observed:**
```
expired.badssl.com                          → status_code: 0  (expected: SSL/network error)
this-domain-definitely-does-not-exist.com   → status_code: 0  (expected: DNS failure)
httpstat.us/404                             → status_code: 0  (expected: 404)
```

`httpstat.us/404` is a service that explicitly returns HTTP 404. It should return `status_code = 404`, not `0`. Storing `0` for all failure modes makes it impossible to distinguish a DNS failure from a real 404.

**Fix:** Capture actual HTTP status codes where a response is received. For network-level failures, use distinct sentinel values (e.g., `-1` = timeout, `-2` = DNS failure, `-3` = SSL error) or a separate `error_type` column.

---

## Bug 22 — Redirect loop not detected

**Severity:** Medium

**Observed:** `/links/redirect-loop-a` links to `loop-b`, which links back to `loop-a`. The page was crawled (the HTML version of `loop-a`) and `has_canonical = 0` was noted. No `redirect_loop` issue was raised.

**Expected:** When the link graph contains a cycle between pages using redirect-like mechanisms, a `redirect_loop` issue should be raised.

---

## Bug 23 — `deep_pages` issue flags homepage instead of buried pages

**Severity:** Medium

**Observed:** The `deep_pages` info issue has `affected_urls = ["https://testing-crawler.pages.dev"]`. The homepage is by definition 0 clicks from itself.

Meanwhile, `/deep/level1/level2/` through `/deep/level1/level2/level3/level4/level5/buried-page` (the genuinely deep pages) were not crawled at all and therefore not flagged.

**Expected:** The homepage should never appear in `deep_pages`. Only pages requiring more than 3 internal link hops from the homepage should be flagged.

**Likely cause:** BFS/DFS starting condition error — possibly treating the root node as a destination rather than the origin, inverting depth measurement.

---

## Bug 24 — `redirect_chains.chain_length` off-by-one; middle hop missing

**Severity:** Low–Medium

**Observed:**
```
source_url:   /links/redirect-chain-start
final_url:    /links/redirect-chain-end
chain_length: 1
chain_path:   ["redirect-chain-start", "redirect-chain-end"]
```

Per the documentation (L8), the actual chain is: `start` → `middle` → `end` (3 hops). The middle page is absent from `chain_path` and `chain_length = 1` is understated (should be 2 at minimum, or 3 if counting total URLs).

**Expected:** All intermediate hops should be recorded in `chain_path`. `chain_length` should equal `chain_path.length - 1` (number of HTTP redirects).

---

## Bug 25 — Hidden text not detected

**Severity:** Low–Medium

**Observed:** `/content/hidden-text` uses four CSS techniques to hide text: `display:none`, `visibility:hidden`, white text on white background, and off-screen absolute positioning. Not detected.

**Expected:** At minimum, `display:none` and `visibility:hidden` are detectable from parsed HTML/CSS without rendering. White-on-white and off-screen positioning require computed style analysis.

---

## Bug 26 — Keyword stuffing not detected

**Severity:** Low–Medium

**Observed:** `/content/keyword-stuffing` repeats "SEO" 50+ times across title, description, and body. The title was flagged as `warning` for length (68 chars), but no keyword density or stuffing analysis ran.

**Expected:** A keyword density check should flag when a single term appears at an abnormally high frequency across title, description, and body.

---

## Bug 27 — Lorem ipsum / placeholder content not detected

**Severity:** Low

**Observed:** `/content/lorem-ipsum` contains entirely Lorem ipsum text, including in the meta description. Not detected.

**Expected:** A dictionary of known placeholder strings (`lorem ipsum`, `dolor sit amet`, etc.) should trigger a `placeholder_content` warning.

---

## Bug 28 — `has_og_tags` does not distinguish incomplete OG

**Severity:** Low

**Observed:** `/meta/incomplete-og` has only `og:title` present (missing `og:type`, `og:url`, `og:description`, `og:image`). The DB stores `has_og_tags = 1` (technically correct — at least one OG tag exists). However the `missing_open_graph` issue also flags this page (correctly).

**Expected:** The DB field should distinguish between "has some OG tags" and "has complete OG tags". Options:
- Add `has_complete_og_tags` boolean column, or
- Store the specific missing tags (e.g., `og_missing_fields = "og:image,og:description"`)

This inconsistency means downstream queries using `has_og_tags = 0` as a filter will undercount affected pages.

---

## Bug 29 — Uppercase URL not flagged

**Severity:** Low

**Observed:** `/urls/UPPERCASE-URL` was crawled and stored. No `uppercase_url` or `url_normalisation` issue was raised.

**Expected:** URLs with uppercase characters should be flagged. Best practice is all-lowercase URL paths; the canonical version should be the lowercase equivalent served via 301.

---

## Bug 30 — Redirect loop page missing canonical not linked to redirect loop detection

**Severity:** Low

**Observed:** `/links/redirect-loop-a` correctly appears in the `missing_canonical` warning. However this appears coincidental — the page simply has no canonical. The redirect loop itself is undetected (see Bug 22). The two issues should be separate: missing canonical on its own is one issue; being part of a redirect loop is another.

---

## Bug 31 — Page-level `nofollow` robots directive not flagged

**Severity:** Low

**Observed:** `/meta/nofollow-page` has `<meta name="robots" content="index, nofollow">`. The page was crawled. `is_indexable = 1` (correct — it's index, not noindex). But the `nofollow` directive, which prevents link equity from flowing to any outbound links, is not flagged.

**Expected:** A `page_nofollow` info issue should be raised for pages with `nofollow` in their robots meta.

---

## Bug 32 — `mixed_content` column always 0; appears unimplemented

**Severity:** Low

**Observed:** `page_audits.mixed_content` exists in the schema but is `0` for all 49 crawled pages, including pages that are known to have mixed content (like `/security/mixed-content`, if it had been crawled).

**Expected:** If the column exists it should be populated. If mixed content detection is not yet implemented, the column should either be removed or documented as a planned feature.

---

## Bug 33 — robots.txt analysis incomplete

**Severity:** Low

**Observed:** The crawler confirmed robots.txt is reachable and flagged the invalid sitemap reference as a domain-level deduction. However the following robots.txt issues from the test documentation were not reported:

- **S1:** `Sitemap: sitemap-old.xml` references a non-existent file (partially detected — marked as invalid, but reason unclear)
- **S2:** Googlebot has both `Allow: /meta/` and `Disallow: /meta/nofollow-page` (conflicting directives)
- **S3:** `Crawl-delay: 10` for Bingbot (not universally supported; may slow crawling unnecessarily)
- **S4:** `/orphan/` is disallowed but pages exist there (blocked pages with content)

**Expected:** robots.txt parsing should report each of these as distinct findings.
