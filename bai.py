#!/usr/bin/env python3
"""Run baiworkspace from a clone; no installation or Python packages required."""
import sys

sys.dont_write_bytecode = True
if sys.version_info < (3, 11):
    sys.exit('baiworkspace needs Python 3.11 or newer.')

from bai.cli import main

if __name__ == '__main__':
    sys.exit(main())
