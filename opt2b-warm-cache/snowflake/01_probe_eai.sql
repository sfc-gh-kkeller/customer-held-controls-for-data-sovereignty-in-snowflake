-- Same probe, but as an External Access UDF (attaching an EAI can change the sandbox lifecycle).
-- Egress allowed only to a harmless public IP-echo host; no key material involved.
USE ROLE ACCOUNTADMIN;
CREATE OR REPLACE NETWORK RULE SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE.PROBE_EGRESS
  MODE = EGRESS TYPE = HOST_PORT VALUE_LIST = ('checkip.amazonaws.com:443');
CREATE OR REPLACE EXTERNAL ACCESS INTEGRATION SOV_OPT2B_PROBE_EAI
  ALLOWED_NETWORK_RULES = (SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE.PROBE_EGRESS) ENABLED = TRUE;
CREATE OR REPLACE FUNCTION SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE.PROCESS_PROBE_EAI(tag STRING)
RETURNS VARIANT
LANGUAGE PYTHON
RUNTIME_VERSION = '3.12'
HANDLER = 'probe'
EXTERNAL_ACCESS_INTEGRATIONS = (SOV_OPT2B_PROBE_EAI)
PACKAGES = ('requests')
AS $$
import os, time, uuid, requests
_cache = {}
_PROCESS_ID = uuid.uuid4().hex[:8]
_LOADED_AT = time.time()
_calls = 0
_fetches = 0
def probe(tag):
    global _calls, _fetches
    _calls += 1
    first = tag not in _cache
    if first:
        _fetches += 1
        requests.get("https://checkip.amazonaws.com", timeout=5)   # stands in for the key fetch
        _cache[tag] = time.time()
    return {"process": _PROCESS_ID, "pid": os.getpid(), "loaded_s_ago": round(time.time() - _LOADED_AT, 1),
            "cache_hit": not first, "cached_s_ago": round(time.time() - _cache[tag], 1),
            "calls_in_process": _calls, "fetches_in_process": _fetches}
$$;
GRANT USAGE ON FUNCTION SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE.PROCESS_PROBE_EAI(STRING) TO ROLE SOV_ANALYST;
GRANT USAGE ON FUNCTION SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE.PROCESS_PROBE_EAI(STRING) TO ROLE SOV_VIEWER;
