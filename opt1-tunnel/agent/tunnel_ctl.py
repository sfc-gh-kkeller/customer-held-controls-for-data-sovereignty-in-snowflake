#!/usr/bin/env python3
"""tunnel_ctl – the data owner's switch for the sovereignty tunnel.

    python tunnel_ctl.py up --ttl 15m --reason "Q3 payroll audit, ticket HR-123"
    python tunnel_ctl.py status
    python tunnel_ctl.py down

* `up` starts onpremise_agent.py as a child process and kills it after the TTL,
  no matter what. Without an explicit TTL the tunnel never opens.
* Every open/close is appended to tunnel_audit.log (owned by the on-prem side).
* Optional --suspend-service also suspends the SPCS service on close (via snow CLI),
  so even the Snowflake-side endpoint disappears.
"""
import argparse
import datetime as dt
import getpass
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
PIDFILE = HERE / ".tunnel.pid"
AUDIT = HERE / "tunnel_audit.log"
MAX_TTL_S = 4 * 3600
SERVICE = "SOVEREIGNTY_DEMO.OPT1_TUNNEL.TUNNEL_SERVICE"
POOL = os.getenv("TUNNEL_POOL", "SOV_TUNNEL_POOL")
READY_TIMEOUT_S = 600


def audit(event: str, **kw):
    rec = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "event": event,
           "operator": getpass.getuser(), **kw}
    with AUDIT.open("a") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"[audit] {rec}")


def parse_ttl(s: str) -> int:
    m = re.fullmatch(r"(\d+)([smh])", s)
    if not m:
        raise SystemExit("TTL must look like 90s, 15m or 2h")
    secs = int(m[1]) * {"s": 1, "m": 60, "h": 3600}[m[2]]
    if secs > MAX_TTL_S:
        raise SystemExit(f"TTL capped at {MAX_TTL_S // 3600}h by policy")
    return secs


def snow(sql: str):
    conn = os.getenv("SNOW_CONNECTION", "default")
    subprocess.run(["snow", "sql", "-c", conn, "-q", sql], check=False,
                   stdout=subprocess.DEVNULL)


def snow_rows(sql: str) -> list[dict]:
    conn = os.getenv("SNOW_CONNECTION", "default")
    r = subprocess.run(["snow", "sql", "-c", conn, "--format", "json", "-q", sql],
                       capture_output=True, text=True, stdin=subprocess.DEVNULL)
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"snow sql failed: {(r.stderr or r.stdout).strip()[:300]}")
    return data[-1] if data and isinstance(data[0], list) else data


def pool_state() -> str:
    rows = snow_rows(f"SHOW COMPUTE POOLS LIKE '{POOL}'")
    return rows[0]["state"] if rows else "MISSING"


def service_status() -> str:
    db, schema, name = SERVICE.split(".")
    rows = snow_rows(f"SHOW SERVICES LIKE '{name}' IN SCHEMA {db}.{schema}")
    return rows[0]["status"] if rows else "MISSING"


def ensure_snowflake_side_up():
    """Resume compute pool + service if suspended, and wait until the service is RUNNING."""
    ps = pool_state()
    print(f"compute pool {POOL}: {ps}")
    if ps == "MISSING":
        raise SystemExit("compute pool not found – run `pixi run deploy` first")
    if ps == "SUSPENDED":
        snow(f"ALTER COMPUTE POOL {POOL} RESUME")
        audit("pool_resumed", pool=POOL)
    st = service_status()
    print(f"service {SERVICE}: {st}")
    if st == "MISSING":
        raise SystemExit("tunnel service not found – run `pixi run deploy` first")
    if st in ("SUSPENDED", "SUSPENDING"):
        snow(f"ALTER SERVICE {SERVICE} RESUME")
        audit("service_resumed", service=SERVICE)
    deadline = time.time() + READY_TIMEOUT_S
    while (st := service_status()) != "RUNNING":
        if time.time() > deadline:
            raise SystemExit(f"service not RUNNING after {READY_TIMEOUT_S}s (status {st})")
        print(f"  waiting for service ({st}, pool {pool_state()}) …")
        time.sleep(15)
    print("Snowflake side ready.")


def suspend_snowflake_side():
    snow(f"ALTER SERVICE {SERVICE} SUSPEND")
    audit("service_suspended", service=SERVICE)
    snow(f"ALTER COMPUTE POOL {POOL} SUSPEND")
    audit("pool_suspended", pool=POOL)


def ask_suspend() -> bool:
    if not sys.stdin.isatty():
        return False
    ans = input(f"Also suspend the Snowflake tunnel container + compute pool ({SERVICE})? [y/N] ")
    return ans.strip().lower() in ("y", "yes")


def cmd_up(a):
    if PIDFILE.exists():
        raise SystemExit("tunnel already up – run `down` first")
    ttl = parse_ttl(a.ttl)
    if not a.skip_snowflake:
        ensure_snowflake_side_up()
    env = dict(os.environ)
    for fname, var in ((".handshake_secret", "HANDSHAKE_SECRET"), (".pat", "SNOWFLAKE_PAT")):
        f = HERE / fname
        if f.exists():
            env[var] = f.read_text().strip()
    proc = subprocess.Popen([sys.executable, "onpremise_agent.py"], cwd=HERE, env=env)
    PIDFILE.write_text(str(proc.pid))
    deadline = time.time() + ttl
    stopped_by_down = []

    def _on_term(*_):
        stopped_by_down.append(True)
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _on_term)
    audit("tunnel_up", ttl_s=ttl, reason=a.reason, pid=proc.pid)
    reason = "ttl_expired"
    try:
        while time.time() < deadline:
            if proc.poll() is not None:
                reason = f"agent_exited({proc.returncode})"
                break
            time.sleep(1)
    except KeyboardInterrupt:
        reason = "operator_down" if stopped_by_down else "operator_interrupt"
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        PIDFILE.unlink(missing_ok=True)
        audit("tunnel_down", reason=reason)
        if a.suspend_service or (reason != "operator_down" and ask_suspend()):
            suspend_snowflake_side()


def cmd_down(a):
    if PIDFILE.exists():
        pid = int(PIDFILE.read_text())
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        PIDFILE.unlink(missing_ok=True)
        audit("tunnel_down", reason="operator_down", pid=pid)
    else:
        print("tunnel agent is down")
    if a.suspend or (a.suspend is None and ask_suspend()):
        suspend_snowflake_side()
    else:
        print(f"Snowflake service left as is ({service_status()}).")


def cmd_status(_):
    print("agent:  " + ("UP (pid %s)" % PIDFILE.read_text() if PIDFILE.exists() else "DOWN"))
    print(f"pool:   {pool_state()}")
    print(f"service:{service_status()}")


def main():
    p = argparse.ArgumentParser()
    sp = p.add_subparsers(dest="cmd", required=True)
    up = sp.add_parser("up")
    up.add_argument("--ttl", required=True)
    up.add_argument("--reason", required=True)
    up.add_argument("--suspend-service", action="store_true",
                    help="suspend service + pool on close without asking")
    up.add_argument("--skip-snowflake", action="store_true",
                    help="don't check/resume the compute pool and service")
    down = sp.add_parser("down")
    g = down.add_mutually_exclusive_group()
    g.add_argument("--suspend", dest="suspend", action="store_true", default=None)
    g.add_argument("--keep", dest="suspend", action="store_false")
    sp.add_parser("status")
    a = p.parse_args()
    {"up": cmd_up, "down": cmd_down, "status": cmd_status}[a.cmd](a)


if __name__ == "__main__":
    main()
