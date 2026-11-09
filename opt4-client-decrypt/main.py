"""Backwards-compatible entry point. The app now lives in app.py (multipage) + secure_analytics/ (core)."""
import runpy
from pathlib import Path

runpy.run_path(str(Path(__file__).with_name("app.py")), run_name="__main__")
