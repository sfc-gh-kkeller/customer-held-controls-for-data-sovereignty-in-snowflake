"""Option 2: external function + key broker with TOTP step-up (step-up is automated with the enrolled secret).

  cd opt2-keybroker && eval "$(aws configure export-credentials --format env)" && \
      DEMO_USER=<user> pixi run python ../bench/b_opt2.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "opt2-keybroker" / "client"))
os.environ.setdefault("DEMO_USER", "")


def _aws_env() -> bool:
    """Export `aws login` session credentials (the pixi env lacks awscrt). Placeholders if there are none, so the
    demo client imports; then only the optional revoke is skipped."""
    import subprocess
    r = subprocess.run(["aws", "configure", "export-credentials", "--format", "env-no-export"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            k, _, v = line.partition("=")
            os.environ[k] = v
        return True
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "none")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "none")
    return False


AWS_OK = _aws_env()
import demo  # noqa: E402  (reuses stepup / revoke_all from the demo client)
from common import connect, run_query, save, stats, timer  # noqa: E402

AGG = ("SELECT department, COUNT(*) n, ROUND(AVG(salary)) avg_salary "
       "FROM SOVEREIGNTY_DEMO.OPT2_BYOK.EMPLOYEES GROUP BY 1 ORDER BY 1")
# Two classes (salary + pii) decrypted per row: 200 rows x 3 encrypted columns
ROWS = "SELECT employee_id, full_name, iban, salary FROM SOVEREIGNTY_DEMO.OPT2_BYOK.EMPLOYEES ORDER BY 1"
DENIED = AGG  # same query without a grant: broker says no, values are masked


def revoke() -> str:
    """Revoking deletes the grants in DynamoDB (needs AWS credentials). Without them the grants expire by TTL."""
    try:
        demo.revoke_all()
        return "revoked"
    except Exception as e:  # noqa: BLE001
        return f"not revoked ({type(e).__name__}); grants expire after their TTL"


def main():
    pre = revoke()
    c, cur = connect("SOV_ANALYST")
    res = {"denied_no_grant": run_query(cur, DENIED, runs=3)}
    step: list = []
    grants = []
    for cls in ("salary", "pii"):
        with timer(step):
            grants.append(demo.stepup(cls, 15, "Performance benchmark of the key broker, ticket PERF-1"))
    res["stepup_grant_ms"] = stats(step)
    res["stepup_responses"] = [{k: str(v).replace(demo.USER, "<DEMO_USER>") for k, v in g.items()
                               if k != "token"} for g in grants]
    res["aggregate_200"] = run_query(cur, AGG)
    res["rows_200_x3_cols"] = run_query(cur, ROWS, runs=5)
    res["revoke"] = {"before": pre, "after": revoke()}
    save("opt2", res)
    c.close()


if __name__ == "__main__":
    main()
