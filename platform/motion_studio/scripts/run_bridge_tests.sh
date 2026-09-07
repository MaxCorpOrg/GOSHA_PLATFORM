#!/usr/bin/env bash
set -euo pipefail

studio_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="$(bash "$studio_dir/scripts/ensure_bridge_env.sh")"
cd -- "$studio_dir"
PYTHONPATH="$studio_dir" "$python_bin" -m unittest discover -s tests -p 'test_*.py'
