#!/usr/bin/env python3
"""Option 2 – client-side column-level AES-256-GCM encryption, then load to Snowflake.

Runs on the data owner's side. For each classification it asks AWS KMS for a fresh
256-bit data key (GenerateDataKey), encrypts the sensitive columns locally, stores ONLY
the KMS-wrapped key in DynamoDB, and uploads ciphertext to Snowflake. Plaintext keys
never leave this process (and the Lambda broker), and are never stored in Snowflake.

Envelope per value:  base64( iv[12] || ciphertext || tag[16] )   AAD = "<classification>"

  uv run encrypt_and_load.py --outputs ../infra/outputs.json
"""
import argparse
import base64
import csv
import json
import os
import subprocess
import tempfile
from pathlib import Path

import boto3
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "shared" / "sample_data" / "employees_full.csv"
COLUMN_CLASS = {"full_name": "pii", "national_id": "pii", "iban": "pii",
                "salary": "salary", "bonus": "salary"}
CONN = os.getenv("SNOW_CONNECTION", "default")


def encrypt(key: bytes, value: str, aad: str) -> str:
    iv = os.urandom(12)
    ct_tag = AESGCM(key).encrypt(iv, value.encode(), aad.encode())
    return base64.b64encode(iv + ct_tag).decode()


def get_or_create_key(kms, keys_table, kms_arn: str, cls: str, rotate: bool) -> bytes:
    item = None if rotate else keys_table.get_item(Key={"classification": cls}).get("Item")
    if item:
        return kms.decrypt(CiphertextBlob=base64.b64decode(item["wrapped_key"]),
                           EncryptionContext={"classification": cls})["Plaintext"]
    dk = kms.generate_data_key(KeyId=kms_arn, KeySpec="AES_256",
                               EncryptionContext={"classification": cls})
    keys_table.put_item(Item={"classification": cls,
                              "wrapped_key": base64.b64encode(dk["CiphertextBlob"]).decode(),
                              "kms_key_arn": kms_arn})
    return dk["Plaintext"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outputs", default=str(Path(__file__).parent.parent / "infra" / "outputs.json"))
    ap.add_argument("--rotate", action="store_true", help="generate new data keys")
    a = ap.parse_args()
    out = json.load(open(a.outputs))["SovereigntyKeyBroker"]

    region = "eu-central-1"
    kms = boto3.client("kms", region_name=region)
    keys_table = boto3.resource("dynamodb", region_name=region).Table(out["KeysTable"])
    keys = {c: get_or_create_key(kms, keys_table, out["KmsKeyArn"], c, a.rotate)
            for c in sorted(set(COLUMN_CLASS.values()))}

    rows = list(csv.DictReader(open(SRC)))
    with tempfile.TemporaryDirectory() as td:
        enc_path = Path(td) / "employees_enc.csv"
        with open(enc_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                for col, cls in COLUMN_CLASS.items():
                    r[col] = encrypt(keys[cls], r[col], cls)
                w.writerow(r)
        print(f"encrypted {len(rows)} rows, columns {list(COLUMN_CLASS)}")
        sql = f"""
        USE ROLE SOV_BYOK_ADMIN; USE WAREHOUSE SOV_WH; USE SCHEMA SOVEREIGNTY_DEMO.OPT2_BYOK;
        PUT file://{enc_path} @~/sov_byok OVERWRITE=TRUE AUTO_COMPRESS=TRUE;
        TRUNCATE TABLE EMPLOYEES_ENC;
        COPY INTO EMPLOYEES_ENC FROM @~/sov_byok/employees_enc.csv.gz
          FILE_FORMAT=(TYPE=CSV SKIP_HEADER=1 FIELD_OPTIONALLY_ENCLOSED_BY='"') PURGE=TRUE;
        SELECT COUNT(*) FROM EMPLOYEES_ENC;
        """
        subprocess.run(["snow", "sql", "-c", CONN, "-q", sql], check=True)
    # wipe plaintext keys from memory as far as Python allows
    keys.clear()


if __name__ == "__main__":
    main()
