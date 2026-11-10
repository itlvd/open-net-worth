"""Wallet sign-in (Sign-In with Ethereum, EIP-4361) beside the password.

Signatures are produced in-test with a stdlib secp256k1 signer over fixed synthetic keys, and the
recovery primitive is pinned to the published web3.js ``accounts.sign`` vector, so these tests need
no Ethereum library.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi.testclient import TestClient

import backend.core.config as config
from backend.core import eth_sig, security

_XHR = {"X-Requested-With": "XMLHttpRequest"}
_KEY_A = 0x1111111111111111111111111111111111111111111111111111111111111111
_KEY_B = 0x2222222222222222222222222222222222222222222222222222222222222222
_PASSWORD = "correct horse"


# ── a tiny RFC-6979 signer, test-only ────────────────────────────────────────


def _sign(message: str, key: int) -> str:
    digest = eth_sig.personal_message_hash(message)
    n = eth_sig._N
    x = key.to_bytes(32, "big")
    v, k = b"\x01" * 32, b"\x00" * 32
    k = hmac.new(k, v + b"\x00" + x + digest, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    k = hmac.new(k, v + b"\x01" + x + digest, hashlib.sha256).digest()
    v = hmac.new(k, v, hashlib.sha256).digest()
    while True:
        v = hmac.new(k, v, hashlib.sha256).digest()
        nonce = int.from_bytes(v, "big")
        if 0 < nonce < n:
            break
    point = eth_sig._mul(nonce, eth_sig._G)
    r = point[0] % n
    s = pow(nonce, -1, n) * (int.from_bytes(digest, "big") + r * key) % n
    recid = point[1] & 1
    if s > n // 2:
        s, recid = n - s, recid ^ 1
    return "0x" + (r.to_bytes(32, "big") + s.to_bytes(32, "big") + bytes([27 + recid])).hex()


ADDR_A = eth_sig.address_from_private_key(_KEY_A)
ADDR_B = eth_sig.address_from_private_key(_KEY_B)


# ── primitives ───────────────────────────────────────────────────────────────


def test_keccak256_is_not_sha3():
    assert eth_sig.keccak256(b"").hex() == (
        "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
    )


def test_recovers_the_published_web3js_vector():
    sig = (
        "0xb91467e570a6466aa9e9876cbcd013baba02900b8979d43fe208a4a4f339f5fd"
        "6007e74cd82e037b800186422fc2da167c747ef045e5d18a5f5d4300f8e1a0291c"
    )
    assert eth_sig.recover_personal_sign("Some data", sig) == (
        "0x2c7536E3605D9C16a7a3D7b1898e529396a65c23"
    )


def test_recovery_rejects_tampering_and_garbage():
    sig = _sign("hello", _KEY_A)
    assert eth_sig.recover_personal_sign("hello", sig) == ADDR_A
    assert eth_sig.recover_personal_sign("hello!", sig) != ADDR_A
    assert eth_sig.recover_personal_sign("hello", "0x1234") is None
    assert eth_sig.recover_personal_sign("hello", "zz" * 65) is None
    raw = bytes.fromhex(sig[2:])
    high_s = (eth_sig._N - int.from_bytes(raw[32:64], "big")).to_bytes(32, "big")
    malleated = "0x" + (raw[:32] + high_s + bytes([raw[64] ^ 1])).hex()
    assert eth_sig.recover_personal_sign("hello", malleated) is None


def test_checksum_address_eip55():
    assert eth_sig.to_checksum_address("0x5aaeb6053f3e94c9b9a09f33669435e7ef1beaed") == (
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
    )


# ── routes ───────────────────────────────────────────────────────────────────


@pytest.fixture
def app_env(monkeypatch, db_isolated):
    for key in (
        "AUTH_DISABLED",
        "AUTH_TRUST_LOOPBACK",
        "AUTH_PASSWORD",
        "AUTH_PASSWORD_HASH",
        "AUTH_API_TOKEN",
        "AUTH_SESSION_KEY",
        "AUTH_SESSION_TTL",
    ):
        monkeypatch.delenv(key, raising=False)
    config._settings = None
    from backend.core import ratelimit
    from backend.db.auth_store import AuthStore
    from backend.services import wallet_auth

    ratelimit.reset()
    wallet_auth.reset()
    store = AuthStore()
    store.replace_password(security.hash_password(_PASSWORD, iterations=1000))
    store.update_auth_settings(trust_loopback=False)
    yield store
    ratelimit.reset()
    wallet_auth.reset()
    config._settings = None


def _client(**kw) -> TestClient:
    from backend.main import app

    return TestClient(app, headers=_XHR, **kw)


def _signed(client: TestClient, key: int, address: str, purpose: str) -> dict:
    r = client.post("/api/auth/wallet/challenge", json={"address": address, "purpose": purpose})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "wants you to sign in with your Ethereum account" in body["message"]
    assert f"Nonce: {body['nonce']}" in body["message"]
    return {"nonce": body["nonce"], "signature": _sign(body["message"], key)}


def _logged_in() -> TestClient:
    c = _client()
    assert c.post("/api/auth/login", json={"password": _PASSWORD}).status_code == 200
    return c


def _link(client: TestClient, key=_KEY_A, address=ADDR_A, password=_PASSWORD):
    payload = _signed(client, key, address, "link") | {"old_password": password}
    return client.post("/api/auth/wallet/link", json=payload)


def test_link_then_sign_in_with_the_wallet(app_env):
    owner = _logged_in()
    r = _link(owner, address=ADDR_A.lower())
    assert r.status_code == 200 and r.json()["wallet_address"] == ADDR_A
    assert owner.get("/api/auth/settings").json()["wallet_address"] == ADDR_A

    visitor = _client()
    status = visitor.get("/api/auth/status").json()
    assert status["authenticated"] is False and status["wallet_login"] is True
    assert ADDR_A.lower() not in str(status).lower()
    r = visitor.post("/api/auth/wallet/login", json=_signed(visitor, _KEY_A, ADDR_A, "login"))
    assert r.status_code == 200
    assert visitor.get("/api/auth/status").json()["authenticated"] is True


def test_another_wallet_cannot_sign_in(app_env):
    _link(_logged_in())
    visitor = _client()
    r = visitor.post("/api/auth/wallet/login", json=_signed(visitor, _KEY_B, ADDR_B, "login"))
    assert r.status_code == 401
    assert visitor.get("/api/auth/status").json()["authenticated"] is False


def test_signature_for_the_claimed_address_must_come_from_it(app_env):
    _link(_logged_in())
    visitor = _client()
    r = visitor.post("/api/auth/wallet/login", json=_signed(visitor, _KEY_B, ADDR_A, "login"))
    assert r.status_code == 401


def test_challenge_is_single_use_and_purpose_bound(app_env):
    owner = _logged_in()
    link_payload = _signed(owner, _KEY_A, ADDR_A, "link")
    visitor = _client()
    assert visitor.post("/api/auth/wallet/login", json=link_payload).status_code == 401
    assert _link(owner).status_code == 200

    payload = _signed(visitor, _KEY_A, ADDR_A, "login")
    assert visitor.post("/api/auth/wallet/login", json=payload).status_code == 200
    replay = _client()
    assert replay.post("/api/auth/wallet/login", json=payload).status_code == 401


def test_expired_challenge_is_refused(app_env, monkeypatch):
    owner = _logged_in()
    _link(owner)
    visitor = _client()
    payload = _signed(visitor, _KEY_A, ADDR_A, "login")
    from backend.services import wallet_auth

    real = wallet_auth.time.time
    monkeypatch.setattr(
        wallet_auth.time, "time", lambda: real() + wallet_auth.CHALLENGE_TTL_SECONDS + 1
    )
    assert visitor.post("/api/auth/wallet/login", json=payload).status_code == 401


def test_link_needs_the_current_password(app_env):
    owner = _logged_in()
    assert _link(owner, password="wrong-password").status_code == 403
    assert _link(owner, password=None).status_code == 403
    assert owner.get("/api/auth/settings").json()["wallet_address"] == ""


def test_link_and_unlink_require_a_session(app_env):
    stranger = _client()
    payload = _signed(stranger, _KEY_A, ADDR_A, "link") | {"old_password": _PASSWORD}
    assert stranger.post("/api/auth/wallet/link", json=payload).status_code == 401
    r = stranger.post("/api/auth/wallet/unlink", json={"old_password": _PASSWORD})
    assert r.status_code == 401


def test_unlink_turns_wallet_sign_in_off(app_env):
    owner = _logged_in()
    _link(owner)
    assert (
        owner.post("/api/auth/wallet/unlink", json={"old_password": "nope-nope"}).status_code == 403
    )
    assert (
        owner.post("/api/auth/wallet/unlink", json={"old_password": _PASSWORD}).status_code == 200
    )
    visitor = _client()
    assert "wallet_login" not in visitor.get("/api/auth/status").json()
    r = visitor.post("/api/auth/wallet/login", json=_signed(visitor, _KEY_A, ADDR_A, "login"))
    assert r.status_code == 401


def test_removing_the_password_unlinks_the_wallet(app_env):
    owner = _logged_in()
    _link(owner)
    app_env.remove_password()
    assert app_env.wallet_address() == ""


def test_failed_wallet_logins_share_the_password_limiter(app_env):
    _link(_logged_in())
    visitor = _client()
    codes = [
        visitor.post(
            "/api/auth/wallet/login", json=_signed(visitor, _KEY_B, ADDR_B, "login")
        ).status_code
        for _ in range(12)
    ]
    assert 429 in codes


def test_challenge_validates_input(app_env):
    c = _client()
    assert c.post("/api/auth/wallet/challenge", json={"address": "0x12"}).status_code == 422
    r = c.post("/api/auth/wallet/challenge", json={"address": ADDR_A, "purpose": "pay"})
    assert r.status_code == 422


def test_challenge_refuses_a_host_this_instance_does_not_answer_to(app_env):
    """A phishing server must not get a message naming ITS domain (the wallet would see no
    mismatch on its page) — the challenge is refused before a nonce exists."""
    c = _client()
    r = c.post(
        "/api/auth/wallet/challenge",
        json={"address": ADDR_A},
        headers={"Host": "evil.example"},
    )
    assert r.status_code == 421


def test_challenge_names_an_allowed_public_host(app_env, monkeypatch):
    monkeypatch.setenv("ONW_ALLOWED_HOSTS", "money.example.org")
    config._settings = None
    r = _client().post(
        "/api/auth/wallet/challenge",
        json={"address": ADDR_A},
        headers={"Host": "money.example.org"},
    )
    assert r.status_code == 200
    assert r.json()["message"].startswith("money.example.org wants you to sign in")


def test_one_client_cannot_flood_out_open_challenges(app_env):
    from backend.services import wallet_auth

    flooder = _client(client=("198.51.100.9", 1234))
    owner = _client()
    owner_payload = _signed(owner, _KEY_A, ADDR_A, "link")
    codes = [
        flooder.post("/api/auth/wallet/challenge", json={"address": ADDR_B}).status_code
        for _ in range(wallet_auth._MAX_OPEN_PER_CLIENT + 3)
    ]
    assert codes.count(200) == wallet_auth._MAX_OPEN_PER_CLIENT and 429 in codes
    assert owner_payload["nonce"] in wallet_auth._open
