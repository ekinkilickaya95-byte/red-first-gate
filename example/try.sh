#!/bin/sh
# Five-minute try: runs the whole gate on the synthetic example. Expected: every check GREEN, exit 0.
set -eu
cd "$(dirname "$0")"
rm -rf work
python3 ../red_first_gate.py all --config gate.json --baseline baseline --candidate candidate --work work
