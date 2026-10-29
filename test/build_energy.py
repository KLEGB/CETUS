"""Explicitly build precompiled CES3 engines; simulations never compile code."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform',choices=('windows','linux','all'),default='all')
    parser.add_argument('--go',default=shutil.which('go'))
    parser.add_argument('--moosas-dir',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    for system in (('windows','linux') if args.platform=='all' else (args.platform,)):
        name='CetusSurfaceEnergy'+('.exe' if system=='windows' else '')
        target=root/'ENERGY/bin'/name
        subprocess.run([args.go,'build','-trimpath','-o',str(target),'.'],
            cwd=root/'ENERGY/surface_engine',check=True,
            env={**os.environ,'GOOS':system,'GOARCH':'amd64','CGO_ENABLED':'0'})
        if args.moosas_dir:
            destination=args.moosas_dir.resolve()/'libs/energy'/name
            destination.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(target,destination)
        print(target)


if __name__=='__main__':
    main()
