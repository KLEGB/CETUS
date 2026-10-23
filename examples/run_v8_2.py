"""Compatibility command alias; use run_v8_3.py for CETUS V8.3."""
import runpy
from pathlib import Path
runpy.run_path(str(Path(__file__).with_name("run_v8_3.py")),run_name="__main__")
