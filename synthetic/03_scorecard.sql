-- ============================================================================
-- Synthetic data, step 3: scorecard - which insights survive, how close to real people?
-- ============================================================================
USE ROLE ACCOUNTADMIN;
USE WAREHOUSE SOV_WH;
USE SCHEMA SOVEREIGNTY_DEMO.SYNTH;

CREATE OR REPLACE VIEW BOTH_VERSIONS AS
          SELECT 'real'      AS version, * EXCLUDE employee_id FROM EMPLOYEES_REAL
UNION ALL SELECT 'synthetic',            * EXCLUDE employee_id FROM EMPLOYEES_SYNTH;

-- A. Insight queries a data buyer would ask
SELECT version,
  COUNT(*)                                                                      AS rows_,
  ROUND(AVG(salary))                                                            AS avg_salary,
  ROUND(CORR(level, salary), 2)                                                 AS corr_level_salary,
  ROUND(CORR(salary, bonus), 2)                                                 AS corr_salary_bonus,
  ROUND(AVG(IFF(left_company, 1, 0)), 3)                                        AS attrition,
  ROUND(AVG(IFF(tenure_years <= 2,  IFF(left_company, 1, 0), NULL)), 3)         AS attrition_tenure_0_2,
  ROUND(AVG(IFF(tenure_years >= 12, IFF(left_company, 1, 0), NULL)), 3)         AS attrition_tenure_12p,
  ROUND(AVG(IFF(country = 'PL', salary, NULL)))                                 AS avg_salary_pl,
  ROUND(AVG(IFF(level = 5, salary, NULL)) - AVG(IFF(level = 1, salary, NULL)))  AS level5_minus_level1,
  ROUND(AVG(IFF(department = 'Sales' AND country = 'DE' AND level = 5, salary, NULL))) AS avg_sales_de_l5,
  COUNT_IF(department = 'Legal')                                                AS legal_headcount,
  ROUND(AVG(IFF(department = 'Legal', salary, NULL)))                           AS avg_salary_legal
FROM BOTH_VERSIONS GROUP BY version ORDER BY version;

-- B. Error on the 30 department x level salary averages
SELECT ROUND(AVG(ABS(s.avg_sal - r.avg_sal) / r.avg_sal) * 100, 1) AS mean_abs_pct_error,
       ROUND(MAX(ABS(s.avg_sal - r.avg_sal) / r.avg_sal) * 100, 1) AS worst_segment_pct_error,
       MAX_BY(s.department || ' L' || s.level, ABS(s.avg_sal - r.avg_sal) / r.avg_sal) AS worst_segment
FROM (SELECT department, level, AVG(salary) AS avg_sal FROM EMPLOYEES_SYNTH GROUP BY 1, 2) s
JOIN (SELECT department, level, AVG(salary) AS avg_sal FROM EMPLOYEES_REAL  GROUP BY 1, 2) r
  USING (department, level);

-- C. Privacy: exact copies of a real row
SELECT COUNT(*) AS exact_copies_of_real_rows FROM (
  SELECT * EXCLUDE employee_id FROM EMPLOYEES_SYNTH
  INTERSECT SELECT * EXCLUDE employee_id FROM EMPLOYEES_REAL);

-- D. Privacy: distance to the closest real record (DCR) within the same department/country/level,
--    compared with how close real people are to each other (baseline). 1,000 rows sampled each.
WITH syn AS (SELECT * FROM EMPLOYEES_SYNTH SAMPLE (1000 ROWS)),
d_syn AS (
  SELECT s.employee_id,
         MIN(SQRT(SQUARE((s.salary - r.salary) / 10000) + SQUARE((s.bonus - r.bonus) / 2000)
                  + SQUARE(s.tenure_years - r.tenure_years))) AS dcr
  FROM syn s JOIN EMPLOYEES_REAL r USING (department, country, level) GROUP BY 1),
d_real AS (
  SELECT a.employee_id,
         MIN(SQRT(SQUARE((a.salary - b.salary) / 10000) + SQUARE((a.bonus - b.bonus) / 2000)
                  + SQUARE(a.tenure_years - b.tenure_years))) AS dcr
  FROM (SELECT * FROM EMPLOYEES_REAL WHERE employee_id <= 1000) a
  JOIN EMPLOYEES_REAL b ON b.department = a.department AND b.country = a.country
                       AND b.level = a.level AND b.employee_id <> a.employee_id
  GROUP BY 1)
SELECT 'synthetic -> nearest real' AS comparison, COUNT(*) AS sampled,
       COUNT_IF(dcr < 0.05) AS near_copies, ROUND(MEDIAN(dcr), 3) AS median_dcr FROM d_syn
UNION ALL
SELECT 'real -> nearest other real (baseline)', COUNT(*), COUNT_IF(dcr < 0.05), ROUND(MEDIAN(dcr), 3) FROM d_real;
