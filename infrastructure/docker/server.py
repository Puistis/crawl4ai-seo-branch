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
return { breakdown: breakdown, imageSizes: imageSizes, responseTimeMs: responseTimeMs };
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
    """Extract resource breakdown, per-image sizes, and response time from JS execution result.
    Returns (resource_breakdown, image_sizes, response_time_ms) — all may be None."""
    js_result = getattr(crawl_result, "js_execution_result", None)
    logger.info(f"JS execution result keys for {crawl_result.url}: {list(js_result.keys()) if isinstance(js_result, dict) else type(js_result)}")
    if not isinstance(js_result, dict) or js_result.get("success") is False:
        return None, None, None

    # The JS returns { breakdown: {...}, imageSizes: {...}, responseTimeMs: number }
    # crawl4ai wraps it as: { success: true, results: [ <actual_result> ] }
    data = js_result
    if "results" in js_result and isinstance(js_result["results"], list) and js_result["results"]:
        data = js_result["results"][0]
    elif "result" in js_result:
        data = js_result["result"]
    if not isinstance(data, dict):
        return None, None, None

    breakdown_raw = data.get("breakdown")
    image_sizes_raw = data.get("imageSizes")
    response_time_raw = data.get("responseTimeMs")

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

    return resource_breakdown, image_sizes, response_time_ms


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
            resource_breakdown, image_sizes, resp_time = _extract_resource_timing(last_result)

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
            logger.info(f"Page audit complete: {final_url}")
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
                })

            # Track redirect chains from crawl result metadata
            redirect_entry = None
            if was_redirected and _normalize_url(orig_url) != final_url:
                redirect_entry = {
                    "source_url": orig_url,
                    "final_url": final_url,
                    "chain_length": 1,
                    "chain_path": [orig_url, final_url],
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
    """HEAD-check unique external URLs found during crawl. Returns dict of broken URL -> status code."""
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

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for url in urls_to_check:
            try:
                async with session.head(url, allow_redirects=True) as resp:
                    if resp.status >= 400:
                        broken[url] = resp.status
            except asyncio.TimeoutError:
                broken[url] = 0  # timeout
            except Exception:
                broken[url] = 0  # connection error

    logger.info(f"External link check done: {len(broken)} broken out of {len(urls_to_check)}")
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

    # ── Build broken_links payload (internal + external) ─────────────
    broken_links_payload = []
    # Internal broken links: cross-reference link_graph with broken_internal_urls
    for entry in link_graph_data:
        if entry["link_type"] == "internal" and entry["target_url"] in broken_internal_urls:
            broken_links_payload.append({
                "source_url": entry["source_url"],
                "target_url": entry["target_url"],
                "status_code": None,  # we know it's broken but don't have exact code from link graph
                "anchor_text": entry.get("anchor_text", ""),
                "link_type": "internal",
            })
    # Look up actual status codes from crawl results for internal broken links
    status_code_map = {}
    for cr in (crawl_results or []):
        if cr.status_code:
            status_code_map[cr.url] = cr.status_code
    for bl in broken_links_payload:
        if bl["link_type"] == "internal" and bl["target_url"] in status_code_map:
            bl["status_code"] = status_code_map[bl["target_url"]]

    # External broken links
    for ext_url, ext_status in broken_external_urls.items():
        # Find source pages that link to this broken external URL
        for entry in link_graph_data:
            if entry["link_type"] == "external" and entry["target_url"] == ext_url:
                broken_links_payload.append({
                    "source_url": entry["source_url"],
                    "target_url": ext_url,
                    "status_code": ext_status,
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
        logger.info(f"Starting site-level analysis on {len(crawl_results or [])} crawl results...")
        site_result = _analyzer.analyze_site(
            crawl_results or [],
            response_times=response_times,
            domain_checks=domain_check_result,
            broken_internal_urls=broken_internal_urls,
            crawl_metadata=crawl_metadata,
            broken_external_urls=broken_external_urls,
            redirect_chains=redirect_chains_data,
            internal_link_graph=built_link_graph,
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
    # Glob for Playwright's Chromium
    candidates = _glob.glob("/root/.cache/ms-playwright/chromium-*/chrome-linux/chrome")
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

            # Start crawl in background thread
            t = threading.Thread(
                target=run_crawl_in_thread,
                args=(
                    body.get("job_id", ""),
                    body["url"],
                    body.get("max_pages", 50),
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
