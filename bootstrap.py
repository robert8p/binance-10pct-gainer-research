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
