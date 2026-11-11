-- Option 1b, headless: package notebooks/tunnel_direct_read.ipynb as a Code Bundle and run it with
-- EXECUTE CODE BUNDLE (no Snowsight needed). Used by bench/run_all.py; the tunnel must be up.
--   snow sql -c $SNOW_CONNECTION -f opt1-tunnel/snowflake/21_code_bundle.sql -D "root=$PWD"
USE ROLE ACCOUNTADMIN;
GRANT CREATE CODE BUNDLE ON SCHEMA SOVEREIGNTY_DEMO.OPT1_TUNNEL TO ROLE SOV_TUNNEL_ROLE;

USE ROLE SOV_TUNNEL_ROLE;
USE SCHEMA SOVEREIGNTY_DEMO.OPT1_TUNNEL;
CREATE STAGE IF NOT EXISTS BUNDLE_STAGE;
PUT 'file://<% root %>/opt1-tunnel/notebooks/tunnel_direct_read.ipynb' @BUNDLE_STAGE AUTO_COMPRESS = FALSE OVERWRITE = TRUE;
PUT 'file://<% root %>/opt1-tunnel/bundle/code_bundle.yml' @BUNDLE_STAGE AUTO_COMPRESS = FALSE OVERWRITE = TRUE;
CREATE OR REPLACE CODE BUNDLE TUNNEL_DIRECT_READ_BUNDLE FROM '@SOVEREIGNTY_DEMO.OPT1_TUNNEL.BUNDLE_STAGE'
  COMMENT = 'Option 1b notebook (DuckDB reads through the tunnel), headless';
