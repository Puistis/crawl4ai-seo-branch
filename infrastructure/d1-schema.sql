-- =============================================================================
-- Cloudflare D1 Schema for SEO Audit Platform
-- =============================================================================
-- Design principles:
--   1. Queryable metadata in indexed columns (domain, url, score, severity)
--   2. Full audit JSON stored for completeness (no data loss)
--   3. Flat issues table for "show me all critical issues" queries
--   4. Jobs table for tracking crawl lifecycle
-- =============================================================================

-- ─── Crawl Jobs ──────────────────────────────────────────────────────────────
-- Tracks every crawl request from submission through completion.
-- The Worker creates a row on submit; the Docker container updates it.

CREATE TABLE IF NOT EXISTS crawl_jobs (
    id          TEXT PRIMARY KEY,                -- UUID
    domain      TEXT NOT NULL,                   -- e.g. "example.com"
    start_url   TEXT NOT NULL,                   -- entry URL for the crawl
    config      TEXT DEFAULT '{}',               -- JSON: depth, max_pages, filters, etc.
    status      TEXT NOT NULL DEFAULT 'queued',  -- queued | running | completed | failed
    pages_found INTEGER DEFAULT 0,
    pages_done  INTEGER DEFAULT 0,
    score       INTEGER,                         -- 0-100 site-wide SEO score (set on complete)
    error       TEXT,                            -- error message if failed
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    started_at  TEXT,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_domain ON crawl_jobs(domain);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON crawl_jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON crawl_jobs(created_at);


-- ─── Page Audits ─────────────────────────────────────────────────────────────
-- One row per audited page. Key metrics in columns; full result as JSON.

CREATE TABLE IF NOT EXISTS page_audits (
    id              TEXT PRIMARY KEY,            -- UUID
    job_id          TEXT NOT NULL REFERENCES crawl_jobs(id),
    url             TEXT NOT NULL,
    domain          TEXT NOT NULL,
    status_code     INTEGER,

    -- Key SEO signals (indexed for fast queries)
    title           TEXT,
    title_length    INTEGER,
    title_status    TEXT,                        -- pass | fail | warning | info
    meta_desc       TEXT,
    meta_desc_length INTEGER,
    meta_desc_status TEXT,
    h1_count        INTEGER DEFAULT 0,
    has_canonical   INTEGER DEFAULT 0,           -- boolean: 0/1
    is_indexable    INTEGER DEFAULT 1,           -- boolean: 0/1
    has_json_ld     INTEGER DEFAULT 0,           -- boolean: 0/1
    has_viewport    INTEGER DEFAULT 0,           -- boolean: 0/1
    has_og_tags     INTEGER DEFAULT 0,           -- boolean: 0/1
    word_count      INTEGER DEFAULT 0,
    images_total    INTEGER DEFAULT 0,
    images_no_alt   INTEGER DEFAULT 0,
    internal_links  INTEGER DEFAULT 0,
    external_links  INTEGER DEFAULT 0,
    mixed_content   INTEGER DEFAULT 0,           -- boolean: 0/1
    response_time_ms REAL,                        -- page load time in ms
    page_weight_bytes INTEGER DEFAULT 0,          -- total page weight in bytes

    -- Full audit result (all checks, all details)
    audit_json      TEXT NOT NULL,               -- JSON: complete PageAuditResult

    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pages_job    ON page_audits(job_id);
CREATE INDEX IF NOT EXISTS idx_pages_domain ON page_audits(domain);
CREATE INDEX IF NOT EXISTS idx_pages_url    ON page_audits(url);
CREATE INDEX IF NOT EXISTS idx_pages_title_status ON page_audits(title_status);
CREATE INDEX IF NOT EXISTS idx_pages_h1     ON page_audits(h1_count);


-- ─── Site Issues ─────────────────────────────────────────────────────────────
-- Flat table of all issues found in a site audit. One row per issue.
-- This is the primary table Claude queries: "show critical issues for domain X"

CREATE TABLE IF NOT EXISTS site_issues (
    id              TEXT PRIMARY KEY,            -- UUID
    job_id          TEXT NOT NULL REFERENCES crawl_jobs(id),
    domain          TEXT NOT NULL,
    issue_type      TEXT NOT NULL,               -- e.g. "missing_title", "duplicate_titles"
    severity        TEXT NOT NULL,               -- critical | warning | info
    description     TEXT NOT NULL,
    fix             TEXT,                        -- recommended fix
    affected_count  INTEGER DEFAULT 0,           -- number of affected pages
    affected_urls   TEXT,                        -- JSON array of affected URLs
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_issues_job      ON site_issues(job_id);
CREATE INDEX IF NOT EXISTS idx_issues_domain   ON site_issues(domain);
CREATE INDEX IF NOT EXISTS idx_issues_severity ON site_issues(severity);
CREATE INDEX IF NOT EXISTS idx_issues_type     ON site_issues(issue_type);


-- ─── Site Audit Summaries ────────────────────────────────────────────────────
-- One row per completed site audit. Quick dashboard-level data.

CREATE TABLE IF NOT EXISTS site_summaries (
    id              TEXT PRIMARY KEY,            -- same as job_id
    job_id          TEXT NOT NULL REFERENCES crawl_jobs(id),
    domain          TEXT NOT NULL,
    pages_audited   INTEGER DEFAULT 0,
    score           INTEGER DEFAULT 0,           -- 0-100
    issues_critical INTEGER DEFAULT 0,
    issues_warning  INTEGER DEFAULT 0,
    issues_info     INTEGER DEFAULT 0,
    audit_json      TEXT NOT NULL,               -- JSON: complete SiteAuditResult (minus page_details)
    score_breakdown TEXT,                        -- JSON: per-category score breakdown

    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_summaries_job    ON site_summaries(job_id);
CREATE INDEX IF NOT EXISTS idx_summary_domain ON site_summaries(domain);
CREATE INDEX IF NOT EXISTS idx_summary_score  ON site_summaries(score);


-- ─── Useful Views ────────────────────────────────────────────────────────────

-- Latest audit per domain (for "what's the current state of example.com?")
CREATE VIEW IF NOT EXISTS v_latest_audits AS
SELECT s.*, j.start_url, j.status as job_status, j.completed_at
FROM site_summaries s
JOIN crawl_jobs j ON j.id = s.job_id
WHERE j.status = 'completed'
ORDER BY s.created_at DESC;

-- All critical issues across all domains
CREATE VIEW IF NOT EXISTS v_critical_issues AS
SELECT si.*, j.start_url, j.completed_at
FROM site_issues si
JOIN crawl_jobs j ON j.id = si.job_id
WHERE si.severity = 'critical'
  AND j.status = 'completed'
ORDER BY si.created_at DESC;

-- Pages with SEO problems (quick filter)
-- NOTE: Crawler produces 'pass', 'warning', 'fail' statuses. Most issues surface
-- as 'warning' (length slightly off) with 'fail' reserved for missing tags entirely.
CREATE VIEW IF NOT EXISTS v_problem_pages AS
SELECT
    pa.url,
    pa.domain,
    pa.title_status,
    pa.meta_desc_status,
    pa.h1_count,
    pa.has_canonical,
    pa.is_indexable,
    pa.word_count,
    pa.images_no_alt,
    pa.job_id,
    pa.created_at
FROM page_audits pa
WHERE pa.title_status IN ('fail', 'warning')
   OR pa.meta_desc_status IN ('fail', 'warning')
   OR pa.h1_count = 0
   OR pa.has_viewport = 0
   OR pa.mixed_content = 1
   OR pa.images_no_alt > 0
   OR pa.word_count < 300;


-- ─── Phase 2 Views ──────────────────────────────────────────────────────────

-- Slow or heavy pages
CREATE VIEW IF NOT EXISTS v_performance_issues AS
SELECT
    pa.url,
    pa.domain,
    pa.response_time_ms,
    pa.page_weight_bytes,
    pa.word_count,
    pa.images_total,
    pa.job_id,
    pa.created_at
FROM page_audits pa
WHERE pa.response_time_ms > 3000
   OR pa.page_weight_bytes > 3145728;

-- Score breakdown per domain (latest audit)
CREATE VIEW IF NOT EXISTS v_score_breakdown AS
SELECT
    s.domain,
    s.score,
    s.score_breakdown,
    s.pages_audited,
    s.issues_critical,
    s.issues_warning,
    s.issues_info,
    j.completed_at
FROM site_summaries s
JOIN crawl_jobs j ON j.id = s.job_id
WHERE j.status = 'completed'
ORDER BY s.created_at DESC;


-- =============================================================================
-- Phase 3 Migration — Link Graph, Broken Links, Redirect Chains
-- =============================================================================

-- ─── Link Graph ─────────────────────────────────────────────────────────────
-- Stores every link relationship discovered during a crawl.
-- Enables orphan page detection, link depth analysis, and link equity mapping.

CREATE TABLE IF NOT EXISTS link_graph (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES crawl_jobs(id),
    source_url  TEXT NOT NULL,
    target_url  TEXT NOT NULL,
    anchor_text TEXT,
    is_nofollow INTEGER DEFAULT 0,
    link_type   TEXT DEFAULT 'internal',  -- 'internal' or 'external'
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_link_graph_job    ON link_graph(job_id);
CREATE INDEX IF NOT EXISTS idx_link_graph_source ON link_graph(job_id, source_url);
CREATE INDEX IF NOT EXISTS idx_link_graph_target ON link_graph(job_id, target_url);


-- ─── Broken Links ───────────────────────────────────────────────────────────
-- Stores links that returned 4xx/5xx or timed out during the crawl.

CREATE TABLE IF NOT EXISTS broken_links (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES crawl_jobs(id),
    source_url  TEXT NOT NULL,
    target_url  TEXT NOT NULL,
    status_code INTEGER,           -- 404, 500, 0 (timeout), etc.
    anchor_text TEXT,
    link_type   TEXT DEFAULT 'internal',  -- 'internal' or 'external'
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_broken_links_job ON broken_links(job_id);


-- ─── Redirect Chains ───────────────────────────────────────────────────────
-- Stores URLs that went through one or more redirects during the crawl.

CREATE TABLE IF NOT EXISTS redirect_chains (
    id           TEXT PRIMARY KEY,
    job_id       TEXT NOT NULL REFERENCES crawl_jobs(id),
    source_url   TEXT NOT NULL,       -- The URL that was linked to
    final_url    TEXT NOT NULL,        -- Where it ultimately resolved
    chain_length INTEGER,             -- Number of redirects (1 = single, 2+ = chain)
    chain_path   TEXT,                -- JSON array of URLs in the chain
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_redirect_chains_job ON redirect_chains(job_id);


-- ─── Lighthouse Scores ─────────────────────────────────────────────────────
-- Per-URL Lighthouse performance and quality scores.
-- job_id is NULL for standalone lighthouse_bulk runs, set for crawler-triggered.

CREATE TABLE IF NOT EXISTS lighthouse_scores (
    id                   TEXT PRIMARY KEY,
    job_id               TEXT,                          -- NULL for standalone, set for crawler-triggered
    url                  TEXT NOT NULL,
    device               TEXT DEFAULT 'mobile',         -- 'mobile' or 'desktop'
    performance_score    INTEGER,                       -- 0-100
    accessibility_score  INTEGER,                       -- 0-100
    best_practices_score INTEGER,                       -- 0-100
    seo_score            INTEGER,                       -- 0-100
    lcp_ms               REAL,
    cls                  REAL,
    tbt_ms               REAL,
    fcp_ms               REAL,
    speed_index_ms       REAL,
    tti_ms               REAL,
    diagnostics_json     TEXT,                          -- full Lighthouse diagnostics
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_lighthouse_job ON lighthouse_scores(job_id);
CREATE INDEX IF NOT EXISTS idx_lighthouse_url ON lighthouse_scores(url);


-- ─── Phase 4 Migration — Link Graph rel, Broken Links descriptions ─────────

-- BUG-023: Store full rel attribute value (e.g., "nofollow sponsored")
ALTER TABLE link_graph ADD COLUMN rel TEXT DEFAULT NULL;

-- BUG-022: Store descriptive error name for non-HTTP status codes
ALTER TABLE broken_links ADD COLUMN status_code_desc TEXT DEFAULT NULL;


-- ─── Phase 3 Views ──────────────────────────────────────────────────────────

-- Inbound link counts per page (most linked pages)
CREATE VIEW IF NOT EXISTS v_inbound_links AS
SELECT
    lg.job_id,
    lg.target_url,
    COUNT(*) as inbound_count,
    COUNT(DISTINCT lg.source_url) as unique_sources
FROM link_graph lg
WHERE lg.link_type = 'internal'
GROUP BY lg.job_id, lg.target_url
ORDER BY inbound_count DESC;

-- Broken links summary per job
CREATE VIEW IF NOT EXISTS v_broken_links_summary AS
SELECT
    bl.job_id,
    bl.link_type,
    COUNT(*) as broken_count,
    COUNT(DISTINCT bl.target_url) as unique_broken_urls
FROM broken_links bl
GROUP BY bl.job_id, bl.link_type;
