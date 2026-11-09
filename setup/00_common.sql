-- ============================================================================
-- Shared objects used by every option. Run once, before any option's setup:
--   snow sql -c $SNOW_CONNECTION -f setup/00_common.sql
-- ============================================================================
USE ROLE ACCOUNTADMIN;
CREATE DATABASE IF NOT EXISTS SOVEREIGNTY_DEMO
  COMMENT = 'Customer-held controls for data sovereignty - demo objects';
CREATE WAREHOUSE IF NOT EXISTS SOV_WH
  WAREHOUSE_SIZE = XSMALL AUTO_SUSPEND = 60 AUTO_RESUME = TRUE INITIALLY_SUSPENDED = TRUE;
