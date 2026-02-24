"""
Container HTTP server for Cloudflare Containers.

The CF Worker proxies requests via container.fetch() to this server.
Runs on port 8000 (matching CrawlerContainer.defaultPort).

Endpoints:
    POST /start       — Begin a crawl job (returns immediately, runs in background)
    GET  /status      — Poll crawl progress and retrieve results when done
    GET  /health      — Liveness check
    POST /lighthouse  — Run Lighthouse audits on a list of URLs (synchronous)
"""

import os
import sys
import json
import time
import asyncio
import logging
import threading
import subprocess
import aiohttp
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

sys.path.insert(0, "/app")

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from crawl4ai.deep_crawling import BFSDeepCrawlStrategy
from crawl4ai.seo_audit import SEOAnalyzer
from crawl4ai.seo_audit.site_checks import run_site_checks
from crawl4ai.seo_audit.domain_checks import run_domain_checks

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("seo-container")

# ─── Shared State ─────────────────────────────────────────────────────
# One crawl per container instance (one container per job)

CRAWL_TIMEOUT_S = 4 * 60  # 4 minutes — must be less than Worker's 5-min job timeout
PAGE_TIMEOUT_MS = 30_000  # 30s per page navigation
POST_CRAWL_TIMEOUT_S = 60  # 60s for site analysis + domain checks after crawl
EXTERNAL_CHECK_TIMEOUT_S = 10  # 10s per external HEAD request
MAX_EXTERNAL_CHECKS = 100  # max unique external URLs to check
STATE_FILE = "/tmp/crawl_state.json"
PAGES_FILE = "/tmp/crawl_pages.json"


def _normalize_url(url: str) -> str:
    """Normalize URL by stripping trailing slash (except bare domain root)."""
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    normalized = f"{parsed.scheme}://{parsed.netloc}{path}"
    if parsed.query:
        normalized += f"?{parsed.query}"
    return normalized


# JS snippet to collect resource timing data + navigation timing via Performance API
RESOURCE_TIMING_JS = """
const entries = performance.getEntriesByType('resource');
const breakdown = {};
const imageSizes = {};
for (const e of entries) {
    const t = e.initiatorType || 'other';
    const bytes = e.transferSize || e.encodedBodySize || 0;
    breakdown[t] = (breakdown[t] || 0) + bytes;
    if (t === 'img' && bytes > 0) {
        imageSizes[e.name] = bytes;
    }
}
let responseTimeMs = null;
const nav = performance.getEntriesByType('navigation');
if (nav.length > 0) {
    breakdown['document'] = nav[0].transferSize || nav[0].encodedBodySize || 0;
    responseTimeMs = nav[0].responseEnd - nav[0].requestStart;
}
// BUG-4: Collect image srcs that lack explicit width/height HTML attributes
const imgsMissingDims = [];
// BUG-17: Collect broken image srcs (failed to load)
const imgsBrokenSrc = [];
document.querySelectorAll('img').forEach(img => {
    if (!img.hasAttribute('width') || !img.hasAttribute('height')) {
        imgsMissingDims.push(img.src || img.dataset.src || '');
    }
    // naturalWidth/Height is 0 for images that failed to load (and not SVGs)
    const src = img.src || '';
    if (src && src !== window.location.href && img.complete && img.naturalWidth === 0) {
        imgsBrokenSrc.push(src);
    }
});
return { breakdown: breakdown, imageSizes: imageSizes, responseTimeMs: responseTimeMs, imgsMissingDims: imgsMissingDims, imgsBrokenSrc: imgsBrokenSrc };
"""

state = {
    "status": "idle",       # idle | running | completed | failed
    "job_id": None,
    "pages_found": 0,
    "pages_done": 0,
    "error": None,
    "results": None,        # set when completed
    "partial_pages": [],    # page payloads persisted incrementally
    "partial_snapshots": [],
    "partial_link_graph": [],   # link detail payloads per page
    "redirect_chains": [],      # redirect chain entries from crawl
}
state_lock = threading.Lock()

# Store PageAuditResult objects during incremental auditing.
# Used by _post_crawl_analysis for site-level checks (avoids re-auditing
# and works even when the crawl times out before returning CrawlResult objects).
_page_audit_results = {}  # Dict[str, PageAuditResult]


def _persist_state():
    """Write terminal state (completed/failed) to disk so it survives container sleep."""
    try:
        # Write pages separately (can be large)
        with open(PAGES_FILE, "w") as f:
            json.dump({
                "pages": state.get("partial_pages", []),
                "snapshots": state.get("partial_snapshots", []),
                "link_graph": state.get("partial_link_graph", []),
                "redirect_chains": state.get("redirect_chains", []),
            }, f)
        # Write state without bulky data
        slim = {k: v for k, v in state.items() if k not in ("partial_pages", "partial_snapshots", "partial_link_graph", "redirect_chains")}
        with open(STATE_FILE, "w") as f:
            json.dump(slim, f)
        logger.info(f"State persisted to disk (status={state['status']}, pages={len(state.get('partial_pages', []))})")
    except Exception as e:
        logger.warning(f"Failed to persist state: {e}")


def _load_persisted_state():
    """Load state from disk if available (after container restart from sleep)."""
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r") as f:
                loaded = json.load(f)
            # Merge pages back in
            if os.path.exists(PAGES_FILE):
                with open(PAGES_FILE, "r") as f:
                    pages_data = json.load(f)
                loaded["partial_pages"] = pages_data.get("pages", [])
                loaded["partial_snapshots"] = pages_data.get("snapshots", [])
                loaded["partial_link_graph"] = pages_data.get("link_graph", [])
                loaded["redirect_chains"] = pages_data.get("redirect_chains", [])
            return loaded
    except Exception as e:
        logger.warning(f"Failed to load persisted state: {e}")
    return None


def _audit_page_to_payload(page_audit):
    """Convert a PageAuditResult to the flat dict the Worker expects."""
    try:
        return {
            "url": page_audit.url,
            "status_code": page_audit.status_code,
            "title": page_audit.title.value,
            "title_length": page_audit.title.length,
            "title_status": page_audit.title.status.value,
            "meta_desc": page_audit.meta_description.value,
            "meta_desc_length": page_audit.meta_description.length,
            "meta_desc_status": page_audit.meta_description.status.value,
            "h1_count": page_audit.headings.h1_count,
            "has_canonical": page_audit.canonical.value is not None,
            "is_indexable": page_audit.robots.is_indexable,
            "has_json_ld": page_audit.structured_data.has_json_ld,
            "has_viewport": page_audit.viewport.status.value == "pass",
            "has_og_tags": page_audit.open_graph.status.value != "fail",
            "word_count": page_audit.content.word_count,
            "images_total": page_audit.images.total,
            "images_no_alt": page_audit.images.missing_alt,
            "internal_links": page_audit.links.internal_count,
            "external_links": page_audit.links.external_count,
            "mixed_content": page_audit.mixed_content.has_mixed_content,
            "response_time_ms": page_audit.performance.response_time_ms if page_audit.performance else None,
            "page_weight_bytes": page_audit.performance.page_weight_bytes if page_audit.performance else None,
            "audit_json": page_audit.model_dump_json(),
        }
    except Exception as e:
        logger.error(f"_audit_page_to_payload FAILED for {page_audit.url}: {e}", exc_info=True)
        raise


# ─── Crawl Runner (async, runs in background thread) ─────────────────

def run_crawl_in_thread(job_id: str, url: str, max_pages: int, max_depth: int):
    """Runs the async crawl in a new event loop on a background thread."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_crawl(job_id, url, max_pages, max_depth))
    except Exception as e:
        logger.error(f"Crawl thread failed: {e}", exc_info=True)
        with state_lock:
            if state["status"] == "running":
                state["status"] = "failed"
                state["error"] = str(e)
                _persist_state()
    finally:
        loop.close()
        # Safety net: if state is still 'running' after thread exits, mark failed
        with state_lock:
            if state["status"] == "running":
                logger.error("Crawl thread exited while state still 'running' — marking failed")
                state["status"] = "failed"
                state["error"] = state["error"] or "Crawl thread exited unexpectedly"
                _persist_state()


_analyzer = SEOAnalyzer()


def _extract_resource_timing(crawl_result):
    """Extract resource breakdown, per-image sizes, response time, images missing dims, and broken images from JS execution result.
    Returns (resource_breakdown, image_sizes, response_time_ms, imgs_missing_dims, imgs_broken_src) — all may be None."""
    js_result = getattr(crawl_result, "js_execution_result", None)
    logger.info(f"JS execution result keys for {crawl_result.url}: {list(js_result.keys()) if isinstance(js_result, dict) else type(js_result)}")
    if not isinstance(js_result, dict) or js_result.get("success") is False:
        return None, None, None, None, None

    # The JS returns { breakdown: {...}, imageSizes: {...}, responseTimeMs: number, imgsMissingDims: [...] }
    # crawl4ai wraps it as: { success: true, results: [ <actual_result> ] }
    data = js_result
    if "results" in js_result and isinstance(js_result["results"], list) and js_result["results"]:
        data = js_result["results"][0]
    elif "result" in js_result:
        data = js_result["result"]
    if not isinstance(data, dict):
        return None, None, None, None, None

    breakdown_raw = data.get("breakdown")
    image_sizes_raw = data.get("imageSizes")
    response_time_raw = data.get("responseTimeMs")
    imgs_missing_dims_raw = data.get("imgsMissingDims")
    imgs_broken_src_raw = data.get("imgsBrokenSrc")

    resource_breakdown = None
    if isinstance(breakdown_raw, dict) and breakdown_raw:
        resource_breakdown = {k: int(v) for k, v in breakdown_raw.items() if isinstance(v, (int, float))}
        if resource_breakdown:
            total = sum(resource_breakdown.values())
            logger.info(f"Resource breakdown for {crawl_result.url}: {total} bytes across {len(resource_breakdown)} types")

    image_sizes = None
    if isinstance(image_sizes_raw, dict) and image_sizes_raw:
        image_sizes = {k: int(v) for k, v in image_sizes_raw.items() if isinstance(v, (int, float))}

    response_time_ms = None
    if isinstance(response_time_raw, (int, float)) and response_time_raw > 0:
        response_time_ms = round(float(response_time_raw), 1)

    # BUG-4: List of image srcs that lack explicit width/height in the DOM
    imgs_missing_dims = None
    if isinstance(imgs_missing_dims_raw, list):
        imgs_missing_dims = set(imgs_missing_dims_raw)

    # BUG-17: List of image srcs that failed to load (naturalWidth === 0)
    imgs_broken_src = None
    if isinstance(imgs_broken_src_raw, list):
        imgs_broken_src = set(imgs_broken_src_raw)

    return resource_breakdown, image_sizes, response_time_ms, imgs_missing_dims, imgs_broken_src


async def _on_crawl_progress(crawl_state: dict):
    """Called by BFS strategy after each page is crawled.
    Audits the page immediately and persists to disk for incremental D1 ingestion."""
    pages_crawled = crawl_state.get("pages_crawled", 0)
    urls_count = len(crawl_state.get("visited", set()))

    # Audit the last crawled page incrementally
    last_result = crawl_state.get("last_result")
    if last_result and last_result.success:
        try:
            # Resolve final URL and status code after redirects (BUG-02/03 fix)
            redirected = getattr(last_result, "redirected_url", None)
            was_redirected = redirected and redirected != last_result.url
            final_url = _normalize_url(redirected if was_redirected else last_result.url)
            final_status = 200 if was_redirected else last_result.status_code

            logger.info(f"Auditing page: {final_url} (html={len(last_result.html or '')} bytes, redirected={was_redirected})")

            # Extract resource breakdown + response time from JS execution result (Performance API)
            resource_breakdown, image_sizes, resp_time, imgs_missing_dims, imgs_broken_src = _extract_resource_timing(last_result)

            # Override url and status_code on the result so analyze_page uses the final values
            orig_url, orig_status = last_result.url, last_result.status_code
            last_result.url = final_url
            last_result.status_code = final_status
            page_audit = _analyzer.analyze_page(last_result, response_time_ms=resp_time, resource_breakdown=resource_breakdown)
            last_result.url, last_result.status_code = orig_url, orig_status  # restore

            # Populate file_size_bytes on images from resource timing data
            if image_sizes and page_audit.images.images:
                for img in page_audit.images.images:
                    if img.src in image_sizes:
                        img.file_size_bytes = image_sizes[img.src]

            # BUG-4: Override missing_dimensions using live DOM check (JS hasAttribute)
            # Playwright's rendered HTML may inject intrinsic width/height attributes,
            # so we use the JS-collected list of images truly missing explicit attrs.
            if imgs_missing_dims is not None and page_audit.images.images:
                fixed_missing = 0
                for img in page_audit.images.images:
                    src = img.src
                    if src in imgs_missing_dims:
                        if not img.missing_dimensions:
                            img.missing_dimensions = True
                            fixed_missing += 1
                    else:
                        if img.missing_dimensions:
                            img.missing_dimensions = False
                if fixed_missing > 0 or page_audit.images.missing_dimensions > 0:
                    page_audit.images.missing_dimensions = sum(1 for i in page_audit.images.images if i.missing_dimensions)
                    logger.info(f"BUG-4 fix: {page_audit.images.missing_dimensions} images truly missing dimensions on {final_url}")

            # BUG-17: Override broken_src count using JS-detected broken images
            if imgs_broken_src and page_audit.images.images:
                js_broken = len(imgs_broken_src)
                if js_broken > page_audit.images.broken_src:
                    page_audit.images.broken_src = js_broken
                    logger.info(f"BUG-17 fix: {js_broken} broken image src(s) detected via JS on {final_url}")

            logger.info(f"Page audit complete: {final_url}")
            # Store PageAuditResult for site-level checks (works even if crawl times out)
            _page_audit_results[final_url] = page_audit
            payload = _audit_page_to_payload(page_audit)
            logger.info(f"Payload built: {final_url}")
            snapshot = {"url": final_url, "html": last_result.html[:500_000]} if last_result.html else None

            # Extract link details for the link graph
            link_graph_entries = []
            for ld in page_audit.links.link_details:
                link_graph_entries.append({
                    "source_url": final_url,
                    "target_url": ld.url,
                    "anchor_text": ld.anchor_text[:200] if ld.anchor_text else "",
                    "is_nofollow": ld.is_nofollow,
                    "link_type": ld.link_type,
                    "rel": getattr(ld, "rel", None) or "",
                })

            # Track redirect chains from crawl result metadata
            redirect_entry = None
            if was_redirected and _normalize_url(orig_url) != final_url:
                # Use full redirect chain from Playwright if available
                chain = getattr(last_result, "redirect_chain", None) or [orig_url, final_url]
                redirect_entry = {
                    "source_url": orig_url,
                    "final_url": final_url,
                    "chain_length": len(chain) - 1,
                    "chain_path": chain,
                }

            # BUG-3: Track meta-refresh redirects as redirect chain entries
            meta_refresh_entry = None
            if page_audit.meta_refresh_url:
                meta_target = page_audit.meta_refresh_url
                # Resolve relative URLs
                if not meta_target.startswith("http"):
                    from urllib.parse import urljoin
                    meta_target = urljoin(final_url, meta_target)
                meta_refresh_entry = {
                    "source_url": final_url,
                    "final_url": meta_target,
                    "chain_length": 1,
                    "chain_path": [final_url, meta_target],
                    "is_meta_refresh": True,
                }

            # Dedup: skip if we already audited this final URL
            with state_lock:
                existing_urls = {p["url"] for p in state["partial_pages"]}
                if final_url in existing_urls:
                    logger.info(f"Skipping duplicate page: {final_url} (already audited)")
                else:
                    state["partial_pages"].append(payload)
                    if snapshot:
                        state["partial_snapshots"].append(snapshot)
                    state["partial_link_graph"].extend(link_graph_entries)
                if redirect_entry:
                    state["redirect_chains"].append(redirect_entry)
                if meta_refresh_entry:
                    state["redirect_chains"].append(meta_refresh_entry)
                state["pages_done"] = len(state["partial_pages"])
                state["pages_found"] = max(urls_count, pages_crawled)
                _persist_state()

            logger.info(f"Audited+persisted page {len(state['partial_pages'])}/{urls_count}: {final_url} (links={len(link_graph_entries)})")
            return
        except Exception as e:
            logger.error(f"Failed to audit page {last_result.url}: {e}", exc_info=True)

    with state_lock:
        state["pages_done"] = len(state["partial_pages"])
        state["pages_found"] = max(urls_count, pages_crawled)
    logger.info(f"Progress: {pages_crawled} pages crawled, {urls_count} URLs discovered")


BOT_BLOCKED_DOMAINS = frozenset({
    "twitter.com", "x.com", "linkedin.com", "www.linkedin.com",
    "facebook.com", "www.facebook.com", "instagram.com", "www.instagram.com",
    "tiktok.com", "www.tiktok.com", "pinterest.com", "www.pinterest.com",
})


async def _check_external_links(page_payloads):
    """HEAD-check unique external URLs found during crawl. Returns dict of broken URL -> status code.
    BUG-02/07: Uses concurrent checks with semaphore and better error classification."""
    # Collect unique external URLs from all page audits
    external_urls = set()
    for p in page_payloads:
        try:
            audit = json.loads(p.get("audit_json", "{}"))
            for ld in audit.get("links", {}).get("link_details", []):
                if ld.get("link_type") == "external":
                    external_urls.add(ld["url"])
        except (json.JSONDecodeError, KeyError):
            pass

    if not external_urls:
        return {}

    # Filter out known bot-blocking domains
    filtered_urls = {
        u for u in external_urls
        if urlparse(u).netloc.lower() not in BOT_BLOCKED_DOMAINS
    }
    logger.info(f"Excluded {len(external_urls) - len(filtered_urls)} URLs from bot-blocking domains")

    # Limit to MAX_EXTERNAL_CHECKS
    urls_to_check = list(filtered_urls)[:MAX_EXTERNAL_CHECKS]
    logger.info(f"Checking {len(urls_to_check)} external URLs (of {len(external_urls)} total)...")

    broken: dict = {}
    timeout = aiohttp.ClientTimeout(total=EXTERNAL_CHECK_TIMEOUT_S)
    sem = asyncio.Semaphore(10)  # BUG-07: concurrent checks to avoid overall timeout

    async def _check_one(session, url):
        """Check a single external URL with HEAD then GET fallback."""
        async with sem:
            try:
                async with session.head(url, allow_redirects=True) as resp:
                    if resp.status >= 400:
                        return url, resp.status
                    return url, None
            except asyncio.TimeoutError:
                return url, -1  # timeout
            except aiohttp.ClientConnectorCertificateError:
                return url, -3  # SSL error
            except aiohttp.ClientConnectorDNSError:
                # BUG-02: Distinguish DNS errors from other connection errors
                return url, -4  # DNS resolution failure
            except (aiohttp.ClientConnectorError, aiohttp.ClientError):
                # HEAD may fail due to proxy/WAF blocking; retry with GET
                try:
                    async with session.get(url, allow_redirects=True) as resp2:
                        if resp2.status >= 400:
                            return url, resp2.status
                        return url, None
                except asyncio.TimeoutError:
                    return url, -1
                except aiohttp.ClientConnectorCertificateError:
                    return url, -3
                except aiohttp.ClientConnectorDNSError:
                    return url, -4
                except Exception:
                    return url, -2  # connection error
            except Exception:
                return url, -2  # other network error

    async with aiohttp.ClientSession(timeout=timeout) as session:
        tasks = [_check_one(session, url) for url in urls_to_check]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                continue
            url, status = result
            if status is not None:
                broken[url] = status

    logger.info(f"External link check done: {len(broken)} broken out of {len(urls_to_check)}")
    return broken


async def _check_uncrawled_internal_links(link_graph_data, crawled_urls_norm, domain):
    """HEAD-check internal link targets that weren't visited by BFS.
    Returns dict of broken URL -> status code."""
    # Collect unique internal link targets not in crawled set
    uncrawled_targets = set()
    for entry in link_graph_data:
        if entry.get("link_type") != "internal":
            continue
        target = entry["target_url"]
        norm = _normalize_url(target)
        if norm not in crawled_urls_norm:
            parsed = urlparse(target)
            if parsed.netloc.lower() == domain.lower():
                uncrawled_targets.add(target)

    if not uncrawled_targets:
        return {}

    # Limit to 50 checks to avoid excessive requests
    targets_to_check = list(uncrawled_targets)[:50]
    logger.info(f"HEAD-checking {len(targets_to_check)} uncrawled internal link targets (of {len(uncrawled_targets)} total)")

    broken: dict = {}
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for url in targets_to_check:
            try:
                async with session.head(url, allow_redirects=True) as resp:
                    if resp.status >= 400:
                        broken[url] = resp.status
            except asyncio.TimeoutError:
                pass  # Don't count timeouts as broken for internal links
            except (aiohttp.ClientConnectorError, aiohttp.ClientError):
                broken[url] = -2
            except Exception:
                pass

    logger.info(f"Uncrawled internal link check: {len(broken)} broken out of {len(targets_to_check)}")
    return broken


async def _post_crawl_analysis(domain, scheme, crawl_results, crawl_start):
    """Phase 2+3: domain checks, external link checks, site-level analysis, build payload.
    Runs under a timeout so it can't hang the container."""
    with state_lock:
        n_pages = len(state["partial_pages"])
        link_graph_data = list(state.get("partial_link_graph", []))
        redirect_chains_data = list(state.get("redirect_chains", []))

    # ── Domain checks (robots.txt, sitemap.xml) ─────────────────────
    crawled_urls = {cr.url for cr in crawl_results if cr.success} if crawl_results else set()
    broken_internal_urls = {
        cr.url for cr in crawl_results
        if not cr.success or (cr.status_code and cr.status_code >= 400)
    } if crawl_results else set()

    # BUG-01: Also include soft-404 pages (HTTP 200 but "not found" content)
    for url, par in _page_audit_results.items():
        if par.is_soft_404:
            broken_internal_urls.add(url)
            logger.debug(f"BUG-01: Soft-404 page added to broken internals: {url}")

    domain_check_result = None
    try:
        logger.info("Starting domain checks (robots.txt, sitemap.xml)...")
        domain_check_result = await asyncio.wait_for(
            run_domain_checks(domain, crawled_urls=crawled_urls, scheme=scheme),
            timeout=20,
        )
        logger.info("Domain checks completed")
    except asyncio.TimeoutError:
        logger.warning("Domain checks timed out after 20s (non-fatal)")
    except Exception as e:
        logger.warning(f"Domain checks failed (non-fatal): {e}")

    # ── External link checking (HEAD requests) ───────────────────────
    broken_external_urls = {}
    try:
        logger.info("Starting external link checks...")
        broken_external_urls = await asyncio.wait_for(
            _check_external_links(state["partial_pages"]),
            timeout=30,
        )
        logger.info(f"External link checks done: {len(broken_external_urls)} broken")
    except asyncio.TimeoutError:
        logger.warning("External link checks timed out after 30s (non-fatal)")
    except Exception as e:
        logger.warning(f"External link checks failed (non-fatal): {e}")

    # ── BUG-2/3: Follow meta-refresh chains to detect loops and multi-hop chains ──
    # Collect meta-refresh targets from audited pages
    meta_refresh_graph = {}
    for url, par in _page_audit_results.items():
        if par.meta_refresh_url:
            target = par.meta_refresh_url
            if not target.startswith("http"):
                from urllib.parse import urljoin
                target = urljoin(url, target)
            meta_refresh_graph[url] = target

    # HEAD-fetch uncrawled meta-refresh targets to detect their own meta-refresh
    if meta_refresh_graph:
        crawled_norm = {_normalize_url(u) for u in _page_audit_results.keys()}
        uncrawled_meta_targets = set()
        for target in meta_refresh_graph.values():
            if _normalize_url(target) not in crawled_norm:
                uncrawled_meta_targets.add(target)

        if uncrawled_meta_targets:
            logger.info(f"BUG-2: Fetching {len(uncrawled_meta_targets)} uncrawled meta-refresh targets for loop detection")
            try:
                timeout_meta = aiohttp.ClientTimeout(total=5)
                async with aiohttp.ClientSession(timeout=timeout_meta) as sess:
                    for target_url in list(uncrawled_meta_targets)[:20]:
                        try:
                            async with sess.get(target_url, allow_redirects=True) as resp:
                                if resp.status == 200:
                                    body = await resp.text()
                                    # Check for meta refresh in the response
                                    import re as _re
                                    meta_match = _re.search(
                                        r'<meta[^>]+http-equiv=["\']?refresh["\']?[^>]+content=["\']?\d+;\s*url=([^"\'>\s]+)',
                                        body, _re.IGNORECASE
                                    )
                                    if meta_match:
                                        next_url = meta_match.group(1)
                                        if not next_url.startswith("http"):
                                            next_url = urljoin(target_url, next_url)
                                        meta_refresh_graph[target_url] = next_url
                                        logger.info(f"BUG-2: Found meta-refresh on uncrawled {target_url} -> {next_url}")
                        except Exception as e:
                            logger.debug(f"BUG-2: Failed to fetch {target_url}: {e}")
            except Exception as e:
                logger.warning(f"BUG-2: Meta-refresh chain following failed: {e}")

        # BUG-2/3: Build chains and detect loops from the meta-refresh graph
        # Also build normalized lookup for flexible matching
        norm_to_url = {}
        for u in meta_refresh_graph:
            norm_to_url[_normalize_url(u)] = u

        def _resolve_next(cur):
            nxt = meta_refresh_graph.get(cur)
            if not nxt:
                norm_cur = _normalize_url(cur)
                real = norm_to_url.get(norm_cur)
                if real:
                    nxt = meta_refresh_graph.get(real)
            return nxt

        recorded_chains = set()
        for start_url in meta_refresh_graph:
            visited = set()
            current = start_url
            chain_path = [current]
            is_loop = False
            for _ in range(10):
                if current in visited:
                    is_loop = True
                    break
                visited.add(current)
                next_url = _resolve_next(current)
                if not next_url:
                    break
                chain_path.append(next_url)
                current = next_url

            # Record chains with 2+ hops (or loops)
            if len(chain_path) >= 3 or is_loop:
                chain_key = tuple(chain_path)
                if chain_key not in recorded_chains:
                    recorded_chains.add(chain_key)
                    entry = {
                        "source_url": start_url,
                        "final_url": chain_path[-1],
                        "chain_length": len(chain_path) - 1,
                        "chain_path": chain_path,
                        "is_meta_refresh": True,
                        "is_loop": is_loop,
                    }
                    with state_lock:
                        state["redirect_chains"].append(entry)
                    redirect_chains_data.append(entry)
                    label = "loop" if is_loop else "chain"
                    logger.info(f"BUG-2/3: Detected meta-refresh {label}: {' -> '.join(chain_path)}")

    # ── HEAD-check uncrawled internal link targets ─────────────────────
    # BFS may not visit all link targets (max_pages limit), so HEAD-check them
    crawled_urls_norm = {_normalize_url(cr.url) for cr in crawl_results} if crawl_results else set()
    uncrawled_broken = {}
    try:
        logger.info("Starting uncrawled internal link checks...")
        uncrawled_broken = await asyncio.wait_for(
            _check_uncrawled_internal_links(link_graph_data, crawled_urls_norm, domain),
            timeout=15,
        )
        # Merge into broken_internal_urls
        for url, sc in uncrawled_broken.items():
            broken_internal_urls.add(url)
        logger.info(f"Uncrawled internal check done: {len(uncrawled_broken)} broken")
    except asyncio.TimeoutError:
        logger.warning("Uncrawled internal link checks timed out after 15s (non-fatal)")
    except Exception as e:
        logger.warning(f"Uncrawled internal link checks failed (non-fatal): {e}")

    # ── Build broken_links payload (internal + external) ─────────────
    broken_links_payload = []
    # Build normalized lookup sets for robust URL matching
    status_code_map = {}
    normalized_broken = set()
    for cr in (crawl_results or []):
        norm = _normalize_url(cr.url)
        if cr.status_code:
            status_code_map[norm] = cr.status_code
            status_code_map[cr.url] = cr.status_code
        if not cr.success or (cr.status_code and cr.status_code >= 400):
            normalized_broken.add(norm)
    # Merge broken_internal_urls (includes both crawled 404s and HEAD-checked 404s)
    for u in (broken_internal_urls or set()):
        normalized_broken.add(_normalize_url(u))
    # Merge uncrawled broken with status codes
    for url, sc in uncrawled_broken.items():
        norm = _normalize_url(url)
        status_code_map[norm] = sc
        status_code_map[url] = sc

    def _status_desc(sc):
        """Convert status code to descriptive string (BUG-02/07)."""
        if sc is None:
            return "unknown"
        if sc == -1:
            return "TIMEOUT"
        if sc == -2:
            return "CONNECTION_ERROR"
        if sc == -3:
            return "SSL_ERROR"
        if sc == -4:
            return "DNS_ERROR"
        if sc == 0:
            return "TIMEOUT"
        if sc >= 400:
            return f"HTTP_{sc}"
        return str(sc)

    # Internal broken links: cross-reference link_graph with broken URLs
    for entry in link_graph_data:
        if entry["link_type"] != "internal":
            continue
        target = entry["target_url"]
        norm_target = _normalize_url(target)
        if norm_target in normalized_broken or target in (broken_internal_urls or set()):
            sc = status_code_map.get(norm_target) or status_code_map.get(target)
            broken_links_payload.append({
                "source_url": entry["source_url"],
                "target_url": target,
                "status_code": sc,
                "status_code_desc": _status_desc(sc),
                "anchor_text": entry.get("anchor_text", ""),
                "link_type": "internal",
            })

    # External broken links
    for ext_url, ext_status in broken_external_urls.items():
        # Find source pages that link to this broken external URL
        for entry in link_graph_data:
            if entry["link_type"] == "external" and entry["target_url"] == ext_url:
                broken_links_payload.append({
                    "source_url": entry["source_url"],
                    "target_url": ext_url,
                    "status_code": ext_status,
                    "status_code_desc": _status_desc(ext_status),
                    "anchor_text": entry.get("anchor_text", ""),
                    "link_type": "external",
                })

    logger.info(f"Broken links: {len(broken_links_payload)} total ({len([b for b in broken_links_payload if b['link_type'] == 'internal'])} internal, {len([b for b in broken_links_payload if b['link_type'] == 'external'])} external)")

    crawl_duration = time.time() - crawl_start
    crawl_metadata = {"crawl_duration_s": round(crawl_duration, 1)}

    # ── Site-level analysis (uses crawl_results, NOT re-auditing pages) ──
    # Collect response times from already-audited partial pages
    response_times = {}
    with state_lock:
        for p in state.get("partial_pages", []):
            if p.get("response_time_ms") is not None:
                response_times[p["url"]] = p["response_time_ms"]

    # Build Dict[str, List[str]] link graph from partial_link_graph for orphan/deep page checks
    built_link_graph: dict = {}
    for entry in link_graph_data:
        if entry.get("link_type") == "internal":
            src = entry["source_url"]
            tgt = entry["target_url"]
            built_link_graph.setdefault(src, []).append(tgt)
    logger.info(f"Built link graph: {len(built_link_graph)} source pages, {sum(len(v) for v in built_link_graph.values())} edges")

    try:
        # Use PageAuditResult objects stored during incremental auditing.
        # This is critical: when the crawl times out, crawl_results is empty
        # but _page_audit_results has all the audited pages.
        logger.info(f"Starting site-level analysis on {len(_page_audit_results)} audited pages (crawl_results had {len(crawl_results or [])} items)...")
        site_result = run_site_checks(
            _page_audit_results,
            internal_link_graph=built_link_graph,
            domain_checks=domain_check_result,
            broken_internal_urls=broken_internal_urls,
            crawl_metadata=crawl_metadata,
            broken_external_urls=broken_external_urls,
            redirect_chains=redirect_chains_data,
        )
        logger.info(f"Site analysis done. Score: {site_result.summary.score}/100")
    except Exception as e:
        logger.error(f"Site-level analysis failed: {e}", exc_info=True)
        return {
            "pages": state["partial_pages"],
            "issues": [],
            "summary": {
                "pages_audited": n_pages,
                "score": None,
                "issues_critical": 0, "issues_warning": 0, "issues_info": 0,
                "score_breakdown": None,
                "audit_json": json.dumps({"error": f"Site analysis failed: {e}"}),
            },
            "snapshots": state["partial_snapshots"],
            "domain_checks": None,
            "link_graph": link_graph_data,
            "broken_links": broken_links_payload,
            "redirect_chains": redirect_chains_data,
        }

    # ── Build final payload ──────────────────────────────────────────
    logger.info("Building final payload...")
    issues_payload = []
    for issue in site_result.critical + site_result.warnings + site_result.info:
        issues_payload.append({
            "issue_type": issue.issue_type,
            "severity": issue.severity.value,
            "description": issue.description,
            "fix": issue.fix,
            "affected_count": len(issue.affected_pages),
            "affected_urls": issue.affected_pages[:50],
        })

    summary_dict = site_result.summary.model_dump()
    summary_payload = {
        "pages_audited": summary_dict["pages_audited"],
        "score": summary_dict["score"],
        "issues_critical": summary_dict["issues_critical"],
        "issues_warning": summary_dict["issues_warning"],
        "issues_info": summary_dict["issues_info"],
        "score_breakdown": summary_dict.get("score_breakdown"),
        "audit_json": json.dumps(summary_dict),
    }

    domain_payload = domain_check_result.model_dump() if domain_check_result else None
    logger.info(f"Payload built: {len(state['partial_pages'])} pages, {len(issues_payload)} issues, {len(link_graph_data)} links, {len(broken_links_payload)} broken, {len(redirect_chains_data)} redirects")

    return {
        "pages": state["partial_pages"],
        "issues": issues_payload,
        "summary": summary_payload,
        "snapshots": state["partial_snapshots"],
        "domain_checks": domain_payload,
        "link_graph": link_graph_data,
        "broken_links": broken_links_payload,
        "redirect_chains": redirect_chains_data,
    }


async def _crawl(job_id: str, url: str, max_pages: int, max_depth: int):
    logger.info(f"Starting crawl: job={job_id} url={url} max_pages={max_pages}")
    crawl_start = time.time()

    parsed_url = urlparse(url)
    domain = parsed_url.netloc
    scheme = parsed_url.scheme or "https"

    crawl_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=PAGE_TIMEOUT_MS,
        js_code=RESOURCE_TIMING_JS,
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=max_depth,
            max_pages=max_pages,
            on_state_change=_on_crawl_progress,
        ),
    )

    # ── Phase 1: Crawl (per-page audit happens in _on_crawl_progress) ──
    crawl_results = []
    try:
        logger.info("Phase 1: Starting BFS crawl...")
        async with AsyncWebCrawler() as crawler:
            results = await asyncio.wait_for(
                crawler.arun(url=url, config=crawl_config),
                timeout=CRAWL_TIMEOUT_S,
            )
            crawl_results = results if isinstance(results, list) else [results]
        logger.info(f"Phase 1 complete: crawler returned {len(crawl_results)} results")
    except asyncio.TimeoutError:
        logger.warning(f"Phase 1: Crawl timed out after {CRAWL_TIMEOUT_S}s — using partial results")
    except Exception as e:
        logger.error(f"Phase 1: Crawler failed: {e}", exc_info=True)

    # If we have partial pages from the callback, proceed to site analysis
    # even if the crawl itself timed out or errored
    with state_lock:
        n_pages = len(state["partial_pages"])

    if n_pages == 0:
        msg = f"Crawl produced 0 audited pages for {url}"
        logger.error(msg)
        with state_lock:
            state["status"] = "failed"
            state["error"] = msg
            _persist_state()
        return

    logger.info(f"Phase 1 done: {n_pages} pages audited. Starting Phase 2 (site analysis)...")

    # ── Phase 2+3: Site-level analysis with hard timeout ─────────────
    try:
        results_payload = await asyncio.wait_for(
            _post_crawl_analysis(domain, scheme, crawl_results, crawl_start),
            timeout=POST_CRAWL_TIMEOUT_S,
        )
    except asyncio.TimeoutError:
        logger.error(f"Phase 2 TIMED OUT after {POST_CRAWL_TIMEOUT_S}s — completing with partial results")
        results_payload = {
            "pages": state["partial_pages"],
            "issues": [],
            "summary": {
                "pages_audited": n_pages,
                "score": None,
                "issues_critical": 0, "issues_warning": 0, "issues_info": 0,
                "score_breakdown": None,
                "audit_json": json.dumps({"error": "Post-crawl analysis timed out"}),
            },
            "snapshots": state["partial_snapshots"],
            "domain_checks": None,
            "link_graph": state.get("partial_link_graph", []),
            "broken_links": [],
            "redirect_chains": state.get("redirect_chains", []),
        }
    except Exception as e:
        logger.error(f"Phase 2 FAILED: {e}", exc_info=True)
        results_payload = {
            "pages": state["partial_pages"],
            "issues": [],
            "summary": {
                "pages_audited": n_pages,
                "score": None,
                "issues_critical": 0, "issues_warning": 0, "issues_info": 0,
                "score_breakdown": None,
                "audit_json": json.dumps({"error": f"Post-crawl analysis failed: {e}"}),
            },
            "snapshots": state["partial_snapshots"],
            "domain_checks": None,
            "link_graph": state.get("partial_link_graph", []),
            "broken_links": [],
            "redirect_chains": state.get("redirect_chains", []),
        }

    with state_lock:
        state["status"] = "completed"
        state["pages_done"] = len(state["partial_pages"])
        state["results"] = results_payload
        _persist_state()

    logger.info(f"Job complete: {state['pages_done']} pages, status=completed")


# ─── Lighthouse Runner ────────────────────────────────────────────────

LIGHTHOUSE_TIMEOUT_S = 45  # per-URL timeout
MAX_LIGHTHOUSE_URLS = 20
LIGHTHOUSE_CONCURRENCY = 3


def _resolve_chrome_path() -> str | None:
    """Find Playwright's Chromium binary and set CHROME_PATH for Lighthouse."""
    import glob as _glob
    # Check if already set and valid
    existing = os.environ.get("CHROME_PATH")
    if existing and os.path.isfile(existing):
        return existing
    # Try symlink we created in Dockerfile
    if os.path.isfile("/usr/local/bin/chromium-browser"):
        os.environ["CHROME_PATH"] = "/usr/local/bin/chromium-browser"
        return "/usr/local/bin/chromium-browser"
    # Glob for Playwright's Chromium (chrome-linux for older, chrome-linux64 for >=1.40)
    for pattern in [
        "/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
        "/root/.cache/ms-playwright/chromium-*/chrome-linux64/chrome",
    ]:
        candidates = _glob.glob(pattern)
        if candidates:
            os.environ["CHROME_PATH"] = candidates[0]
            return candidates[0]
    return None


# Resolve once at import time
_CHROME_PATH = _resolve_chrome_path()
if _CHROME_PATH:
    logger.info(f"Lighthouse CHROME_PATH resolved: {_CHROME_PATH}")
else:
    logger.warning("Could not resolve CHROME_PATH — Lighthouse audits will likely fail")


async def _run_lighthouse_single(url: str, device: str) -> dict:
    """Run Lighthouse CLI on a single URL, return parsed result dict."""
    preset = "desktop" if device == "desktop" else ""
    cmd = [
        "lighthouse", url,
        "--output=json",
        "--output-path=stdout",
        "--quiet",
        "--chrome-flags=--headless=new --no-sandbox --disable-gpu --disable-dev-shm-usage",
        "--only-categories=performance,accessibility,best-practices,seo",
    ]
    if _CHROME_PATH:
        cmd.append(f"--chrome-path={_CHROME_PATH}")
    if preset:
        cmd.append(f"--preset={preset}")

    env = os.environ.copy()
    if _CHROME_PATH:
        env["CHROME_PATH"] = _CHROME_PATH

    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            ),
            timeout=5,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=LIGHTHOUSE_TIMEOUT_S)

        if proc.returncode != 0:
            stderr_text = stderr.decode()[:500]
            logger.warning(f"Lighthouse failed for {url} (exit {proc.returncode}): {stderr_text}")
            return {"url": url, "error": f"Lighthouse exit code {proc.returncode}: {stderr_text[:200]}"}

        lhr = json.loads(stdout.decode())
        cats = lhr.get("categories", {})
        audits = lhr.get("audits", {})

        return {
            "url": url,
            "device": device,
            "performance_score": _lh_score(cats.get("performance")),
            "accessibility_score": _lh_score(cats.get("accessibility")),
            "best_practices_score": _lh_score(cats.get("best-practices")),
            "seo_score": _lh_score(cats.get("seo")),
            "lcp_ms": _lh_metric(audits, "largest-contentful-paint"),
            "cls": _lh_metric(audits, "cumulative-layout-shift"),
            "tbt_ms": _lh_metric(audits, "total-blocking-time"),
            "fcp_ms": _lh_metric(audits, "first-contentful-paint"),
            "speed_index_ms": _lh_metric(audits, "speed-index"),
            "tti_ms": _lh_metric(audits, "interactive"),
        }
    except asyncio.TimeoutError:
        logger.warning(f"Lighthouse timed out for {url} after {LIGHTHOUSE_TIMEOUT_S}s")
        return {"url": url, "error": "timeout"}
    except Exception as e:
        logger.error(f"Lighthouse error for {url}: {e}")
        return {"url": url, "error": str(e)}


def _lh_score(category: dict) -> int | None:
    if not category:
        return None
    score = category.get("score")
    return round(score * 100) if score is not None else None


def _lh_metric(audits: dict, key: str) -> float | None:
    audit = audits.get(key)
    if not audit:
        return None
    return audit.get("numericValue")


async def _run_lighthouse_bulk(urls: list, device: str, concurrency: int) -> list:
    """Run Lighthouse on multiple URLs with bounded concurrency."""
    semaphore = asyncio.Semaphore(concurrency)
    results = []

    async def _run_one(url):
        async with semaphore:
            result = await _run_lighthouse_single(url, device)
            results.append(result)

    tasks = [asyncio.create_task(_run_one(u)) for u in urls]
    await asyncio.gather(*tasks, return_exceptions=True)
    return results


def _handle_lighthouse(body: dict) -> dict:
    """Synchronous handler for /lighthouse POST — runs in a thread with its own event loop."""
    urls = body.get("urls", [])
    if not urls:
        return {"error": "urls array is required"}
    if len(urls) > MAX_LIGHTHOUSE_URLS:
        return {"error": f"Maximum {MAX_LIGHTHOUSE_URLS} URLs allowed"}

    device = body.get("device", "mobile")
    concurrency = min(body.get("concurrency", LIGHTHOUSE_CONCURRENCY), 5)
    job_id = body.get("job_id")

    logger.info(f"Lighthouse bulk: {len(urls)} URLs, device={device}, concurrency={concurrency}")

    loop = asyncio.new_event_loop()
    try:
        results = loop.run_until_complete(_run_lighthouse_bulk(urls, device, concurrency))
    finally:
        loop.close()

    # Compute aggregates
    scores = [r["performance_score"] for r in results if r.get("performance_score") is not None]
    lcp_values = [r["lcp_ms"] for r in results if r.get("lcp_ms") is not None]

    return {
        "job_id": job_id,
        "urls_tested": len(results),
        "urls_failed": len([r for r in results if "error" in r]),
        "avg_performance": round(sum(scores) / len(scores)) if scores else None,
        "avg_lcp_ms": round(sum(lcp_values) / len(lcp_values)) if lcp_values else None,
        "results": results,
    }


# ─── HTTP Request Handler ─────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok"})

        elif self.path == "/status":
            with state_lock:
                current_state = state
                # If in-memory state is idle, check disk for persisted results
                if current_state["status"] == "idle":
                    persisted = _load_persisted_state()
                    if persisted and persisted.get("status") in ("completed", "failed"):
                        current_state = persisted
                        logger.info("Loaded persisted state from disk")
                    elif persisted and persisted.get("status") == "running":
                        # Container crashed mid-crawl — mark as failed but preserve partial pages
                        persisted["status"] = "failed"
                        persisted["error"] = "Container restarted mid-crawl (partial results preserved)"
                        current_state = persisted
                        logger.info(f"Loaded crashed-crawl state from disk: {len(persisted.get('partial_pages', []))} partial pages")

                resp = {
                    "status": current_state["status"],
                    "job_id": current_state.get("job_id"),
                    "pages_found": current_state.get("pages_found", 0),
                    "pages_done": current_state.get("pages_done", 0),
                    "error": current_state.get("error"),
                }
                # Include full results when completed (Worker ingests them)
                if current_state["status"] == "completed":
                    resp["results"] = current_state.get("results")
                # Include partial pages while running or on failure (incremental persistence)
                elif current_state["status"] in ("running", "failed"):
                    partial = current_state.get("partial_pages", [])
                    if partial:
                        resp["partial_pages"] = partial
                        resp["partial_snapshots"] = current_state.get("partial_snapshots", [])
                        resp["partial_link_graph"] = current_state.get("partial_link_graph", [])

                n_partial = len(current_state.get("partial_pages", []))
                logger.info(
                    f"/status polled: status={current_state['status']} "
                    f"pages_done={current_state.get('pages_done', 0)} "
                    f"partial_pages={n_partial} "
                    f"has_results={'results' in resp}"
                )
            self._respond(200, resp)

        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/start":
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length)) if content_length else {}

            with state_lock:
                if state["status"] == "running":
                    self._respond(409, {"error": "crawl already running"})
                    return

                state["status"] = "running"
                state["job_id"] = body.get("job_id")
                state["pages_found"] = 0
                state["pages_done"] = 0
                state["error"] = None
                state["results"] = None
                state["partial_pages"] = []
                state["partial_snapshots"] = []
                state["partial_link_graph"] = []
                state["redirect_chains"] = []
                _page_audit_results.clear()

            # Start crawl in background thread
            t = threading.Thread(
                target=run_crawl_in_thread,
                args=(
                    body.get("job_id", ""),
                    body["url"],
                    body.get("max_pages", 200),
                    body.get("max_depth", 3),
                ),
                daemon=True,
            )
            t.start()

            self._respond(202, {"status": "started", "job_id": body.get("job_id")})

        elif self.path == "/lighthouse":
            content_length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_length)) if content_length else {}
            try:
                result = _handle_lighthouse(body)
                status_code = 400 if "error" in result and "urls_tested" not in result else 200
                self._respond(status_code, result)
            except Exception as e:
                logger.error(f"Lighthouse handler error: {e}", exc_info=True)
                self._respond(500, {"error": str(e)})

        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, code: int, data: dict):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        # Suppress default access logs, use our logger instead
        logger.debug(f"{self.address_string()} {format % args}")


# ─── Main ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = 8000
    server = HTTPServer(("0.0.0.0", port), Handler)
    logger.info(f"SEO audit container listening on port {port}")
    server.serve_forever()
