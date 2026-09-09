"""Native macOS/Windows smoke test in an isolated temporary installation (no real API keys)."""
import hashlib
import os
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'launcher'))
from lernapp_launcher import credential_store as store


def main():
    if not store.supported():
        raise RuntimeError('This test must run on macOS or Windows')
    with tempfile.TemporaryDirectory(prefix='lernapp-native-') as temp:
        root = Path(temp)
        secret = os.urandom(32)
        suffix = 'backup:updates/version-test/backup-data/.env'
        try:
            assert store.read(root) is None
            store.write(root, secret)
            assert store.read(root) == secret
            store.write(root, secret)
            # Restarted process can access the native secret, with no inherited plaintext key.
            env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'launcher')}
            child = subprocess.check_output([sys.executable, '-c', 'from pathlib import Path; import hashlib,sys; from lernapp_launcher import credential_store as s; print(hashlib.sha256(s.read(Path(sys.argv[1]))).hexdigest())', str(root)], env=env, text=True).strip()
            assert child == hashlib.sha256(secret).hexdigest()
            old_env = b'CREDENTIAL_ENCRYPTION_KEY=legacy-test-key\nOTHER_SECRET=private-value\n'
            backup = root / 'updates/version-test/backup-data'
            backup.mkdir(parents=True)
            (backup / '.env').write_bytes(old_env)
            assert store.protect_legacy_backups(root) == 1
            assert store.backup_env(root, backup) == old_env
            assert store.protect_legacy_backups(root) == 0
            for file in root.rglob('*'):
                if file.is_file():
                    assert secret not in file.read_bytes()
                    assert b'legacy-test-key' not in file.read_bytes()
                    assert b'private-value' not in file.read_bytes()
            if sys.platform == 'win32':
                encrypted = store._dpapi(secret)
                assert store._dpapi(encrypted, decrypt=True) == secret
                try:
                    store._dpapi(encrypted[:-8] + b'corrupted', decrypt=True)
                except store.StorageError:
                    pass
                else:
                    raise AssertionError('Corrupted DPAPI data must fail closed')
            print('Native storage, process restart, protected backup recovery and no plaintext copies: PASS')
        finally:
            store.delete(root, suffix)
            store.delete(root)


if __name__ == '__main__':
    main()
