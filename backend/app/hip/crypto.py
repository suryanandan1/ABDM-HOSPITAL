"""ABDM health-information encryption (ECDH on Curve25519 + HKDF-SHA256 + AES-256-GCM).

Matches the scheme used by NHA's ABDM wrapper and Fidelius CLI (both BouncyCastle):
- keys live on BouncyCastle's "curve25519" in short-Weierstrass form; public keys are uncompressed points
  (0x04 || X || Y), private keys are big-endian integers (Java BigInteger.toByteArray());
- shared secret = 32-byte big-endian X coordinate of d·Q;
- xor = senderNonce XOR receiverNonce; HKDF salt = xor[:20]; AES-GCM IV = xor[-12:]; 128-bit tag appended.
The HIP shares its public key X.509-encoded (explicit parameters), as the NHA wrapper does; both raw points and
X.509 keys are accepted from the HIU. Verified against Fidelius CLI's published test vectors (tests/test_hip.py).
"""

import base64
import secrets

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

P = 2**255 - 19
MONT_A = 486662
SHIFT = MONT_A * pow(3, -1, P) % P  # Weierstrass x = Montgomery u + A/3
WEI_A = (3 - MONT_A * MONT_A) * pow(3, -1, P) % P
WEI_B = (2 * MONT_A**3 - 9 * MONT_A) * pow(27, -1, P) % P
N = 2**252 + 27742317777372353535851937790883648493
BASE_U = 9

# DER SubjectPublicKeyInfo header with explicit curve25519 (Weierstrass) domain parameters, as produced by
# BouncyCastle's ECPublicKey.getEncoded() for these keys; the 65-byte uncompressed point follows it.
X509_PREFIX = bytes.fromhex(
    "308201313081ea06072a8648ce3d02013081de020101302b06072a8648ce3d01010220"
    "7fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffed3044"
    "04202aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa984914a144"
    "04207b425ed097b425ed097b425ed097b425ed097b425ed097b4260b5e9c7710c864"
    "0441042aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaad245a"
    "20ae19a1b8a086b4e01edd2c7748d14c923d4d7e6d7c61b229e9c5a27eced3d9"
    "02201000000000000000000000000000000014def9dea2f79cd65812631a5cf5d3ed020108034200")

CRYPTO_ALG, CURVE, PARAMETERS = "ECDH", "Curve25519", "Curve25519/32byte random key"


class CryptoError(ValueError):
    pass


def ladder(scalar: int, u: int) -> int:
    """Montgomery ladder (RFC 7748 arithmetic, without clamping, like BouncyCastle's plain d·Q)."""
    x2, z2, x3, z3, swap = 1, 0, u, 1, 0
    for t in reversed(range(255)):
        bit = (scalar >> t) & 1
        swap ^= bit
        if swap:
            x2, x3, z2, z3 = x3, x2, z3, z2
        swap = bit
        a, b = (x2 + z2) % P, (x2 - z2) % P
        aa, bb = a * a % P, b * b % P
        e = (aa - bb) % P
        c, d = (x3 + z3) % P, (x3 - z3) % P
        da, cb = d * a % P, c * b % P
        x3, z3 = (da + cb) ** 2 % P, u * (da - cb) ** 2 % P
        x2, z2 = aa * bb % P, e * (aa + 121665 * e) % P
    if swap:
        x2, z2 = x3, z3
    return x2 * pow(z2, P - 2, P) % P


def sqrt_mod(value: int) -> int:
    """Square root modulo P (P = 5 mod 8)."""
    root = pow(value, (P + 3) // 8, P)
    if root * root % P != value % P:
        root = root * pow(2, (P - 1) // 4, P) % P
    if root * root % P != value % P:
        raise CryptoError("Not a point on the curve")
    return root


def point_from_u(u: int) -> bytes:
    x = (u + SHIFT) % P
    y = sqrt_mod((x**3 + WEI_A * x + WEI_B) % P)
    return b"\x04" + x.to_bytes(32, "big") + y.to_bytes(32, "big")


def u_from_public_key(key: bytes) -> int:
    """Accept a raw uncompressed point or an X.509 SubjectPublicKeyInfo for this curve."""
    if key.startswith(X509_PREFIX):
        key = key[len(X509_PREFIX):]
    if len(key) != 65 or key[0] != 4:
        raise CryptoError("Public key must be an uncompressed Curve25519 point or its X.509 encoding")
    x, y = int.from_bytes(key[1:33], "big"), int.from_bytes(key[33:], "big")
    if (y * y - (x**3 + WEI_A * x + WEI_B)) % P:
        raise CryptoError("Public key is not on Curve25519")
    return (x - SHIFT) % P


def java_bigint_bytes(value: int) -> bytes:
    """Java BigInteger.toByteArray() for a positive value (big-endian, sign bit kept clear)."""
    return value.to_bytes(value.bit_length() // 8 + 1, "big")


def generate_key_material() -> dict:
    """Ephemeral HIP key pair and nonce for one data transfer (never stored or logged)."""
    private = secrets.randbelow(N - 1) + 1
    public = point_from_u(ladder(private, BASE_U))
    return {"private": private, "public": public, "x509": X509_PREFIX + public, "nonce": secrets.token_bytes(32)}


def session_key(private: int, peer_public: bytes, own_nonce: bytes, peer_nonce: bytes, sender_first: bool):
    shared_u = ladder(private, u_from_public_key(peer_public))
    shared = ((shared_u + SHIFT) % P).to_bytes(32, "big")
    first, second = (own_nonce, peer_nonce) if sender_first else (peer_nonce, own_nonce)
    xor = bytes(b ^ second[i % len(second)] for i, b in enumerate(first))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=xor[:20], info=None).derive(shared)
    return AESGCM(key), xor[-12:]


def encrypt(plaintext: str, sender_private: int, sender_nonce: bytes, receiver_public: bytes, receiver_nonce: bytes) -> str:
    cipher, iv = session_key(sender_private, receiver_public, sender_nonce, receiver_nonce, sender_first=True)
    return base64.b64encode(cipher.encrypt(iv, plaintext.encode(), None)).decode()


def decrypt(ciphertext: str, receiver_private: int, receiver_nonce: bytes, sender_public: bytes, sender_nonce: bytes) -> str:
    cipher, iv = session_key(receiver_private, sender_public, receiver_nonce, sender_nonce, sender_first=False)
    return cipher.decrypt(iv, base64.b64decode(ciphertext), None).decode()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def unb64(text: str) -> bytes:
    try:
        return base64.b64decode(text, validate=True)
    except (ValueError, TypeError) as exc:
        raise CryptoError("Invalid base64 value in key material") from exc
