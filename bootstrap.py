"""Generic loader for encrypted Crypto500 releases."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tarfile

from cryptography.hazmat.primitives import hashes, hpke
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


LEGACY_AAD = b"crypto500-private-release-v1"
PUBLIC_CARRIER = "HPKE-X25519-HKDF-SHA256-AES256GCM-v1"
RECIPIENT_DOMAIN = b"crypto500/release-recipient/x25519/v1"
CARRIER_DOMAIN = b"crypto500/release-carrier/hpke/v1\x00"
SUITE = hpke.Suite(hpke.KEM.X25519, hpke.KDF.HKDF_SHA256, hpke.AEAD.AES_256_GCM)


def hex32(value, name):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"Invalid 32-byte {name}")
    return bytes.fromhex(value)


def public_envelope(spec):
    if not isinstance(spec, dict) or set(spec) != {"algorithm", "salt_hex", "recipient_public_hex"}:
        raise ValueError("Unexpected public release envelope fields")
    if spec["algorithm"] != PUBLIC_CARRIER:
        raise ValueError("Unsupported release envelope version")
    salt = hex32(spec["salt_hex"], "release salt")
    public = hex32(spec["recipient_public_hex"], "release recipient")
    return salt, public, CARRIER_DOMAIN + salt + public


def encrypt_for_recipient(plaintext, spec):
    _, public, info = public_envelope(spec)
    return SUITE.encrypt(plaintext, X25519PublicKey.from_public_bytes(public), info)


def release_key(master, manifest):
    """Decode pre-public-key releases with their original authenticated data."""
    if "key_derivation" not in manifest:
        return master
    spec = manifest["key_derivation"]
    if not isinstance(spec, dict) or spec.get("algorithm") != "HKDF-SHA256-v1":
        raise ValueError("Unsupported release key derivation")
    salt_hex = spec.get("salt_hex")
    if not isinstance(salt_hex, str) or len(salt_hex) != 64:
        raise ValueError("Expected 32-byte release salt")
    salt = bytes.fromhex(salt_hex)
    if len(salt) != 32:
        raise ValueError("Expected 32-byte release salt")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                info=b"crypto500-private-release-key-v1").derive(master)


def open_release(master, manifest, payload):
    spec = manifest.get("key_derivation")
    if not isinstance(spec, dict) or spec.get("algorithm") != PUBLIC_CARRIER:
        key = release_key(master, manifest)
        return AESGCM(key).decrypt(payload[:12], payload[12:], LEGACY_AAD)
    required = {"source_files", "bundle_parts", "encrypted_bytes", "plaintext_sha256", "ciphertext_sha256", "key_derivation"}
    if set(manifest) != required:
        raise ValueError("Unexpected public release manifest fields")
    for field in ("source_files", "bundle_parts", "encrypted_bytes"):
        if type(manifest[field]) is not int or manifest[field] <= 0:
            raise ValueError(f"Invalid manifest {field}")
    for field in ("plaintext_sha256", "ciphertext_sha256"):
        hex32(manifest[field], field)
    salt, public, info = public_envelope(spec)
    if len(master) != 32:
        raise ValueError("Invalid deployment key length")
    seed = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=RECIPIENT_DOMAIN).derive(master)
    recipient = X25519PrivateKey.from_private_bytes(seed)
    if recipient.public_key().public_bytes_raw() != public:
        raise ValueError("Envelope recipient does not match this deployment")
    if len(payload) != manifest["encrypted_bytes"] or hashlib.sha256(payload).hexdigest() != manifest["ciphertext_sha256"]:
        raise ValueError("Release payload does not match the pinned manifest")
    plaintext = SUITE.decrypt(payload, recipient, info)
    if hashlib.sha256(plaintext).hexdigest() != manifest["plaintext_sha256"]:
        raise ValueError("Release plaintext does not match the pinned manifest")
    return plaintext


def main():
    manifest_path = Path("release-manifest.json")
    manifest = json.loads(manifest_path.read_bytes()) if manifest_path.exists() else {}
    parts = sorted(Path("bundle").glob("part-*.b64"))
    if "bundle_parts" in manifest and len(parts) != manifest["bundle_parts"]:
        raise ValueError("Release part count does not match manifest")
    encoded = b"".join(part.read_bytes().strip() for part in parts)
    payload = base64.b64decode(encoded, validate=True)
    master = base64.urlsafe_b64decode(os.environ["CRYPTO500_BUNDLE_KEY"])
    plaintext = open_release(master, manifest, payload)
    destination = Path("/tmp/crypto500-release")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:gz") as archive:
        archive.extractall(destination, filter="data")
    os.chdir(destination)
    if len(sys.argv) > 1:
        command = sys.argv[1]
        if command not in {"watchdog", "maintenance"}:
            raise SystemExit("Unsupported release command")
        os.execv(sys.executable, [sys.executable, "-m", f"crypto500.{command}", *sys.argv[2:]])
    os.execv(sys.executable, [sys.executable, "-m", "uvicorn", "crypto500.web:app", "--host", "0.0.0.0",
                              "--port", os.getenv("PORT", "10000"), "--workers", "1",
                              "--timeout-graceful-shutdown", "25"])


if __name__ == "__main__":
    main()
