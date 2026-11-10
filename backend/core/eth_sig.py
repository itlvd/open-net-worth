"""Ethereum signature verification — stdlib only (Keccak-256 + secp256k1 public-key recovery).

The one thing wallet sign-in needs from Ethereum: given the exact text a wallet signed with
``personal_sign`` (EIP-191) and its 65-byte signature, which address signed it. Every input here is
public (message, signature, address), so nothing needs constant-time arithmetic; there is no private
key anywhere in this module. Malformed input returns ``None`` rather than raising — the caller is an
unauthenticated login route.

``hashlib.sha3_256`` is NOT Keccak-256 (FIPS-202 changed the padding byte), hence the permutation
below.
"""

from __future__ import annotations

import re
from typing import Optional

# ── Keccak-256 ────────────────────────────────────────────────────────────────

_MASK = (1 << 64) - 1
_RC = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)  # fmt: skip
_ROT = (
    (0, 36, 3, 41, 18), (1, 44, 10, 45, 2), (62, 6, 43, 15, 61),
    (28, 55, 25, 21, 56), (27, 20, 39, 8, 14),
)  # fmt: skip
_RATE = 136  # bytes, for a 256-bit output


def _rol(v: int, n: int) -> int:
    return ((v << n) | (v >> (64 - n))) & _MASK if n else v


def _keccak_f(a: list) -> None:
    for rc in _RC:
        c = [a[x][0] ^ a[x][1] ^ a[x][2] ^ a[x][3] ^ a[x][4] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                a[x][y] ^= d[x]
        b = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                b[y][(2 * x + 3 * y) % 5] = _rol(a[x][y], _ROT[x][y])
        for x in range(5):
            for y in range(5):
                a[x][y] = b[x][y] ^ ((~b[(x + 1) % 5][y]) & b[(x + 2) % 5][y])
        a[0][0] ^= rc


def keccak256(data: bytes) -> bytes:
    """Keccak-256 as Ethereum uses it (original Keccak padding ``0x01``, not SHA3's ``0x06``)."""
    padded = bytearray(data)
    padded.append(0x01)
    padded.extend(b"\x00" * (-len(padded) % _RATE))
    padded[-1] |= 0x80
    state = [[0] * 5 for _ in range(5)]
    for off in range(0, len(padded), _RATE):
        block = padded[off : off + _RATE]
        for i in range(_RATE // 8):
            state[i % 5][i // 5] ^= int.from_bytes(block[8 * i : 8 * i + 8], "little")
        _keccak_f(state)
    out = b"".join(state[i % 5][i // 5].to_bytes(8, "little") for i in range(4))
    return out


# ── secp256k1 ─────────────────────────────────────────────────────────────────

_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)


def _add(p: Optional[tuple], q: Optional[tuple]) -> Optional[tuple]:
    if p is None:
        return q
    if q is None:
        return p
    if p[0] == q[0]:
        if (p[1] + q[1]) % _P == 0:
            return None
        lam = 3 * p[0] * p[0] * pow(2 * p[1], -1, _P) % _P
    else:
        lam = (q[1] - p[1]) * pow(q[0] - p[0], -1, _P) % _P
    x = (lam * lam - p[0] - q[0]) % _P
    return (x, (lam * (p[0] - x) - p[1]) % _P)


def _mul(k: int, p: Optional[tuple]) -> Optional[tuple]:
    result = None
    while k:
        if k & 1:
            result = _add(result, p)
        p = _add(p, p)
        k >>= 1
    return result


def _recover_pubkey(digest: bytes, r: int, s: int, recid: int) -> Optional[tuple]:
    if not (0 < r < _N and 0 < s < _N) or recid not in (0, 1):
        return None
    x = r
    y_sq = (pow(x, 3, _P) + 7) % _P
    y = pow(y_sq, (_P + 1) // 4, _P)
    if y * y % _P != y_sq:
        return None
    if y % 2 != recid:
        y = _P - y
    z = int.from_bytes(digest, "big")
    r_inv = pow(r, -1, _N)
    sr = _mul(s, (x, y))
    zg = _mul((-z) % _N, _G)
    return _mul(r_inv, _add(sr, zg))


def pubkey_to_address(point: tuple) -> str:
    raw = point[0].to_bytes(32, "big") + point[1].to_bytes(32, "big")
    return to_checksum_address("0x" + keccak256(raw)[-20:].hex())


def address_from_private_key(key: int) -> str:
    """Test/fixture helper: the address a private key controls."""
    point = _mul(key, _G)
    assert point is not None
    return pubkey_to_address(point)


# ── addresses + EIP-191 ───────────────────────────────────────────────────────

_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def is_address(value: object) -> bool:
    return isinstance(value, str) and bool(_ADDRESS_RE.match(value))


def to_checksum_address(address: str) -> str:
    """EIP-55 mixed-case form. Raises ``ValueError`` on a malformed address."""
    if not is_address(address):
        raise ValueError("not an Ethereum address")
    lower = address[2:].lower()
    h = keccak256(lower.encode("ascii")).hex()
    return "0x" + "".join(c.upper() if int(h[i], 16) >= 8 else c for i, c in enumerate(lower))


def personal_message_hash(message: str) -> bytes:
    raw = message.encode("utf-8")
    return keccak256(b"\x19Ethereum Signed Message:\n" + str(len(raw)).encode("ascii") + raw)


def recover_personal_sign(message: str, signature: str) -> Optional[str]:
    """The checksummed address whose key produced ``signature`` over ``message`` via
    ``personal_sign``, or ``None`` for anything malformed. High-s signatures are refused
    (EIP-2: every wallet emits low-s, so a high-s one is a malleated copy)."""
    if not isinstance(signature, str) or not isinstance(message, str):
        return None
    sig_hex = signature[2:] if signature.startswith("0x") else signature
    try:
        sig = bytes.fromhex(sig_hex)
    except ValueError:
        return None
    if len(sig) != 65:
        return None
    r = int.from_bytes(sig[:32], "big")
    s = int.from_bytes(sig[32:64], "big")
    v = sig[64]
    recid = v - 27 if v >= 27 else v
    if s > _N // 2:
        return None
    point = _recover_pubkey(personal_message_hash(message), r, s, recid)
    if point is None:
        return None
    return pubkey_to_address(point)
