"""Test paths independent of the current working directory."""
from pathlib import Path

TEST_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = TEST_ROOT.parent
DATA = TEST_ROOT / 'data'
VALIDATION = TEST_ROOT / 'validation'
OUTPUT = TEST_ROOT / 'output'
ENERGY_CONFIGS = PROJECT_ROOT / 'ENERGY' / 'configs'
