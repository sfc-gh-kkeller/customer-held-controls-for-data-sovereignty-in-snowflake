#!/usr/bin/env python3
"""Option 4 – seed: encrypt PII locally (AES-256-CBC, app wire format) and load to Snowflake.

Wire format per cell (what main.py's SnowflakeDecryptor expects):  base64(ciphertext) || base64(iv16)
The key never touches Snowflake (unlike sql/01_create_test_encrypted_table.sql, which encrypts in-database).
"""
import base64
import csv
import os
import subprocess
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

HERE = Path(__file__).parent
SRC = HERE.parent / "shared" / "sample_data" / "employees_full.csv"
KEY = base64.b64decode((HERE / ".secrets" / "aes_key.b64").read_text().strip())
ENC_COLS = ["full_name", "national_id", "iban", "salary", "bonus"]
CONN = os.getenv("SNOW_CONNECTION", "default")


def enc(value: str) -> str:
    iv = os.urandom(16)
    p = padding.PKCS7(128).padder()
    data = p.update(value.encode()) + p.finalize()
    e = Cipher(algorithms.AES(KEY), modes.CBC(iv)).encryptor()
    return base64.b64encode(e.update(data) + e.finalize()).decode() + base64.b64encode(iv).decode()


rows = list(csv.DictReader(open(SRC)))
with tempfile.TemporaryDirectory() as td:
    out = Path(td) / "employees_cbc.csv"
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        for r in rows:
            w.writerow({k: enc(v) if k in ENC_COLS else v for k, v in r.items()})
    subprocess.run(["snow", "sql", "-c", CONN, "-q", f"""
        USE ROLE SOV_CLIENTDEC_ROLE; USE WAREHOUSE SOV_WH; USE SCHEMA SOVEREIGNTY_DEMO.OPT4_CLIENT_DECRYPT;
        PUT file://{out} @~/sov4 OVERWRITE=TRUE;
        TRUNCATE TABLE EMPLOYEES_ENCRYPTED;
        COPY INTO EMPLOYEES_ENCRYPTED FROM @~/sov4/employees_cbc.csv.gz
          FILE_FORMAT=(TYPE=CSV SKIP_HEADER=1 FIELD_OPTIONALLY_ENCLOSED_BY='"') PURGE=TRUE;
        SELECT COUNT(*) AS loaded FROM EMPLOYEES_ENCRYPTED;"""], check=True)
print(f"encrypted {len(rows)} rows locally (columns {ENC_COLS}) and loaded ciphertext")
