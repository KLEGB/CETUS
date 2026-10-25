"""EnergyUnpack: export every building and its complete input snapshot."""
import argparse
from result_store import export_json
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('input');p.add_argument('output');a=p.parse_args()
 print(export_json(a.input,a.output))
