# SEO Audit Platform — Architecture Documentation

**Last updated:** 2026-02-19

## Overview

An SEO audit platform that crawls websites and produces per-page + site-level SEO audit reports. Built on a forked version of crawl4ai with a custom `seo_audit` module. Deployed on Cloudflare (Worker + Containers + D1 + R2).

---

## Repository Structure

```
crawl4ai-seo-branch/
├── crawl4ai/                          # Forked crawl4ai library (modified)
│   ├── seo_audit/                     # Custom SEO audit layer
│   │   ├── __init__.py                # Public API exports
│   │   ├── analyzer.py                # SEOAnalyzer class (analyze_page, analyze_site)
│   │   ├── checks.py                  # Per-page checks (title, meta, headings, images, etc.)
│   │   ├── site_checks.py            # Site-level checks (run_site_checks)
│   │   ├── domain_checks.py          # Domain-level checks (robots.txt, sitemap, SSL)
│   │   └── models.py                  # Pydantic models (PageAuditResult, SiteAuditResult, etc.)
│   ├── deep_crawling/
│   │   ├── bfs_strategy.py           # BFSDeepCrawlStrategy — modified for per-page timeout
│   │   └── ...
│   ├── async_webcrawler.py            # AsyncWebCrawler, arun(), arun_many()
│   ├── async_configs.py               # CrawlerRunConfig (page_timeout, deep_crawl_strategy, etc.)
│   └── models.py                      # CrawlResult model
├── infrastructure/
│   ├── docker/
│   │   ├── Dockerfile                 # Container image (Python 3.11 + Playwright + crawl4ai)
│   │   ├── server.py                  # HTTP server inside container (port 8000)
│   │   └── requirements.txt           # Python deps for container
│   ├── worker/
│   │   ├── src/index.ts               # Cloudflare Worker (TypeScript) — API gateway
│   │   ├── wrangler.toml              # Wrangler config (bindings, D1, R2, containers)
│   │   └── package.json               # Node deps
│   └── d1-schema.sql                  # D1 database schema
└── summaries/                         # Documentation and summaries
```

---

## Architecture Diagram

```
┌─────────────┐     ┌──────────────────────┐     ┌─────────────────────────┐
│   Client     │────▶│  Cloudflare Worker   │────▶│  Cloudflare Container   │
│  (Browser)   │◀────│  (seo-audit-gateway) │◀────│  (Python + Playwright)  │
└─────────────┘     └──────────────────────┘     └─────────────────────────┘
                           │      │                        │
                           │      │                        │ crawl4ai + seo_audit
                           ▼      ▼                        │
                     ┌──────┐  ┌──────┐              ┌─────────────┐
                     │  D1  │  │  R2  │              │  Target     │
                     │  DB  │  │Bucket│              │  Websites   │
                     └──────┘  └──────┘              └─────────────┘
```

### Components

1. **Cloudflare Worker** (`infrastructure/worker/src/index.ts`)
   - API gateway — handles all HTTP requests
   - MCP server (via `agents` SDK) for AI tool integration
   - Manages crawl job lifecycle: submit, poll, cancel, list, get
   - Spins up Durable Object containers for each crawl job
   - Ingests results into D1 database
   - Key functions: `submitCrawl`, `pollJob`, `ingestResults`, `ingestPartialPages`

2. **Container** (`infrastructure/docker/server.py`)
   - Python HTTP server on port 8000
   - One crawl per container instance (one container per job)
   - Endpoints: `POST /start`, `GET /status`, `GET /health`
   - Runs crawl4ai with BFS strategy, audits pages incrementally
   - Persists state to `/tmp/crawl_state.json` for crash recovery

3. **D1 Database** (Cloudflare D1, schema in `infrastructure/d1-schema.sql`)
   - Tables: `crawl_jobs`, `page_audits`, `site_summaries`, `site_issues`
   - Views: `v_performance_issues`, `v_score_breakdown`

4. **R2 Bucket** (`seo-audit-snapshots`)
   - Stores raw HTML snapshots keyed by `{jobId}/{encodedUrl}.html`

---

## Crawl Flow

1. **Client** calls Worker API → `submitCrawl(url, maxPages, maxDepth)`
2. **Worker** creates a `crawl_jobs` row in D1, spins up a Durable Object container
3. **Container** receives `POST /start` → starts background crawl thread
4. **BFS Strategy** (`bfs_strategy.py`) crawls pages **one at a time** with 30s hard timeout per page
5. **On each page crawled**, the `_on_crawl_progress` callback in `server.py`:
   - Runs `SEOAnalyzer.analyze_page()` on the CrawlResult
   - Converts to flat payload dict via `_audit_page_to_payload()`
   - Appends to `state["partial_pages"]` and persists to disk
6. **Worker polls** container `/status` periodically via `pollJob`
   - Ingests `partial_pages` into D1 incrementally (deduplicates by URL)
7. **After crawl completes**, container runs site-level analysis (`analyze_site`)
   - Domain checks (robots.txt, sitemap, SSL)
   - Site-wide issues and score calculation
8. **Worker** ingests final results (issues, summary) into D1, marks job completed

### Timeouts
- **Per-page**: 30 seconds (`PAGE_TIMEOUT_MS = 30_000` in server.py, enforced by `asyncio.wait_for` in `_arun_batch`)
- **Overall crawl**: 4 minutes (`CRAWL_TIMEOUT_S = 240`)
- **Job expiry**: 5 minutes (Worker-side `JOB_TIMEOUT_MS`)

### Incremental Persistence
- Pages are audited and persisted to disk as they're crawled (in BFS callback)
- Container `/status` returns `partial_pages` while running
- Worker ingests partial pages into D1 on each poll
- If crawl times out, partial pages are still available and job completes with them

---

## Deployment Process

### Prerequisites
- Node.js + npm installed
- Wrangler CLI (`npm i -g wrangler` or use local in worker/)
- Authenticated with Cloudflare (`wrangler login`)

### Deploy Steps (MUST do after every code change)

The container image includes a **copy** of the `crawl4ai/` directory. Since the Dockerfile is in `infrastructure/docker/` but `crawl4ai/` is at the repo root, you must copy it before deploying.

```powershell
# 1. Copy crawl4ai source into docker build context
cd c:\Users\Waild\seo-crawler\crawl4ai-seo-branch
if (Test-Path "infrastructure\docker\crawl4ai") { Remove-Item -Recurse -Force "infrastructure\docker\crawl4ai" }
Copy-Item -Recurse "crawl4ai" "infrastructure\docker\crawl4ai"

# 2. Deploy Worker + Container (wrangler builds the Docker image and pushes it)
cd infrastructure\worker
npm run deploy

# 3. Clean up the copied crawl4ai (don't commit it)
cd c:\Users\Waild\seo-crawler\crawl4ai-seo-branch
Remove-Item -Recurse -Force "infrastructure\docker\crawl4ai"
```

**Important:** `npm run deploy` in the worker directory does EVERYTHING:
- Uploads the Worker TypeScript code
- Builds the Docker image from `infrastructure/docker/Dockerfile`
- Pushes the image to Cloudflare's container registry
- Rolls out the new container version

### D1 Schema Changes
```powershell
cd infrastructure\worker
npm run db:init   # runs: wrangler d1 execute seo-audit-db --file=../d1-schema.sql
```

The schema uses `CREATE TABLE IF NOT EXISTS` and migration-safe `ALTER TABLE` with try/catch, so it's safe to re-run.

---

## Key Files to Edit

| What | File | Notes |
|------|------|-------|
| Per-page SEO checks | `crawl4ai/seo_audit/checks.py` | Title, meta, headings, images, links, etc. |
| Site-level checks | `crawl4ai/seo_audit/site_checks.py` | Cross-page analysis, scoring, issues |
| Domain checks | `crawl4ai/seo_audit/domain_checks.py` | robots.txt, sitemap, SSL |
| Pydantic models | `crawl4ai/seo_audit/models.py` | All data models |
| SEO analyzer orchestrator | `crawl4ai/seo_audit/analyzer.py` | `analyze_page()`, `analyze_site()` |
| Public API exports | `crawl4ai/seo_audit/__init__.py` | What's importable |
| BFS crawl strategy | `crawl4ai/deep_crawling/bfs_strategy.py` | Per-page timeout, crawl loop |
| Container server | `infrastructure/docker/server.py` | HTTP server, crawl runner, state mgmt |
| Worker API | `infrastructure/worker/src/index.ts` | All API endpoints, job lifecycle, D1 ingestion |
| DB schema | `infrastructure/d1-schema.sql` | Tables, views, migrations |
| Wrangler config | `infrastructure/worker/wrangler.toml` | Bindings, env vars |

---

## Known Design Decisions

1. **One container per crawl job** — simplifies state management, no shared state between jobs
2. **BFS crawls pages sequentially** (not in parallel batches) — enables per-page timeout and incremental persistence
3. **Dual persistence**: disk (`/tmp/`) for container crash recovery + D1 for durable storage
4. **Worker polls container** — container doesn't push to Worker; Worker pulls on each `pollJob` call
5. **`crawl4ai/` is a modified fork** — changes to BFS strategy, models, etc. are local to this repo
6. **`infrastructure/docker/crawl4ai/` is ephemeral** — only exists during deploy, must NOT be committed (should be in .gitignore)

---

## Common Issues & Debugging

- **Crawl stuck**: Check if per-page timeout is working. Container logs show `Page timed out after 30s: <url>` for timed-out pages.
- **0 rows in page_audits**: Incremental persistence not working. Check that `_on_crawl_progress` is being called (BFS `on_state_change` callback).
- **has_microdata error**: Fixed — was referencing non-existent attribute on `StructuredDataCheck`. Use `has_json_ld` or `len(items) > 0`.
- **Container returns idle when DB says running**: Container restarted and lost in-memory state. Disk persistence should recover it; if not, Worker marks job failed.
- **Deploy fails**: Make sure `crawl4ai/` is copied into `infrastructure/docker/` before running `npm run deploy`.

---

## Cloudflare Resources

- **Worker**: `seo-audit-gateway` at `https://seo-audit-gateway.a-laitinen.workers.dev`
- **D1 Database**: `seo-audit-db` (ID: `4b98d8a3-fb04-45db-985d-58e041aceb5b`)
- **R2 Bucket**: `seo-audit-snapshots`
- **Container**: `seo-audit-gateway-crawlercontainer` (Firecracker, standard-1 instance)
