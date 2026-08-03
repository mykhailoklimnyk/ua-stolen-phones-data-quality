-- Queries behind docs/profiling_report.md, run against the raw source JSON.
-- Reproduce with: duckdb -c ".read docs/profiling_queries.sql"
-- after pointing `src` at a downloaded snapshot.

CREATE OR REPLACE VIEW src AS
SELECT * FROM read_json('wantedmt.json',
    columns={id:'VARCHAR', ovd:'VARCHAR', insert_date:'VARCHAR', nz:'VARCHAR',
             imei:'VARCHAR', nk:'VARCHAR', dk:'VARCHAR', dtl:'VARCHAR'},
    format='array');

-- Volume, key uniqueness, and the date range as published.
SELECT count(*) AS rows, count(DISTINCT id) AS distinct_id,
       min(insert_date), max(insert_date), min(nullif(dk,'')), max(nullif(dk,''))
FROM src;

-- Emptiness per field. The source writes '' rather than null, so every check
-- has to go through nullif(trim(x), '').
SELECT
  round(avg(CASE WHEN nullif(trim(imei),'') IS NULL THEN 1 ELSE 0 END), 5) AS imei_empty,
  round(avg(CASE WHEN nullif(trim(nz),'')   IS NULL THEN 1 ELSE 0 END), 5) AS nz_empty,
  round(avg(CASE WHEN nullif(trim(dtl),'')  IS NULL THEN 1 ELSE 0 END), 5) AS dtl_empty,
  round(avg(CASE WHEN nullif(trim(nk),'')   IS NULL THEN 1 ELSE 0 END), 5) AS nk_empty,
  round(avg(CASE WHEN nullif(trim(dk),'')   IS NULL THEN 1 ELSE 0 END), 5) AS dk_empty
FROM src;

-- Dates are ISO-8601 in form but not in value: this returns 0 while dk still
-- reaches back to year 0201. Format compliance proves nothing about content.
SELECT count(*) FILTER (WHERE insert_date NOT SIMILAR TO '\d{4}-\d{2}-\d{2}T.*') AS bad_insert,
       count(*) FILTER (WHERE dk <> '' AND dk NOT SIMILAR TO '\d{4}-\d{2}-\d{2}T.*') AS bad_dk
FROM src;

SELECT left(insert_date, 4) AS yr, count(*) FROM src GROUP BY 1 ORDER BY 1;

-- IMEI shape. 15 digits dominate; 14 are missing the check digit; nothing in
-- the 28-32 range, so no record holds two identifiers.
SELECT length(regexp_replace(imei, '\D', '', 'g')) AS digits, count(*) c
FROM src GROUP BY 1 ORDER BY c DESC;

-- Non-digit characters actually present in the IMEI field.
SELECT regexp_replace(imei, '[0-9]', '', 'g') AS junk, count(*) c
FROM src WHERE regexp_matches(imei, '\D') GROUP BY 1 ORDER BY c DESC LIMIT 25;

-- TAC concentration: how many prefixes cover half, four fifths, 95% of records.
WITH t AS (SELECT substr(regexp_replace(imei,'\D','','g'), 1, 8) AS tac, count(*) c
           FROM src WHERE length(regexp_replace(imei,'\D','','g')) >= 14 GROUP BY 1),
     s AS (SELECT c, sum(c) OVER (ORDER BY c DESC) cum, sum(c) OVER () tot,
                  row_number() OVER (ORDER BY c DESC) rn FROM t)
SELECT max(rn) FILTER (WHERE cum <= tot*0.5)  AS tacs_for_50pct,
       max(rn) FILTER (WHERE cum <= tot*0.8)  AS tacs_for_80pct,
       max(rn) FILTER (WHERE cum <= tot*0.95) AS tacs_for_95pct
FROM s;

-- Brand/model spellings: uppercasing and whitespace collapsing remove only a
-- small part of the variation, so the rest is genuinely different text.
SELECT count(DISTINCT nz) AS raw,
       count(DISTINCT upper(trim(nz))) AS uppercased,
       count(DISTINCT regexp_replace(upper(trim(nz)), '\s+', ' ', 'g')) AS collapsed
FROM src;

-- Cyrillic look-alikes hiding part of a brand behind one substituted letter.
SELECT nz, count(*) c FROM src
WHERE nz IN ('XIAOMI','XІAOMІ','HUAWEI','HUAWEІ','MEIZU','MEІZU',
             'APPLE IPHONE','APPLE ІPHONE')
GROUP BY 1 ORDER BY c DESC;

-- Police units, the only geography the register carries.
SELECT ovd, count(*) c FROM src GROUP BY 1 ORDER BY c DESC LIMIT 100;

-- dtl is empty in most records; what is left is a mix of circumstances and
-- country of manufacture rather than a usable enumeration.
SELECT dtl, count(*) c FROM src GROUP BY 1 ORDER BY c DESC LIMIT 20;
