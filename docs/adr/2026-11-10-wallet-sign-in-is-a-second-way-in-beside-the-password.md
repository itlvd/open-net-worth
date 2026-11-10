---
title: Wallet sign-in is a second way in beside the password
status: Accepted
date: 2026-11-10
deciders: orchestrating agent (Claude) + repo owner
---

# Wallet sign-in is a second way in beside the password

## Context

The owner wants to sign in with a browser wallet (MetaMask, Rabby, …). The instance has one
operator and one password; every auth gate (change password, remove password, the login limiter,
the desktop credential) is built around it.

## Decision

- Sign-In with Ethereum (EIP-4361) over `personal_sign`, beside the password — never replacing it.
  One linked address lives in the `auth` row of `secret_config` (`wallet_address`, `""` = off).
- The SERVER writes the whole message and keeps it under a single-use, purpose-bound
  (`login` / `link`), 5-minute nonce (`services/wallet_auth.py`, process memory, capped). Verifying
  recovers the signer of the STORED message — no client text is parsed, so there is no SIWE parser
  to get wrong.
- Signature recovery is stdlib-only (`core/eth_sig.py`: Keccak-256 + secp256k1 recovery, high-s
  refused), pinned to the published web3.js vector and fuzzed against `eth-account` when written.
  No new dependency: `eth-account` would pull pycryptodome, py_ecc and a dozen more into the wheel
  for one verification. Every input is public, so constant-time arithmetic is not needed.
- The message's domain is the request `Host`, and a challenge is REFUSED (421) unless that host is
  one the instance answers to (`_host_allowed`: loopback, `ONW_ALLOWED_HOSTS`, `CORS_ORIGINS`).
  Taking `Host` unchecked let a phishing server mint a message naming its own domain — the
  wallet's origin check then passes on the phishing page and the signature logs in here (security
  review, 2026-11-10).
- Open challenges are capped per client (8) and in all (256); a full table refuses new ones, never
  evicts open ones, so a flood cannot void the owner's challenge mid-signature.
- Linking/unlinking needs the current password through the same `_old_password_refusal` as a
  password change (the desktop credential waives it). Linking needs a password set; removing the
  password — or the `onw reset-password` recovery path — unlinks the wallet in the same write; a
routine password change keeps it. A failed wallet login records on the password
  login limiter. `/api/auth/status` adds only `wallet_login: true`, never the address.
- `challenge` and `login` are auth-exempt; every `/api/auth/wallet/*` route is locked on the demo.

## Consequences

- Losing the wallet never locks the owner out — the password still works.
- Recovery is ~30 ms of pure-Python EC math per attempt, bounded by the login limiter.
- Open challenges are lost on restart (the user just clicks again); a multi-worker deployment
  would need a shared store, which this single-process app does not have.
