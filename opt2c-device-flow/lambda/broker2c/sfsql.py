"""Minimal Snowflake SQL API client (key-pair JWT) used by the key broker for the
query_id call-back. Avoids bundling the full snowflake-connector into Lambda."""
import base64
import hashlib
import json
import time
import urllib.error
import urllib.request

import jwt  # PyJWT
from cryptography.hazmat.primitives import serialization


class SnowflakeSQL:
    def __init__(self, account: str, user: str, private_key_pem: str, role: str, warehouse: str):
        # account identifier for JWT: uppercase, org-account with '-' (no region suffix)
        self.account = account.upper()
        self.host = f"{account.lower()}.snowflakecomputing.com"
        self.user = user.upper()
        self.role = role
        self.warehouse = warehouse
        self._key = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        pub = self._key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self._fp = "SHA256:" + base64.b64encode(hashlib.sha256(pub).digest()).decode()
        self._jwt, self._jwt_exp = None, 0

    def _token(self) -> str:
        now = int(time.time())
        if not self._jwt or now > self._jwt_exp - 60:
            qual = f"{self.account}.{self.user}"
            self._jwt_exp = now + 3600
            self._jwt = jwt.encode({"iss": f"{qual}.{self._fp}", "sub": qual,
                                    "iat": now, "exp": self._jwt_exp},
                                   self._key, algorithm="RS256")
        return self._jwt

    def query(self, sql: str, bindings: list[str], timeout: int = 20) -> list[list]:
        body = {"statement": sql, "timeout": timeout, "role": self.role,
                "warehouse": self.warehouse,
                "bindings": {str(i + 1): {"type": "TEXT", "value": v} for i, v in enumerate(bindings)}}
        req = urllib.request.Request(
            f"https://{self.host}/api/v2/statements", data=json.dumps(body).encode(), method="POST",
            headers={"Authorization": f"Bearer {self._token()}",
                     "X-Snowflake-Authorization-Token-Type": "KEYPAIR_JWT",
                     "Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": "sovereignty-keybroker/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout + 5) as r:
                return json.loads(r.read()).get("data") or []
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"SQL API {e.code}: {e.read()[:300]!r}") from None
