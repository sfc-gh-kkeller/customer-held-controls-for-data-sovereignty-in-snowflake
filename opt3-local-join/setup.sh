#!/usr/bin/env bash
# Option 3 Snowflake side: objects + the non-sensitive half of the data.
set -euo pipefail
cd "$(dirname "$0")"
CONN="${SNOW_CONNECTION:-default}"
snow sql -c "$CONN" -f snowflake/10_objects.sql >/dev/null
snow sql -c "$CONN" -q "USE ROLE SOV_SPLIT_ROLE; USE WAREHOUSE SOV_WH; USE SCHEMA SOVEREIGNTY_DEMO.OPT3_SPLIT;
  PUT file://$(cd .. && pwd)/shared/sample_data/employees_public.csv @~/sov3 OVERWRITE = TRUE;
  TRUNCATE TABLE EMPLOYEES_PUBLIC;
  COPY INTO EMPLOYEES_PUBLIC FROM @~/sov3/employees_public.csv.gz FILE_FORMAT = (TYPE = CSV SKIP_HEADER = 1) PURGE = TRUE;"
echo "Option 3 ready: pixi run lab"
