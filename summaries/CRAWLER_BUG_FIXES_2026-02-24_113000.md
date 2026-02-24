# SEO Crawler Bug Fixes — 2026-02-24

## Overview

Fixed 15 functional bugs and several false negatives from the 199-page crawl bug report against `testing-crawler.pages.dev`. Also increased default `max_pages` to 200 and `max_depth` to 6.

## Files Modified

| File | Changes |
|------|---------|
| `infrastructure/worker/wrangler.toml` | max_pages=200, max_depth=6 |
| `infrastructure/worker/src/index.ts` | Updated MCP tool parameter descriptions |
| `infrastructure/docker/server.py` | BUG-01, BUG-03, BUG-07, BUG-02, BUG-17 fixes |
| `crawl4ai/seo_audit/checks.py` | BUG-03, BUG-12, BUG-16, BUG-17, BUG-18, BUG-21 fixes |
| `crawl4ai/seo_audit/models.py` | New fields: long_alt, keyword_stuffed_alt, broken_src, inline_css_count, inline_style_bytes |
| `crawl4ai/seo_audit/site_checks.py` | BUG-05, BUG-19, BUG-20, BUG-21 + image quality site-level checks |

## Bug Fix Details

### HIGH Priority
- **BUG-01: Broken internal links not detected** — Soft-404 pages (HTTP 200 but "not found" content) now added to `broken_internal_urls` set so links pointing to them are flagged.

### MEDIUM Priority
- **BUG-03: Redirect chain meta-refresh not followed** — Fixed URL extraction to preserve original case (was lowercasing entire URL). Enhanced chain detection to record linear chains (2+ hops) in addition to loops.
- **BUG-05: Noindex-in-sitemap not detected** — Added path-only normalization as fallback for URL matching between sitemap and crawled URLs.
- **BUG-07: External broken links /500, /503 not detected** — Rewrote external link checker to use concurrent checks (`asyncio.gather` with semaphore of 10) instead of sequential, preventing overall timeout from cutting short the checks.
- **BUG-02: httpstat.us misclassified as DNS error** — Added `aiohttp.ClientConnectorDNSError` catch to distinguish DNS failures (-4) from generic connection errors (-2). Updated `_status_desc` to report `DNS_ERROR` vs `CONNECTION_ERROR` separately.
- **BUG-17: Broken image src not verified** — Three-layer fix: (1) `checks.py` detects empty/missing `src` attributes, (2) JS snippet injected via Playwright detects broken images via `naturalWidth === 0`, (3) `server.py` merges JS-detected broken count into page audit.
- **BUG-19: Hreflang bidirectional confirmation** — New `_check_hreflang_bidirectional` site-level check verifies that hreflang target pages link back with a matching hreflang tag.
- **BUG-20: Pagination issues not analyzed** — New `_check_pagination_seo` site-level check flags URLs matching pagination patterns (`/page/N`, `?p=N`, etc.) that lack `rel=next/prev` hints.

### LOW-MEDIUM Priority
- **BUG-04: Content hash includes template** — Already handled: `_compute_content_hash` and `_compute_content_shingles` strip `nav`, `header`, `footer` before processing.
- **BUG-12: Word count inflated by template** — `check_content` now strips `nav`, `header`, `footer` elements before counting words.
- **BUG-16: Long/keyword-stuffed alt text not detected** — `check_images` now detects alt text >125 chars and keyword-stuffed alt (same word repeated 3+ times). New site-level `_check_image_quality_issues` surfaces these as warnings.

### LOW Priority
- **BUG-18: Hreflang fr-XX invalid region code** — Added ISO 3166-1 alpha-2 region code validation (`_VALID_REGION_CODES` frozenset) to `check_hreflang`. Invalid region subtags like `fr-XX` are now flagged.
- **BUG-21: Excessive inline CSS not detected** — Added `inline_css_count` (elements with `style` attr) and `inline_style_bytes` (total `<style>` block size) to `PageAuditResult`. New `_check_excessive_inline_css` site-level check flags pages exceeding thresholds.
- **BUG-15: og:image FP on control page** — Not a bug. Missing og:image is a valid SEO finding regardless of page purpose.
- **BUG-09: Inflated performance score** — Skipped. Requires Lighthouse integration which is a separate feature.

## New Model Fields

```python
# ImageCheck (models.py)
long_alt: int = 0           # Images with alt text > 125 chars
keyword_stuffed_alt: int = 0 # Images with repetitive keyword alt text
broken_src: int = 0          # Images with broken/empty src

# PageAuditResult (models.py)
inline_css_count: int = 0    # Elements with style attribute
inline_style_bytes: int = 0  # Total bytes in <style> blocks
```

## New Site-Level Issue Types

| Issue Type | Severity | Description |
|------------|----------|-------------|
| `hreflang_missing_return` | WARNING | Hreflang tags without bidirectional confirmation |
| `pagination_missing_hints` | WARNING | Paginated URLs missing rel=next/prev |
| `excessive_inline_css` | INFO | Pages with >20 style attrs or >50KB inline styles |
| `long_alt_text` | WARNING | Images with alt text >125 characters |
| `keyword_stuffed_alt` | WARNING | Images with keyword-stuffed alt text |
| `broken_image_src` | WARNING | Images with broken or empty src |

## Error Code Changes (External Link Checker)

| Code | Old Label | New Label |
|------|-----------|-----------|
| -1 | (unmapped) | TIMEOUT |
| -2 | DNS_OR_CONNECTION_ERROR | CONNECTION_ERROR |
| -3 | SSL_ERROR | SSL_ERROR |
| -4 | (new) | DNS_ERROR |

## Deployment

Follow standard deploy steps (copy crawl4ai → docker context, `npm run deploy`, cleanup).
