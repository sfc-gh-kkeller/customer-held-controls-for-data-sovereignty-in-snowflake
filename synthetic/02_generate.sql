-- ============================================================================
-- Synthetic data, step 2: segment generator (plain SQL)
--
--   Learn   noisy statistics per segment (department x country x level) from the real table;
--           segments smaller than MIN_CELL are merged: -> (department, level) -> (department).
--   Sample  brand-new rows from those statistics only. No real row is copied.
--
--   Noise   Laplace noise on every published count, mean and rate. Salaries are clipped to
--           [SAL_LO, SAL_HI] first, so one person can move a segment mean by at most
--           (SAL_HI - SAL_LO) / n. This is DP-style but NOT an audited differential-privacy
--           guarantee: the privacy budget is not composed across statistics, and standard
--           deviations and regression terms are published without noise.
--
--   Assumes tenure/attrition do not depend on segment (true for this source; check per dataset).
-- ============================================================================
USE ROLE ACCOUNTADMIN;
USE WAREHOUSE SOV_WH;
USE SCHEMA SOVEREIGNTY_DEMO.SYNTH;

SET min_cell = 10;        -- smallest segment published on its own
SET eps      = 1.0;       -- noise per statistic: smaller = more privacy, less accuracy
SET sal_lo   = 25000;
SET sal_hi   = 175000;

-- Laplace(0, b) from a uniform u in (0, 1)
CREATE OR REPLACE FUNCTION LAPLACE(b FLOAT, u FLOAT) RETURNS FLOAT
  AS 'b * SIGN(0.5 - u) * LN(1 - 2 * ABS(u - 0.5))';
CREATE OR REPLACE FUNCTION U01() RETURNS FLOAT AS 'UNIFORM(1e-6::FLOAT, 0.999999::FLOAT, RANDOM())';

-- 1. Segment statistics -------------------------------------------------------
CREATE OR REPLACE TABLE SEGMENT_MODEL AS
WITH clipped AS (
  SELECT * REPLACE (LEAST(GREATEST(salary, $sal_lo), $sal_hi) AS salary) FROM EMPLOYEES_REAL
), cell AS (
  SELECT department, country, level, COUNT(*) AS n,
         SUM(COUNT(*)) OVER (PARTITION BY department, level) AS n_dept_level
  FROM clipped GROUP BY 1, 2, 3
), labelled AS (
  SELECT r.*,
         IFF(c.n >= $min_cell, r.country, 'ALL')       AS seg_country,   -- merge across countries
         IFF(c.n_dept_level >= $min_cell, r.level, 0)  AS seg_level      -- 0 = merged across levels
  FROM clipped r JOIN cell c USING (department, country, level)
), stats AS (
  SELECT department, seg_country AS country, seg_level AS level,
         COUNT(*)                                            AS n,
         AVG(salary)                                         AS salary_mean,
         COALESCE(STDDEV(salary), 6000)                      AS salary_sd,
         COALESCE(REGR_SLOPE(bonus, salary), 0)              AS bonus_slope,
         COALESCE(REGR_INTERCEPT(bonus, salary), AVG(bonus)) AS bonus_icpt,
         COALESCE(SQRT(VAR_POP(bonus) * (1 - POWER(COALESCE(CORR(bonus, salary), 0), 2))), 1500) AS bonus_resid_sd
  FROM labelled GROUP BY 1, 2, 3
)
SELECT department, country, level,
       GREATEST(1, ROUND(n + LAPLACE(1 / $eps, U01())))                                AS n_noisy,
       salary_mean + LAPLACE((($sal_hi - $sal_lo) / n) / $eps, U01())                  AS salary_mean,
       salary_sd, bonus_slope, bonus_icpt, bonus_resid_sd
FROM stats;

-- 2. Mixes used to fill merged segments (noisy, learned from the data) --------
CREATE OR REPLACE TABLE COUNTRY_MIX AS
WITH w AS (SELECT country, GREATEST(1, COUNT(*) + LAPLACE(1 / $eps, U01())) AS w
           FROM EMPLOYEES_REAL GROUP BY country)
SELECT country,
       (SUM(w) OVER (ORDER BY country) - w) / SUM(w) OVER () AS cum_lo,
        SUM(w) OVER (ORDER BY country)      / SUM(w) OVER () AS cum_hi
FROM w;

CREATE OR REPLACE TABLE LEVEL_MIX AS
WITH w AS (SELECT level, GREATEST(1, COUNT(*) + LAPLACE(1 / $eps, U01())) AS w
           FROM EMPLOYEES_REAL GROUP BY level)
SELECT level,
       (SUM(w) OVER (ORDER BY level) - w) / SUM(w) OVER () AS cum_lo,
        SUM(w) OVER (ORDER BY level)      / SUM(w) OVER () AS cum_hi
FROM w;

-- 3. Tenure and attrition -----------------------------------------------------
CREATE OR REPLACE TABLE TENURE_MODEL AS
WITH t AS (SELECT tenure_years, COUNT(*) AS n, AVG(IFF(left_company, 1, 0)) AS p
           FROM EMPLOYEES_REAL GROUP BY 1),
noisy AS (
  SELECT tenure_years,
         GREATEST(1, n + LAPLACE(1 / $eps, U01()))                              AS w,
         LEAST(1, GREATEST(0, p + LAPLACE((1 / n) / $eps, U01())))              AS p_left
  FROM t)
SELECT tenure_years, p_left,
       (SUM(w) OVER (ORDER BY tenure_years) - w) / SUM(w) OVER () AS cum_lo,
        SUM(w) OVER (ORDER BY tenure_years)      / SUM(w) OVER () AS cum_hi
FROM noisy;

-- 4. Sample new rows from the published statistics only -----------------------
CREATE OR REPLACE TABLE EMPLOYEES_SYNTH AS
WITH seq AS (SELECT SEQ4() AS i FROM TABLE(GENERATOR(ROWCOUNT => 10000))),
draws AS (
  SELECT m.*,
         UNIFORM(0::FLOAT, 1::FLOAT, RANDOM()) AS u_tenure, UNIFORM(0::FLOAT, 1::FLOAT, RANDOM()) AS u_left,
         UNIFORM(0::FLOAT, 1::FLOAT, RANDOM()) AS u_cty,    UNIFORM(0::FLOAT, 1::FLOAT, RANDOM()) AS u_lvl,
         NORMAL(0, 1, RANDOM()) AS z1, NORMAL(0, 1, RANDOM()) AS z2
  FROM SEGMENT_MODEL m JOIN seq s ON s.i < m.n_noisy
), filled AS (
  SELECT d.*,
         IFF(d.country <> 'ALL', d.country, c.country) AS out_country,
         IFF(d.level <> 0, d.level, l.level)           AS out_level,
         ROUND(LEAST(GREATEST(d.salary_mean + d.salary_sd * d.z1, $sal_lo), $sal_hi)) AS out_salary
  FROM draws d
  JOIN COUNTRY_MIX c ON d.u_cty >= c.cum_lo AND d.u_cty < c.cum_hi
  JOIN LEVEL_MIX   l ON d.u_lvl >= l.cum_lo AND d.u_lvl < l.cum_hi
)
SELECT ROW_NUMBER() OVER (ORDER BY RANDOM())                                   AS employee_id,
       f.department, f.out_country AS country, f.out_level AS level, t.tenure_years,
       f.out_salary                                                            AS salary,
       ROUND(GREATEST(0, f.bonus_icpt + f.bonus_slope * f.out_salary + f.bonus_resid_sd * f.z2)) AS bonus,
       f.u_left < t.p_left                                                     AS left_company
FROM filled f JOIN TENURE_MODEL t ON f.u_tenure >= t.cum_lo AND f.u_tenure < t.cum_hi;

SELECT COUNT(*) AS synthetic_rows FROM EMPLOYEES_SYNTH;
