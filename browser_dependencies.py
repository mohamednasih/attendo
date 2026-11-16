#!/usr/bin/env python3
"""Install isolated dependencies for encrypted Chrome and Brave cookies."""
import argparse
import importlib.metadata
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
DIRECTORY = ROOT / '.browser-deps'
VERSION = '0.20.1'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('install', 'remove'))
    args = parser.parse_args()
    if args.action == 'remove':
        if DIRECTORY.exists():
            shutil.rmtree(DIRECTORY)
        return
    versions = {dist.metadata['Name']: dist.version for dist in importlib.metadata.distributions(path=[str(DIRECTORY)])}
    if versions.get('browser-cookie3') == VERSION:
        return
    subprocess.run([sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check',
                    '--upgrade', '--target', str(DIRECTORY), f'browser-cookie3=={VERSION}'], check=True)


if __name__ == '__main__':
    main()
