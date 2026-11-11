"""Option 1b: notebook on SPCS with DuckDB reading through the tunnel, run headless as a Code Bundle.

Opens the tunnel, runs EXECUTE CODE BUNDLE (deploy it first with opt1-tunnel/snowflake/21_code_bundle.sql), reads
the timings the notebook's last cell wrote to OPT1_TUNNEL.BENCH_1B (timings only, no data), closes the tunnel.

  cd opt1-tunnel && pixi run python ../bench/b_opt1b.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from b_opt1 import AGENT, POINT, answered, tunnel_up  # noqa: E402
from common import connect, save, stats, timer  # noqa: E402


def main():
    proc = tunnel_up()
    c, cur = connect("SOV_TUNNEL_ROLE")
    try:
        deadline = time.time() + 300
        while True:
            try:
                cur.execute(POINT)
                if answered(cur):
                    break
            except Exception:  # noqa: BLE001
                pass
            if time.time() > deadline:
                raise SystemExit("tunnel not ready")
            time.sleep(2)
        run: list = []
        with timer(run):
            cur.execute("EXECUTE CODE BUNDLE SOVEREIGNTY_DEMO.OPT1_TUNNEL.TUNNEL_DIRECT_READ_BUNDLE "
                        "ENTRYPOINT = 'tunnel_direct_read.ipynb'")
            cur.fetchall()
        cur.execute("SELECT result FROM SOVEREIGNTY_DEMO.OPT1_TUNNEL.BENCH_1B ORDER BY ts DESC LIMIT 1")
        inside = json.loads(cur.fetchone()[0])
    finally:
        subprocess.run([sys.executable, "tunnel_ctl.py", "down", "--keep"], cwd=AGENT, capture_output=True)
        try:
            proc.wait(60)
        except subprocess.TimeoutExpired:
            proc.kill()
    save("opt1b", {"headless_run_incl_pool_start_ms": stats(run), "inside_notebook_ms": inside,
                   "note": "inside_notebook = DuckDB in the notebook container, through the tunnel"})
    c.close()


if __name__ == "__main__":
    main()
