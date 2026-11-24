"""ExpressNet's two light obfuscations: the system-record XOR and the password encoder.

Neither is encryption; both are reversible. Re-implemented from observed
behaviour so old data can be read and logins carried over.
"""
from __future__ import annotations

# --- system parameters record (SYSTEM/X3SYSPAR.DAT) ---------------------------
# Each byte j of the record is XORed with (0xAB + j) & 0xFF.
SYSPAR_KEY0 = 0xAB


def descramble_syspar(rec: bytes, key0: int = SYSPAR_KEY0) -> bytes:
    return bytes(b ^ ((key0 + j) & 0xFF) for j, b in enumerate(rec))


scramble_syspar = descramble_syspar          # XOR is its own inverse


# --- passwords (SYSTEM/PASSWORD.DAT) ------------------------------------------
# An 8-byte field. A one-byte key is derived from the user record; the text is
# XORed against a 19-byte table, rotated, then bits 2 and 4 are swapped in
# every third byte.
_TABLE = bytes.fromhex("79 23 42 e1 53 89 16 bd 02 9b 07 0f 03 b6 cf 04 57 05 00")
_T7 = _TABLE[7] - 256 if _TABLE[7] > 127 else _TABLE[7]
PW_LEN = 8
PW_OFFSET = 0x37
KEY_SALT_OFFSET = 0x4F


def password_key(rec: bytes) -> int:
    k = 0
    for i in range(13):
        k = ((k + rec[1 + i]) & 0xFF) ^ rec[KEY_SALT_OFFSET]
    return k


def _start(key: int, n: int):
    hi, lo = (key >> 4) + 1, (key & 0xF) + 1
    if lo >= n:
        lo -= n
    if hi >= n:
        hi -= n
    return hi, lo


def _swap_bits_2_4(b: int) -> int:
    return (b & 0xEB) + ((b & 0x04) << 2) + ((b & 0x10) >> 2)


def _keystream(buf: bytearray, key: int, n: int) -> None:
    hi, lo = _start(key, n)
    ik, j = lo, hi
    for _ in range(n):
        buf[j] ^= _TABLE[ik] ^ ((_T7 * ik) & 0xFF)
        ik = 0 if ik + 1 >= len(_TABLE) else ik + 1
        j = 0 if j + 1 >= n else j + 1


def encode_password(plain: str, key: int) -> bytes:
    n = PW_LEN
    buf = bytearray(plain.encode("latin-1")[:n].ljust(16, b"\0"))
    _keystream(buf, key, n)
    hi, _ = _start(key, n)
    out = bytearray(80)
    j = hi
    for x in range(8):
        out[x] = buf[j]
        j = 0 if j + 1 >= n else j + 1
    for i in range(0, 3 * (n // 3 + 1), 3):
        out[i] = _swap_bits_2_4(out[i])
    return bytes(out[:out.index(0)])


def decode_password(stored: bytes, key: int) -> str:
    n = PW_LEN
    t = bytearray(stored.split(b"\0", 1)[0].ljust(80, b"\0"))
    for i in range(0, 3 * (n // 3 + 1), 3):
        t[i] = _swap_bits_2_4(t[i])
    buf = bytearray(16)
    hi, _ = _start(key, n)
    j = hi
    for x in range(8):
        buf[j] = t[x]
        j = 0 if j + 1 >= n else j + 1
    _keystream(buf, key, n)
    return bytes(buf[:n]).split(b"\0", 1)[0].decode("latin-1")


def password_from_record(rec: bytes) -> str:
    """Best-effort plain text. See check_password for why it can be one character short."""
    return decode_password(rec[PW_OFFSET:PW_OFFSET + PW_LEN], password_key(rec))


def check_password(plain: str, rec: bytes) -> bool:
    """Verify a login the way ExpressNet does: encode the attempt and compare.

    Quirks of the original, kept on purpose: when the key's high nibble is 0xF the
    encoder starts one byte past the 8-byte buffer, so the 8th character is never
    stored (and any 8th character is accepted); and the result is copied with
    strcpy, so a scrambled byte that comes out as 0 cuts the stored value short.
    """
    stored = rec[PW_OFFSET:PW_OFFSET + PW_LEN].split(b"\0", 1)[0]
    return encode_password(plain, password_key(rec)) == stored
