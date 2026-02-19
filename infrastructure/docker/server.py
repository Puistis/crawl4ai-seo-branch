"""
Container HTTP server for Cloudflare Containers.

The CF Worker proxies requests via container.fetch() to this server.
Runs on port 8000 (matching CrawlerContainer.defaultPort).

Endpoints:
    POST /start   — Begin a crawl job (returns immediately, runs in background)
    GET  /status   — Poll crawl progress and retrieve results when done
    GET  /health   — Liveness check
"""

import os
import sys
import json
import time
import asyncio
import logging
import threading
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
STATE_FILE = "/tmp/crawl_state.json"
PAGES_FILE = "/tmp/crawl_pages.json"

state = {
    "status": "idle",       # idle | running | completed | failed
    "job_id": None,
    "pages_found": 0,
    "pages_done": 0,
    "error": None,
    "results": None,        # set when completed
    "partial_pages": [],    # page payloads persisted incrementally
    "partial_snapshots": [],
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
            }, f)
        # Write state without bulky data
        slim = {k: v for k, v in state.items() if k not in ("partial_pages", "partial_snapshots")}
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
            return loaded
    except Exception as e:
        logger.warning(f"Failed to load persisted state: {e}")
    return None


def _audit_page_to_payload(page_audit):
    """Convert a PageAuditResult to the flat dict the Worker expects."""
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


async def _on_crawl_progress(crawl_state: dict):
    """Called by BFS strategy after each page is crawled.
    Audits the page immediately and persists to disk for incremental D1 ingestion."""
    pages_crawled = crawl_state.get("pages_crawled", 0)
    urls_count = len(crawl_state.get("visited", set()))

    # Audit the last crawled page incrementally
    last_result = crawl_state.get("last_result")
    if last_result and last_result.success:
        try:
            resp_time = None
            if hasattr(last_result, "response_time") and last_result.response_time is not None:
                resp_time = last_result.response_time * 1000
            page_audit = _analyzer.analyze_page(last_result, response_time_ms=resp_time)
            payload = _audit_page_to_payload(page_audit)
            snapshot = {"url": last_result.url, "html": last_result.html[:500_000]} if last_result.html else None

            with state_lock:
                state["partial_pages"].append(payload)
                if snapshot:
                    state["partial_snapshots"].append(snapshot)
                state["pages_done"] = len(state["partial_pages"])
                state["pages_found"] = max(urls_count, pages_crawled)
                _persist_state()

            logger.info(f"Audited+persisted page {len(state['partial_pages'])}/{urls_count}: {last_result.url}")
            return
        except Exception as e:
            logger.warning(f"Failed to audit page {last_result.url}: {e}")

    with state_lock:
        state["pages_done"] = len(state["partial_pages"])
        state["pages_found"] = max(urls_count, pages_crawled)
    logger.info(f"Progress: {pages_crawled} pages crawled, {urls_count} URLs discovered")


async def _crawl(job_id: str, url: str, max_pages: int, max_depth: int):
    logger.info(f"Starting crawl: job={job_id} url={url} max_pages={max_pages}")
    crawl_start = time.time()

    parsed_url = urlparse(url)
    domain = parsed_url.netloc
    scheme = parsed_url.scheme or "https"

    crawl_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        page_timeout=PAGE_TIMEOUT_MS,
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=max_depth,
            max_pages=max_pages,
            on_state_change=_on_crawl_progress,
        ),
    )

    # ── Phase 1: Crawl (per-page audit happens in _on_crawl_progress) ──
    crawl_results = []
    try:
        async with AsyncWebCrawler() as crawler:
            results = await asyncio.wait_for(
                crawler.arun(url=url, config=crawl_config),
                timeout=CRAWL_TIMEOUT_S,
            )
            crawl_results = results if isinstance(results, list) else [results]
    except asyncio.TimeoutError:
        logger.warning(f"Crawl timed out after {CRAWL_TIMEOUT_S}s — using partial results")
    except Exception as e:
        logger.error(f"Crawler failed: {e}", exc_info=True)

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

    logger.info(f"Crawl phase done: {n_pages} pages audited, running site-level analysis")

    # ── Phase 2: Site-level analysis ─────────────────────────────────
    crawled_urls = {cr.url for cr in crawl_results if cr.success} if crawl_results else set()
    broken_internal_urls = {
        cr.url for cr in crawl_results
        if not cr.success or (cr.status_code and cr.status_code >= 400)
    } if crawl_results else set()

    domain_check_result = None
    try:
        domain_check_result = await run_domain_checks(
            domain, crawled_urls=crawled_urls, scheme=scheme
        )
        logger.info("Domain checks completed")
    except Exception as e:
        logger.warning(f"Domain checks failed (non-fatal): {e}")

    crawl_duration = time.time() - crawl_start
    crawl_metadata = {"crawl_duration_s": round(crawl_duration, 1)}

    # Collect response times for site analysis
    response_times = {}
    for cr in (crawl_results or []):
        if hasattr(cr, "response_time") and cr.response_time is not None:
            response_times[cr.url] = cr.response_time * 1000

    try:
        site_result = _analyzer.analyze_site(
            crawl_results or [],
            response_times=response_times,
            domain_checks=domain_check_result,
            broken_internal_urls=broken_internal_urls,
            crawl_metadata=crawl_metadata,
        )
        logger.info(f"Site analysis done. Score: {site_result.summary.score}/100")
    except Exception as e:
        logger.error(f"Site-level analysis failed: {e}", exc_info=True)
        with state_lock:
            state["status"] = "completed"
            state["results"] = {
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
            }
            _persist_state()
        return

    # ── Phase 3: Build final payload ─────────────────────────────────
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

    with state_lock:
        state["status"] = "completed"
        state["pages_done"] = len(state["partial_pages"])
        state["results"] = {
            "pages": state["partial_pages"],
            "issues": issues_payload,
            "summary": summary_payload,
            "snapshots": state["partial_snapshots"],
            "domain_checks": domain_payload,
        }
        _persist_state()

    logger.info(f"Results ready: {len(state['partial_pages'])} pages, {len(issues_payload)} issues")


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
