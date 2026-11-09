"""Configuration.

Two sources, strictly separated:
* ``.streamlit/secrets.toml`` – credentials, AES key, connection defaults, roles. Read-only for the app.
* ``settings.yaml`` – non-secret, admin-editable runtime settings. Only keys listed in DEFAULT_SETTINGS
  are ever written, so a secret can never end up in this file.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

DEFAULT_SETTINGS: dict[str, Any] = {
    "cache_ttl_minutes": 60,          # decrypted data lives at most this long in the local cache
    "purge_interval_seconds": 60,
    "max_live_rows": 50_000,          # cap on rows pulled from a live source per table
    "csv_export_enabled": False,
    "duckdb_memory_limit": "2GB",
    "auto_decrypt_on_connect": True,
}

SECRET_KEYS = {"pat", "password", "aes_key", "client_secret", "cookie_secret", "private_key", "token"}


@dataclass
class SourceDefaults:
    connection_name: str = "snowflake"
    account: str = ""
    user: str = ""
    pat: str = ""
    warehouse: str = ""
    database: str = ""
    schema: str = "PUBLIC"
    role: str = ""


@dataclass
class EncryptedTable:
    table_name: str
    encrypted_columns: list[str]


@dataclass
class AppConfig:
    settings: dict[str, Any]
    snowflake: SourceDefaults
    aes_key_b64: str
    encrypted_tables: list[EncryptedTable]
    roles: dict[str, list[str]]           # role -> list of emails / "@domain" entries
    dev_login: bool
    oidc_configured: bool
    settings_path: Path = field(default=Path("settings.yaml"))

    def save_settings(self) -> None:
        """Persist only whitelisted, non-secret settings."""
        clean = {k: self.settings[k] for k in DEFAULT_SETTINGS if k in self.settings}
        self.settings_path.write_text(yaml.safe_dump(clean, sort_keys=True))


def _as_dict(obj: Any) -> dict:
    if obj is None:
        return {}
    if isinstance(obj, Mapping):
        return {k: _as_dict(v) if isinstance(v, Mapping) else v for k, v in obj.items()}
    return dict(obj)


def load_settings(path: Path) -> dict[str, Any]:
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    if path.exists():
        loaded = yaml.safe_load(path.read_text()) or {}
        settings.update({k: v for k, v in loaded.items() if k in DEFAULT_SETTINGS})
    return settings


def load_config(secrets: Mapping[str, Any], settings_path: Path = Path("settings.yaml")) -> AppConfig:
    s = _as_dict(secrets)
    sf = s.get("snowflake", {})
    enc = s.get("encryption", {})
    app = s.get("app", {})
    roles = s.get("roles", {}) or {}
    settings = load_settings(settings_path)
    if "auto_decrypt_on_connect" in enc and not (settings_path.exists() and
                                                "auto_decrypt_on_connect" in (yaml.safe_load(settings_path.read_text()) or {})):
        settings["auto_decrypt_on_connect"] = bool(enc["auto_decrypt_on_connect"])
    return AppConfig(
        settings=settings,
        snowflake=SourceDefaults(**{k: str(v) for k, v in sf.items() if k in SourceDefaults.__annotations__}),
        aes_key_b64=str(enc.get("aes_key", "")),
        encrypted_tables=[EncryptedTable(t["table_name"], list(t["encrypted_columns"]))
                          for t in s.get("encrypted_tables", [])],
        roles={r: [str(x).lower() for x in v] for r, v in roles.items()},
        dev_login=bool(app.get("dev_login", False)),
        oidc_configured=bool(s.get("auth", {}).get("client_id") or
                             any(isinstance(v, dict) and v.get("client_id") for v in s.get("auth", {}).values())),
        settings_path=settings_path,
    )


def redact(obj: Any) -> Any:
    """Deep-copy with secret values replaced – for any config shown in the UI or logs."""
    if isinstance(obj, Mapping):
        return {k: ("•••" if k.lower() in SECRET_KEYS and v else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj
