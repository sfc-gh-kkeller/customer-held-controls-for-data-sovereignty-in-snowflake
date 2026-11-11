"""Baseline (plaintext in Snowflake), Option 2b key fetch, synthetic data. Pure Snowflake, no external parts.

  cd opt2c-device-flow && pixi run python ../bench/b_snowflake.py
"""
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import CONN, ROOT, connect, run_query, save, stats, timer  # noqa: E402

W200 = ("SELECT department, COUNT(*) n, ROUND(AVG(salary)) avg_salary FROM SOVEREIGNTY_DEMO.SYNTH.EMPLOYEES_REAL "
        "WHERE employee_id <= 200 GROUP BY 1 ORDER BY 1")
W5000 = ("SELECT department, COUNT(*) n, ROUND(AVG(salary)) avg_salary FROM SOVEREIGNTY_DEMO.SYNTH.EMPLOYEES_REAL "
         "GROUP BY 1 ORDER BY 1")
SYN5000 = W5000.replace("EMPLOYEES_REAL", "EMPLOYEES_SYNTH")
# Option 2b: every row calls the EAI UDF; the first call per Python process does an HTTPS fetch that stands in
# for the key fetch from the vault, later calls hit the per-process memo
P2B = ("SELECT COUNT(*) n, COUNT(DISTINCT p:process) procs, SUM(IFF(p:cache_hit::BOOLEAN, 0, 1)) fetches FROM "
       "(SELECT SOVEREIGNTY_DEMO.OPT2B_WARM_CACHE.PROCESS_PROBE_EAI('salary') p "
       "FROM SOVEREIGNTY_DEMO.OPT2_BYOK.EMPLOYEES_ENC)")
P2B_NOEAI = P2B.replace("PROCESS_PROBE_EAI", "PROCESS_PROBE")


def main():
    c, cur = connect("ACCOUNTADMIN")
    res = {
        "baseline_200": run_query(cur, W200),
        "baseline_5000": run_query(cur, W5000),
        "synthetic_5000": run_query(cur, SYN5000),
        "opt2b_probe_eai_200": run_query(cur, P2B),
        "opt2b_probe_no_eai_200": run_query(cur, P2B_NOEAI),
    }
    # Synthetic generation (one-off cost of producing the synthetic copy)
    gen: list = []
    with timer(gen):
        r = subprocess.run(["snow", "sql", "-c", CONN, "-f", str(ROOT / "synthetic/02_generate.sql")],
                           capture_output=True, text=True)
    res["synthetic_generate_5000"] = {"wall": stats(gen), "ok": r.returncode == 0}
    save("snowflake", res)
    c.close()


if __name__ == "__main__":
    main()
