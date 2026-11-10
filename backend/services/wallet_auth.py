"""Wallet sign-in — Sign-In with Ethereum (EIP-4361) challenges for MetaMask, Rabby & co.

The server writes the whole message a wallet is asked to sign and remembers it under a single-use
nonce; verifying never parses text the client sent back — it recovers the signer of the stored
message and compares addresses. A challenge is bound to its PURPOSE (sign in vs link this wallet),
so a signature collected for one can never be replayed as the other, and it expires in minutes.

Challenges live in process memory: a restart only voids the few minutes of open ones. The table is
capped, per client and in all, so unauthenticated callers cannot grow it — and a full table refuses
new challenges rather than evicting open ones.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from backend.core.errors import ValidationError
from backend.core.eth_sig import is_address, recover_personal_sign, to_checksum_address

CHALLENGE_TTL_SECONDS = 300
_MAX_OPEN_CHALLENGES = 256
_MAX_OPEN_PER_CLIENT = 8
PURPOSES = ("login", "link")

_STATEMENTS = {
    "login": "Sign in to Open Net Worth. This request will not trigger a blockchain "
    "transaction or cost any gas fees.",
    "link": "Link this wallet to Open Net Worth so it can sign you in. This request will not "
    "trigger a blockchain transaction or cost any gas fees.",
}


class WalletAuthError(ValidationError):
    """A wallet challenge could not be issued or verified."""


class ChallengeLimitError(WalletAuthError):
    """Too many open challenges — from this client, or in all. Old ones are never evicted to make
    room: an eviction would let a flood void the owner's challenge while they sign it."""


@dataclass(frozen=True)
class _Challenge:
    purpose: str
    client: str
    address: str
    message: str
    expires_at: float


_lock = threading.Lock()
_open: "OrderedDict[str, _Challenge]" = OrderedDict()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_message(
    *, domain: str, uri: str, address: str, statement: str, chain_id: int, nonce: str, now: float
) -> str:
    """The EIP-4361 text, field for field, so wallets show their sign-in view and domain check."""
    return (
        f"{domain} wants you to sign in with your Ethereum account:\n"
        f"{address}\n\n"
        f"{statement}\n\n"
        f"URI: {uri}\n"
        "Version: 1\n"
        f"Chain ID: {chain_id}\n"
        f"Nonce: {nonce}\n"
        f"Issued At: {_iso(now)}\n"
        f"Expiration Time: {_iso(now + CHALLENGE_TTL_SECONDS)}"
    )


def _prune(now: float) -> None:
    for nonce in [n for n, c in _open.items() if c.expires_at <= now]:
        del _open[nonce]


def issue_challenge(
    *, purpose: str, address: str, chain_id: int, domain: str, uri: str, client: str = ""
) -> dict:
    """Mint a single-use challenge for ``address``; return ``{nonce, message}`` to sign."""
    if purpose not in PURPOSES:
        raise WalletAuthError(f"purpose must be one of {', '.join(PURPOSES)}")
    if not is_address(address):
        raise WalletAuthError("address must be a 0x-prefixed 20-byte hex Ethereum address")
    if not isinstance(chain_id, int) or chain_id <= 0:
        raise WalletAuthError("chain_id must be a positive integer")
    checksummed = to_checksum_address(address)
    now = time.time()
    nonce = secrets.token_hex(16)
    message = build_message(
        domain=domain,
        uri=uri,
        address=checksummed,
        statement=_STATEMENTS[purpose],
        chain_id=chain_id,
        nonce=nonce,
        now=now,
    )
    with _lock:
        _prune(now)
        if len(_open) >= _MAX_OPEN_CHALLENGES:
            raise ChallengeLimitError("too many sign-in requests in progress, try again shortly")
        if sum(1 for c in _open.values() if c.client == client) >= _MAX_OPEN_PER_CLIENT:
            raise ChallengeLimitError("too many sign-in requests from this address, try again soon")
        _open[nonce] = _Challenge(
            purpose, client, checksummed, message, now + CHALLENGE_TTL_SECONDS
        )
    return {"nonce": nonce, "message": message}


def consume_challenge(*, nonce: str, signature: str, purpose: str) -> Optional[str]:
    """Verify ``signature`` against the stored challenge and burn it. Returns the checksummed
    address that signed, or ``None`` when the nonce is unknown/expired/of another purpose or the
    signature is not the challenge address's. A nonce is burned on ANY attempt, right or wrong."""
    if not isinstance(nonce, str):
        return None
    with _lock:
        challenge = _open.pop(nonce, None)
    if challenge is None or challenge.purpose != purpose or challenge.expires_at <= time.time():
        return None
    signer = recover_personal_sign(challenge.message, signature)
    if signer is None or signer != challenge.address:
        return None
    return signer


def same_address(a: str, b: str) -> bool:
    return bool(a) and bool(b) and is_address(a) and is_address(b) and a.lower() == b.lower()


def reset() -> None:
    """Test hook: forget every open challenge."""
    with _lock:
        _open.clear()
