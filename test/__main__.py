"""Run the suite: python -m test [-q ...]."""
import subprocess
import sys
from .paths import TEST_ROOT

raise SystemExit(subprocess.call([sys.executable, '-m', 'pytest', str(TEST_ROOT), *sys.argv[1:]]))
