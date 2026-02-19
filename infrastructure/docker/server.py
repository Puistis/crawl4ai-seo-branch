"""
Container HTTP server for Cloudflare Containers.

The CF Worker proxies requests via container.fetch() to this server.
Runs on port 8000 (matching CrawlerContainer.defaultPort).

Endpoints:
    POST /start   — Begin a crawl job (returns immediately, runs in background)
    GET  /status   — Poll crawl progress and retrieve results when done
    GET  /health   — Liveness check
"""

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

state = {
    "status": "idle",       # idle | running | completed | failed
    "job_id": None,
    "pages_found": 0,
    "pages_done": 0,
    "error": None,
    "results": None,        # set when completed
}
state_lock = threading.Lock()


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
    finally:
        loop.close()
        # Safety net: if state is still 'running' after thread exits, mark failed
        with state_lock:
            if state["status"] == "running":
                logger.error("Crawl thread exited while state still 'running' — marking failed")
                state["status"] = "failed"
                state["error"] = state["error"] or "Crawl thread exited unexpectedly"


async def _on_crawl_progress(crawl_state: dict):
    """Called by BFS strategy after each page is crawled."""
    pages_crawled = crawl_state.get("pages_crawled", 0)
    urls_count = len(crawl_state.get("visited", set()))
    with state_lock:
        state["pages_done"] = pages_crawled
        state["pages_found"] = max(urls_count, pages_crawled)
    logger.info(f"Progress: {pages_crawled} pages crawled, {urls_count} URLs discovered")


async def _crawl(job_id: str, url: str, max_pages: int, max_depth: int):
    logger.info(f"Starting crawl: job={job_id} url={url} max_pages={max_pages}")
    crawl_start = time.time()

    analyzer = SEOAnalyzer()
    parsed_url = urlparse(url)
    domain = parsed_url.netloc
    scheme = parsed_url.scheme or "https"

    crawl_config = CrawlerRunConfig(
        cache_mode=CacheMode.BYPASS,
        deep_crawl_strategy=BFSDeepCrawlStrategy(
            max_depth=max_depth,
            max_pages=max_pages,
            on_state_change=_on_crawl_progress,
        ),
    )

    # Wrap crawler in a timeout so it can't hang forever
    try:
        async with AsyncWebCrawler() as crawler:
            results = await asyncio.wait_for(
                crawler.arun(url=url, config=crawl_config),
                timeout=CRAWL_TIMEOUT_S,
            )
            crawl_results = results if isinstance(results, list) else [results]
    except asyncio.TimeoutError:
        msg = f"Crawl timed out after {CRAWL_TIMEOUT_S}s"
        logger.error(msg)
        with state_lock:
            state["status"] = "failed"
            state["error"] = msg
        return
    except Exception as e:
        msg = f"Crawler failed: {e}"
        logger.error(msg, exc_info=True)
        with state_lock:
            state["status"] = "failed"
            state["error"] = msg
        return

    # Validate we actually got results
    if not crawl_results or all(not cr.success for cr in crawl_results):
        msg = f"Crawl produced 0 successful pages for {url}"
        logger.error(msg)
        with state_lock:
            state["status"] = "failed"
            state["error"] = msg
        return

    with state_lock:
        state["pages_found"] = len(crawl_results)

    logger.info(f"Crawled {len(crawl_results)} pages, running SEO audit")

    # Collect response times from CrawlResult metadata (if available)
    response_times = {}
    for cr in crawl_results:
        if hasattr(cr, "response_time") and cr.response_time is not None:
            response_times[cr.url] = cr.response_time * 1000  # s -> ms

    # Detect broken internal links (failed crawl results = 404s or non-success)
    crawled_urls = {cr.url for cr in crawl_results if cr.success}
    broken_internal_urls = {
        cr.url for cr in crawl_results
        if not cr.success or (cr.status_code and cr.status_code >= 400)
    }

    # Run domain-level checks (robots.txt, sitemap.xml)
    try:
        domain_check_result = await run_domain_checks(
            domain, crawled_urls=crawled_urls, scheme=scheme
        )
        logger.info("Domain checks completed")
    except Exception as e:
        logger.warning(f"Domain checks failed (non-fatal): {e}")
        domain_check_result = None

    crawl_duration = time.time() - crawl_start
    crawl_metadata = {
        "crawl_duration_s": round(crawl_duration, 1),
    }

    # Run SEO audit
    site_result = analyzer.analyze_site(
        crawl_results,
        response_times=response_times,
        domain_checks=domain_check_result,
        broken_internal_urls=broken_internal_urls,
        crawl_metadata=crawl_metadata,
    )
    logger.info(f"Audit done. Score: {site_result.summary.score}/100")

    # Build result payload (same shape the Worker expects)
    pages_payload = []
    snapshots_payload = []

    for url_key, page_audit in site_result.page_details.items():
        pages_payload.append({
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
            "response_time_ms": page_audit.performance.response_time_ms,
            "page_weight_bytes": page_audit.performance.page_weight_bytes,
            "audit_json": page_audit.model_dump_json(),
        })

    for cr in crawl_results:
        if cr.success and cr.html:
            snapshots_payload.append({
                "url": cr.url,
                "html": cr.html[:500_000],
            })

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

    # Include domain checks in results if available
    domain_payload = None
    if domain_check_result:
        domain_payload = domain_check_result.model_dump()

    with state_lock:
        state["status"] = "completed"
        state["pages_done"] = len(pages_payload)
        state["results"] = {
            "pages": pages_payload,
            "issues": issues_payload,
            "summary": summary_payload,
            "snapshots": snapshots_payload,
            "domain_checks": domain_payload,
        }

    logger.info(f"Results ready: {len(pages_payload)} pages, {len(issues_payload)} issues")


# ─── HTTP Request Handler ─────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok"})

        elif self.path == "/status":
            with state_lock:
                resp = {
                    "status": state["status"],
                    "job_id": state["job_id"],
                    "pages_found": state["pages_found"],
                    "pages_done": state["pages_done"],
                    "error": state["error"],
                }
                # Include full results when completed (Worker ingests them)
                if state["status"] == "completed":
                    resp["results"] = state["results"]
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
