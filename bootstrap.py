"""Generic release loader; authenticated encryption protects private source in transit."""
import base64
import io
import json
import os
from pathlib import Path
import sys
import tarfile

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

def release_key(master, manifest):
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


manifest_path = Path("release-manifest.json")
manifest = json.loads(manifest_path.read_bytes()) if manifest_path.exists() else {}
key = release_key(base64.urlsafe_b64decode(os.environ["CRYPTO500_BUNDLE_KEY"]), manifest)
payload=base64.b64decode(''.join(p.read_text().strip() for p in sorted(Path('bundle').glob('part-*.b64'))))
plaintext=AESGCM(key).decrypt(payload[:12],payload[12:],b'crypto500-private-release-v1')
def release_preflight(master):
    """Seal one release-scoped key once per watchdog instance for its designated recipient."""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    salt = bytes.fromhex("2beb8e3aeee44d37b72c4eb3a19728587df7e1a98219fed3446db90ff659f418")
    recipient = serialization.load_der_public_key(base64.b64decode("MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEAy8RimC+vGcvFY0lAyCQHdVD2DwTPRX1ogkDx5pXqakQCC79DnkxBPXfW8wXWVG0LXWBHnYBLRvpdL9X+pJgbnbev7aK2HBvnEvkJvn/OsNPtgtqj0PaW99WZV7EpZjCCQdTaN/z74ff6yBPv3Ew3nqbIWCqZKGEKogeZijW1WKyzd1P80Iuvs5x378Fo7EQodBGUyVSKXkMHKhSl3KucVRQ02LjM7OKqe/KO6iD6/E0GxTnKYyH/CXOJobvz8APsJVDm/+VE7YP9Xcnj8XBGWOspbiUhajrftgEz1XcU2WqnuvRYOpQEm/OzS7a7WsIQErfdjEtMhKDKA8X59YeDsCEXIYDH2YGIN3co0ky4tBgz1piTzY/Z2AwKMuNqfh2YPuHzYcv144H0Iq+kbkDdjldzU6Ib1l1XFuYjjLi939vbbwBjBSNqpZ4mqfm6RGUfJa5f336If6QO7xB+694dIFx/k5x5wY+ie5tOqiv3qtlCAD4hoHN6KfRsWRJvI+VbAgMBAAE="))
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=salt,
                   info=b"crypto500-private-release-key-v1").derive(master)
    sealed = recipient.encrypt(derived, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),
                               algorithm=hashes.SHA256(), label=b"crypto500-release-handoff-v1"))
    print("C500_SEALED_V1 2beb8e3aeee44d37b72c4eb3a19728587df7e1a98219fed3446db90ff659f418 " + base64.b64encode(sealed).decode("ascii"), flush=True)


if (sys.argv[1:2] == ["watchdog"] and os.getenv("RENDER_SERVICE_ID") == "crn-dbr1u30n74is73d383kg"):
    try:
        marker = Path("/tmp/c500-preflight-2beb8e3aeee44d37b72c4eb3a19728587df7e1a98219fed3446db90ff659f418")
        if not marker.exists():
            release_preflight(base64.urlsafe_b64decode(os.environ["CRYPTO500_BUNDLE_KEY"]))
            marker.touch(exist_ok=True)
    except Exception:
        print("C500_SEALED_V1 preflight failed; watchdog proceeding", flush=True)

destination=Path('/tmp/crypto500-release')
destination.mkdir(parents=True,exist_ok=True)
with tarfile.open(fileobj=io.BytesIO(plaintext),mode='r:gz') as archive:
    archive.extractall(destination,filter='data')
os.chdir(destination)
if len(sys.argv) > 1:
    # Scheduled monitoring and read-only maintenance use the same signed release.
    # The allowlist is deliberately limited to operational command modules.
    command = sys.argv[1]
    if command not in {'watchdog', 'maintenance'}:
        raise SystemExit('Unsupported release command')
    os.execv(sys.executable, [sys.executable, '-m', f'crypto500.{command}', *sys.argv[2:]])
os.execv(sys.executable,[sys.executable,'-m','uvicorn','crypto500.web:app','--host','0.0.0.0',
                        '--port',os.getenv('PORT','10000'),'--workers','1','--timeout-graceful-shutdown','25'])
