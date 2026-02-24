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
    broken_external_urls: Optional[Dict[str, int]] = None,
    redirect_chains: Optional[List[Dict[str, Any]]] = None,
) -> SiteAuditResult:
    """
    Run all site-wide SEO checks across a set of audited pages.

    Args:
        page_results: Mapping of URL -> PageAuditResult.
        internal_link_graph: Optional mapping of URL -> list of URLs it links to.
        domain_checks: Optional domain-level check results (robots.txt, sitemap).
        broken_internal_urls: Optional set of internal URLs that returned 404.
        crawl_metadata: Optional dict with crawl timing info (crawl_duration_s, etc.).
        broken_external_urls: Optional mapping of external URL -> HTTP status code.
        redirect_chains: Optional list of redirect chain dicts.

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

    # ── Empty H1 tags (BUG-11) ──────────────────────────────────────
    issue = _check_empty_h1(page_results)
    if issue:
        warnings.append(issue)

    # ── Multiple H1 tags ─────────────────────────────────────────────
    issue = _check_multiple_h1(page_results)
    if issue:
        warnings.append(issue)

    # ── Missing titles ────────────────────────────────────────────────
    issue = _check_missing_titles(page_results)
    if issue:
        critical.append(issue)

    # ── Missing meta descriptions ─────────────────────────────────────
    issue = _check_missing_descriptions(page_results)
    if issue:
        warnings.append(issue)

    # ── Title length warnings ─────────────────────────────────────────
    for ti in _check_title_length_warnings(page_results):
        warnings.append(ti)

    # ── Description length warnings ───────────────────────────────────
    for di in _check_description_length_warnings(page_results):
        warnings.append(di)

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

    # ── Meta refresh redirects ───────────────────────────────────────
    issue = _check_meta_refresh(page_results)
    if issue:
        warnings.append(issue)

    # ── Multiple/broken canonical ─────────────────────────────────────
    issue = _check_multiple_canonicals(page_results)
    if issue:
        warnings.append(issue)

    # ── Missing charset ───────────────────────────────────────────────
    issue = _check_missing_charset(page_results)
    if issue:
        info.append(issue)

    # ── Missing lang ──────────────────────────────────────────────────
    issue = _check_missing_lang(page_results)
    if issue:
        warnings.append(issue)

    # ── H1 length warnings ───────────────────────────────────────────
    issue = _check_h1_length(page_results)
    if issue:
        warnings.append(issue)

    # ── Skipped heading levels ───────────────────────────────────────
    issue = _check_skipped_headings(page_results)
    if issue:
        warnings.append(issue)

    # ── Excessive links ──────────────────────────────────────────────
    issue = _check_excessive_links(page_results)
    if issue:
        warnings.append(issue)

    # ── JavaScript/empty hrefs ───────────────────────────────────────
    issue = _check_javascript_hrefs(page_results)
    if issue:
        warnings.append(issue)

    # ── Broken images ────────────────────────────────────────────────
    issue = _check_broken_images(page_results)
    if issue:
        warnings.append(issue)

    # ── Hidden text ───────────────────────────────────────────────────
    issue = _check_hidden_text(page_results)
    if issue:
        warnings.append(issue)

    # ── Keyword stuffing ─────────────────────────────────────────────
    issue = _check_keyword_stuffing(page_results)
    if issue:
        warnings.append(issue)

    # ── Placeholder content ──────────────────────────────────────────
    issue = _check_placeholder_content(page_results)
    if issue:
        warnings.append(issue)

    # ── Uppercase URLs ───────────────────────────────────────────────
    issue = _check_uppercase_urls(page_results)
    if issue:
        info.append(issue)

    # ── Incomplete OG tags ───────────────────────────────────────────
    issue = _check_incomplete_og(page_results)
    if issue:
        info.append(issue)

    # ── Page-level nofollow ──────────────────────────────────────────
    issue = _check_page_nofollow(page_results)
    if issue:
        info.append(issue)

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

    issue = _check_heavy_javascript(page_results)
    if issue:
        warnings.append(issue)

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

    # ── Noindex pages in sitemap ────────────────────────────────────
    if domain_checks:
        issue = _check_noindex_in_sitemap(page_results, domain_checks)
        if issue:
            warnings.append(issue)

    # ── Orphan pages (if link graph available) ────────────────────────
    if internal_link_graph is not None:
        issue = _check_orphan_pages(page_results, internal_link_graph)
        if issue:
            warnings.append(issue)

        issue = _check_deep_pages(page_results, internal_link_graph)
        if issue:
            info.append(issue)

    # ── Broken external links ─────────────────────────────────────────
    if broken_external_urls:
        issue = _check_broken_external_links(page_results, broken_external_urls)
        if issue:
            warnings.append(issue)

    # ── Redirect chains ───────────────────────────────────────────────
    if redirect_chains:
        chain_issues = _check_redirect_chains(redirect_chains)
        for ci in chain_issues:
            if ci.severity == IssueSeverity.CRITICAL:
                critical.append(ci)
            elif ci.severity == IssueSeverity.WARNING:
                warnings.append(ci)
            else:
                info.append(ci)

    # ── Canonical validation (T4: M13, X5) ────────────────────────────
    for ci in _check_canonical_validation(page_results):
        if ci.severity == IssueSeverity.WARNING:
            warnings.append(ci)
        else:
            info.append(ci)

    # ── Duplicate body content (T5: C7, C8) ──────────────────────────
    issue = _check_duplicate_content(page_results)
    if issue:
        warnings.append(issue)

    # ── Soft 404 detection (T7: X1) ──────────────────────────────────
    issue = _check_soft_404(page_results)
    if issue:
        warnings.append(issue)

    # ── Internal nofollow waste (T11: L4) ────────────────────────────
    issue = _check_internal_nofollow(page_results)
    if issue:
        info.append(issue)

    # ── Sponsored/UGC link attributes (T11: L5) ─────────────────────
    issue = _check_sponsored_ugc_internal(page_results)
    if issue:
        info.append(issue)

    # ── Images missing dimensions (T15: I5) ──────────────────────────
    issue = _check_images_missing_dimensions(page_results)
    if issue:
        warnings.append(issue)

    # ── Empty alt text distinction (T23) ─────────────────────────────
    issue = _check_empty_alt_text(page_results)
    if issue:
        info.append(issue)

    # ── Long URLs (T20) ──────────────────────────────────────────────
    issue = _check_long_urls(page_results)
    if issue:
        info.append(issue)

    # ── Trailing slash inconsistency (T19) ───────────────────────────
    issue = _check_trailing_slash_inconsistency(page_results)
    if issue:
        info.append(issue)

    # ── Pagination (T17) ─────────────────────────────────────────────
    issue = _check_pagination_issues(page_results)
    if issue:
        info.append(issue)

    # ── Iframe content (T18) ─────────────────────────────────────────
    issue = _check_iframe_content(page_results)
    if issue:
        info.append(issue)

    # ── Iframe accessibility issues (FN-12) ──────────────────────────
    issue = _check_iframe_accessibility(page_results)
    if issue:
        warnings.append(issue)

    # ── Placeholder meta descriptions (FN-9) ─────────────────────────
    issue = _check_placeholder_meta_desc(page_results)
    if issue:
        warnings.append(issue)

    # ── Structured data validation (T8/T13) ──────────────────────────
    issue = _check_structured_data_issues(page_results)
    if issue:
        (warnings if issue.severity == IssueSeverity.WARNING else info).append(issue)

    # ── Hreflang validation (T14) ────────────────────────────────────
    issue = _check_hreflang_issues(page_results)
    if issue:
        warnings.append(issue)

    # ── Robots.txt deep analysis (T12: S1-S4) ────────────────────────
    if domain_checks:
        for ri in _check_robots_deep_analysis(domain_checks):
            if ri.severity == IssueSeverity.WARNING:
                warnings.append(ri)
            else:
                info.append(ri)

    # ── Orphan pages via sitemap (T21) ───────────────────────────────
    if domain_checks:
        issue = _check_orphan_pages_via_sitemap(page_results, domain_checks, internal_link_graph)
        if issue:
            info.append(issue)

    # ── Redirect loops (BUG-14/L9) ───────────────────────────────────
    issue = _check_redirect_loops(page_results)
    if issue:
        critical.append(issue)

    # ── Robots.txt compliance (BUG-09/X4) ─────────────────────────────
    if domain_checks:
        issue = _check_robots_compliance(page_results, domain_checks)
        if issue:
            warnings.append(issue)

    # ── BUG-19: Hreflang bidirectional confirmation ───────────────────
    issue = _check_hreflang_bidirectional(page_results)
    if issue:
        warnings.append(issue)

    # ── BUG-20: Enhanced pagination analysis ──────────────────────────
    issue = _check_pagination_seo(page_results)
    if issue:
        warnings.append(issue)

    # ── BUG-21: Excessive inline CSS detection ────────────────────────
    issue = _check_excessive_inline_css(page_results)
    if issue:
        info.append(issue)

    # ── BUG-16/17: Image alt text quality and broken src ──────────────
    for img_issue in _check_image_quality_issues(page_results):
        warnings.append(img_issue)

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

    # ── Crawl metadata (includes link graph stats) ────────────────────
    meta = _compute_crawl_metadata(page_results, crawl_metadata, internal_link_graph)

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
    affected = [url for url, r in pages.items() if r.headings.h1_count == 0 and not r.headings.has_empty_h1]
    if not affected:
        return None
    return SiteIssue(
        issue_type="missing_h1",
        severity=IssueSeverity.CRITICAL,
        affected_pages=affected,
        description=f"{len(affected)} page(s) missing H1 tag",
        fix="Add a single, descriptive H1 tag to each page",
    )


def _check_empty_h1(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with empty H1 elements (BUG-11: distinct from missing H1)."""
    affected = [url for url, r in pages.items() if r.headings.has_empty_h1]
    if not affected:
        return None
    return SiteIssue(
        issue_type="empty_h1",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) have empty H1 tag (element exists but contains no text)",
        fix="Add descriptive text to the H1 tag, or remove the empty element",
    )


def _check_multiple_h1(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    affected = [url for url, r in pages.items() if r.headings.h1_count > 1]
    if not affected:
        return None
    return SiteIssue(
        issue_type="multiple_h1",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) with multiple H1 tags",
        fix="Use a single H1 tag per page to clearly define the primary heading",
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


def _check_title_length_warnings(pages: Dict[str, PageAuditResult]) -> List[SiteIssue]:
    """Surface pages with title length warnings (too short or too long) as site-level issues."""
    issues: List[SiteIssue] = []
    short = [url for url, r in pages.items() if r.title.value and r.title.length < 30]
    long = [url for url, r in pages.items() if r.title.value and r.title.length > 65]
    if short:
        issues.append(SiteIssue(
            issue_type="short_title",
            severity=IssueSeverity.WARNING,
            affected_pages=short,
            description=f"{len(short)} page(s) with title too short (<30 chars)",
            fix="Expand title tags to 30-65 characters for optimal search display",
        ))
    if long:
        issues.append(SiteIssue(
            issue_type="long_title",
            severity=IssueSeverity.WARNING,
            affected_pages=long,
            description=f"{len(long)} page(s) with title too long (>65 chars, may be truncated)",
            fix="Shorten title tags to 30-65 characters to prevent truncation in search results",
        ))
    return issues


def _check_description_length_warnings(pages: Dict[str, PageAuditResult]) -> List[SiteIssue]:
    """Surface pages with meta description length warnings as site-level issues."""
    issues: List[SiteIssue] = []
    short = [url for url, r in pages.items() if r.meta_description.value and r.meta_description.length < 50]
    long = [url for url, r in pages.items() if r.meta_description.value and r.meta_description.length > 160]
    if short:
        issues.append(SiteIssue(
            issue_type="short_description",
            severity=IssueSeverity.WARNING,
            affected_pages=short,
            description=f"{len(short)} page(s) with meta description too short (<50 chars)",
            fix="Expand meta descriptions to 50-160 characters for better search snippets",
        ))
    if long:
        issues.append(SiteIssue(
            issue_type="long_description",
            severity=IssueSeverity.WARNING,
            affected_pages=long,
            description=f"{len(long)} page(s) with meta description too long (>160 chars, will be truncated)",
            fix="Shorten meta descriptions to 50-160 characters to prevent truncation",
        ))
    return issues


def _check_meta_refresh(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages using meta refresh redirects (bad SEO practice)."""
    affected = [url for url, r in pages.items() if r.meta_refresh_url]
    if not affected:
        return None
    return SiteIssue(
        issue_type="meta_refresh",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) using meta refresh redirects",
        fix="Replace meta refresh redirects with proper HTTP 301 redirects",
    )


def _check_multiple_canonicals(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with multiple conflicting canonical tags."""
    affected = [
        url for url, r in pages.items()
        if r.canonical.value and "Multiple conflicting" in r.canonical.note
    ]
    if not affected:
        return None
    return SiteIssue(
        issue_type="multiple_canonicals",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) with multiple conflicting canonical tags",
        fix="Use a single canonical tag per page pointing to the preferred URL",
    )


def _check_missing_charset(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages missing charset declaration."""
    affected = [url for url, r in pages.items() if r.charset.status != CheckStatus.PASS]
    if not affected:
        return None
    return SiteIssue(
        issue_type="missing_charset",
        severity=IssueSeverity.INFO,
        affected_pages=affected,
        description=f"{len(affected)} page(s) missing charset declaration",
        fix="Add <meta charset=\"utf-8\"> to the <head> of each page",
    )


def _check_missing_lang(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages missing lang attribute on <html>."""
    affected = [url for url, r in pages.items() if r.lang.status != CheckStatus.PASS]
    if not affected:
        return None
    return SiteIssue(
        issue_type="missing_lang",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) missing lang attribute on <html>",
        fix="Add lang attribute to <html> tag (e.g. <html lang=\"en\">) for accessibility and SEO",
    )


def _check_h1_length(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with excessively long H1 tags (>70 chars)."""
    affected = []
    for url, r in pages.items():
        if r.headings.h1_values:
            for h1 in r.headings.h1_values:
                if len(h1) > 70:
                    affected.append(url)
                    break
    if not affected:
        return None
    return SiteIssue(
        issue_type="long_h1",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) with H1 tag exceeding 70 characters",
        fix="Keep H1 tags concise (under 70 characters) for better SEO impact",
    )


def _check_skipped_headings(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with skipped heading levels (e.g. H1 -> H3).
    Deduplicates template-level issues: if the same skip pattern appears on >50%
    of pages, it's reported as a template issue rather than per-page."""
    affected = [url for url, r in pages.items() if r.headings.skipped_levels]
    if not affected:
        return None

    n = len(pages)
    # Count how often each skip pattern appears
    pattern_counts: Counter = Counter()
    for url in affected:
        r = pages[url]
        for skip in r.headings.skipped_levels:
            pattern_counts[skip] += 1

    # If >30% of all pages share the same skip pattern, it's a template issue
    template_skips = [p for p, c in pattern_counts.items() if c > n * 0.3]
    if template_skips:
        # Only flag pages that have skips NOT caused by the template
        non_template_affected = []
        for url in affected:
            r = pages[url]
            unique_skips = [s for s in r.headings.skipped_levels if s not in template_skips]
            if unique_skips:
                non_template_affected.append(url)

        template_desc = f"Template-level heading skip ({', '.join(template_skips)}) affects {pattern_counts[template_skips[0]]}/{n} pages"
        if non_template_affected:
            return SiteIssue(
                issue_type="skipped_headings",
                severity=IssueSeverity.WARNING,
                affected_pages=non_template_affected,
                description=f"{len(non_template_affected)} page(s) with non-template skipped heading levels. {template_desc}",
                fix="Fix heading hierarchy in page content. The template heading skip should be fixed in the site template.",
            )
        else:
            return SiteIssue(
                issue_type="skipped_headings",
                severity=IssueSeverity.INFO,
                affected_pages=[],
                description=template_desc,
                fix="Fix the heading hierarchy in the site template/footer to avoid skipped levels",
            )

    return SiteIssue(
        issue_type="skipped_headings",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) with skipped heading levels",
        fix="Maintain proper heading hierarchy (H1 -> H2 -> H3) without skipping levels",
    )


def _check_excessive_links(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with more than 150 total links."""
    affected = [
        url for url, r in pages.items()
        if (r.links.internal_count + r.links.external_count) > 150
    ]
    if not affected:
        return None
    return SiteIssue(
        issue_type="excessive_links",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) with more than 150 links",
        fix="Reduce the number of links per page to improve crawl efficiency and link equity distribution",
    )


def _check_javascript_hrefs(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with javascript: hrefs or empty hrefs (not crawlable by search engines)."""
    affected = [url for url, r in pages.items()
                if r.links.javascript_href_count > 0 or r.links.empty_href_count > 0]
    if not affected:
        return None
    total_js = sum(r.links.javascript_href_count for r in pages.values())
    total_empty = sum(r.links.empty_href_count for r in pages.values())
    parts = []
    if total_js:
        parts.append(f"{total_js} javascript:")
    if total_empty:
        parts.append(f"{total_empty} empty/hash-only")
    return SiteIssue(
        issue_type="javascript_hrefs",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{' + '.join(parts)} href(s) across {len(affected)} page(s)",
        fix="Replace javascript: hrefs with proper URLs or button elements for non-navigation actions",
    )


def _check_broken_images(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with images that have empty or missing src attributes."""
    affected = []
    total_broken = 0
    for url, r in pages.items():
        broken_count = 0
        for img in r.images.images:
            if not img.src or img.src.strip() == "":
                broken_count += 1
        if broken_count > 0:
            affected.append(url)
            total_broken += broken_count
    if not affected:
        return None
    return SiteIssue(
        issue_type="broken_images",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{total_broken} broken/empty image src(s) across {len(affected)} page(s)",
        fix="Fix or remove images with broken, empty, or missing src attributes",
    )


def _check_hidden_text(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with hidden text (display:none, visibility:hidden)."""
    affected = [url for url, r in pages.items() if r.hidden_text]
    if not affected:
        return None
    return SiteIssue(
        issue_type="hidden_text",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) contain hidden text (display:none or visibility:hidden)",
        fix="Remove hidden text or use proper CSS techniques that don't hide content from search engines",
    )


def _check_keyword_stuffing(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with keyword stuffing."""
    affected = [url for url, r in pages.items() if r.keyword_stuffing]
    if not affected:
        return None
    details = [f"{url}: {pages[url].keyword_stuffing}" for url in affected[:5]]
    return SiteIssue(
        issue_type="keyword_stuffing",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) with suspected keyword stuffing: {'; '.join(details)}",
        fix="Reduce keyword density to natural levels; focus on quality content over keyword repetition",
    )


def _check_placeholder_content(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with lorem ipsum / placeholder content."""
    affected = [url for url, r in pages.items() if r.has_placeholder_content]
    if not affected:
        return None
    return SiteIssue(
        issue_type="placeholder_content",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) contain placeholder/lorem ipsum content",
        fix="Replace placeholder text with real content before publishing",
    )


def _check_uppercase_urls(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with uppercase characters in URLs."""
    affected = [url for url, r in pages.items() if r.url_check.has_uppercase]
    if not affected:
        return None
    return SiteIssue(
        issue_type="uppercase_urls",
        severity=IssueSeverity.INFO,
        affected_pages=affected,
        description=f"{len(affected)} page(s) with uppercase characters in URL path",
        fix="Use lowercase URLs and set up 301 redirects from uppercase variants",
    )


def _check_incomplete_og(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with incomplete Open Graph tags (some present but missing required ones)."""
    affected = []
    for url, r in pages.items():
        if r.open_graph.present_tags and r.open_graph.missing_tags:
            affected.append(url)
    if not affected:
        return None
    return SiteIssue(
        issue_type="incomplete_og",
        severity=IssueSeverity.INFO,
        affected_pages=affected,
        description=f"{len(affected)} page(s) have incomplete Open Graph tags (missing required properties)",
        fix="Add all required OG tags: og:title, og:type, og:url, og:image",
    )


def _check_page_nofollow(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with nofollow robots directive (prevents link equity flow)."""
    affected = [
        url for url, r in pages.items()
        if r.robots.is_indexable and not r.robots.is_followable
    ]
    if not affected:
        return None
    return SiteIssue(
        issue_type="page_nofollow",
        severity=IssueSeverity.INFO,
        affected_pages=affected,
        description=f"{len(affected)} page(s) have nofollow robots directive (link equity not passed)",
        fix="Review whether nofollow is intentional; remove if links should pass equity",
    )


def _check_noindex_in_sitemap(
    pages: Dict[str, PageAuditResult],
    domain_checks: DomainCheckResult,
) -> Optional[SiteIssue]:
    """Flag pages that are noindex but appear in the sitemap (contradictory signals).
    BUG-05 fix: uses path-only matching as fallback for robust URL comparison."""
    sitemap_urls = set(domain_checks.sitemap.urls_in_sitemap) if domain_checks.sitemap.urls_in_sitemap else set()
    if not sitemap_urls:
        return None

    def _norm_full(u):
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc.lower()}{(p.path.rstrip('/') or '/')}"

    def _norm_path(u):
        return (urlparse(u).path.rstrip("/") or "/").lower()

    # Build lookup sets: full URL and path-only
    norm_sitemap_full = {_norm_full(u) for u in sitemap_urls}
    norm_sitemap_path = {_norm_path(u) for u in sitemap_urls}

    affected = []
    for url, r in pages.items():
        if not r.robots.is_indexable:
            if _norm_full(url) in norm_sitemap_full or _norm_path(url) in norm_sitemap_path:
                affected.append(url)
    if not affected:
        return None
    return SiteIssue(
        issue_type="noindex_in_sitemap",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} noindex page(s) found in sitemap.xml (contradictory signals)",
        fix="Either remove the noindex directive or remove these pages from the sitemap",
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
    # If >50% of pages are thin, it's likely the site's nature — downgrade to INFO
    ratio = len(affected) / len(pages) if pages else 0
    severity = IssueSeverity.INFO if ratio > 0.5 else IssueSeverity.WARNING
    return SiteIssue(
        issue_type="thin_content",
        severity=severity,
        affected_pages=affected,
        description=f"{len(affected)} page(s) with thin content (<200 words, or <100 for form pages)",
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

    # Find homepage — check both link_graph keys and pages keys
    homepage = None
    all_urls = set(link_graph.keys()) | set(pages.keys())
    for url in all_urls:
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

    # Identify homepage URLs (with and without trailing slash) to exclude them
    homepage_urls = set()
    for url in all_urls:
        parsed = urlparse(url)
        if parsed.path in ("", "/"):
            homepage_urls.add(url)

    deep = [url for url, depth in depths.items() if depth > 3 and url not in homepage_urls]
    # Also include pages unreachable via link graph (but not the homepage)
    unreachable = [url for url in pages if url not in depths and url not in homepage_urls]
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
    """Flag pages with non-modern image formats (JPEG/PNG instead of WebP/AVIF).
    Note: missing dimensions are covered by _check_images_missing_dimensions."""
    affected = [url for url, r in pages.items() if r.images.non_modern_format > 0]
    if not affected:
        return None

    total_legacy = sum(r.images.non_modern_format for r in pages.values())
    return SiteIssue(
        issue_type="image_optimization",
        severity=IssueSeverity.INFO,
        affected_pages=affected,
        description=f"{total_legacy} image(s) in non-modern format across {len(affected)} page(s)",
        fix="Use WebP or AVIF format for photos to reduce file size",
    )


def _check_broken_links(
    pages: Dict[str, PageAuditResult],
    broken_urls: Set[str],
) -> Optional[SiteIssue]:
    """Flag pages that link to broken internal URLs (404s) (BUG-1 fix).
    Uses normalized URL matching to handle trailing slash differences."""
    def _norm(u):
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc.lower()}{(p.path.rstrip('/') or '/')}"

    norm_broken = {_norm(u) for u in broken_urls}
    # Also keep originals for broader matching
    all_broken = broken_urls | norm_broken

    affected = []
    for url, r in pages.items():
        page_broken = []
        for link_url in r.links.internal_urls:
            if link_url in all_broken or _norm(link_url) in norm_broken:
                page_broken.append(link_url)
        # Also check link_details for more thorough matching
        if not page_broken:
            for ld in r.links.link_details:
                if ld.link_type == "internal" and (ld.url in all_broken or _norm(ld.url) in norm_broken):
                    page_broken.append(ld.url)
        if page_broken:
            affected.append(url)

    if not affected:
        return None
    return SiteIssue(
        issue_type="broken_internal_links",
        severity=IssueSeverity.CRITICAL,
        affected_pages=affected,
        description=f"{len(broken_urls)} broken internal link target(s) found across {len(affected)} page(s)",
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
    """Flag pages with total page weight > 3MB."""
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
        description=f"{len(affected)} page(s) with total page weight > 3MB",
        fix="Reduce page weight by optimizing images, minifying CSS/JS, and removing unused code",
    )


def _check_heavy_javascript(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages where JavaScript transfer size exceeds 500KB."""
    js_threshold = 500 * 1024  # 500KB
    affected = []
    for url, r in pages.items():
        bd = r.performance.resource_breakdown
        if bd:
            js_bytes = bd.get("script", 0)
            if js_bytes > js_threshold:
                affected.append(url)
    if not affected:
        return None
    return SiteIssue(
        issue_type="heavy_javascript",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) with JavaScript bundles > 500KB",
        fix="Split JavaScript bundles, use code splitting, defer non-critical scripts, and remove unused code",
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
        unique_blocked = list(dict.fromkeys(r.blocked_paths[:20]))  # deduplicate, preserve order
        issues.append(SiteIssue(
            issue_type="robots_blocks_important",
            severity=IssueSeverity.CRITICAL,
            affected_pages=unique_blocked,
            description=f"robots.txt blocks important pages: {', '.join(unique_blocked[:5])}",
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

    if s.url_count == 0 and s.exists and s.is_valid_xml:
        issues.append(SiteIssue(
            issue_type="sitemap_empty",
            severity=IssueSeverity.WARNING,
            description="sitemap.xml exists but contains no page URLs",
            fix="Add all important pages to sitemap.xml so search engines can discover them",
        ))
    elif s.crawled_not_in_sitemap:
        issues.append(SiteIssue(
            issue_type="pages_not_in_sitemap",
            severity=IssueSeverity.INFO,
            affected_pages=s.crawled_not_in_sitemap[:50],
            description=f"{len(s.crawled_not_in_sitemap)} crawled page(s) not listed in sitemap.xml",
            fix="Add all important pages to sitemap.xml",
        ))

    return issues


# ─── New Site-Level Checks (Tiers 1-4) ───────────────────────────────


def _check_canonical_validation(
    pages: Dict[str, PageAuditResult],
) -> List[SiteIssue]:
    """Validate canonical targets: flag canonicals pointing to 404s or different pages (T4: M13, X5)."""
    issues: List[SiteIssue] = []
    crawled_urls = set(pages.keys())
    # Normalize crawled URLs for matching
    norm_crawled = set()
    for u in crawled_urls:
        parsed = urlparse(u)
        norm_crawled.add(f"{parsed.scheme}://{parsed.netloc.lower()}{parsed.path.rstrip('/') or '/'}")

    wrong_canonical = []
    cross_page_canonical = []

    for url, r in pages.items():
        if not r.canonical.value or r.canonical.is_self_referencing:
            continue
        canon = r.canonical.value
        parsed_canon = urlparse(canon)
        norm_canon = f"{parsed_canon.scheme}://{parsed_canon.netloc.lower()}{parsed_canon.path.rstrip('/') or '/'}"
        parsed_page = urlparse(url)
        norm_page = f"{parsed_page.scheme}://{parsed_page.netloc.lower()}{parsed_page.path.rstrip('/') or '/'}"

        # Same domain but different path: cross-page canonical
        if parsed_canon.netloc.lower() == parsed_page.netloc.lower() and norm_canon != norm_page:
            # Check if canonical target exists in crawled pages
            if norm_canon not in norm_crawled:
                wrong_canonical.append(url)
            else:
                cross_page_canonical.append(url)

    if wrong_canonical:
        issues.append(SiteIssue(
            issue_type="canonical_target_missing",
            severity=IssueSeverity.WARNING,
            affected_pages=wrong_canonical,
            description=f"{len(wrong_canonical)} page(s) have canonical pointing to non-existent/uncrawled URL",
            fix="Ensure canonical tags point to valid, accessible pages",
        ))

    if cross_page_canonical:
        issues.append(SiteIssue(
            issue_type="cross_page_canonical",
            severity=IssueSeverity.INFO,
            affected_pages=cross_page_canonical,
            description=f"{len(cross_page_canonical)} page(s) have canonical pointing to a different page",
            fix="Verify cross-page canonicals are intentional (e.g. pagination, URL variants)",
        ))

    return issues


def _check_duplicate_content(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Detect pages with identical or near-duplicate body content (T5/BUG-8: C7, C8).
    Uses exact hash matching first, then Jaccard similarity on shingles for near-duplicates."""
    # Phase 1: Exact duplicates via content hash
    hash_map: Dict[str, List[str]] = defaultdict(list)
    for url, r in pages.items():
        if r.status_code and 300 <= r.status_code < 400:
            continue
        if r.content_hash:
            hash_map[r.content_hash].append(url)

    exact_duplicates = {h: urls for h, urls in hash_map.items() if len(urls) > 1}
    exact_affected: set = set()
    for urls in exact_duplicates.values():
        exact_affected.update(urls)

    # Phase 2: Near-duplicates via Jaccard similarity on shingles (BUG-8)
    near_affected: set = set()
    SIMILARITY_THRESHOLD = 0.7
    shingle_pages = [
        (url, r.content_shingles)
        for url, r in pages.items()
        if r.content_shingles
        and url not in exact_affected
        and (not r.status_code or r.status_code < 300 or r.status_code >= 400)
    ]
    # Compare pairs (O(n^2) but n is small — max 50 pages)
    for i in range(len(shingle_pages)):
        for j in range(i + 1, len(shingle_pages)):
            url_a, shingles_a = shingle_pages[i]
            url_b, shingles_b = shingle_pages[j]
            intersection = len(shingles_a & shingles_b)
            union = len(shingles_a | shingles_b)
            if union > 0 and intersection / union >= SIMILARITY_THRESHOLD:
                near_affected.add(url_a)
                near_affected.add(url_b)

    all_affected = list(exact_affected | near_affected)
    if not all_affected:
        return None

    parts = []
    if exact_duplicates:
        parts.append(f"{len(exact_duplicates)} group(s) of identical content")
    if near_affected:
        parts.append(f"{len(near_affected)} page(s) with near-duplicate content (>70% similar)")

    return SiteIssue(
        issue_type="duplicate_content",
        severity=IssueSeverity.WARNING,
        affected_pages=all_affected,
        description=f"Duplicate content across {len(all_affected)} pages: {'; '.join(parts)}",
        fix="Consolidate duplicate content pages or use canonical tags to indicate the preferred version",
    )


def _check_soft_404(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages that return HTTP 200 but display 'not found' content (T7: X1)."""
    affected = [url for url, r in pages.items() if r.is_soft_404]
    if not affected:
        return None
    return SiteIssue(
        issue_type="soft_404",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) return HTTP 200 but display 'not found' content (soft 404)",
        fix="Return proper 404 status code for missing pages, or fix the content if the page exists",
    )


def _check_internal_nofollow(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with internal links using rel=nofollow (wastes link equity) (T11: L4)."""
    affected = [url for url, r in pages.items() if r.links.internal_nofollow_count > 0]
    if not affected:
        return None
    total = sum(r.links.internal_nofollow_count for r in pages.values())
    return SiteIssue(
        issue_type="internal_nofollow",
        severity=IssueSeverity.INFO,
        affected_pages=affected,
        description=f"{total} internal link(s) with rel=nofollow across {len(affected)} page(s) (wastes link equity)",
        fix="Remove nofollow from internal links unless deliberately preventing crawling of specific pages",
    )


def _check_sponsored_ugc_internal(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with rel=sponsored or rel=ugc link attributes (T11: L5)."""
    affected = [url for url, r in pages.items()
                if r.links.sponsored_count > 0 or r.links.ugc_count > 0]
    if not affected:
        return None
    total_sponsored = sum(r.links.sponsored_count for r in pages.values())
    total_ugc = sum(r.links.ugc_count for r in pages.values())
    parts = []
    if total_sponsored:
        parts.append(f"{total_sponsored} sponsored")
    if total_ugc:
        parts.append(f"{total_ugc} UGC")
    return SiteIssue(
        issue_type="sponsored_ugc_links",
        severity=IssueSeverity.INFO,
        affected_pages=affected,
        description=f"{' + '.join(parts)} link(s) across {len(affected)} page(s)",
        fix="Review sponsored/UGC link attributes to ensure they are applied correctly",
    )


def _check_images_missing_dimensions(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Surface pages with images missing width/height as a distinct issue (T15: I5)."""
    affected = [url for url, r in pages.items() if r.images.missing_dimensions > 0]
    if not affected:
        return None
    total = sum(r.images.missing_dimensions for r in pages.values())
    return SiteIssue(
        issue_type="images_missing_dimensions",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{total} image(s) across {len(affected)} page(s) missing explicit width/height attributes",
        fix="Add width and height attributes to img tags to prevent Cumulative Layout Shift (CLS)",
    )


def _check_empty_alt_text(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Distinguish images with empty alt (decorative) from those missing alt entirely (T23)."""
    affected = [url for url, r in pages.items() if r.images.empty_alt > 0]
    if not affected:
        return None
    total = sum(r.images.empty_alt for r in pages.values())
    return SiteIssue(
        issue_type="images_empty_alt",
        severity=IssueSeverity.INFO,
        affected_pages=affected,
        description=f"{total} image(s) across {len(affected)} page(s) have empty alt text (decorative images?)",
        fix="Use empty alt=\"\" only for decorative images. Content images should have descriptive alt text.",
    )


def _check_long_urls(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with URLs longer than 100 characters (T20)."""
    affected = [url for url, r in pages.items() if r.url_check.length > 100]
    if not affected:
        return None
    return SiteIssue(
        issue_type="long_urls",
        severity=IssueSeverity.INFO,
        affected_pages=affected,
        description=f"{len(affected)} page(s) with URLs exceeding 100 characters",
        fix="Shorten URLs to improve readability and shareability",
    )


def _check_trailing_slash_inconsistency(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Detect inconsistent trailing slash usage across the site (T19).
    Checks both page URLs and canonical vs internal link target mismatches."""
    with_slash = 0
    without_slash = 0
    canonical_slash_mismatch = 0

    for url, r in pages.items():
        parsed = urlparse(url)
        if parsed.path == "/" or not parsed.path:
            continue  # Skip homepage
        if parsed.path.endswith("/"):
            with_slash += 1
        else:
            without_slash += 1
        # Check if canonical uses different slash convention than the page URL
        if r.canonical.value and r.canonical.is_self_referencing:
            canon_parsed = urlparse(r.canonical.value)
            page_has_slash = parsed.path.endswith("/")
            canon_has_slash = canon_parsed.path.endswith("/")
            if page_has_slash != canon_has_slash:
                canonical_slash_mismatch += 1

    # Also check internal link targets vs their canonical destinations
    link_slash_mismatches = []
    for url, r in pages.items():
        if not r.canonical.value:
            continue
        canon_parsed = urlparse(r.canonical.value)
        canon_has_slash = canon_parsed.path.endswith("/") and canon_parsed.path != "/"
        # Check internal links pointing to this page without matching slash convention
        for ld in r.links.link_details:
            if ld.link_type == "internal":
                ld_parsed = urlparse(ld.url)
                if ld_parsed.path != "/" and ld_parsed.path:
                    ld_has_slash = ld_parsed.path.endswith("/")
                    if ld_has_slash != canon_has_slash:
                        link_slash_mismatches.append(url)
                        break

    total_issues = (1 if (with_slash > 0 and without_slash > 0) else 0) + canonical_slash_mismatch + len(link_slash_mismatches)
    if total_issues == 0:
        return None

    parts = []
    if with_slash > 0 and without_slash > 0:
        majority = "with trailing slash" if with_slash > without_slash else "without trailing slash"
        parts.append(f"{with_slash} URLs with slash, {without_slash} without (majority: {majority})")
    if canonical_slash_mismatch:
        parts.append(f"{canonical_slash_mismatch} page(s) with canonical/URL slash mismatch")
    if link_slash_mismatches:
        parts.append(f"{len(link_slash_mismatches)} page(s) with internal links using different slash convention than canonicals")

    return SiteIssue(
        issue_type="trailing_slash_inconsistency",
        severity=IssueSeverity.WARNING if link_slash_mismatches else IssueSeverity.INFO,
        affected_pages=link_slash_mismatches[:50],
        description=f"Trailing slash inconsistency: {'; '.join(parts)}",
        fix="Standardize trailing slash usage and set up 301 redirects for the non-preferred format",
    )


def _check_pagination_issues(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag paginated pages missing rel=next/prev hints (T17)."""
    paginated = [url for url, r in pages.items() if r.rel_next or r.rel_prev]
    if not paginated:
        return None
    return SiteIssue(
        issue_type="pagination_detected",
        severity=IssueSeverity.INFO,
        affected_pages=paginated,
        description=f"{len(paginated)} page(s) with rel=next/prev pagination links detected",
        fix="Ensure all paginated pages have proper rel=next/prev and consider adding a view-all page",
    )


def _check_iframe_content(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages relying on iframe content (T18)."""
    affected = [url for url, r in pages.items() if r.iframe_count > 0]
    if not affected:
        return None
    total = sum(r.iframe_count for r in pages.values())
    return SiteIssue(
        issue_type="iframe_content",
        severity=IssueSeverity.INFO,
        affected_pages=affected,
        description=f"{total} iframe(s) across {len(affected)} page(s) — content inside iframes is not indexed",
        fix="Move important content out of iframes for better SEO visibility",
    )


def _check_iframe_accessibility(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with iframes missing title attribute or having empty src (FN-12)."""
    affected = []
    total_missing_title = 0
    total_empty_src = 0
    for url, r in pages.items():
        if r.iframes_missing_title > 0 or r.iframes_empty_src > 0:
            affected.append(url)
            total_missing_title += r.iframes_missing_title
            total_empty_src += r.iframes_empty_src
    if not affected:
        return None
    parts = []
    if total_missing_title:
        parts.append(f"{total_missing_title} missing title attribute")
    if total_empty_src:
        parts.append(f"{total_empty_src} with empty src")
    return SiteIssue(
        issue_type="iframe_accessibility",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"Iframe issues across {len(affected)} page(s): {'; '.join(parts)}",
        fix="Add descriptive title attributes to iframes for accessibility. Remove or fix iframes with empty src.",
    )


def _check_placeholder_meta_desc(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Flag pages with lorem ipsum in meta description (FN-9)."""
    affected = [url for url, r in pages.items() if r.has_placeholder_meta_desc]
    if not affected:
        return None
    return SiteIssue(
        issue_type="placeholder_meta_description",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) have Lorem ipsum placeholder text in their meta description",
        fix="Replace placeholder meta descriptions with real, descriptive content",
    )


def _check_orphan_pages_via_sitemap(
    pages: Dict[str, PageAuditResult],
    domain_checks: Optional[DomainCheckResult],
    link_graph: Optional[Dict[str, List[str]]],
) -> Optional[SiteIssue]:
    """Discover orphan pages: in sitemap but not linked from any crawled page (T21)."""
    if not domain_checks or not domain_checks.sitemap.urls_in_sitemap:
        return None
    if not link_graph:
        return None

    # Collect all URLs linked to from crawled pages
    linked_to: Set[str] = set()
    for targets in link_graph.values():
        linked_to.update(targets)
    # Add crawled page URLs themselves
    all_known = set(pages.keys()) | linked_to

    # Normalize for comparison
    def _norm(u):
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc.lower()}{p.path.rstrip('/') or '/'}"

    norm_known = {_norm(u) for u in all_known}

    orphans = []
    for sitemap_url in domain_checks.sitemap.urls_in_sitemap:
        if _norm(sitemap_url) not in norm_known:
            orphans.append(sitemap_url)

    if not orphans:
        return None
    return SiteIssue(
        issue_type="sitemap_orphan_pages",
        severity=IssueSeverity.INFO,
        affected_pages=orphans[:50],
        description=f"{len(orphans)} page(s) in sitemap are not linked from any crawled page (potential orphans)",
        fix="Add internal links to these pages or remove them from the sitemap if obsolete",
    )


def _check_structured_data_issues(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Surface structured data errors as a site-level issue (T8/T13).
    Only pages with actual errors (not just missing recommended fields) are flagged."""
    affected = []
    total_errors = 0
    total_warnings = 0
    for url, r in pages.items():
        page_errors = sum(len(i.errors) for i in r.structured_data.items)
        page_warnings = sum(len(i.warnings) for i in r.structured_data.items)
        total_errors += page_errors
        total_warnings += page_warnings
        # Only flag pages with actual errors, not just missing recommended fields
        if page_errors > 0:
            affected.append(url)

    if not affected:
        return None

    parts = [f"{total_errors} error(s)"]
    if total_warnings:
        parts.append(f"{total_warnings} recommendation(s)")

    return SiteIssue(
        issue_type="structured_data_issues",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"Structured data errors on {len(affected)} page(s): {', '.join(parts)}",
        fix="Fix missing required fields and schema errors in JSON-LD structured data",
    )


def _check_hreflang_issues(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Surface hreflang validation errors as a site-level issue (T14)."""
    affected = []
    all_errors: List[str] = []
    for url, r in pages.items():
        if r.hreflang.validation_errors:
            affected.append(url)
            all_errors.extend(r.hreflang.validation_errors)

    if not affected:
        return None
    return SiteIssue(
        issue_type="hreflang_issues",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"Hreflang validation issues on {len(affected)} page(s): {'; '.join(all_errors[:5])}",
        fix="Fix invalid language codes and ensure bidirectional hreflang confirmation",
    )


def _check_robots_deep_analysis(domain_checks: Optional[DomainCheckResult]) -> List[SiteIssue]:
    """Surface detailed robots.txt findings as site-level issues (T12: S1-S4)."""
    issues: List[SiteIssue] = []
    if not domain_checks or not domain_checks.robots_txt.exists:
        return issues

    r = domain_checks.robots_txt

    # S1: Broken sitemap references
    if r.broken_sitemap_refs:
        issues.append(SiteIssue(
            issue_type="robots_broken_sitemap_ref",
            severity=IssueSeverity.WARNING,
            affected_pages=r.broken_sitemap_refs,
            description=f"robots.txt references {len(r.broken_sitemap_refs)} non-existent sitemap(s): {', '.join(r.broken_sitemap_refs[:3])}",
            fix="Update robots.txt Sitemap directives to point to valid sitemap URLs",
        ))

    # S2: Conflicting rules
    if r.conflicting_rules:
        issues.append(SiteIssue(
            issue_type="robots_conflicting_rules",
            severity=IssueSeverity.WARNING,
            affected_pages=[],
            description=f"robots.txt has {len(r.conflicting_rules)} conflicting Allow/Disallow rule(s): {'; '.join(r.conflicting_rules[:3])}",
            fix="Resolve conflicting Allow/Disallow directives in robots.txt",
        ))

    # S3: Crawl-delay
    if r.crawl_delay_directives:
        issues.append(SiteIssue(
            issue_type="robots_crawl_delay",
            severity=IssueSeverity.INFO,
            affected_pages=[],
            description=f"robots.txt has crawl-delay directive(s): {'; '.join(r.crawl_delay_directives[:3])}",
            fix="Review crawl-delay settings — they may slow search engine crawling unnecessarily",
        ))

    return issues


def _check_redirect_loops(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """Detect meta refresh redirect loops (BUG-14/L9).
    If page A has meta_refresh pointing to B, and B points to A, it's a loop."""
    # Build meta refresh graph: url -> redirect target
    refresh_graph: Dict[str, str] = {}
    for url, r in pages.items():
        if r.meta_refresh_url:
            refresh_graph[url] = r.meta_refresh_url

    if not refresh_graph:
        return None

    # Detect cycles by following chains up to 10 hops
    loops_found: List[str] = []
    for start_url in refresh_graph:
        visited_in_chain: Set[str] = set()
        current = start_url
        for _ in range(10):
            if current in visited_in_chain:
                # Found a loop
                if start_url not in loops_found:
                    loops_found.append(start_url)
                break
            visited_in_chain.add(current)
            next_url = refresh_graph.get(current)
            if not next_url:
                break
            # Normalize for comparison
            parsed_next = urlparse(next_url)
            # Try to find the full URL in our pages
            matched = False
            for page_url in refresh_graph:
                parsed_page = urlparse(page_url)
                if parsed_next.path.rstrip("/") == parsed_page.path.rstrip("/"):
                    current = page_url
                    matched = True
                    break
            if not matched:
                break

    if not loops_found:
        return None
    return SiteIssue(
        issue_type="redirect_loops",
        severity=IssueSeverity.CRITICAL,
        affected_pages=loops_found,
        description=f"{len(loops_found)} meta refresh redirect loop(s) detected — pages redirect in an infinite cycle",
        fix="Break the redirect loop by removing or fixing the meta refresh tags creating the cycle",
    )


def _check_robots_compliance(
    pages: Dict[str, PageAuditResult],
    domain_checks: Optional[DomainCheckResult],
) -> Optional[SiteIssue]:
    """Flag pages that were crawled but are disallowed by robots.txt (BUG-09/X4)."""
    if not domain_checks or not domain_checks.robots_txt.exists:
        return None
    if not domain_checks.robots_txt.blocked_paths:
        return None

    blocked = set(domain_checks.robots_txt.blocked_paths)
    affected = []
    for url in pages:
        parsed = urlparse(url)
        path = parsed.path.rstrip("/") or "/"
        for bp in blocked:
            bp_clean = bp.rstrip("/") or "/"
            if path.startswith(bp_clean):
                affected.append(url)
                break

    if not affected:
        return None
    return SiteIssue(
        issue_type="crawled_but_blocked",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} page(s) were crawled but are disallowed by robots.txt",
        fix="Either remove the robots.txt Disallow directive or remove links to these pages",
    )


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

    # Titles (15 pts): pass only if status is PASS (good length)
    titles_pass = sum(1 for r in results if r.title.status == CheckStatus.PASS)
    titles = _category_score(titles_pass, n, 15)

    # Meta descriptions (10 pts): pass only if status is PASS (good length)
    meta_pass = sum(1 for r in results if r.meta_description.status == CheckStatus.PASS)
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
    # BUG-024: Flag when score is based only on basic metrics (no Lighthouse)
    has_lighthouse = any(
        getattr(r.performance, "lighthouse_performance", None) is not None
        for r in results
    )
    if not has_lighthouse:
        performance = CategoryScore(
            score=performance.score,
            max_score=performance.max_score,
            pass_rate=performance.pass_rate,
            details=f"{performance.details} (basic metrics only — Lighthouse unavailable)",
        )

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
    link_graph: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """Build crawl metadata dict for the summary, including link graph stats."""
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

    # Link graph stats
    if link_graph is not None:
        meta["link_graph_stats"] = _compute_link_graph_stats(pages, link_graph)

    return meta


def _compute_link_graph_stats(
    pages: Dict[str, PageAuditResult],
    link_graph: Dict[str, List[str]],
) -> Dict[str, Any]:
    """Compute link graph statistics for the site summary."""
    total_internal = sum(r.links.internal_count for r in pages.values())
    total_external = sum(r.links.external_count for r in pages.values())
    total_unique_targets = sum(r.links.unique_internal_targets for r in pages.values())
    n_pages = len(pages)
    avg_internal = round(total_internal / n_pages, 1) if n_pages else 0
    avg_unique = round(total_unique_targets / n_pages, 1) if n_pages else 0

    # Find orphan pages (no inbound internal links, excluding homepage)
    # Also count inbound links per page for most_linked_pages
    inbound_counts: Counter = Counter()
    linked_to: Set[str] = set()
    for targets in link_graph.values():
        linked_to.update(targets)
        for t in targets:
            inbound_counts[t] += 1
    orphan_count = 0
    for url in pages:
        parsed = urlparse(url)
        if parsed.path in ("", "/"):
            continue
        if url not in linked_to:
            orphan_count += 1

    # Top 10 most-linked pages
    most_linked = [
        {"url": url, "inbound_links": count}
        for url, count in inbound_counts.most_common(10)
    ]

    # BFS from homepage to compute depth distribution
    homepage = None
    for url in link_graph:
        parsed = urlparse(url)
        if parsed.path in ("", "/"):
            homepage = url
            break
    if not homepage:
        homepage = next(iter(link_graph), None)

    pages_at_depth: Dict[str, int] = {}
    max_depth = 0
    if homepage:
        depths: Dict[str, int] = {homepage: 0}
        queue = [homepage]
        visited_bfs = {homepage}
        while queue:
            current = queue.pop(0)
            d = depths[current]
            for linked in link_graph.get(current, []):
                if linked not in visited_bfs and linked in pages:
                    visited_bfs.add(linked)
                    depths[linked] = d + 1
                    queue.append(linked)
        for d in depths.values():
            key = str(d)
            pages_at_depth[key] = pages_at_depth.get(key, 0) + 1
            if d > max_depth:
                max_depth = d

    return {
        "total_internal_links": total_internal,
        "total_external_links": total_external,
        "unique_internal_targets": len(linked_to),
        "orphan_pages": orphan_count,
        "avg_internal_links_per_page": avg_internal,
        "avg_unique_targets_per_page": avg_unique,
        "max_link_depth": max_depth,
        "pages_at_depth": pages_at_depth,
        "most_linked_pages": most_linked,
    }


def _check_broken_external_links(
    pages: Dict[str, PageAuditResult],
    broken_external: Dict[str, int],
) -> Optional[SiteIssue]:
    """Flag pages that link to broken external URLs."""
    if not broken_external:
        return None

    # Find which pages link to these broken external URLs
    affected = []
    for url, r in pages.items():
        page_ext_urls = set(r.links.external_urls)
        if page_ext_urls & set(broken_external.keys()):
            affected.append(url)

    if not affected:
        return None

    # Build a per-URL summary: how many pages link to each broken URL
    url_page_counts: Dict[str, int] = {}
    for url, r in pages.items():
        page_ext_urls = set(r.links.external_urls)
        for broken_url in broken_external:
            if broken_url in page_ext_urls:
                url_page_counts[broken_url] = url_page_counts.get(broken_url, 0) + 1

    # Build human-readable summary of broken URLs
    broken_summaries = []
    for broken_url, count in sorted(url_page_counts.items(), key=lambda x: -x[1]):
        domain = urlparse(broken_url).netloc
        status = broken_external.get(broken_url, 0)
        status_label = f"HTTP {status}" if status else "timeout"
        broken_summaries.append(f"{domain} ({status_label}, {count} page{'s' if count != 1 else ''})")

    desc = f"{len(broken_external)} unique broken external URL(s) across {len(affected)} page(s): {'; '.join(broken_summaries[:5])}"

    return SiteIssue(
        issue_type="broken_external_links",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=desc,
        fix="Fix or remove links to external pages that return 404/5xx or time out",
    )


def _check_redirect_chains(
    chains: List[Dict[str, Any]],
) -> List[SiteIssue]:
    """Generate issues from redirect chain data."""
    issues: List[SiteIssue] = []

    # Redirect chains (2+ hops)
    long_chains = [c for c in chains if c.get("chain_length", 0) >= 2]
    if long_chains:
        affected = [c["source_url"] for c in long_chains]
        issues.append(SiteIssue(
            issue_type="redirect_chains",
            severity=IssueSeverity.WARNING,
            affected_pages=affected[:50],
            description=f"{len(long_chains)} URL(s) go through 2+ redirects before resolving",
            fix="Update links to point directly to the final URL to reduce redirect hops",
        ))

    # Redirect loops (chain_path contains duplicates, excluding self-referential non-loops)
    loops = []
    for c in chains:
        path = c.get("chain_path", [])
        # Skip entries where source == final with chain_length <= 1 (not a real loop)
        if c.get("source_url") == c.get("final_url") and c.get("chain_length", 0) <= 1:
            continue
        if len(path) != len(set(path)):
            loops.append(c["source_url"])
    if loops:
        issues.append(SiteIssue(
            issue_type="redirect_loops",
            severity=IssueSeverity.CRITICAL,
            affected_pages=loops[:50],
            description=f"{len(loops)} URL(s) are caught in redirect loops",
            fix="Fix the redirect configuration to eliminate circular redirects",
        ))

    return issues


def _check_hreflang_bidirectional(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """BUG-19: Check that hreflang tags have bidirectional confirmation.
    If page A declares hreflang pointing to page B, page B should point back to page A."""
    hreflang_map: Dict[str, Dict[str, str]] = {}
    for url, r in pages.items():
        if r.hreflang.entries:
            lang_to_href = {}
            for entry in r.hreflang.entries:
                if entry.href:
                    lang_to_href[entry.lang] = entry.href
            if lang_to_href:
                hreflang_map[url] = lang_to_href

    if not hreflang_map:
        return None

    def _norm(u):
        p = urlparse(u)
        return f"{p.scheme}://{p.netloc.lower()}{(p.path.rstrip('/') or '/')}"

    norm_to_url = {_norm(u): u for u in hreflang_map}
    missing_return = []

    for url, lang_hrefs in hreflang_map.items():
        for lang, href in lang_hrefs.items():
            if lang == "x-default":
                continue
            norm_href = _norm(href)
            target_url = norm_to_url.get(norm_href)
            if target_url and target_url in hreflang_map:
                target_hrefs = hreflang_map[target_url]
                norm_url = _norm(url)
                has_return = any(_norm(h) == norm_url for h in target_hrefs.values())
                if not has_return and url not in missing_return:
                    missing_return.append(url)

    if not missing_return:
        return None
    return SiteIssue(
        issue_type="hreflang_missing_return",
        severity=IssueSeverity.WARNING,
        affected_pages=missing_return[:50],
        description=f"{len(missing_return)} page(s) have hreflang tags without bidirectional confirmation from target page",
        fix="Ensure each hreflang target page links back with a matching hreflang tag",
    )


def _check_pagination_seo(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """BUG-20: Check for pagination SEO issues beyond just detecting rel=next/prev.
    Flags pages with pagination URL patterns that lack proper link hints."""
    import re as _re
    pagination_pattern = _re.compile(r'[?&/](page|p|pg)[=/]?\d+', _re.IGNORECASE)
    affected = []
    for url, r in pages.items():
        if pagination_pattern.search(url) and not r.rel_next and not r.rel_prev:
            affected.append(url)
    if not affected:
        return None
    return SiteIssue(
        issue_type="pagination_missing_hints",
        severity=IssueSeverity.WARNING,
        affected_pages=affected,
        description=f"{len(affected)} paginated URL(s) missing rel=next/prev hints",
        fix="Add rel=next and rel=prev link elements to paginated pages for proper crawl handling",
    )


def _check_excessive_inline_css(pages: Dict[str, PageAuditResult]) -> Optional[SiteIssue]:
    """BUG-21: Flag pages with excessive inline CSS (style attributes or large <style> blocks).
    Uses inline_css_count and inline_style_bytes from PageAuditResult if available."""
    affected = []
    for url, r in pages.items():
        if getattr(r, 'inline_css_count', 0) > 20 or getattr(r, 'inline_style_bytes', 0) > 50000:
            affected.append(url)
    if not affected:
        return None
    return SiteIssue(
        issue_type="excessive_inline_css",
        severity=IssueSeverity.INFO,
        affected_pages=affected,
        description=f"{len(affected)} page(s) have excessive inline CSS (>20 style attrs or >50KB inline styles)",
        fix="Move inline styles to external CSS files for better caching and maintainability",
    )


def _check_image_quality_issues(pages: Dict[str, PageAuditResult]) -> List[SiteIssue]:
    """BUG-16/17: Surface image quality issues at site level."""
    issues_list: List[SiteIssue] = []

    long_alt_pages = [url for url, r in pages.items() if r.images.long_alt > 0]
    if long_alt_pages:
        total = sum(r.images.long_alt for r in pages.values())
        issues_list.append(SiteIssue(
            issue_type="long_alt_text",
            severity=IssueSeverity.WARNING,
            affected_pages=long_alt_pages[:50],
            description=f"{total} image(s) across {len(long_alt_pages)} page(s) have alt text >125 characters",
            fix="Shorten alt text to be concise and descriptive (under 125 characters)",
        ))

    stuffed_pages = [url for url, r in pages.items() if r.images.keyword_stuffed_alt > 0]
    if stuffed_pages:
        total = sum(r.images.keyword_stuffed_alt for r in pages.values())
        issues_list.append(SiteIssue(
            issue_type="keyword_stuffed_alt",
            severity=IssueSeverity.WARNING,
            affected_pages=stuffed_pages[:50],
            description=f"{total} image(s) across {len(stuffed_pages)} page(s) have keyword-stuffed alt text",
            fix="Write natural, descriptive alt text instead of repeating keywords",
        ))

    broken_pages = [url for url, r in pages.items() if r.images.broken_src > 0]
    if broken_pages:
        total = sum(r.images.broken_src for r in pages.values())
        issues_list.append(SiteIssue(
            issue_type="broken_image_src",
            severity=IssueSeverity.WARNING,
            affected_pages=broken_pages[:50],
            description=f"{total} image(s) across {len(broken_pages)} page(s) have broken or empty src",
            fix="Fix broken image URLs or remove images with invalid sources",
        ))

    return issues_list
