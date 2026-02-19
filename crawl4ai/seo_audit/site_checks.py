"""
Site-wide SEO checks.

Ported from seo-audit-mcp's crawl-site.ts generateCrawlSummary() logic.
Operates on a collection of PageAuditResult objects to find cross-page issues
like duplicate titles, orphan pages, deep link structures, etc.
"""

from collections import defaultdict, Counter
from typing import List, Dict, Set, Optional, Any
from urllib.parse import urlparse

from .models import (
    IssueSeverity,
    CheckStatus,
    PageAuditResult,
    SiteIssue,
    SiteAuditSummary,
    SiteAuditResult,
    CategoryScore,
    ScoreBreakdown,
    DomainCheckResult,
)


def run_site_checks(
    page_results: Dict[str, PageAuditResult],
    internal_link_graph: Optional[Dict[str, List[str]]] = None,
    domain_checks: Optional[DomainCheckResult] = None,
    broken_internal_urls: Optional[Set[str]] = None,
    crawl_metadata: Optional[Dict[str, Any]] = None,
) -> SiteAuditResult:
    """
    Run all site-wide SEO checks across a set of audited pages.

    Args:
        page_results: Mapping of URL -> PageAuditResult.
        internal_link_graph: Optional mapping of URL -> list of URLs it links to.
        domain_checks: Optional domain-level check results (robots.txt, sitemap).
        broken_internal_urls: Optional set of internal URLs that returned 404.
        crawl_metadata: Optional dict with crawl timing info (crawl_duration_s, etc.).

    Returns:
        SiteAuditResult with summary, issues, and per-page details.
    """
    critical: List[SiteIssue] = []
    warnings: List[SiteIssue] = []
    info: List[SiteIssue] = []

    # ── Duplicate titles ──────────────────────────────────────────────
    issue = _check_duplicate_titles(page_results)
    if issue:
        (warnings if issue.severity == IssueSeverity.WARNING else critical).append(issue)

    # ── Duplicate meta descriptions ───────────────────────────────────
    issue = _check_duplicate_descriptions(page_results)
    if issue:
        (warnings if issue.severity == IssueSeverity.WARNING else critical).append(issue)

    # ── Missing H1 tags ──────────────────────────────────────────────
    issue = _check_missing_h1(page_results)
    if issue:
        critical.append(issue)

    # ── Missing titles ────────────────────────────────────────────────
    issue = _check_missing_titles(page_results)
    if issue:
        critical.append(issue)

    # ── Missing meta descriptions ─────────────────────────────────────
    issue = _check_missing_descriptions(page_results)
    if issue:
        warnings.append(issue)

    # ── Missing canonical tags ────────────────────────────────────────
    issue = _check_missing_canonicals(page_results)
    if issue:
        warnings.append(issue)

    # ── Missing Open Graph ────────────────────────────────────────────
    issue = _check_missing_open_graph(page_results)
    if issue:
        info.append(issue)

    # ── Thin content ──────────────────────────────────────────────────
    issue = _check_thin_content(page_results)
    if issue:
        warnings.append(issue)

    # ── Missing viewport ──────────────────────────────────────────────
    issue = _check_missing_viewport(page_results)
    if issue:
        critical.append(issue)

    # ── Mixed content ─────────────────────────────────────────────────
    issue = _check_site_mixed_content(page_results)
    if issue:
        warnings.append(issue)

    # ── Image alt text ────────────────────────────────────────────────
    issue = _check_images_missing_alt(page_results)
    if issue:
        warnings.append(issue)

    # ── Image optimization ────────────────────────────────────────────
    issue = _check_image_optimization(page_results)
    if issue:
        (warnings if issue.severity == IssueSeverity.WARNING else info).append(issue)

    # ── Broken internal links ─────────────────────────────────────────
    if broken_internal_urls:
        issue = _check_broken_links(page_results, broken_internal_urls)
        if issue:
            warnings.append(issue)

    # ── Performance issues ────────────────────────────────────────────
    issue = _check_slow_pages(page_results)
    if issue:
        (warnings if issue.severity == IssueSeverity.WARNING else info).append(issue)

    issue = _check_heavy_pages(page_results)
    if issue:
        (warnings if issue.severity == IssueSeverity.WARNING else info).append(issue)

    # ── Domain-level issues (robots.txt, sitemap) ─────────────────────
    if domain_checks:
        domain_issues = _check_domain_issues(domain_checks)
        for di in domain_issues:
            if di.severity == IssueSeverity.CRITICAL:
                critical.append(di)
            elif di.severity == IssueSeverity.WARNING:
                warnings.append(di)
            else:
                info.append(di)

    # ── Orphan pages (if link graph available) ────────────────────────
    if internal_link_graph is not None:
        issue = _check_orphan_pages(page_results, internal_link_graph)
        if issue:
            warnings.append(issue)

        issue = _check_deep_pages(page_results, internal_link_graph)
        if issue:
            info.append(issue)

    # ── Compute score breakdown ───────────────────────────────────────
    breakdown = _compute_score_breakdown(page_results, domain_checks)

    # Total score = sum of all category scores
    score = sum(
        getattr(breakdown, field).score
        for field in breakdown.model_fields
    )

    # ── Compute pass rates ────────────────────────────────────────────
    pass_rates = _compute_pass_rates(page_results)

    # ── Top issues summary ────────────────────────────────────────────
    all_issues = critical + warnings + info
    top_issues = _compute_top_issues(all_issues)

    # ── Crawl metadata ────────────────────────────────────────────────
    meta = _compute_crawl_metadata(page_results, crawl_metadata)

    n_critical = len(critical)
    n_warning = len(warnings)
    n_info = len(info)

    summary = SiteAuditSummary(
        pages_audited=len(page_results),
        issues_critical=n_critical,
        issues_warning=n_warning,
        issues_info=n_info,
        score=score,
        score_breakdown=breakdown,
        pass_rates=pass_rates,
        top_issues=top_issues,
        crawl_metadata=meta,
    )

    return SiteAuditResult(
        summary=summary,
        critical=critical,
        warnings=warnings,
        info=info,
        page_details=page_results,
        domain_checks=domain_checks,
    )


# ─── Individual Site-Wide Checks ──────────────────────────────────────


def _check_duplicate_titles(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    title_map: Dict[str, List[str]] = defaultdict(list)
    for url, result in pages.items():
        # Skip redirect pages (301/302/etc.) — they share the destination's title
        if result.status_code and 300 <= result.status_code < 400:
            continue
        if result.title.value:
            title_map[result.title.value.lower()].append(url)

    duplicates = {t: urls for t, urls in title_map.items() if len(urls) > 1}
    if not duplicates:
        return None

    affected = []
    for urls in duplicates.values():
        affected.extend(urls)

    return SiteIssue(
        issue_type="duplicate_titles",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(duplicates)} duplicate title(s) across {len(affected)} pages",
        fix="Ensure each page has a unique, descriptive title tag",
    )


def _check_duplicate_descriptions(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    desc_map: Dict[str, List[str]] = defaultdict(list)
    for url, result in pages.items():
        # Skip redirect pages (301/302/etc.) — they share the destination's description
        if result.status_code and 300 <= result.status_code < 400:
            continue
        if result.meta_description.value:
            desc_map[result.meta_description.value.lower()].append(url)

    duplicates = {d: urls for d, urls in desc_map.items() if len(urls) > 1}
    if not duplicates:
        return None

    affected = []
    for urls in duplicates.values():
        affected.extend(urls)

    return SiteIssue(
        issue_type="duplicate_meta_descriptions",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(duplicates)} duplicate meta description(s) across {len(affected)} pages",
        fix="Write unique meta descriptions for each page",
    )


def _check_missing_h1(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    affected = [url for url, r in pages.items() if r.headings.h1_count == 0]
    if not affected:
        return None
    return SiteIssue(
        issue_type="missing_h1",
        severity=IssueSeverity.CRITICAL,
        affected_pages=affected,
        description=f"{len(affected)} page(s) missing H1 tag",
        fix="Add a single, descriptive H1 tag to each page",
    )


def _check_missing_titles(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    affected = [url for url, r in pages.items() if not r.title.value]
    if not affected:
        return None
    return SiteIssue(
        issue_type="missing_title",
        severity=IssueSeverity.CRITICAL,
        affected_pages=affected,
        description=f"{len(affected)} page(s) missing title tag",
        fix="Add a descriptive title tag (50-60 chars) to each page",
    )


def _check_missing_descriptions(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    affected = [url for url, r in pages.items() if not r.meta_description.value]
    if not affected:
        return None
    return SiteIssue(
        issue_type="missing_meta_description",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) missing meta description",
        fix="Write a compelling meta description (150-160 chars) for each page",
    )


def _check_missing_canonicals(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    affected = [url for url, r in pages.items() if not r.canonical.value]
    if not affected:
        return None
    return SiteIssue(
        issue_type="missing_canonical",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) missing canonical tag",
        fix="Add self-referencing canonical tags to prevent duplicate content issues",
    )


def _check_missing_open_graph(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    affected = [url for url, r in pages.items() if r.open_graph.status == CheckStatus.FAIL]
    if not affected:
        return None
    return SiteIssue(
        issue_type="missing_open_graph",
        severity=IssueSeverity.INFO,
        affected_pages=affected,
        description=f"{len(affected)} page(s) missing Open Graph tags",
        fix="Add og:title, og:description, og:image for better social sharing",
    )


def _check_thin_content(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    # Only flag pages whose per-page content check already reported a warning.
    # This respects the lower threshold for form/transactional pages.
    affected = [url for url, r in pages.items() if r.content.status == CheckStatus.WARNING]
    if not affected:
        return None
    return SiteIssue(
        issue_type="thin_content",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) with thin content",
        fix="Expand content with relevant, valuable information (form/transactional pages have a lower threshold)",
    )


def _check_missing_viewport(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    affected = [url for url, r in pages.items() if r.viewport.status == CheckStatus.FAIL]
    if not affected:
        return None
    return SiteIssue(
        issue_type="missing_viewport",
        severity=IssueSeverity.CRITICAL,
        affected_pages=affected,
        description=f"{len(affected)} page(s) missing viewport meta tag",
        fix="Add <meta name='viewport' content='width=device-width, initial-scale=1'>",
    )


def _check_site_mixed_content(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    affected = [url for url, r in pages.items() if r.mixed_content.has_mixed_content]
    if not affected:
        return None
    return SiteIssue(
        issue_type="mixed_content",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) with mixed content (HTTP on HTTPS)",
        fix="Update all resource URLs to use HTTPS",
    )


def _check_images_missing_alt(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    affected = [url for url, r in pages.items() if r.images.missing_alt > 0]
    if not affected:
        return None

    total_missing = sum(r.images.missing_alt for r in pages.values())
    return SiteIssue(
        issue_type="images_missing_alt",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{total_missing} image(s) across {len(affected)} page(s) missing alt text",
        fix="Add descriptive alt text to all images for accessibility and SEO",
    )


def _check_orphan_pages(
    pages: Dict[str, PageAuditResult],
    link_graph: Dict[str, List[str]],
) -> Optional[SiteIssue]:
    """Find pages that no other page links to."""
    all_urls = set(pages.keys())
    linked_to: Set[str] = set()
    for targets in link_graph.values():
        linked_to.update(targets)

    # Exclude homepage (first URL or root path)
    orphans = []
    for url in all_urls:
        parsed = urlparse(url)
        # Skip the homepage
        if parsed.path in ("", "/"):
            continue
        if url not in linked_to:
            orphans.append(url)

    if not orphans:
        return None
    return SiteIssue(
        issue_type="orphan_pages",
        severity=IssueSeverity.WARNING,
        affected_pages=orphans,
        description=f"{len(orphans)} orphan page(s) with no internal links pointing to them",
        fix="Add internal links from relevant pages to improve discoverability",
    )


def _check_deep_pages(
    pages: Dict[str, PageAuditResult],
    link_graph: Dict[str, List[str]],
) -> Optional[SiteIssue]:
    """Find pages >3 clicks from the homepage (BFS depth)."""
    if not link_graph:
        return None

    # Find homepage
    homepage = None
    for url in link_graph:
        parsed = urlparse(url)
        if parsed.path in ("", "/"):
            homepage = url
            break
    if not homepage:
        homepage = next(iter(link_graph), None)
    if not homepage:
        return None

    # BFS from homepage
    depths: Dict[str, int] = {homepage: 0}
    queue = [homepage]
    visited = {homepage}

    while queue:
        current = queue.pop(0)
        current_depth = depths[current]
        for linked in link_graph.get(current, []):
            if linked not in visited and linked in pages:
                visited.add(linked)
                depths[linked] = current_depth + 1
                queue.append(linked)

    deep = [url for url, depth in depths.items() if depth > 3]
    # Also include pages unreachable via link graph
    unreachable = [url for url in pages if url not in depths]
    deep.extend(unreachable)

    if not deep:
        return None
    return SiteIssue(
        issue_type="deep_pages",
        severity=IssueSeverity.INFO,
        affected_pages=deep,
        description=f"{len(deep)} page(s) are >3 clicks from homepage or unreachable via internal links",
        fix="Improve internal linking to keep important pages within 3 clicks of homepage",
    )


def _check_image_optimization(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with image optimization issues (missing dimensions, non-modern formats)."""
    affected = [
        url for url, r in pages.items()
        if r.images.missing_dimensions > 0 or r.images.non_modern_format > 0
    ]
    if not affected:
        return None

    total_no_dims = sum(r.images.missing_dimensions for r in pages.values())
    total_legacy = sum(r.images.non_modern_format for r in pages.values())
    parts = []
    if total_no_dims:
        parts.append(f"{total_no_dims} missing width/height")
    if total_legacy:
        parts.append(f"{total_legacy} non-modern format")

    return SiteIssue(
        issue_type="image_optimization",
        severity=IssueSeverity.WARNING if total_no_dims > 0 else IssueSeverity.INFO,
        affected_pages=affected,
        description=f"Image optimization issues across {len(affected)} page(s): {'; '.join(parts)}",
        fix="Add explicit width/height to images (prevents CLS) and use WebP/AVIF for photos",
    )


def _check_broken_links(
    pages: Dict[str, PageAuditResult],
    broken_urls: Set[str],
) -> Optional[SiteIssue]:
    """Flag pages that link to broken internal URLs (404s)."""
    affected = []
    for url, r in pages.items():
        page_broken = [u for u in r.links.internal_urls if u in broken_urls]
        if page_broken:
            affected.append(url)

    if not affected:
        return None
    return SiteIssue(
        issue_type="broken_internal_links",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(broken_urls)} broken internal link(s) found across {len(affected)} page(s)",
        fix="Fix or remove links to pages that return 404",
    )


def _check_slow_pages(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with response time > 3s."""
    affected = [
        url for url, r in pages.items()
        if r.performance.response_time_ms is not None and r.performance.response_time_ms > 3000
    ]
    if not affected:
        return None
    return SiteIssue(
        issue_type="slow_pages",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) with response time > 3 seconds",
        fix="Optimize server response time, enable caching, reduce server-side processing",
    )


def _check_heavy_pages(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with page weight > 3MB."""
    affected = [
        url for url, r in pages.items()
        if r.performance.page_weight_bytes > 3 * 1024 * 1024
    ]
    if not affected:
        return None
    return SiteIssue(
        issue_type="heavy_pages",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) with HTML size > 3MB",
        fix="Reduce page weight by optimizing images, minifying CSS/JS, and removing unused code",
    )


def _check_domain_issues(dc: DomainCheckResult) -> List[SiteIssue]:
    """Convert domain check results into SiteIssues."""
    issues: List[SiteIssue] = []

    # robots.txt
    r = dc.robots_txt
    if not r.exists:
        issues.append(SiteIssue(
            issue_type="missing_robots_txt",
            severity=IssueSeverity.WARNING,
            description="robots.txt not found",
            fix="Create a robots.txt file to guide search engine crawlers",
        ))
    elif r.blocks_important_pages:
        issues.append(SiteIssue(
            issue_type="robots_blocks_important",
            severity=IssueSeverity.CRITICAL,
            description=f"robots.txt blocks important pages: {', '.join(r.blocked_paths[:5])}",
            fix="Review Disallow rules in robots.txt to ensure important pages are crawlable",
        ))

    if r.exists and not r.has_sitemap_reference:
        issues.append(SiteIssue(
            issue_type="robots_no_sitemap_ref",
            severity=IssueSeverity.INFO,
            description="robots.txt does not reference a sitemap",
            fix="Add 'Sitemap: https://yourdomain.com/sitemap.xml' to robots.txt",
        ))

    # sitemap.xml
    s = dc.sitemap
    if not s.exists:
        issues.append(SiteIssue(
            issue_type="missing_sitemap",
            severity=IssueSeverity.WARNING,
            description="sitemap.xml not found",
            fix="Create a sitemap.xml to help search engines discover your pages",
        ))
    elif not s.is_valid_xml:
        issues.append(SiteIssue(
            issue_type="invalid_sitemap",
            severity=IssueSeverity.WARNING,
            description="sitemap.xml contains invalid XML",
            fix="Fix XML syntax errors in sitemap.xml",
        ))
    elif s.is_too_large:
        issues.append(SiteIssue(
            issue_type="sitemap_too_large",
            severity=IssueSeverity.WARNING,
            description="sitemap.xml exceeds recommended size (>10MB)",
            fix="Split sitemap into multiple files using a sitemap index",
        ))

    if s.crawled_not_in_sitemap:
        issues.append(SiteIssue(
            issue_type="pages_not_in_sitemap",
            severity=IssueSeverity.INFO,
            affected_pages=s.crawled_not_in_sitemap[:50],
            description=f"{len(s.crawled_not_in_sitemap)} crawled page(s) not listed in sitemap.xml",
            fix="Add all important pages to sitemap.xml",
        ))

    return issues


# ─── Scoring & Aggregation Helpers ───────────────────────────────────


def _category_score(passed: int, total: int, max_points: int) -> CategoryScore:
    """Compute a category score based on pass rate."""
    if total == 0:
        return CategoryScore(score=max_points, max_score=max_points, pass_rate=1.0, details="N/A")
    rate = passed / total
    score = round(rate * max_points)
    return CategoryScore(
        score=score,
        max_score=max_points,
        pass_rate=round(rate, 3),
        details=f"{passed}/{total} passed",
    )


def _compute_score_breakdown(
    pages: Dict[str, PageAuditResult],
    domain_checks: Optional[DomainCheckResult] = None,
) -> ScoreBreakdown:
    """
    Compute per-category scores. Total budget = 100 points distributed as:
      titles: 15, meta_descriptions: 10, headings: 10, images: 15,
      content: 15, technical: 15, structured_data: 5, domain: 10, performance: 5
    """
    n = len(pages)
    results = list(pages.values())

    # Titles (15 pts): pass if status != FAIL
    titles_pass = sum(1 for r in results if r.title.status != CheckStatus.FAIL)
    titles = _category_score(titles_pass, n, 15)

    # Meta descriptions (10 pts)
    meta_pass = sum(1 for r in results if r.meta_description.status != CheckStatus.FAIL)
    meta = _category_score(meta_pass, n, 10)

    # Headings (10 pts): pass if has at least one H1
    h1_pass = sum(1 for r in results if r.headings.h1_count >= 1)
    headings = _category_score(h1_pass, n, 10)

    # Images (15 pts): composite — alt text + dimensions + format
    if n > 0:
        img_total = sum(r.images.total for r in results)
        if img_total > 0:
            img_ok = img_total - sum(
                r.images.missing_alt + r.images.missing_dimensions + r.images.non_modern_format
                for r in results
            )
            img_ok = max(0, img_ok)
            images = _category_score(img_ok, img_total, 15)
        else:
            images = CategoryScore(score=15, max_score=15, pass_rate=1.0, details="No images")
    else:
        images = _category_score(0, 0, 15)

    # Content (15 pts): pass if word_count >= 300
    content_pass = sum(1 for r in results if r.content.word_count >= 300)
    content = _category_score(content_pass, n, 15)

    # Technical (15 pts): composite — canonical, viewport, charset, lang, no mixed content
    tech_checks_per_page = 5
    tech_total = n * tech_checks_per_page
    tech_pass = 0
    for r in results:
        if r.canonical.status != CheckStatus.FAIL:
            tech_pass += 1
        if r.viewport.status == CheckStatus.PASS:
            tech_pass += 1
        if r.charset.status == CheckStatus.PASS:
            tech_pass += 1
        if r.lang.status == CheckStatus.PASS:
            tech_pass += 1
        if not r.mixed_content.has_mixed_content:
            tech_pass += 1
    technical = _category_score(tech_pass, tech_total, 15)

    # Structured data (5 pts)
    sd_pass = sum(1 for r in results if r.structured_data.has_json_ld or len(r.structured_data.items) > 0)
    structured_data = _category_score(sd_pass, n, 5)

    # Domain (10 pts): robots.txt (5) + sitemap (5)
    if domain_checks:
        domain_score = 0
        domain_max = 10
        domain_parts = []
        # robots.txt: 5 pts
        if domain_checks.robots_txt.exists and not domain_checks.robots_txt.blocks_important_pages:
            domain_score += 5
            domain_parts.append("robots.txt OK")
        elif domain_checks.robots_txt.exists:
            domain_score += 2
            domain_parts.append("robots.txt blocks important pages")
        else:
            domain_parts.append("robots.txt missing")
        # sitemap: 5 pts
        if domain_checks.sitemap.exists and domain_checks.sitemap.is_valid_xml:
            domain_score += 5
            domain_parts.append("sitemap OK")
        elif domain_checks.sitemap.exists:
            domain_score += 2
            domain_parts.append("sitemap invalid XML")
        else:
            domain_parts.append("sitemap missing")
        domain = CategoryScore(
            score=domain_score, max_score=domain_max,
            pass_rate=round(domain_score / domain_max, 3),
            details="; ".join(domain_parts),
        )
    else:
        domain = CategoryScore(score=10, max_score=10, pass_rate=1.0, details="Not checked")

    # Performance (5 pts): pass if page_weight < 3MB and response_time < 3s
    perf_pass = sum(
        1 for r in results
        if r.performance.page_weight_bytes <= 3 * 1024 * 1024
        and (r.performance.response_time_ms is None or r.performance.response_time_ms <= 3000)
    )
    performance = _category_score(perf_pass, n, 5)

    return ScoreBreakdown(
        titles=titles,
        meta_descriptions=meta,
        headings=headings,
        images=images,
        content=content,
        technical=technical,
        structured_data=structured_data,
        domain=domain,
        performance=performance,
    )


def _compute_pass_rates(pages: Dict[str, PageAuditResult]) -> Dict[str, float]:
    """Compute pass rates by check type across all pages."""
    n = len(pages)
    if n == 0:
        return {}
    results = list(pages.values())

    return {
        "title_pass_rate": round(sum(1 for r in results if r.title.status == CheckStatus.PASS) / n, 3),
        "meta_desc_pass_rate": round(sum(1 for r in results if r.meta_description.status == CheckStatus.PASS) / n, 3),
        "h1_pass_rate": round(sum(1 for r in results if r.headings.h1_count >= 1) / n, 3),
        "canonical_pass_rate": round(sum(1 for r in results if r.canonical.status != CheckStatus.FAIL) / n, 3),
        "viewport_pass_rate": round(sum(1 for r in results if r.viewport.status == CheckStatus.PASS) / n, 3),
        "images_alt_pass_rate": round(
            sum(1 for r in results if r.images.missing_alt == 0) / n, 3
        ),
        "structured_data_rate": round(
            sum(1 for r in results if r.structured_data.has_json_ld or len(r.structured_data.items) > 0) / n, 3
        ),
        "content_sufficient_rate": round(sum(1 for r in results if r.content.word_count >= 300) / n, 3),
        "no_mixed_content_rate": round(
            sum(1 for r in results if not r.mixed_content.has_mixed_content) / n, 3
        ),
    }


def _compute_top_issues(all_issues: List[SiteIssue], limit: int = 5) -> List[Dict[str, Any]]:
    """Return top N issues sorted by severity then affected count."""
    severity_order = {IssueSeverity.CRITICAL: 0, IssueSeverity.WARNING: 1, IssueSeverity.INFO: 2}
    sorted_issues = sorted(
        all_issues,
        key=lambda i: (severity_order.get(i.severity, 9), -len(i.affected_pages)),
    )
    return [
        {
            "issue_type": i.issue_type,
            "severity": i.severity.value,
            "affected_count": len(i.affected_pages),
            "description": i.description,
        }
        for i in sorted_issues[:limit]
    ]


def _compute_crawl_metadata(
    pages: Dict[str, PageAuditResult],
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build crawl metadata dict for the summary."""
    results = list(pages.values())
    response_times = [
        r.performance.response_time_ms for r in results
        if r.performance.response_time_ms is not None
    ]
    avg_response = round(sum(response_times) / len(response_times), 1) if response_times else None
    total_weight = sum(r.performance.page_weight_bytes for r in results)

    meta: Dict[str, Any] = {
        "total_pages_found": len(pages),
        "avg_response_time_ms": avg_response,
        "total_page_weight_bytes": total_weight,
    }
    if extra:
        meta.update(extra)
    return meta
