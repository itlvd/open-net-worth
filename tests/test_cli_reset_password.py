"""`onw reset-password` — writes a verifiable hash through AuthStore, rotates the session key,
refuses a short or mismatched password, and refuses a folder that holds no book."""

from __future__ import annotations

import io
import os
import subprocess
import sys

import pytest

from backend.cli import build_parser, main
from backend.core import security


class _Stdin:
    """What a pipe or terminal looks like to the CLI: a byte buffer plus isatty."""

    def __init__(self, data: bytes = b"", tty: bool = False) -> None:
        self.buffer = io.BytesIO(data)
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_reset_password_is_in_the_help():
    assert "reset-password" in build_parser().format_help()
    assert build_parser().parse_args(["reset-password", "--password-stdin"]).password_stdin is True


def test_stdin_password_sets_hash_and_rotates_session_key(db_isolated, monkeypatch, capsys):
    from backend.db.auth_store import AuthStore

    store = AuthStore()
    store.set_password_hash(security.hash_password("forgotten-one"))
    before_key = store.session_key_bytes()
    monkeypatch.setattr("sys.stdin", _Stdin(b"a-new-password\n"))

    assert main(["reset-password", "--password-stdin"]) == 0

    fresh = AuthStore()
    assert security.verify_password("a-new-password", fresh.password_hash())
    assert not security.verify_password("forgotten-one", fresh.password_hash())
    assert fresh.session_key_bytes() != before_key
    out = capsys.readouterr().out
    assert "Password updated" in out and "a-new-password" not in out
    assert "API token was not rotated" in out and "Rotate" in out


def test_reset_unlinks_a_sign_in_wallet(db_isolated, monkeypatch, capsys):
    from backend.db.auth_store import AuthStore

    store = AuthStore()
    store.set_password_hash(security.hash_password("forgotten-one"))
    store.set_wallet_address("0x00000000000000000000000000000000000000A1")
    monkeypatch.setattr("sys.stdin", _Stdin(b"a-new-password\n"))

    assert main(["reset-password", "--password-stdin"]) == 0

    assert AuthStore().wallet_address() == ""
    assert "wallet linked for sign-in was unlinked" in capsys.readouterr().out


def test_prompt_path_confirms_and_rejects_a_mismatch(db_isolated, monkeypatch, capsys):
    from backend.db.auth_store import AuthStore

    answers = iter(["first-password", "other-password", "same-password", "same-password"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    monkeypatch.setattr("sys.stdin", _Stdin(tty=True))

    assert main(["reset-password"]) == 1
    assert "do not match" in capsys.readouterr().err
    assert not AuthStore().is_password_set()

    assert main(["reset-password"]) == 0
    assert security.verify_password("same-password", AuthStore().password_hash())


def test_short_password_is_refused_and_nothing_is_written(db_isolated, monkeypatch, capsys):
    from backend.db.auth_store import AuthStore

    monkeypatch.setattr("sys.stdin", _Stdin(b"seven77\n"))
    assert main(["reset-password", "--password-stdin"]) == 1
    assert "at least 8" in capsys.readouterr().err
    assert not AuthStore().is_password_set()


def _onw(root, *args: str, stdin: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "ONW_ROOT": str(root), "ONW_QA_MODE": "1"}
    for key in ("DATA_DIR", "CACHE_DIR", "LOGS_DIR", "ONW_DATABASE_URL", "AUTH_PASSWORD"):
        env.pop(key, None)
    return subprocess.run(
        [sys.executable, *args],
        input=stdin,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_cli_against_a_real_root_and_without_one(tmp_path):
    root = tmp_path / "book"
    empty = _onw(
        root, "-m", "backend.cli", "reset-password", "--password-stdin", stdin="pw-12345678\n"
    )
    assert empty.returncode == 1
    assert "No Open Net Worth book" in empty.stderr
    # A wrong ONW_ROOT is only reported on: not one folder of an instance is created for it.
    assert not root.exists()

    migrate = _onw(
        root,
        "-c",
        "from backend.db.migrate import ensure_migrated; ensure_migrated(allow_live_instance=True)",
        stdin="",
    )
    assert migrate.returncode == 0, migrate.stderr

    done = _onw(
        root, "-m", "backend.cli", "reset-password", "--password-stdin", stdin="pw-12345678\n"
    )
    assert done.returncode == 0, done.stderr
    check = _onw(
        root,
        "-c",
        "from backend.core.security import verify_password; from backend.db.auth_store import AuthStore;"
        "print(verify_password('pw-12345678', AuthStore().password_hash()))",
        stdin="",
    )
    assert check.stdout.strip() == "True", check.stderr


def test_stdin_password_is_decoded_as_utf8_and_drops_a_bom(db_isolated, monkeypatch):
    from backend.db.auth_store import AuthStore

    monkeypatch.setattr("sys.stdin", _Stdin("\ufeffpässwörd-12\r\n".encode("utf-8")))
    assert main(["reset-password", "--password-stdin"]) == 0
    assert security.verify_password("pässwörd-12", AuthStore().password_hash())


def test_invalid_utf8_on_stdin_is_refused(db_isolated, monkeypatch, capsys):
    from backend.db.auth_store import AuthStore

    monkeypatch.setattr("sys.stdin", _Stdin(b"\xff\xfe-not-utf8-pw\n"))
    assert main(["reset-password", "--password-stdin"]) == 1
    assert "UTF-8" in capsys.readouterr().err
    assert not AuthStore().is_password_set()


def test_no_terminal_and_no_flag_refuses_without_prompting(db_isolated, monkeypatch, capsys):
    from backend.db.auth_store import AuthStore

    monkeypatch.setattr("sys.stdin", _Stdin(b"would-be-echoed\n", tty=False))
    monkeypatch.setattr(
        "getpass.getpass", lambda prompt="": pytest.fail("must not prompt without a terminal")
    )
    assert main(["reset-password"]) == 1
    assert "--password-stdin" in capsys.readouterr().err
    assert not AuthStore().is_password_set()


def test_stdin_password_keeps_its_spaces_and_drops_only_the_line_ending(db_isolated, monkeypatch):
    from backend.db.auth_store import AuthStore

    monkeypatch.setattr("sys.stdin", _Stdin(b"  two spaces each side  \r\n"))
    assert main(["reset-password", "--password-stdin"]) == 0
    stored = AuthStore().password_hash()
    assert security.verify_password("  two spaces each side  ", stored)
    assert not security.verify_password("two spaces each side", stored)


def test_a_blank_or_whitespace_only_password_is_refused(db_isolated, monkeypatch, capsys):
    from backend.db.auth_store import AuthStore

    monkeypatch.setattr("sys.stdin", _Stdin(b"            \n"))
    assert main(["reset-password", "--password-stdin"]) == 1
    assert "only spaces" in capsys.readouterr().err
    assert not AuthStore().is_password_set()
