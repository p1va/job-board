-- Job boards on hosted ATS sites, found in Common Crawl's columnar URL index (Athena, us-east-1).
-- Claude Code (t15), 2026-11-08; reviewed by Codex (t17): approved for EXPLAIN + a one-crawl trial.
--
-- What it finds: board keys (company slugs) from URLs on ATS hosts, e.g. jobs.lever.co/<slug>.
-- It reads URLs only, not page contents, so boards embedded on companies' own sites are not found
-- here (that needs the separate HTML stage in ATHENA_EMBEDDED_BOARDS.md).
--
-- Setup is the same as embedded-boards/README.md in the fork: database ccindex, table
-- ccindex.ccindex from Common Crawl's cc-index-create-table-flat.sql, crawls added as partitions.
--
-- Cost control: the index is sorted by url_surtkey (reversed host, e.g. 'co,lever,jobs)/acme'), so
-- ranges on url_surtkey should let Athena skip most row groups. How much is not measured yet.
-- Athena charges $5 per TB scanned (10 MB minimum per query). Budget: 20 GB per query (~$0.10).
--   0. Optional safety net: in the Athena workgroup settings, set a per-query data usage limit of
--      20 GB, so a query that would scan more is cancelled instead of billed in full.
--   1. Add the partitions (instant, no scan).
--   2. EXPLAIN the export (no scan): check the plan filters crawl, subset and url_surtkey ranges.
--   3. Run the export for ONE crawl; note "Data scanned" in the console.
--   4. If it stayed within budget, run it for all three crawls (about 3x that). Otherwise stop and
--      send us the scanned size.
--   5. Download the CSV as results/<newest crawl>-ats-boards.csv.

-- 1. Partitions (once per crawl). Newest first at https://index.commoncrawl.org/collinfo.json
ALTER TABLE ccindex.ccindex ADD IF NOT EXISTS
  PARTITION (crawl = 'CC-MAIN-2026-43', subset = 'warc')
  LOCATION 's3://commoncrawl/cc-index/table/cc-main/warc/crawl=CC-MAIN-2026-43/subset=warc/'
  PARTITION (crawl = 'CC-MAIN-2026-39', subset = 'warc')
  LOCATION 's3://commoncrawl/cc-index/table/cc-main/warc/crawl=CC-MAIN-2026-39/subset=warc/'
  PARTITION (crawl = 'CC-MAIN-2026-34', subset = 'warc')
  LOCATION 's3://commoncrawl/cc-index/table/cc-main/warc/crawl=CC-MAIN-2026-34/subset=warc/';

-- 2./3./4. Export: one row per (ats, api_region, board_key). Put "EXPLAIN " in front for step 2.
-- For step 3 keep one crawl in the IN list.
WITH hits AS (
  SELECT crawl, url, url_host_name AS host, url_path AS path, url_query AS query, fetch_status
  FROM ccindex.ccindex
  WHERE crawl IN ('CC-MAIN-2026-43', 'CC-MAIN-2026-39', 'CC-MAIN-2026-34')
    AND subset = 'warc'
    AND (   (url_surtkey >= 'com,ashbyhq,jobs)/'       AND url_surtkey < 'com,ashbyhq,jobs)0')
         OR (url_surtkey >= 'io,greenhouse,'           AND url_surtkey < 'io,greenhouse-')
         OR (url_surtkey >= 'co,lever,'                AND url_surtkey < 'co,lever-')
         OR (url_surtkey >= 'com,workable,'            AND url_surtkey < 'com,workable-')
         OR (url_surtkey >= 'com,smartrecruiters,'     AND url_surtkey < 'com,smartrecruiters-')
         OR (url_surtkey >= 'de,personio,'             AND url_surtkey < 'de,personio-')
         OR (url_surtkey >= 'com,personio,'            AND url_surtkey < 'com,personio-')
         OR (url_surtkey >= 'com,recruitee,'           AND url_surtkey < 'com,recruitee-')
         OR (url_surtkey >= 'com,teamtailor,'          AND url_surtkey < 'com,teamtailor-'))
),
parsed AS (
  SELECT
    crawl, url, fetch_status, host, path,
    CASE
      WHEN host = 'jobs.ashbyhq.com' THEN 'ashby'
      WHEN host IN ('boards.greenhouse.io', 'job-boards.greenhouse.io',
                    'boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io') THEN 'greenhouse'
      WHEN host IN ('jobs.lever.co', 'jobs.eu.lever.co') THEN 'lever'
      WHEN host = 'apply.workable.com' THEN 'workable'
      -- older form <slug>.workable.com (redirects to apply.workable.com/<slug>)
      WHEN regexp_like(host, '^[a-z0-9-]+\.workable\.com$')
           AND host NOT IN ('www.workable.com', 'jobs.workable.com', 'help.workable.com', 'resources.workable.com',
                            'api.workable.com', 'blog.workable.com', 'status.workable.com', 'developers.workable.com') THEN 'workable'
      WHEN host IN ('jobs.smartrecruiters.com', 'careers.smartrecruiters.com') THEN 'smartrecruiters'
      WHEN regexp_like(host, '^[a-z0-9-]+\.jobs\.personio\.(de|com)$') THEN 'personio'
      WHEN regexp_like(host, '^[a-z0-9-]+\.recruitee\.com$')
           AND host NOT IN ('www.recruitee.com', 'app.recruitee.com', 'api.recruitee.com',
                            'careers.recruitee.com', 'blog.recruitee.com', 'support.recruitee.com') THEN 'recruitee'
      WHEN regexp_like(host, '^[a-z0-9-]+\.teamtailor\.com$')
           AND host NOT IN ('www.teamtailor.com', 'app.teamtailor.com', 'api.teamtailor.com',
                            'career.teamtailor.com', 'support.teamtailor.com', 'partner.teamtailor.com') THEN 'teamtailor'
    END AS ats,
    CASE
      WHEN host IN ('jobs.eu.lever.co') THEN 'eu'
      WHEN host IN ('boards.eu.greenhouse.io', 'job-boards.eu.greenhouse.io') THEN 'eu'
    END AS api_region,
    CASE
      -- Greenhouse embeds: /embed/job_board?for=<slug> and /embed/job_app?for=<slug>&token=<id>
      WHEN host LIKE '%greenhouse.io' AND split_part(path, '/', 2) = 'embed'
        THEN regexp_extract(query, '(?:^|&)for=([^&]+)', 1)
      -- slug in the host: <slug>.jobs.personio.de, <slug>.recruitee.com, <slug>.teamtailor.com
      WHEN host LIKE '%.jobs.personio.%' OR host LIKE '%.recruitee.com' OR host LIKE '%.teamtailor.com'
           OR (host LIKE '%.workable.com' AND host <> 'apply.workable.com')
        THEN split_part(host, '.', 1)
      -- slug as the first path segment everywhere else
      ELSE split_part(path, '/', 2)
    END AS slug
  FROM hits
)
SELECT
  ats,
  api_region,
  -- SmartRecruiters and Lever identifiers are case-sensitive (Lever /Legend works, /legend is 404).
  CASE WHEN ats IN ('smartrecruiters', 'lever') THEN slug ELSE lower(slug) END AS board_key,
  COUNT(*)                          AS captures,
  COUNT_IF(fetch_status = 200)      AS ok_captures,
  COUNT(DISTINCT crawl)             AS crawls,
  max_by(url, crawl)                AS example_url   -- full URL, keeps ?for= on Greenhouse embeds
FROM parsed
WHERE ats IS NOT NULL
  AND regexp_like(slug, '^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$')
  AND lower(slug) NOT IN ('api', 'embed', 'j', 'jobs', 'job', 'apply', 'careers', 'static', 'assets',
                          'favicon.ico', 'robots.txt', 'sitemap.xml', 'search', 'login', 'signup', 'privacy')
GROUP BY 1, 2, 3
ORDER BY ats, captures DESC;
