"""Run a Neighborhood: python -m ENERGY --input ... --output ..."""
import argparse
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main(argv=None):
    parser = argparse.ArgumentParser(description='CETUS Neighborhood energy simulation')
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    # Help and argument validation do not need simulation dependencies.
    from ENERGY import Neighborhood
    from ENERGY.coupled import simulate_coupled
    source = Path(args.input).resolve()
    target = Path(args.output).resolve()
    neighborhood = Neighborhood.loadJSON(source)
    result = simulate_coupled(neighborhood, output_dir=target.parent / (target.stem + '_city'))
    produced = Path(result['building_result_file'])
    if produced != target:
        produced.replace(target)
    neighborhood.building_result_file = str(target)
    neighborhood.saveJSON(target.with_name(target.stem + '_neighborhood.json'))
    print(target)
    return target


if __name__ == '__main__':
    main()
