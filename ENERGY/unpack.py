"""Standard-library-only export: python -m ENERGY.unpack source.bin target.json."""
import argparse
from pathlib import Path
import sys

if __package__ in (None, ''):
    # Also support python -S /absolute/path/ENERGY/unpack.py from any directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ENERGY.result_store import export_json
else:
    from .result_store import export_json


def main(argv=None):
    parser = argparse.ArgumentParser(description='Export all CETUS building inputs and results')
    parser.add_argument('input')
    parser.add_argument('output')
    args = parser.parse_args(argv)
    target = export_json(args.input, args.output)
    print(target)
    return target


if __name__ == '__main__':
    main()
