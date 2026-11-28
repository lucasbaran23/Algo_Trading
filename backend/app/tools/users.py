"""Manage hosted-mode users from the command line.

    cd backend
    python -m app.tools.users list
    python -m app.tools.users create you@example.com --tier ultimate     # asks for a password
    python -m app.tools.users set-tier someone@example.com premium      # comp a beta tester, fix a billing issue
"""
from __future__ import annotations

import argparse
import getpass
import time

from ..accounts import store, tiers


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    c = sub.add_parser("create")
    c.add_argument("email")
    c.add_argument("--tier", default="free", choices=tiers.TIERS)
    s = sub.add_parser("set-tier")
    s.add_argument("email")
    s.add_argument("tier", choices=tiers.TIERS)
    a = ap.parse_args()
    if a.cmd == "list":
        for r in store.db().execute("SELECT id, email, tier, is_admin, created_at FROM users ORDER BY id"):
            print(f"{r['id']:>5}  {r['tier']:<9} {'admin ' if r['is_admin'] else '      '}{r['email']}  "
                  f"(joined {time.strftime('%Y-%m-%d', time.localtime(r['created_at']))})")
        print("totals:", store.count_users())
    elif a.cmd == "create":
        pw = getpass.getpass("Password (8+ characters): ")
        u = store.create_user(a.email, pw, a.tier)
        print("created", u.public())
    else:
        u = store.get_user_by_email(a.email)
        if u is None:
            raise SystemExit("no such user")
        store.set_tier(u.id, a.tier)
        print(f"{u.email}: {u.tier} -> {a.tier}")


if __name__ == "__main__":
    main()
