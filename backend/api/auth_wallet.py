"""Wallet sign-in router — Sign-In with Ethereum beside the password (MetaMask, Rabby, …).

Thin: validate → ``services.wallet_auth`` / ``AuthStore`` → set the same ``onw_session`` cookie the
password login sets. A wallet is a SECOND way in, never the only one: linking needs a password to
be set and the current password (or the desktop app's own credential), and removing the password
unlinks the wallet. Human-only, like every auth route — there is no Lucius tool for it.

``challenge`` and ``login`` are in the gate's exempt set so the login screen reaches them.
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from backend.api._deps import _host_allowed
from backend.api.auth import (
    _client_key,
    _old_password_refusal,
    _require_xhr,
    _set_session_cookie,
)
from backend.core import ratelimit
from backend.db.auth_store import AuthStore
from backend.services import wallet_auth

router = APIRouter()


class ChallengeBody(BaseModel):
    address: str
    chain_id: int = 1
    purpose: str = "login"


class SignedBody(BaseModel):
    nonce: str
    signature: str


class LinkBody(SignedBody):
    old_password: Optional[str] = None


class UnlinkBody(BaseModel):
    old_password: Optional[str] = None


def _json(status: int, detail: str, headers: Optional[dict] = None) -> Response:
    return Response(
        content=json.dumps({"detail": detail}),
        media_type="application/json",
        status_code=status,
        headers=headers,
    )


@router.post("/api/auth/wallet/challenge")
def wallet_challenge(body: ChallengeBody, request: Request):
    """Mint the EIP-4361 message this address is asked to sign. Issued for ANY well-formed
    address — whether it is the linked one is only revealed by a failed sign-in, never here.

    The message names the domain the wallet compares with the page it is shown on, so it may
    only be a host this instance answers to (loopback, ``ONW_ALLOWED_HOSTS``, ``CORS_ORIGINS``):
    otherwise a phishing server could ask for a challenge with ``Host: evil.example``, show it
    on evil.example where the wallet sees no mismatch, and replay the signature here."""
    if not _host_allowed(request):
        return _json(421, "host not allowed — add it to ONW_ALLOWED_HOSTS to sign in with a wallet")
    host = request.headers.get("host") or ""
    try:
        return wallet_auth.issue_challenge(
            purpose=body.purpose,
            address=body.address,
            chain_id=body.chain_id,
            domain=host,
            uri=f"{request.url.scheme}://{host}",
            client=_client_key(request),
        )
    except wallet_auth.ChallengeLimitError as exc:
        return _json(429, str(exc), {"Retry-After": "60"})
    except wallet_auth.WalletAuthError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/api/auth/wallet/login")
def wallet_login(body: SignedBody, request: Request, response: Response):
    """Verify a signed login challenge from the linked wallet → set the session cookie. Shares
    the password login's brute-force limiter, key and 401 shape."""
    key = _client_key(request)
    allowed, retry_after = ratelimit.login_limiter.check(key)
    if not allowed:
        return _json(429, "too many attempts, try again later", {"Retry-After": str(retry_after)})
    store = AuthStore()
    signer = wallet_auth.consume_challenge(
        nonce=body.nonce, signature=body.signature, purpose="login"
    )
    linked = store.wallet_address()
    if (
        not store.is_password_set()
        or signer is None
        or not wallet_auth.same_address(signer, linked)
    ):
        ratelimit.login_limiter.record(key)
        return _json(401, "wallet sign-in failed")
    ratelimit.login_limiter.reset(key)
    _set_session_cookie(request, response)
    return {"ok": True}


@router.post("/api/auth/wallet/link")
def wallet_link(body: LinkBody, request: Request):
    """Link the wallet that signed a ``link`` challenge. Authenticated (not exempt), XHR-only,
    and gated on the current password exactly like a password change."""
    _require_xhr(request)
    store = AuthStore()
    if not store.is_password_set():
        raise HTTPException(409, "set a password first — a wallet signs in beside it, not instead")
    signer = wallet_auth.consume_challenge(
        nonce=body.nonce, signature=body.signature, purpose="link"
    )
    if signer is None:
        raise HTTPException(422, "the signature does not match the challenge — try again")
    refusal = _old_password_refusal(store, request, body.old_password)
    if refusal is not None:
        return refusal
    store.set_wallet_address(signer)
    return {"ok": True, "wallet_address": signer}


@router.post("/api/auth/wallet/unlink")
def wallet_unlink(body: UnlinkBody, request: Request):
    """Turn wallet sign-in off. Same current-password gate as linking."""
    _require_xhr(request)
    store = AuthStore()
    if not store.wallet_address():
        raise HTTPException(409, "no wallet is linked")
    refusal = _old_password_refusal(store, request, body.old_password)
    if refusal is not None:
        return refusal
    store.set_wallet_address("")
    return {"ok": True}
