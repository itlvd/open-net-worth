"""`onw reset-password` — set a new password from the machine the book lives on.

The way back in when the old password is forgotten and the desktop app is not the front door:
only someone who can run a command here, as the user that owns the data folder, gets it. It
writes through ``AuthStore`` into the same database the server reads (the working folder is
resolved exactly as ``onw start`` resolves it), so it works whether or not the server is
running — the running server sees the new hash on its next request, and the rotated session
key signs every browser out.
"""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path


def _read_password(from_stdin: bool) -> str | None:
    if from_stdin:
        # Bytes, not the console's code page: a Windows pipe decodes text with a legacy
        # codec, and a PowerShell pipe prepends a BOM. Same bytes -> same hash as the browser.
        # Only the line terminator and that BOM are removed; any other whitespace is part of
        # the password, exactly as it is when typed at the login screen.
        raw = sys.stdin.buffer.readline().rstrip(b"\r\n")
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            print("The password on stdin is not valid UTF-8.", file=sys.stderr)
            return None
    if not sys.stdin.isatty():
        print(
            "No terminal to prompt on. Run this in a terminal, or pass --password-stdin.",
            file=sys.stderr,
        )
        return None
    first = getpass.getpass("New password: ")
    if getpass.getpass("Confirm new password: ") != first:
        print("The two passwords do not match.", file=sys.stderr)
        return None
    return first


def _no_book(data_dir: Path) -> int:
    print(
        f"No Open Net Worth book found in {data_dir}. Start the app once to create it, or "
        "point this at the right folder with ONW_ROOT.",
        file=sys.stderr,
    )
    return 1


def run_reset_password(*, password_stdin: bool) -> int:
    from backend.core.security import MIN_PASSWORD_LENGTH, hash_password

    password = _read_password(password_stdin)
    if password is None:
        return 1
    # The same rules the Settings endpoint applies, so one password means one thing: kept exactly
    # as given, never empty or only whitespace, at least MIN_PASSWORD_LENGTH characters.
    if not password.strip():
        print("The password cannot be empty or only spaces.", file=sys.stderr)
        return 1
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"The password must be at least {MIN_PASSWORD_LENGTH} characters.", file=sys.stderr)
        return 1

    from backend.core.config import get_settings

    data_dir = get_settings().data_dir
    # Importing `core.paths` (and so the engine) creates data/, cache/, logs/ and backups/ under
    # the working folder — a mistyped ONW_ROOT must not leave a husk of an instance behind. So a
    # missing database file is reported from config alone, before anything below is imported.
    if not os.environ.get("ONW_DATABASE_URL") and not (data_dir / "onw.db").exists():
        return _no_book(data_dir)

    from sqlalchemy import inspect

    from backend.db.auth_store import AuthStore
    from backend.db.engine import database_url, get_engine, resolved_sqlite_path
    from backend.db.tables import secret_config

    db_path = resolved_sqlite_path(database_url())
    if (db_path is not None and not db_path.exists()) or not inspect(get_engine()).has_table(
        secret_config.name
    ):
        return _no_book(data_dir)

    # The recovery path: whoever needed it may have lost more than the password, so a linked
    # sign-in wallet is unlinked too (link it again in Settings -> Security).
    AuthStore().replace_password(hash_password(password), clear_wallet=True)
    print(
        f"Password updated for the book in {data_dir}. Every signed-in session was signed out.\n"
        "Any wallet linked for sign-in was unlinked.\n"
        "The API token was not rotated; to rotate it, use Settings -> Security -> Rotate."
    )
    return 0
