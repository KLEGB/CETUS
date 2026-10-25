"""Isolated simulation process: clean cancellation and file-backed logs."""
import sys
import traceback
from pathlib import Path
from ENERGY import Neighborhood
from .simulation import run_comparison
from .model import write_json


def main():
    folder=Path(sys.argv[1]).resolve()
    try:
        n=Neighborhood.loadJSON(folder/'input.json')
        run_comparison(n,folder,report=lambda msg:print(msg,flush=True))
        write_json(folder/'status.json',dict(status='complete'))
    except Exception as exc:
        traceback.print_exc()
        write_json(folder/'status.json',dict(status='failed',error=str(exc)))
        raise SystemExit(1)


if __name__=='__main__':main()
