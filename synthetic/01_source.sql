-- ============================================================================
-- Synthetic data, step 1: the "real" sensitive source
--   5,000 generated employees with known structure, so the scorecard can check what survives:
--     * salary driven by level, department and country
--     * bonus tied to salary
--     * attrition falling with tenure
--     * a rare department (Legal, ~12 people)
--     * a three-way effect: Sales x DE x level 5 earns a premium
-- ============================================================================
USE ROLE ACCOUNTADMIN;
USE WAREHOUSE SOV_WH;
CREATE SCHEMA IF NOT EXISTS SOVEREIGNTY_DEMO.SYNTH;
USE SCHEMA SOVEREIGNTY_DEMO.SYNTH;

CREATE OR REPLACE TABLE EMPLOYEES_REAL AS
WITH base AS (
  SELECT SEQ4() + 1 AS employee_id,
         UNIFORM(0, 999, RANDOM(11)) AS r_dept, UNIFORM(0, 999, RANDOM(12)) AS r_cty,
         UNIFORM(1, 5, RANDOM(13))   AS level,  UNIFORM(0, 15, RANDOM(14)) AS tenure_years,
         NORMAL(0, 1, RANDOM(15))    AS z1,     NORMAL(0, 1, RANDOM(16))   AS z2,
         UNIFORM(0::FLOAT, 1::FLOAT, RANDOM(17)) AS u
  FROM TABLE(GENERATOR(ROWCOUNT => 5000))
), dims AS (
  SELECT *,
    CASE WHEN r_dept < 300 THEN 'Engineering' WHEN r_dept < 550 THEN 'Sales'
         WHEN r_dept < 750 THEN 'Operations' WHEN r_dept < 900 THEN 'Finance'
         WHEN r_dept < 997 THEN 'HR' ELSE 'Legal' END AS department,
    CASE WHEN r_cty < 400 THEN 'DE' WHEN r_cty < 700 THEN 'FR'
         WHEN r_cty < 900 THEN 'NL' ELSE 'PL' END AS country
  FROM base
)
SELECT employee_id, department, country, level, tenure_years,
       ROUND(GREATEST(25000,
         40000 + level * 14000
         + CASE department WHEN 'Engineering' THEN 12000 WHEN 'Legal' THEN 30000 WHEN 'Finance' THEN 6000 ELSE 0 END
         + CASE country WHEN 'DE' THEN 8000 WHEN 'PL' THEN -15000 ELSE 0 END
         + IFF(department = 'Sales' AND country = 'DE' AND level = 5, 45000, 0)
         + z1 * 6000))                                           AS salary,
       ROUND(GREATEST(0, (0.08 + 0.02 * level) * (40000 + level * 14000) + z2 * 1500)) AS bonus,
       u < 0.30 - 0.018 * tenure_years                           AS left_company
FROM dims;

SELECT COUNT(*) AS employees, COUNT_IF(department = 'Legal') AS legal_rare FROM EMPLOYEES_REAL;
