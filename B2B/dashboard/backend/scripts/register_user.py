"""Register / manage dashboard users.

Usage:
    uv run python -m scripts.register_user create <username>
    uv run python -m scripts.register_user set-password <username>
    uv run python -m scripts.register_user delete <username>
    uv run python -m scripts.register_user list

Run from the `dashboard/backend/` directory so `app.*` imports resolve.
"""
from __future__ import annotations

import getpass
import sys
from pathlib import Path

# Make `app.*` importable when invoked as `python scripts/register_user.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import users  # noqa: E402


def _read_password(prompt: str = "Password: ") -> str:
    pw = getpass.getpass(prompt)
    confirm = getpass.getpass("Confirm password: ")
    if pw != confirm:
        sys.exit("Passwords do not match.")
    return pw


def cmd_create(username: str) -> None:
    pw = _read_password()
    try:
        u = users.create_user(username, pw)
    except ValueError as e:
        sys.exit(f"Error: {e}")
    print(f"Created user id={u.id} username={u.username}")


def cmd_set_password(username: str) -> None:
    pw = _read_password()
    try:
        ok = users.set_password(username, pw)
    except ValueError as e:
        sys.exit(f"Error: {e}")
    if not ok:
        sys.exit(f"No user named '{username}'.")
    print(f"Password updated for {username}.")


def cmd_delete(username: str) -> None:
    if not users.delete_user(username):
        sys.exit(f"No user named '{username}'.")
    print(f"Deleted {username}.")


def cmd_list() -> None:
    rows = users.list_users()
    if not rows:
        print("(no users)")
        return
    width = max(len(u.username) for u in rows)
    for u in rows:
        last = u.last_login_at or "never"
        print(f"{u.id:>3}  {u.username.ljust(width)}  created={u.created_at}  last_login={last}")


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        sys.exit(__doc__)
    cmd, *rest = argv[1:]
    if cmd == "create" and len(rest) == 1:
        cmd_create(rest[0])
    elif cmd == "set-password" and len(rest) == 1:
        cmd_set_password(rest[0])
    elif cmd == "delete" and len(rest) == 1:
        cmd_delete(rest[0])
    elif cmd == "list" and not rest:
        cmd_list()
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
