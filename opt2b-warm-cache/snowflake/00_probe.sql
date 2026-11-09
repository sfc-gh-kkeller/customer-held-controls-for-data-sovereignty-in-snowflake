-- Option 2b probe: does a Python UDF process (and its module-level cache) survive across queries,
-- sessions and roles while the warehouse is warm? No network / no key involved – just process identity.
USE ROLE ACCOUNTADMIN;
CREATE SCHEMA IF NOT EXISTS SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE;
CREATE OR REPLACE FUNCTION SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE.PROCESS_PROBE(tag STRING)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
HANDLER = 'probe'
AS $$
import os, time, uuid
_cache = {}          # stands in for the cached key
_PROCESS_ID = uuid.uuid4().hex[:8]
_LOADED_AT = time.time()
_calls = 0
def probe(tag):
    global _calls
    _calls += 1
    first = tag not in _cache
    if first:
        _cache[tag] = time.time()          # "fetch the key" once per process
    return {"process": _PROCESS_ID, "pid": os.getpid(), "loaded_s_ago": round(time.time() - _LOADED_AT, 1),
            "cache_hit": not first, "cached_s_ago": round(time.time() - _cache[tag], 1), "calls_in_process": _calls}
$$;
GRANT USAGE ON SCHEMA SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE TO ROLE SOV_ANALYST;
GRANT USAGE ON SCHEMA SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE TO ROLE SOV_VIEWER;
GRANT USAGE ON FUNCTION SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE.PROCESS_PROBE(STRING) TO ROLE SOV_ANALYST;
GRANT USAGE ON FUNCTION SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE.PROCESS_PROBE(STRING) TO ROLE SOV_VIEWER;
