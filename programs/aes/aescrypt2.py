#!/usr/bin/env python
"""AES-256 file encryption program.

This is a port of the program from mbedtls.

"""
import argparse
import io
import sys

import mbedtls


def get_filesize(stream):
    """Return the size of the `stream`."""
    filesize = stream.seek(0, io.SEEK_END)
    stream.seek(0, io.SEEK_SET)
    return filesize


def generate_iv(istream, *, size):
    """Generate the initialization vector.

    Generate the initialization vector as:
    IV = SHA-256(filesize || filename)[0..15].

    """
    buffer = mbedtls.hash.sha256(
        bytes((get_filesize(istream) >> (_ << 3)) % 256 for _ in range(8))
    )
    buffer.update(istream.name.encode())
    return buffer.digest()[:size]


def digest_key(key, iv, *, size):
    """Digest the `key` with the `iv`.

    Hash the IV and the secret key together 8192 times
    using the result to setup the AES context and HMAC.

    """
    digest = bytearray(iv)
    for _ in range(8192):
        hash_ = mbedtls.hash.sha256()
        hash_.update(digest)
        hash_.update(key)
        digest[:] = hash_.digest()
    return bytes(digest)


def encrypt(istream, ostream, key):
    cipher = mbedtls.cipher.AES
    iv = bytearray(generate_iv(istream, size=cipher.block_size))

    # The last four bits in the IV are actually used
    # to store the file size modulo the AES block size.
    lastn = get_filesize(istream) & 0x0F
    iv[-1] = (iv[-1] & 0xF0) | lastn

    # Append the IV at the beginning of the output.
    written = ostream.write(iv)
    assert written == cipher.block_size

    digest = digest_key(key, iv, size=cipher.block_size)

    ciph = cipher.new(digest, mbedtls.cipher.Mode.ECB, iv=b"")
    hmac = mbedtls.hmac.sha256(digest)

    buffer = bytearray(ciph.block_size)
    offset = get_filesize(istream)
    while True:
        offset -= istream.readinto(buffer)
        buffer[:] = (
            int.from_bytes(buffer, "big") ^ int.from_bytes(iv, "big")
        ).to_bytes(ciph.block_size, "big")
        buffer[:] = ciph.encrypt(buffer)
        hmac.update(buffer)
        written = ostream.write(buffer)
        assert written == ciph.block_size
        iv[:] = buffer
        if offset == 0:
            break
    ostream.write(hmac.digest())
    ostream.close()


def decrypt(istream, ostream, key):
    cipher = mbedtls.cipher.AES
    filesize = get_filesize(istream)
    if filesize < 48 or (filesize & 0x0F) != 0:
        raise IOError("Invalid input file")

    iv = bytearray(istream.read(16))
    lastn = iv[-1] & 0x0F

    digest = digest_key(key, iv, size=cipher.block_size)

    ciph = cipher.new(digest, mbedtls.cipher.Mode.ECB, iv=b"")
    hmac = mbedtls.hmac.sha256(digest)

    offset = filesize - (cipher.block_size + hmac.digest_size)
    buffer = bytearray(ciph.block_size)
    tmp = bytearray(cipher.block_size)
    while True:
        offset -= istream.readinto(buffer)
        if offset:
            nwrite = len(buffer)
        else:
            nwrite = lastn

        tmp[:] = buffer
        hmac.update(buffer)
        buffer[:] = ciph.decrypt(buffer)
        buffer[:] = (
            int.from_bytes(buffer, "big") ^ int.from_bytes(iv, "big")
        ).to_bytes(ciph.block_size, "big")
        ostream.write(buffer[:nwrite])
        iv[:] = tmp
        if offset == 0:
            break

    if istream.read(hmac.digest_size) != hmac.digest():
        raise ValueError("HMAC check failed: wrong key, or file corrupted")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AES-256 file encryption program"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument(
        "--encrypt", "-e", action="store_true", help="encrypt file"
    )
    action.add_argument(
        "--decrypt", "-d", action="store_true", help="decrypt file"
    )
    parser.add_argument(
        "--key",
        "-k",
        type=lambda k: k.encode(),
        required=True,
        help="encryption key",
    )
    parser.add_argument(
        "--output",
        "-o",
        nargs="?",
        type=argparse.FileType("wb"),
        default=sys.stdout.buffer,
    )
    parser.add_argument(
        "input",
        type=argparse.FileType("rb"),
    )
    args = parser.parse_args()
    if args.encrypt:
        encrypt(args.input, args.output, args.key)
    else:
        decrypt(args.input, args.output, args.key)
