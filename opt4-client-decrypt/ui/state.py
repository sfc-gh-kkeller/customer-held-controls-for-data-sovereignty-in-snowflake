"""Per-session state. Everything holding plaintext or credentials lives in st.session_state (never cache_resource),
so decrypted data is isolated per browser session and disappears with it."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import streamlit as st

from secure_analytics.auth import Identity
from secure_analytics.cache import LocalCache
from secure_analytics.config import AppConfig, load_config
from secure_analytics.connections import Source
from secure_analytics.crypto import Decryptor

APP_DIR = Path(__file__).resolve().parents[1]


@dataclass
class Session:
    cache: LocalCache
    sources: dict[str, Source] = field(default_factory=dict)
    decryptor: Decryptor | None = None
    key_error: str = ""
    last_result: pd.DataFrame | None = None
    last_result_label: str = ""
    audit: list[dict] = field(default_factory=list)
    last_purge: float = field(default_factory=time.time)

    @property
    def primary(self) -> Source | None:
        return next(iter(self.sources.values()), None)


def config() -> AppConfig:
    try:
        secrets = st.secrets.to_dict()
    except Exception:
        secrets = {}
    return load_config(secrets, APP_DIR / "settings.yaml")


def session(cfg: AppConfig) -> Session:
    if "sa" not in st.session_state:
        s = Session(LocalCache(ttl_seconds=int(cfg.settings["cache_ttl_minutes"]) * 60,
                               memory_limit=str(cfg.settings["duckdb_memory_limit"])))
        try:
            s.decryptor = Decryptor.from_b64(cfg.aes_key_b64)
        except ValueError as e:
            s.key_error = str(e)
        st.session_state.sa = s
    s: Session = st.session_state.sa
    if time.time() - s.last_purge > int(cfg.settings["purge_interval_seconds"]):
        for name in s.cache.purge_expired():
            audit("cache_expired", name)
        s.last_purge = time.time()
    return s


def identity() -> Identity:
    return st.session_state.identity


def audit(event: str, detail: str = "") -> None:
    s: Session | None = st.session_state.get("sa")
    ident: Identity | None = st.session_state.get("identity")
    if s is not None:
        s.audit.append({"time": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "user": ident.email if ident else "-", "event": event, "detail": detail[:300]})
        del s.audit[:-500]


def set_result(df: pd.DataFrame, label: str) -> None:
    s: Session = st.session_state.sa
    s.last_result, s.last_result_label = df, label


def require(perm: str) -> None:
    if not identity().can(perm):
        st.warning("Your role does not allow this page.", icon=":material/block:")
        st.stop()


def empty_state(text: str, icon: str, page: str | None = None, label: str = "") -> None:
    with st.container(border=True):
        st.info(text, icon=icon)
        if page:
            st.page_link(page, label=label, icon=":material/arrow_forward:")
