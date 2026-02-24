# Bug Fixes Round 8 (2026-02-23)

**Source:** `CRAWLER_BUG_REPORT.md` | **Test URL:** https://testing-crawler.pages.dev

## 16 Fixes Across 5 Files

### checks.py
1. **BUG-11** — Empty H1 vs missing H1 distinction (has_empty_h1 field)
2. **BUG-9** — Placeholder FP: strip nav/header/footer/a before scanning
3. **FN-11** — Hidden text: 4 techniques (white-on-white, off-screen)
4. **FN-12** — IFrame missing title / empty src detection
5. **FN-9** — Lorem ipsum in meta description (has_placeholder_meta_desc)
6. **FN-8** — Skipped headings: detect first heading skipping H1
7. **BUG-8** — Content shingles for near-duplicate detection

### models.py
8. **New fields:** has_empty_h1, iframes_missing_title, iframes_empty_src, has_placeholder_meta_desc, content_shingles

### site_checks.py
9. **BUG-11** — empty_h1 WARNING issue; missing_h1 excludes empty H1
10. **FN-12** — iframe_accessibility WARNING issue
11. **FN-9** — placeholder_meta_description WARNING issue
12. **BUG-8** — Near-duplicate via Jaccard similarity (70% threshold)
13. **BUG-1** — Broken internal links: normalized URL matching, CRITICAL severity

### server.py
14. **BUG-4** — JS snippet detects images missing explicit width/height attrs
15. **BUG-5** — External link check: GET fallback when HEAD fails
16. **BUG-3** — Meta-refresh redirect chain tracking
17. **BUG-2** — Post-crawl meta-refresh loop detection (fetches uncrawled targets)

### domain_checks.py
18. **BUG-6** — Sitemap ref validation: GET + XML parse (not just HEAD)
19. **BUG-7** — Invalid XML sitemap: regex fallback URL extraction

### index.ts
20. **BUG-10** — Lighthouse job_id: generate UUID and pass to container
