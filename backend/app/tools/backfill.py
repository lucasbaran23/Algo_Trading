"""Backfill years of real 1-minute bars from Databento into the intraday store.

    cd backend
    python -m app.tools.backfill NG --years 3            # shows the cost, downloads if under DATABENTO_MAX_USD
    python -m app.tools.backfill NG --years 3 --dry-run  # cost only
"""
from __future__ import annotations

import argparse
import json

from ..config import Settings
from ..data import databento


class _MD:                                   # the store path only needs settings
    def __init__(self, settings):
        self.settings = settings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("symbol", nargs="?", default="NG")
    ap.add_argument("--years", type=float, default=2.0)
    ap.add_argument("--max-usd", type=float, default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    res = databento.backfill(_MD(Settings()), a.symbol.upper(), a.years, a.max_usd, a.dry_run)
    print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
