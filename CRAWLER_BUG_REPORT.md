# Bug Report: SEO Crawler — Cross-Referenced Against Test Site Documentation

**Test Target:** https://testing-crawler.pages.dev  
**Crawl Job ID:** `988325eb-d027-4a99-ab19-834c17091149`  
**Date:** 2026-02-24  
**Crawl Settings:** max_pages=300, default depth  
**Pages Found:** 335 | Pages Crawled: 260  
**Reference Doc:** SEO Test Site Issue Documentation (78 distinct intentional issues across ~71 pages)

---

## Cross-Reference Summary: What the Crawler Missed

The test site documentation catalogs 78 distinct issues across ~71 unique pages. Below is a full cross-reference showing what the crawler detected, partially detected, and completely missed.

### Pages NOT Crawled (should have been discovered)

| Page | URL | Why It Was Missed |
|---|---|---|
| Redirect chain start | `/links/redirect-chain-start` | Page uses meta refresh — crawler followed redirect but didn't record the page itself in page_audits |
| Redirect chain middle | `/links/redirect-chain-middle` | Intermediate redirect hop — never appeared in page_audits or link_graph |
| Deep level 3 | `/deep/level1/level2/level3/` | Discovered in link_graph (from level2) but never crawled |
| Deep level 4 | `/deep/level1/level2/level3/level4/` | Never discovered |
| Deep level 5 / buried | `/deep/level1/level2/level3/level4/level5/buried-page` | Never discovered |
| Orphan page 1 | `/orphan/lost-page-1` | By design: no inbound links, blocked by robots.txt |
| Orphan page 2 | `/orphan/lost-page-2` | By design: no inbound links, blocked by robots.txt |
| Deep orphan | `/orphan/deep-orphan` | By design: orphaned + noindex + blocked by robots.txt |

### Issues DETECTED Correctly

| Doc ID | Issue | Detection | Notes |
|---|---|---|---|
| S1 | Broken sitemap reference in robots.txt | ✅ Full | `robots_broken_sitemap_ref` warning — sitemap-old.xml |
| S2 | Conflicting robots.txt rules | ✅ Full | `robots_conflicting_rules` — Googlebot Allow /meta/ vs Disallow /meta/nofollow-page |
| S3 | Crawl-delay directive | ✅ Full | `robots_crawl_delay` info — Crawl-delay: 10 for Bingbot |
| S6 | Trailing slash inconsistency | ✅ Full | `trailing_slash_inconsistency` — 35 pages with mismatch |
| M1 | Missing title | ✅ Full | `missing_title` critical — `/meta/missing-title` |
| M2/M3 | Duplicate titles | ✅ Full | `duplicate_titles` — detected 2 duplicate groups across 194 pages |
| M4 | Title too long | ✅ Full | `long_title` — 2 pages |
| M5 | Title too short | ✅ Full | `short_title` — 195 pages |
| M6 | Missing meta description | ✅ Full | `missing_meta_description` — `/meta/missing-description` |
| M7 | Description too long | ✅ Full | `long_description` — 1 page |
| M8 | Description too short | ✅ Full | `short_description` — 7 pages |
| M9/M10 | Duplicate descriptions | ✅ Full | `duplicate_meta_descriptions` — 194 pages |
| M11 | Missing viewport | ✅ Full | `missing_viewport` critical — `/meta/missing-viewport` |
| M12 | Missing canonical | ✅ Full | `missing_canonical` — 4 pages |
| M13 | Wrong canonical (404 target) | ✅ Full | `canonical_target_missing` — `/meta/wrong-canonical` |
| M14 | Multiple canonicals | ✅ Full | `multiple_canonicals` — `/meta/multiple-canonicals` |
| M15 | Missing OG tags | ✅ Full | `missing_open_graph` — 30 pages |
| M16 | Incomplete OG tags | ✅ Full | `incomplete_og` — 230 pages |
| M18 | Page-level nofollow | ✅ Full | `page_nofollow` info |
| M19 | Meta refresh redirect | ✅ Full | `meta_refresh` warning — 3 pages |
| M20 | Missing lang attribute | ✅ Full | `missing_lang` — `/meta/missing-lang` |
| M21 | Missing charset | ✅ Full | `missing_charset` — `/meta/missing-charset` |
| C1 | No H1 | ✅ Full | `missing_h1` critical — `/content/no-h1` |
| C2 | Multiple H1 | ✅ Full | `multiple_h1` — `/content/multiple-h1` |
| C3 | H1 too long | ✅ Full | `long_h1` — 2 pages |
| C4 | Empty H1 | ✅ Full | `empty_h1` — `/content/empty-h1` |
| C5 | Skipped headings | ✅ Full | `skipped_headings` — 61 pages |
| C6 | Thin content | ✅ Full | `thin_content` — 63 pages |
| C7/C8 | Duplicate content | ✅ Full | `duplicate_content` — 194 pages (1 identical group + 2 near-duplicates) |
| C9 | Keyword stuffing | ✅ Full | `keyword_stuffing` — 'seo' at 30.3% density |
| C10 | Hidden text | ✅ Full | `hidden_text` — `/content/hidden-text` |
| C11 | Lorem ipsum | ✅ Full | `placeholder_content` + `placeholder_meta_description` |
| L2 | Broken external links | ✅ Full | `broken_external_links` — 3 URLs (DNS, connection, SSL errors) |
| L3 | Too many links | ✅ Full | `excessive_links` — 193 pages with 150+ links |
| L4 | Internal nofollow | ✅ Full | `internal_nofollow` info — 4 links on 1 page |
| L5 | Sponsored/UGC links | ✅ Full | `sponsored_ugc_links` — 2 + 2 on `/links/nofollow-links` |
| L6/L7 | JavaScript/empty hrefs | ✅ Full | `javascript_hrefs` — 3 js + 5 empty/hash on 1 page |
| L9 | Redirect loop | ✅ Full | `redirect_loops` critical — loop-a ↔ loop-b detected |
| I1 | Missing alt text | ✅ Full | `images_missing_alt` — 2 images on 1 page |
| I2 | Empty alt | ✅ Full | `images_empty_alt` info — 3 images on 1 page |
| I3 | Long/keyword-stuffed alt | ✅ Full | `long_alt_text` + `keyword_stuffed_alt` — 2 images on 1 page |
| I5 | Missing dimensions | ✅ Full | `images_missing_dimensions` — 3 images on 1 page |
| U1 | Uppercase URL | ✅ Full | `uppercase_urls` info |
| U2 | Long URL | ✅ Full | `long_urls` info |
| SD1 | Valid JSON-LD | ✅ Full | Correctly passes validation on `/schema/valid` |
| SD2 | Invalid JSON-LD | ✅ Full | `structured_data_issues` — `/schema/invalid-json` |
| SD3 | Missing schema fields | ✅ Full | `structured_data_issues` — `/schema/missing-fields` |
| SD4 | Wrong schema type | ✅ Full | `structured_data_issues` — `/schema/wrong-type` |
| H1 | Invalid hreflang codes | ✅ Full | `hreflang_issues` — english, en_US, zz, fr-XX all flagged |
| SEC1 | Mixed content | ✅ Full | `mixed_content` — 2 pages |
| SEC2 | HTTP canonical on HTTPS | ✅ Full | Included in mixed_content warning for `/security/http-canonical` |
| X1 | Soft 404 | ✅ Full | `soft_404` warning — `/misc/soft-404` |
| X2 | IFrame issues | ✅ Full | `iframe_accessibility` + `iframe_content` |
| X3 | Excessive inline CSS | ✅ Full | `excessive_inline_css` info |
| X4 | Blocked by robots but linked | ✅ Full | `crawled_but_blocked` — 2 pages |
| X5 | Canonical to different page | ✅ Full | `cross_page_canonical` info — 192 pages |

### Issues PARTIALLY Detected

| Doc ID | Issue | Detection | What's Missing |
|---|---|---|---|
| S5 | Noindex in sitemap | ⚠️ Partial | Page `/meta/noindex-in-sitemap` was crawled and has noindex. But the crawler does not raise a specific "noindex page found in sitemap" issue. The `noindex-in-sitemap` issue type is not in `site_issues`. |
| L1 | Broken internal links (8 targets) | ⚠️ Partial | Only `/misc/soft-404` is flagged as broken. The 7 other broken link targets (`/page-that-does-not-exist`, `/products/deleted-product`, `/blog/old-post-removed`, `/about/team/john-doe`, `/services/discontinued-service`, `/category/nonexistent`, `/2024/01/archived-content`, `/api/v1/docs`) are NOT detected as broken because the site returns HTTP 200 with homepage content for them (catch-all behavior). |
| L8 | Redirect chain (3 hops) | ⚠️ Partial | Crawler records chain_length=1 for `/links/redirect-chain-start` with only a trailing slash redirect. The full 3-hop chain (start → middle → end) was not traced. `/links/redirect-chain-middle` never appears in any table. |
| I4 | Broken image sources (4 images) | ⚠️ Partial | Page audit JSON shows 4 images total but `broken_src: 3`. Additionally, the `broken_images` site issue says "1 broken/empty image src" while `broken_image_src` says "3 images with broken or empty src". The doc says there are 4 broken images. One broken image may not be detected, and the two warnings give conflicting counts. |
| D1-D5 | Deep page structure (5 levels) | ⚠️ Partial | Only levels 1 and 2 were crawled. Level 3 was discovered (appears in link_graph from level2) but never crawled. Levels 4 and 5 were never discovered. max_depth=6 should have been sufficient. |
| H2 | Missing hreflang return tags | ⚠️ Partial | The page `/hreflang/missing-return` was crawled, but the specific issue of missing bidirectional confirmation, missing self-referencing hreflang, and missing x-default is not explicitly flagged as a separate issue. Only H1 (wrong-codes) appears in `hreflang_issues`. |
| P1-P3 | Pagination issues | ⚠️ Partial | Pages were crawled and short descriptions flagged. But missing `rel=next/prev`, "page 1 at /1" anti-pattern, and near-identical pagination descriptions are NOT specifically flagged. |

### Issues NOT Detected

| Doc ID | Issue | Notes |
|---|---|---|
| S4 | Orphan path blocked in robots.txt | `/orphan/` disallowed in robots.txt — but the crawler doesn't flag that the disallowed path contains actual pages. Not a clear miss since orphan pages are unreachable by design. |
| O1 | Orphan: `/orphan/lost-page-1` | Not crawled (by design — no inbound links + robots blocked). However, crawler detected orphan pages `/links/redirect-chain-end` and `/insecure-page` instead. The /orphan/* pages are genuinely unreachable. |
| O2 | Orphan: `/orphan/lost-page-2` | Same as O1 |
| O3 | Orphan: `/orphan/deep-orphan` | Same as O1, plus noindex + not in sitemap |
| M17 | Noindex page in sitemap conflict | The page was crawled, but no issue specifically says "this noindex page appears in the sitemap". The crawler doesn't cross-reference noindex directives against sitemap contents. |

---

## Detailed Bug Reports

### BUG-001: Broken Internal Links — Soft 404 Pages Recorded with Empty Status Code

**Severity:** High  
**Component:** Broken link detection / `broken_links` table

All 644 internal entries in the `broken_links` table have an **empty `status_code`** and `status_code_desc` = `"unknown"`. The only unique internal target URL is `/misc/soft-404`, which returns HTTP 200.

```sql
SELECT COUNT(*) as total, status_code, status_code_desc 
FROM broken_links 
WHERE job_id = '988325eb-d027-4a99-ab19-834c17091149' AND link_type = 'internal'
GROUP BY status_code, status_code_desc;
-- Result: 644 rows with status_code = '' (empty), status_code_desc = 'unknown'
```

**Fix:** Populate `status_code` with the actual HTTP response code. Set `status_code_desc` to `"soft_404"` when appropriate. Distinguish soft 404s from real 404s in the issue output.

---

### BUG-002: Catch-All Pages (HTTP 200 for Non-Existent URLs) Not Detected as Broken [CRITICAL]

**Severity:** Critical  
**Component:** Broken link detection  
**Cross-ref:** L1 (broken internal links — 8 targets expected, only soft 404 detected)

The `/links/broken-internal` page links to 8 URLs that should be broken:
- `/page-that-does-not-exist`
- `/products/deleted-product`
- `/blog/old-post-removed`
- `/about/team/john-doe`
- `/services/discontinued-service`
- `/category/nonexistent`
- `/2024/01/archived-content`
- `/api/v1/docs`

All return HTTP 200 with the homepage content (911 words, title "SEO Test Site"). The crawler treats them as legitimate pages.

**Expected:** The crawler should detect these as catch-all/soft-404 pages by comparing their content hash to the homepage. All share `content_hash` identical to the homepage. The crawler already computes content hashes and shingles — it should use them to flag "this page's content is identical to the homepage but has a different URL" as a suspected catch-all response.

**Impact:** The `broken_internal_links` critical issue reports "1 broken target" when there are actually 8+. Seven real broken link targets are completely missed.

---

### BUG-003: Lighthouse Bulk Audit Timeout on All URLs

**Severity:** High  
**Component:** `lighthouse_bulk` tool

All 5 URLs tested with Lighthouse timed out. Pages are ~10KB static HTML with ~40ms response times.

```
lighthouse_bulk result: 5 URL(s) tested (5 failed)
All: ERROR — timeout
```

**Investigate:** Container headless Chrome startup, timeout thresholds, network/DNS in Cloudflare container.

---

### BUG-004: Deep Page Crawling Stops at Level 2 (Should Reach Level 5) — CONFIRMED NOT A BUDGET ISSUE

**Severity:** Critical  
**Component:** Crawl depth traversal  
**Cross-ref:** D1-D5 (5 levels expected, only 2 crawled)

The site has a 5-level deep page chain:
```
/deep/level1/ → /deep/level1/level2/ → .../level3/ → .../level4/ → .../level5/buried-page
```

**Reproduced across two crawls with different budgets:**

| Crawl | max_pages | Pages Crawled | Generated-link pages | Deep levels reached | Budget remaining |
|---|---|---|---|---|---|
| 1st | 300 | 260 | 178 | 2 of 5 | 40 unused |
| 2nd | 400 | 209 | 127 | 2 of 5 | **191 unused** |

**Both crawls show identical behavior:**
- **Crawled:** level1, level2 ✅
- **Discovered but not crawled:** level3 (appears in link_graph as target from level2 in both crawls) ❌
- **Never discovered:** level4, level5/buried-page ❌

**This is NOT a budget issue.** The second crawl had 191 pages of unused budget and still stopped at level 2. The crawler knows about level3 (it's recorded in the link_graph) but never visits it. This is a depth traversal bug — the crawler is failing to follow discovered links beyond a certain point.

**Additional evidence:** The second crawl actually crawled *fewer* pages (209 vs 260) with a *larger* budget (400 vs 300), and fewer generated-link pages (127 vs 178). The crawl is terminating prematurely and inconsistently regardless of budget.

**Possible root causes:**
1. The crawler may have a hardcoded or effective max_depth that's lower than the configured value, preventing traversal past depth ~3-4 from the homepage.
2. The URL queue may be draining/terminating before all discovered URLs are visited.
3. There may be a trailing slash issue — level2 links to `/deep/level1/level2/level3` but the canonical may be `/deep/level1/level2/level3/` — the crawler might be treating these as different URLs and getting confused.
4. The crawl loop may be exiting early when the rate of new URL discovery drops below some threshold.

**Fix:** Investigate why discovered-but-not-crawled URLs are being abandoned. The link_graph proves level3 was discovered — there is no reason it shouldn't be crawled when 191 pages of budget remain.

---

### BUG-005: Redirect Chain Not Fully Traced (3 Hops Expected, Only Trailing Slash Recorded)

**Severity:** High  
**Component:** Redirect chain analysis  
**Cross-ref:** L8 (redirect chain with 3 hops: start → middle → end)

The test site has a 3-hop meta refresh redirect chain:
```
/links/redirect-chain-start → /links/redirect-chain-middle → /links/redirect-chain-end
```

**Crawler recorded:**
```json
{
  "source_url": "/links/redirect-chain-start",
  "final_url": "/links/redirect-chain-end",
  "chain_length": 1,
  "chain_path": ["/links/redirect-chain-start", "/links/redirect-chain-start/"]
}
```

**Problems:**
1. `chain_length` is 1, recording only a trailing slash redirect, not the actual meta refresh chain.
2. `/links/redirect-chain-middle` never appears in ANY table (page_audits, link_graph, redirect_chains).
3. `/links/redirect-chain-start` was not crawled as a page (not in page_audits) — only the redirect destination (`/links/redirect-chain-end`) was recorded.
4. The chain path shows the final_url is `/links/redirect-chain-end` but the chain_path only has the slash redirect — the full hop sequence is lost.

**Fix:** When following meta refresh redirects, record each hop in the chain_path. The redirect-chain-start and redirect-chain-middle pages should be recorded even though they redirect.

---

### BUG-006: Broken Image Count Discrepancy

**Severity:** Medium  
**Component:** Image audit / site issue aggregation  
**Cross-ref:** I4 (4 broken images expected)

The `/images/broken-src` page has 4 broken images per the documentation:
1. Non-existent local file (`/images/nonexistent-photo.jpg`)
2. Non-existent domain (`https://this-domain-does-not-exist-xyz.com/photo.png`)
3. Deleted file (`/assets/deleted-image.webp`)
4. Empty src (`""`)

**Crawler reports:**
- Page-level audit JSON: `"broken_src": 3` (but lists all 4 images)
- Site issue `broken_images`: "1 broken/empty image src(s) across 1 page(s)"
- Site issue `broken_image_src`: "3 image(s) across 1 page(s) have broken or empty src"

**Problems:**
1. Three different counts for the same issue: 1, 3, and 3 (should be 4).
2. The `broken_images` warning says 1, while `broken_image_src` says 3 — two different issue types reporting conflicting numbers for the same page.
3. One of the 4 broken images is not counted at all.

**Fix:** Unify broken image counting. All 4 images on this page have invalid/broken sources and should be counted.

---

### BUG-007: Missing "Noindex Page in Sitemap" Detection

**Severity:** Medium  
**Component:** Sitemap cross-referencing  
**Cross-ref:** M17, S5

The page `/meta/noindex-in-sitemap` has `<meta name="robots" content="noindex, nofollow">` but appears in sitemap.xml. This is a well-known SEO conflict.

**Crawler behavior:** The page was crawled and its noindex directive was recorded. The sitemap was parsed (and flagged as having invalid XML). But no issue specifically flags the conflict: "this noindex page appears in the sitemap."

**Fix:** After parsing both the sitemap and the page audits, cross-reference to find any noindex pages that appear in the sitemap. Raise a warning like "X noindex page(s) found in sitemap.xml — remove them or change the indexing directive."

---

### BUG-008: Missing Pagination Issue Detection

**Severity:** Medium  
**Component:** Pagination analysis  
**Cross-ref:** P1-P3

Three pagination pages (`/pagination/1`, `/pagination/2`, `/pagination/3`) were crawled, but no pagination-specific issues were raised:

| Expected Issue | Detected? |
|---|---|
| P1: First page at /1 (should be at base URL) | ❌ Not detected |
| P2: Missing rel=next/prev hints | ❌ Not detected (page audits show `rel_next: null`, `rel_prev: null` but no warning raised) |
| P3: Near-identical meta descriptions across pages | ❌ Not detected as a pagination-specific issue (only flagged as `short_description`) |

**Fix:** Add pagination-aware issue detection. When pages follow a pattern like `/path/1`, `/path/2`, `/path/3` and lack `rel=next/prev`, flag it. When paginated pages have near-identical meta descriptions differing only in page number, flag that too.

---

### BUG-009: Missing Hreflang Return Tag Detection

**Severity:** Low  
**Component:** Hreflang validation  
**Cross-ref:** H2

The page `/hreflang/missing-return` declares fr/de/es alternates that don't exist (no bidirectional confirmation). It also lacks a self-referencing hreflang and missing x-default. The crawler crawled the page but only raised `hreflang_issues` for `/hreflang/wrong-codes` (H1), not for `/hreflang/missing-return` (H2).

**Fix:** Validate that hreflang alternate targets exist and contain reciprocal hreflang declarations. Flag missing self-referencing hreflang and missing x-default.

---

### BUG-010: Critical Issue Count Mismatch in Job Summary

**Severity:** Low  
**Component:** Issue counting / summary  

Job summary says "Critical Issues: 5" but `get_issues(severity="critical")` returns 6 entries:
1. missing_h1
2. missing_title
3. missing_viewport
4. redirect_loops (HTTP redirect type)
5. redirect_loops (meta refresh type)
6. broken_internal_links

`redirect_loops` appears twice with different descriptions. The summary undercounts.

---

### BUG-011: Anchor Text Extraction Includes Raw CSS

**Severity:** Medium  
**Component:** broken_links anchor_text extraction  

Observed in shared DB data from other crawl jobs: `anchor_text` field contains raw CSS rules instead of just visible link text. Example:
```
"Send me an email\n  .btn {\n    display: inline-flex;..."
```

**Fix:** Strip CSS/style content from anchor text extraction. Use `.textContent` equivalent rather than `.innerHTML`.

---

### BUG-012: max_pages Parameter Ignored — Crawl Terminates Early With Large Unused Budget

**Severity:** High (upgraded from Low)  
**Component:** Crawl termination logic  

**Reproduced across two crawls:**

| Crawl | max_pages | Pages Crawled | Pages Discovered | Budget Used | Budget Wasted |
|---|---|---|---|---|---|
| 1st | 300 | 260 | 335 | 87% | 40 pages |
| 2nd | 400 | 209 | 335 | 52% | **191 pages** |

The second crawl is particularly damning: 335 pages were discovered but only 209 crawled, leaving 191 pages of unused budget (48% waste). The crawler stopped with 126 discovered-but-unvisited URLs remaining.

This is not just a minor discrepancy — the crawler is terminating well before exhausting either its page budget or the discovered URL queue. Combined with BUG-004 (deep pages discovered but never visited), this points to a fundamental issue in the crawl loop's termination condition.

**The crawl is also non-deterministic:** Running the same site with a *larger* budget produced *fewer* results (209 < 260). This suggests a race condition or non-deterministic queue drain in the crawl loop.

---

## Summary Table

| Bug ID | Severity | Cross-ref | Description |
|---|---|---|---|
| BUG-001 | High | — | Empty status_code for all internal broken links in broken_links table |
| BUG-002 | Critical | L1 | Catch-all pages (200 serving homepage) not detected as broken — 7 of 8 broken targets missed |
| BUG-003 | High | — | Lighthouse bulk audits all timeout |
| BUG-004 | **Critical** | D1-D5 | Deep pages stop at level 2; levels 3-5 never crawled despite budget and depth settings. **Confirmed not a budget issue** — reproduced with 191 unused pages of budget. Discovered URLs abandoned. |
| BUG-005 | High | L8 | 3-hop redirect chain only records trailing slash; middle page never seen |
| BUG-006 | Medium | I4 | Broken image count is 1 or 3 depending on issue type; should be 4 |
| BUG-007 | Medium | M17, S5 | No "noindex page in sitemap" detection |
| BUG-008 | Medium | P1-P3 | No pagination-specific issue detection (missing rel=next/prev, /1 pattern, similar descriptions) |
| BUG-009 | Low | H2 | Missing hreflang return tag validation for `/hreflang/missing-return` |
| BUG-010 | Low | — | Critical issue count: summary says 5, API returns 6 |
| BUG-011 | Medium | — | Raw CSS in anchor_text field |
| BUG-012 | **High** | D1-D5 | max_pages ignored — crawl terminates early. 2nd crawl used only 52% of budget (209/400). Non-deterministic: larger budget produced fewer results. |

## Detection Rate Summary

- **Issues fully detected:** 52 out of 78 (67%)
- **Issues partially detected:** 7 (9%)
- **Issues not detected:** 5 (6%)
- **Issues not applicable (orphan pages by design):** 3
- **Missing pages (not crawled):** 8 pages including 3 deep levels, 2 redirect chain pages, 3 orphan pages
