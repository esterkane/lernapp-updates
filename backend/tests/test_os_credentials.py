"""Migration failures must never discard the only working credential key."""
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from app.core import credentials
from app.core.db import get_engine
from app.db.base import Base, Learner, ProviderCredential
from cryptography.fernet import Fernet
from lernapp_launcher import credential_store
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session


@pytest.fixture
def migration(tmp_path, monkeypatch):
    parent = get_engine()
    schema = 'secure_' + uuid.uuid4().hex
    with parent.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_engine(parent.url, connect_args={'options': f'-c search_path={schema},public'}).execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    legacy = Fernet.generate_key()
    secret = b'sk-admin-migration-example'
    old_token = Fernet(legacy).encrypt(secret).decode()
    with engine.begin() as conn:
        conn.execute(Learner.__table__.insert().values(id='default'))
        conn.execute(ProviderCredential.__table__.insert().values(id='admin', learner_id='default', provider='openai_billing_admin', ciphertext=old_token))
    @contextmanager
    def session():
        with Session(engine) as db, db.begin():
            yield db
    config = SimpleNamespace(credential_encryption_key=legacy.decode(), app_env='prod')
    path = tmp_path / '.env'
    path.write_text('CREDENTIAL_ENCRYPTION_KEY='+legacy.decode()+'\nTTS_SPEED=0.85\n')
    store = {}
    monkeypatch.setattr(credentials, 'db_session', session)
    monkeypatch.setattr(credentials, 'get_settings', lambda: config)
    monkeypatch.setattr(credentials, 'reload_settings', lambda: setattr(config, 'credential_encryption_key', legacy.decode() if 'CREDENTIAL_ENCRYPTION_KEY' in path.read_text() else None))
    monkeypatch.setattr(credentials, 'data_dir', lambda: tmp_path)
    monkeypatch.setattr(credentials, 'native_store_enabled', lambda: True)
    monkeypatch.setattr(credential_store, 'read', lambda root: store.get('key'))
    def write(root, key):
        store['key'] = key
    monkeypatch.setattr(credential_store, 'write', write)
    monkeypatch.delenv('CREDENTIAL_ENCRYPTION_KEY', raising=False)
    yield tmp_path, store, legacy, secret, old_token, engine, config
    engine.dispose()
    with parent.begin() as conn:
        conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))


def test_migrate_rotates_master_without_losing_credentials(migration):
    root, store, legacy, secret, old, engine, config = migration
    assert credentials.ensure_encryption_key()
    assert store['key'] != legacy
    assert 'CREDENTIAL_ENCRYPTION_KEY' not in (root / '.env').read_text()
    assert 'TTS_SPEED=0.85' in (root / '.env').read_text()
    with engine.connect() as conn:
        token = conn.scalar(select(ProviderCredential.ciphertext))
    assert token != old and Fernet(store['key']).decrypt(token.encode()) == secret
    assert credentials.get_api_key('default', 'openai_billing_admin') == secret.decode()
    assert not credentials.ensure_encryption_key()


def test_locked_store_preserves_legacy_key_and_database(migration, monkeypatch):
    root, store, legacy, secret, old, engine, config = migration
    def locked(*args):
        raise credential_store.StorageError('locked')
    monkeypatch.setattr(credential_store, 'write', locked)
    with pytest.raises(credentials.CredentialError, match='locked'):
        credentials.ensure_encryption_key()
    assert legacy.decode() in (root / '.env').read_text()
    with engine.connect() as conn:
        assert conn.scalar(select(ProviderCredential.ciphertext)) == old


def test_interruption_after_db_commit_resumes_safely(migration, monkeypatch):
    root, store, legacy, secret, old, engine, config = migration
    strip = credential_store.strip_legacy
    def interrupted(*args):
        raise OSError('interrupted')
    monkeypatch.setattr(credential_store, 'strip_legacy', interrupted)
    with pytest.raises(OSError):
        credentials.ensure_encryption_key()
    first = store['key']
    assert legacy.decode() in (root / '.env').read_text()
    monkeypatch.setattr(credential_store, 'strip_legacy', strip)
    assert not credentials.ensure_encryption_key()
    assert store['key'] == first
    assert credentials.get_api_key('default', 'openai_billing_admin') == secret.decode()


def test_missing_key_never_generates_replacement(migration):
    root, store, legacy, secret, old, engine, config = migration
    config.credential_encryption_key = None
    with pytest.raises(credentials.CredentialError, match='fehlt'):
        credentials.ensure_encryption_key()
    assert not store


def test_corrupt_ciphertext_preserves_legacy_key(migration):
    root, store, legacy, secret, old, engine, config = migration
    with engine.begin() as conn:
        conn.execute(ProviderCredential.__table__.update().values(ciphertext='corrupt'))
    with pytest.raises(credentials.CredentialError, match='geprüft'):
        credentials.ensure_encryption_key()
    assert not store and legacy.decode() in (root / '.env').read_text()


def test_native_read_failure_does_not_fall_back_to_plaintext(migration, monkeypatch):
    def locked(*args):
        raise credential_store.StorageError('locked')
    monkeypatch.setattr(credential_store, 'read', locked)
    with pytest.raises(credentials.CredentialError, match='locked'):
        credentials._fernet()
