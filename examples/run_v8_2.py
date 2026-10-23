"""Compatibility alias; use run_v8_4.py for CETUS V8.4."""
import runpy
from pathlib import Path
runpy.run_path(str(Path(__file__).with_name("run_v8_4.py")),run_name="__main__")
