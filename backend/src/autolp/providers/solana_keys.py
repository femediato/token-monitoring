"""Solana address helpers: base58 decoding and the ed25519 on-curve test.

Program-derived addresses (PDAs) are deliberately chosen to lie *off* the ed25519 curve,
so no private key exists for them; every ordinary wallet address lies *on* the curve.
An owner that is off-curve is therefore controlled by a program (AMM vault authority,
multisig, locker, bonding curve), regardless of which program owns its account.
"""

from functools import lru_cache

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_INDEX = {c: i for i, c in enumerate(_B58)}

_P = 2**255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58_INDEX[ch]
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(s) - len(s.lstrip("1"))
    return b"\x00" * pad + raw


@lru_cache(maxsize=65536)
def is_on_curve(address: str) -> bool:
    """True if the 32-byte public key decompresses to a valid ed25519 point."""
    try:
        key = b58decode(address)
    except KeyError:
        return False
    if len(key) != 32:
        return False
    y = int.from_bytes(key, "little") & ((1 << 255) - 1)
    sign = key[31] >> 7
    if y >= _P:
        return False
    u = (y * y - 1) % _P
    v = (_D * y * y + 1) % _P
    # Candidate square root of u/v (RFC 8032, section 5.1.3).
    x = (u * pow(v, 3, _P) * pow(u * pow(v, 7, _P), (_P - 5) // 8, _P)) % _P
    vx2 = (v * x * x) % _P
    if vx2 == u % _P:
        pass
    elif vx2 == (-u) % _P:
        x = (x * _SQRT_M1) % _P
    else:
        return False
    return not (x == 0 and sign == 1)


def is_pda(address: str) -> bool:
    """A valid 32-byte address that is off the ed25519 curve (program-derived)."""
    try:
        valid = len(b58decode(address)) == 32
    except KeyError:
        return False
    return valid and not is_on_curve(address)
