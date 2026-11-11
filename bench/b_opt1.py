"""Option 1: on-demand tunnel. Snowflake joins its public table with on-prem rows read live through the tunnel.

Starts the tunnel itself (TTL 30 min, audited) and closes it at the end.

  cd opt1-tunnel && pixi run python ../bench/b_opt1.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import connect, run_query, save, stats, timer  # noqa: E402

AGENT = Path(__file__).resolve().parents[1] / "opt1-tunnel" / "agent"
AGG = ("SELECT p.department, COUNT(*) n, ROUND(AVG(s.salary)) avg_salary "
       "FROM SOVEREIGNTY_DEMO.OPT1_TUNNEL.EMPLOYEES_PUBLIC p "
       "JOIN SOVEREIGNTY_DEMO.OPT1_TUNNEL.EMPLOYEES_SENSITIVE_LIVE s USING (employee_id) GROUP BY 1 ORDER BY 1")
POINT = ("SELECT SOVEREIGNTY_DEMO.OPT1_TUNNEL.QUERY_ONPREM("
         "'SELECT salary FROM demo.demo.employees_sensitive WHERE employee_id = 42')")
EXTRACT = "SELECT COUNT(*), SUM(salary) FROM SOVEREIGNTY_DEMO.OPT1_TUNNEL.EMPLOYEES_SENSITIVE_LIVE"


LOG = Path(__file__).parent / "results" / "opt1_agent.log"


def answered(cur) -> bool:
    """QUERY_ONPREM returns the agent's error as a VARIANT string when the tunnel is closed (no SQL error),
    so only a list of rows counts as an answer."""
    v = cur.fetchone()[0]
    return isinstance(json.loads(v) if isinstance(v, str) else v, list)


def tunnel_up() -> subprocess.Popen:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, "tunnel_ctl.py", "down", "--keep"], cwd=AGENT, capture_output=True)
    return subprocess.Popen([sys.executable, "tunnel_ctl.py", "up", "--ttl", "30m", "--reason",
                             "performance benchmark"], cwd=AGENT, stdin=subprocess.DEVNULL,
                            stdout=open(LOG, "w"), stderr=subprocess.STDOUT)


def main():
    res = {}
    t_up: list = []
    with timer(t_up):
        proc = tunnel_up()
        c, cur = connect("ACCOUNTADMIN")
        deadline = time.time() + 300
        while True:                      # first successful query = tunnel ready end to end
            err = ""
            try:
                cur.execute(POINT)
                if answered(cur):
                    break
            except Exception as e:  # noqa: BLE001
                err = str(e)
            if time.time() > deadline or proc.poll() is not None:
                raise SystemExit(f"tunnel not ready: {err} (see {LOG})")
            time.sleep(2)
    res["tunnel_up_to_first_answer_ms"] = stats(t_up)
    try:
        res["point_lookup"] = run_query(cur, POINT)
        res["join_aggregate_200"] = run_query(cur, AGG)
        res["extract_200"] = run_query(cur, EXTRACT)
    finally:
        subprocess.run([sys.executable, "tunnel_ctl.py", "down", "--keep"], cwd=AGENT)
        try:
            proc.wait(60)
        except subprocess.TimeoutExpired:
            proc.kill()
    time.sleep(2)
    t_down: list = []
    with timer(t_down):
        try:
            cur.execute(POINT)
            v = str(cur.fetchone()[0])
            res["after_down_point"] = ("UNEXPECTED: data returned" if v.lstrip().startswith("[")
                                       else f"no data, function returned the error as a value: {v[:90]}")
        except Exception as e:  # noqa: BLE001
            res["after_down_point"] = f"SQL error: {str(e)[:120]}"
    res["after_down_ms"] = stats(t_down)
    cur.execute("SELECT COUNT(*) FROM SOVEREIGNTY_DEMO.OPT1_TUNNEL.EMPLOYEES_SENSITIVE_LIVE")
    res["after_down_view_rows"] = cur.fetchone()[0]
    save("opt1", res)
    c.close()


if __name__ == "__main__":
    main()
