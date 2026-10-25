"""Run native tests in temporary source copies without touching installed engines."""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from test.paths import PROJECT_ROOT, TEST_ROOT

ENGINES = {'energy': PROJECT_ROOT / 'ENERGY' / 'surface_engine',
           'rad': PROJECT_ROOT / 'RAD' / 'transport_engine'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine', nargs='?', choices=('all', *ENGINES), default='all')
    args = parser.parse_args(argv)
    go = shutil.which('go')
    if go is None:
        parser.error('Native source tests require Go; normal CETUS runs do not.')
    version = subprocess.run([go, 'version'], env={**os.environ, 'GOTOOLCHAIN': 'local'},
                             capture_output=True, text=True, check=True)
    match = re.search(r'go(\d+)\.(\d+)', version.stdout)
    if not match or tuple(map(int, match.groups())) < (1, 22):
        parser.error(f'Native tests require Go 1.22+; found {version.stdout.strip()}. No compiler is downloaded automatically.')
    selected = ENGINES if args.engine == 'all' else {args.engine: ENGINES[args.engine]}
    for name, source in selected.items():
        with tempfile.TemporaryDirectory(prefix=f'cetus-test-{name}-') as temporary:
            folder = Path(temporary).resolve()
            assert folder.is_relative_to(Path(tempfile.gettempdir()).resolve())
            for item in source.iterdir():
                if item.suffix == '.go' or item.name in ('go.mod', 'go.sum'):
                    shutil.copy2(item, folder / item.name)
            for item in (TEST_ROOT / 'native' / name).glob('*_test.go'):
                shutil.copy2(item, folder / item.name)
            print(f'Running {name} native tests', flush=True)
            subprocess.run([go, 'test', './...'], cwd=folder, check=True)


if __name__ == '__main__':
    main()
