"""Runs the offline tests. No network, no server required.

    .venv/Scripts/python run_tests.py
"""

from __future__ import annotations

import sys

from tests import test_cbb

if __name__ == "__main__":
    failures = 0
    for module in (test_cbb,):
        print(f"\n--- {module.__name__} ---")
        failures += module.main()
    sys.exit(1 if failures else 0)
