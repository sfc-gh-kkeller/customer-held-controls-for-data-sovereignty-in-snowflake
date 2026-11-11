"""Homomorphic bonus: Snowflake sums encrypted salaries per department (BFV), only the key holder decrypts.

Compared with the same aggregate on plaintext. Uses the deployed HE schema (100 employees).

  cd bonus-homomorphic && pixi run python ../bench/b_he.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from common import RUNS, save, stats, timer  # noqa: E402
from he_snowflake.config import get_session, load_config  # noqa: E402
from he_snowflake.crypto import decrypt, decrypt_packed_sum  # noqa: E402
from he_snowflake.keys import load_all  # noqa: E402

PER_ROW = ("SELECT e.department, COUNT(*) n, HE_SUM(k.context, e.salary_ct) ct FROM EMPLOYEES_ENC e "
           "JOIN HE_KEYS k ON k.key_id = e.bfv_key_id GROUP BY 1 ORDER BY 1")
PACKED = ("SELECT HE_PACKED_SUM(k.context, k.rotation_keys, p.ct, p.n_values) FROM PACKED_COLUMNS p "
          "JOIN HE_KEYS k ON k.key_id = p.key_id WHERE p.column_name = 'SALARY_USD'")


def main():
    cfg = load_config()
    s = get_session(cfg)
    s.sql("ALTER SESSION SET USE_CACHED_RESULT = FALSE").collect()
    keys = load_all(cfg.key_dir, max_packed_values=cfg.n_employees)
    q, dec, q2, dec2 = [], [], [], []
    for _ in range(RUNS + 1):
        with timer(q):
            rows = s.sql(PER_ROW).collect()
        with timer(dec):
            avg = {r[0]: round(decrypt(keys["bfv"], r[2]) / r[1]) for r in rows}
        with timer(q2):
            blob = s.sql(PACKED).collect()[0][0]
        with timer(dec2):
            total = decrypt_packed_sum(keys["bfv_packed"], blob, cfg.n_employees)
    d = lambda xs: xs[1:]  # noqa: E731
    save("he", {"rows": cfg.n_employees,
                "per_row_ct_sum_by_dept": {"first_ms": round(q[0]), "query": stats(d(q)), "decrypt": stats(d(dec))},
                "packed_simd_total": {"first_ms": round(q2[0]), "query": stats(d(q2)), "decrypt": stats(d(dec2))},
                "sample": list(avg.items())[:2], "packed_total": int(total)})
    s.close()


if __name__ == "__main__":
    main()
