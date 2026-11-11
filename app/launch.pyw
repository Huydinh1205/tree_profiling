"""Starts Tree Profiling without a console window. The Desktop shortcut, 'Tree Profiling.bat' and a
double-clicked .treeprof file all come through here:   pythonw launch.pyw [results.treeprof]"""
import os
import sys

here = os.path.dirname(os.path.abspath(__file__))
os.chdir(here)
sys.path.insert(0, here)

if __name__ == "__main__":
    from treeprof_app.__main__ import main

    sys.exit(main())
