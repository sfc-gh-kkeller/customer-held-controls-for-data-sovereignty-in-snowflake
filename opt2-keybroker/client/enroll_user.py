#!/usr/bin/env python3
"""Admin: enroll a Snowflake user for TOTP step-up (prints a QR code for an authenticator app).

  uv run enroll_user.py <SNOWFLAKE_USER> --classes salary,pii
  (user defaults to $DEMO_USER)
"""
import argparse
import json
import os
from pathlib import Path

import boto3
import pyotp
import qrcode

ap = argparse.ArgumentParser()
ap.add_argument("user", nargs="?", default=os.getenv("DEMO_USER", ""),
                help="Snowflake user name (default: $DEMO_USER)")
ap.add_argument("--classes", default="salary,pii")
ap.add_argument("--outputs", default=str(Path(__file__).parent.parent / "infra" / "outputs.json"))
ap.add_argument("--show", action="store_true", help="re-print the QR for the saved secret, no rotation")
a = ap.parse_args()
if not a.user:
    ap.error("give the Snowflake user name, or set DEMO_USER")

secret_file = Path(__file__).parent.parent / ".secrets" / f"totp_{a.user.upper()}.txt"
if a.show:
    secret = secret_file.read_text().strip()
else:
    out = json.load(open(a.outputs))["SovereigntyKeyBroker"]
    secret = pyotp.random_base32()
    boto3.resource("dynamodb", region_name="eu-central-1").Table(out["UsersTable"]).put_item(Item={
        "user": a.user.upper(), "totp_secret": secret,
        "allowed_classifications": a.classes.split(",")})
uri = pyotp.TOTP(secret).provisioning_uri(name=a.user.upper(), issuer_name="Sovereignty Key Broker")
qr = qrcode.QRCode(border=1)
qr.add_data(uri)
qr.print_ascii(invert=True)
secret_file.parent.mkdir(exist_ok=True)
secret_file.write_text(secret)
secret_file.chmod(0o600)
print(f"Enrolled {a.user.upper()} for {a.classes}. Scan the QR code; secret also saved to {secret_file} (demo only).")
