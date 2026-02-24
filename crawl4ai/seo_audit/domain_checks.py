"""
Domain-level SEO checks: robots.txt and sitemap.xml validation.

These are fetched once per domain (not per-page) and run during the crawl.
"""

import logging
from typing import List, Optional, Set
from xml.etree import ElementTree

import aiohttp

from .models import (
    CheckStatus,
    RobotsTxtCheck,
    SitemapCheck,
    DomainCheckResult,
)

logger = logging.getLogger("seo-domain-checks")

# Paths that should generally not be blocked by robots.txt
IMPORTANT_PATHS = ["/", "/about", "/contact", "/products", "/services", "/blog"]

# Max sitemap size before flagging (50MB per Google spec, we flag at 10MB)
SITEMAP_SIZE_LIMIT = 10 * 1024 * 1024
# Max URLs in a single sitemap (50,000 per spec, we flag at 50,000)
SITEMAP_URL_LIMIT = 50_000


async def check_robots_txt(
    domain: str,
    scheme: str = "https",
    timeout: int = 10,
) -> RobotsTxtCheck:
    """
    Fetch and validate robots.txt for a domain.

    Checks:
    - Existence (HTTP 200)
    - Disallow rules that block important pages
    - Sitemap reference
    """
    url = f"{scheme}://{domain}/robots.txt"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    return RobotsTxtCheck(
                        exists=False,
                        status=CheckStatus.WARNING,
                        note=f"robots.txt not found (HTTP {resp.status})",
                    )

                content = await resp.text()
    except Exception as e:
        return RobotsTxtCheck(
            exists=False,
            status=CheckStatus.WARNING,
            note=f"Could not fetch robots.txt: {e}",
        )

    blocked_paths: List[str] = []
    has_sitemap_ref = False
    blocks_important = False
    findings: List[str] = []
    sitemap_refs: List[str] = []
    conflicting_rules: List[str] = []
    crawl_delay_directives: List[str] = []

    # Parse into per-user-agent sections
    current_agent = "*"
    agent_rules: dict = {}  # agent -> list of (directive, path)

    for line in content.splitlines():
        line = line.strip()
        lower = line.lower()

        # Skip comments and empty lines
        if not line or line.startswith("#"):
            continue

        if lower.startswith("user-agent:"):
            current_agent = line.split(":", 1)[1].strip()
            if current_agent not in agent_rules:
                agent_rules[current_agent] = []

        elif lower.startswith("sitemap:"):
            has_sitemap_ref = True
            # Extract full URL after "Sitemap:" prefix (case-insensitive)
            sitemap_url = line[len("sitemap:"):].strip()
            if sitemap_url:
                sitemap_refs.append(sitemap_url)

        elif lower.startswith("crawl-delay:"):
            delay_val = line.split(":", 1)[1].strip()
            directive = f"Crawl-delay: {delay_val} for {current_agent}"
            crawl_delay_directives.append(directive)
            findings.append(f"{directive} (may slow crawling)")

        elif lower.startswith("disallow:"):
            path = line.split(":", 1)[1].strip()
            if path:
                blocked_paths.append(path)
                if current_agent not in agent_rules:
                    agent_rules[current_agent] = []
                agent_rules[current_agent].append(("disallow", path))
                for important in IMPORTANT_PATHS:
                    if important.startswith(path) or path == "/":
                        blocks_important = True

        elif lower.startswith("allow:"):
            path = line.split(":", 1)[1].strip()
            if path:
                if current_agent not in agent_rules:
                    agent_rules[current_agent] = []
                agent_rules[current_agent].append(("allow", path))

    # Detect conflicting Allow/Disallow for the same agent
    # Note: Allow / + Disallow /specific is standard (not a conflict)
    for agent, rules in agent_rules.items():
        allows = [p for d, p in rules if d == "allow"]
        disallows = [p for d, p in rules if d == "disallow"]
        for allow_path in allows:
            for disallow_path in disallows:
                # Skip trivial non-conflicts: Allow / + Disallow /anything is standard
                if allow_path == "/":
                    continue
                # Skip: Disallow / + Allow /specific is also standard (override)
                if disallow_path == "/":
                    continue
                # Real conflict: overlapping specific paths
                if allow_path.startswith(disallow_path) or disallow_path.startswith(allow_path):
                    conflict = f"{agent}: Allow {allow_path} vs Disallow {disallow_path}"
                    conflicting_rules.append(conflict)
                    findings.append(f"Conflicting directives for {conflict}")

    # BUG-6: Validate sitemap references — check reachability AND valid XML content
    broken_sitemap_refs: List[str] = []
    if sitemap_refs:
        try:
            async with aiohttp.ClientSession() as sess:
                for smap_url in sitemap_refs[:5]:
                    try:
                        async with sess.get(smap_url, timeout=aiohttp.ClientTimeout(total=10), allow_redirects=True) as resp:
                            if resp.status >= 400:
                                broken_sitemap_refs.append(smap_url)
                                findings.append(f"Broken sitemap reference: {smap_url} (HTTP {resp.status})")
                            else:
                                # Verify content is valid XML with sitemap elements
                                body = await resp.read()
                                try:
                                    root = ElementTree.fromstring(body)
                                    tag = root.tag.lower()
                                    if "urlset" not in tag and "sitemapindex" not in tag:
                                        broken_sitemap_refs.append(smap_url)
                                        findings.append(f"Sitemap reference {smap_url} is not valid sitemap XML (root: {root.tag})")
                                except ElementTree.ParseError:
                                    broken_sitemap_refs.append(smap_url)
                                    findings.append(f"Sitemap reference {smap_url} returns invalid XML")
                    except Exception:
                        broken_sitemap_refs.append(smap_url)
                        findings.append(f"Broken sitemap reference: {smap_url} (unreachable)")
        except Exception:
            pass

    issues = []
    if blocks_important:
        issues.append("blocks important pages")
    if not has_sitemap_ref:
        issues.append("no sitemap reference")
    if crawl_delay_directives:
        issues.append("has crawl-delay directive")
    if broken_sitemap_refs:
        issues.append(f"{len(broken_sitemap_refs)} broken sitemap ref(s)")
    if conflicting_rules:
        issues.append(f"{len(conflicting_rules)} conflicting rule(s)")

    if blocks_important:
        status = CheckStatus.WARNING
    elif broken_sitemap_refs or conflicting_rules:
        status = CheckStatus.WARNING
    elif not has_sitemap_ref:
        status = CheckStatus.INFO
    else:
        status = CheckStatus.PASS

    if issues:
        note = f"robots.txt found: {'; '.join(issues)}"
    else:
        note = "robots.txt properly configured"

    return RobotsTxtCheck(
        exists=True,
        has_sitemap_reference=has_sitemap_ref,
        sitemap_refs=sitemap_refs[:10],
        broken_sitemap_refs=broken_sitemap_refs,
        blocked_paths=blocked_paths[:20],
        blocks_important_pages=blocks_important,
        conflicting_rules=conflicting_rules[:10],
        crawl_delay_directives=crawl_delay_directives[:10],
        raw_content=content[:5000],
        findings=findings[:20],
        status=status,
        note=note,
    )


async def _fetch_sitemap_urls(
    sitemap_url: str,
    session: aiohttp.ClientSession,
    timeout: int = 15,
    depth: int = 0,
) -> List[str]:
    """Fetch a sitemap (or sitemap index) and return all page URLs.
    Recursively follows sitemapindex entries up to 2 levels deep."""
    if depth > 2:
        return []
    try:
        async with session.get(sitemap_url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
            if resp.status != 200:
                return []
            content_bytes = await resp.read()
    except Exception:
        return []

    try:
        root = ElementTree.fromstring(content_bytes)
    except ElementTree.ParseError:
        return []

    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    if root.tag.endswith("sitemapindex"):
        # It's a sitemap index — recursively fetch each child sitemap
        child_urls = []
        for sitemap_el in root.findall(f"{ns}sitemap"):
            loc = sitemap_el.find(f"{ns}loc")
            if loc is not None and loc.text:
                child_urls.append(loc.text.strip())
        all_page_urls: List[str] = []
        for child_url in child_urls[:50]:  # limit to 50 child sitemaps
            child_pages = await _fetch_sitemap_urls(child_url, session, timeout, depth + 1)
            all_page_urls.extend(child_pages)
        return all_page_urls
    else:
        # Regular sitemap — extract page URLs
        page_urls = []
        for url_el in root.findall(f"{ns}url"):
            loc = url_el.find(f"{ns}loc")
            if loc is not None and loc.text:
                page_urls.append(loc.text.strip())
        return page_urls


async def check_sitemap(
    domain: str,
    crawled_urls: Optional[Set[str]] = None,
    scheme: str = "https",
    timeout: int = 15,
) -> SitemapCheck:
    """
    Fetch and validate sitemap.xml for a domain.

    Checks:
    - Existence
    - Valid XML
    - URL count and size
    - Follows sitemapindex to child sitemaps
    - Cross-reference with crawled URLs (if provided)
    """
    url = f"{scheme}://{domain}/sitemap.xml"
    crawled = crawled_urls or set()

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                if resp.status != 200:
                    return SitemapCheck(
                        exists=False,
                        status=CheckStatus.WARNING,
                        note=f"sitemap.xml not found (HTTP {resp.status})",
                    )

                content_bytes = await resp.read()
    except Exception as e:
        return SitemapCheck(
            exists=False,
            status=CheckStatus.WARNING,
            note=f"Could not fetch sitemap.xml: {e}",
        )

    is_too_large = len(content_bytes) > SITEMAP_SIZE_LIMIT

    # Parse XML
    sitemap_urls: List[str] = []
    is_valid_xml = False
    is_index = False
    try:
        root = ElementTree.fromstring(content_bytes)
        is_valid_xml = True

        # Handle namespace — sitemaps use {http://www.sitemaps.org/schemas/sitemap/0.9}
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        # Check for sitemap index
        if root.tag.endswith("sitemapindex"):
            is_index = True
            # Follow child sitemaps to get actual page URLs
            async with aiohttp.ClientSession() as session:
                sitemap_urls = await _fetch_sitemap_urls(url, session, timeout)
        else:
            # Regular sitemap — extract page URLs
            for url_el in root.findall(f"{ns}url"):
                loc = url_el.find(f"{ns}loc")
                if loc is not None and loc.text:
                    sitemap_urls.append(loc.text.strip())
    except ElementTree.ParseError:
        # BUG-7: Even with invalid XML, try to extract URLs via regex for cross-referencing
        import re as _re
        raw_text = content_bytes.decode("utf-8", errors="replace")
        fallback_urls = _re.findall(r'<loc>\s*(https?://[^<\s]+)\s*</loc>', raw_text)
        return SitemapCheck(
            exists=True,
            is_valid_xml=False,
            urls_in_sitemap=fallback_urls[:200],
            url_count=len(fallback_urls),
            status=CheckStatus.WARNING,
            note=f"sitemap.xml exists but contains invalid XML ({len(fallback_urls)} URLs extracted via fallback)",
        )

    url_count = len(sitemap_urls)

    # Cross-reference with crawled URLs (normalize trailing slashes for comparison)
    def _norm(u: str) -> str:
        from urllib.parse import urlparse as _urlparse
        p = _urlparse(u)
        path = p.path.rstrip("/") or "/"
        return f"{p.scheme}://{p.netloc}{path}"

    sitemap_normalized = {_norm(u) for u in sitemap_urls}
    crawled_normalized = {_norm(u): u for u in crawled}

    urls_not_in_crawl = []
    crawled_not_in_sitemap = []

    if crawled:
        urls_not_in_crawl = [u for u in sitemap_urls if _norm(u) not in crawled_normalized][:50]
        crawled_not_in_sitemap = [
            orig for norm, orig in crawled_normalized.items()
            if norm not in sitemap_normalized
        ][:50]

    issues = []
    if is_too_large:
        issues.append("sitemap too large (>10MB)")
    if url_count == 0 and is_valid_xml:
        issues.append("sitemap exists but contains no page URLs")
    if url_count > SITEMAP_URL_LIMIT:
        issues.append(f"too many URLs ({url_count:,})")
    if crawled_not_in_sitemap:
        issues.append(f"{len(crawled_not_in_sitemap)} crawled URL(s) not in sitemap")

    if is_too_large or not is_valid_xml or url_count == 0:
        status = CheckStatus.WARNING
    elif issues:
        status = CheckStatus.INFO
    else:
        status = CheckStatus.PASS

    index_note = " (via sitemapindex)" if is_index else ""
    if issues:
        note = f"sitemap.xml{index_note}: {url_count} URLs; {'; '.join(issues)}"
    else:
        note = f"sitemap.xml{index_note} valid with {url_count} URLs"

    return SitemapCheck(
        exists=True,
        is_valid_xml=is_valid_xml,
        url_count=url_count,
        urls_in_sitemap=sitemap_urls[:200],
        urls_not_in_crawl=urls_not_in_crawl,
        crawled_not_in_sitemap=crawled_not_in_sitemap,
        is_too_large=is_too_large,
        status=status,
        note=note,
    )


async def run_domain_checks(
    domain: str,
    crawled_urls: Optional[Set[str]] = None,
    scheme: str = "https",
) -> DomainCheckResult:
    """
    Run all domain-level checks (robots.txt + sitemap.xml).

    Args:
        domain: The domain to check (e.g. "example.com").
        crawled_urls: Set of URLs that were successfully crawled.
        scheme: URL scheme (default "https").

    Returns:
        DomainCheckResult with robots_txt and sitemap check results.
    """
    robots = await check_robots_txt(domain, scheme=scheme)
    sitemap = await check_sitemap(domain, crawled_urls=crawled_urls, scheme=scheme)

    return DomainCheckResult(
        robots_txt=robots,
        sitemap=sitemap,
    )
