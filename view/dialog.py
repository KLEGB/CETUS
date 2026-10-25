"""Run a native picker in a disposable process on its main GUI thread."""
import json
import sys
import tkinter as tk
from tkinter import filedialog


def main():
    kind=sys.argv[1] if len(sys.argv)>1 else 'open'
    root=tk.Tk();root.withdraw();root.attributes('-topmost',True)
    try:
        if kind=='save':path=filedialog.asksaveasfilename(parent=root,title='保存 Neighborhood',defaultextension='.json',filetypes=[('Neighborhood JSON','*.json')])
        else:
            patterns={'shp':'*.shp','weather':'*.epw *.csv','rad':'*.npz','load':'*.json'}
            path=filedialog.askopenfilename(parent=root,title='选择 CETUS 文件',filetypes=[('CETUS',patterns.get(kind,'*.*')),('所有文件','*.*')])
    finally:root.destroy()
    print(json.dumps(dict(path=path),ensure_ascii=False))


if __name__=='__main__':main()
