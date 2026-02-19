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

    for line in content.splitlines():
        line = line.strip()
        lower = line.lower()

        if lower.startswith("sitemap:"):
            has_sitemap_ref = True

        if lower.startswith("disallow:"):
            path = line.split(":", 1)[1].strip()
            if path:
                blocked_paths.append(path)
                # Check if any important path is blocked
                for important in IMPORTANT_PATHS:
                    if important.startswith(path) or path == "/":
                        blocks_important = True

    issues = []
    if blocks_important:
        issues.append("blocks important pages")
    if not has_sitemap_ref:
        issues.append("no sitemap reference")

    if blocks_important:
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
        blocked_paths=blocked_paths[:20],
        blocks_important_pages=blocks_important,
        raw_content=content[:5000],
        status=status,
        note=note,
    )


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
    try:
        root = ElementTree.fromstring(content_bytes)
        is_valid_xml = True

        # Handle namespace — sitemaps use {http://www.sitemaps.org/schemas/sitemap/0.9}
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        # Check for sitemap index
        if root.tag.endswith("sitemapindex"):
            # It's a sitemap index — extract child sitemap URLs
            for sitemap_el in root.findall(f"{ns}sitemap"):
                loc = sitemap_el.find(f"{ns}loc")
                if loc is not None and loc.text:
                    sitemap_urls.append(loc.text.strip())
        else:
            # Regular sitemap — extract page URLs
            for url_el in root.findall(f"{ns}url"):
                loc = url_el.find(f"{ns}loc")
                if loc is not None and loc.text:
                    sitemap_urls.append(loc.text.strip())
    except ElementTree.ParseError:
        return SitemapCheck(
            exists=True,
            is_valid_xml=False,
            status=CheckStatus.WARNING,
            note="sitemap.xml exists but contains invalid XML",
        )

    url_count = len(sitemap_urls)

    # Cross-reference with crawled URLs
    sitemap_set = set(sitemap_urls)
    urls_not_in_crawl = []
    crawled_not_in_sitemap = []

    if crawled:
        urls_not_in_crawl = [u for u in sitemap_urls if u not in crawled][:50]
        crawled_not_in_sitemap = [u for u in crawled if u not in sitemap_set][:50]

    issues = []
    if is_too_large:
        issues.append("sitemap too large (>10MB)")
    if url_count > SITEMAP_URL_LIMIT:
        issues.append(f"too many URLs ({url_count:,})")
    if crawled_not_in_sitemap:
        issues.append(f"{len(crawled_not_in_sitemap)} crawled URL(s) not in sitemap")

    if is_too_large or not is_valid_xml:
        status = CheckStatus.WARNING
    elif issues:
        status = CheckStatus.INFO
    else:
        status = CheckStatus.PASS

    if issues:
        note = f"sitemap.xml: {url_count} URLs; {'; '.join(issues)}"
    else:
        note = f"sitemap.xml valid with {url_count} URLs"

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
