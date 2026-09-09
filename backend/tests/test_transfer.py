"""Real PostgreSQL round trip across isolated schemas, without paid provider calls."""
import io
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from app.core.db import get_engine
from app.db.base import AudioFile, Base, Chunk, Document, Learner, ProviderCredential, VocabItem
from app.db.exams import Exam
from app.services import transfer
from cryptography.fernet import Fernet
from dotenv import dotenv_values
from sqlalchemy import create_engine, select, text


@pytest.fixture(params=[False, True], ids=["legacy", "native"])
def installation(tmp_path, monkeypatch, request):
    from lernapp_launcher import credential_store
    keys = {}
    monkeypatch.setattr(transfer.credentials, 'native_store_enabled', lambda: request.param)
    monkeypatch.setattr(credential_store, 'read', lambda root: keys.get(str(root)))
    monkeypatch.setattr(credential_store, 'write', lambda root, key: keys.setdefault(str(root), key))
    root = get_engine()
    engines = []
    schemas = []
    for _ in range(2):
        schema = 'transfer_' + uuid.uuid4().hex
        with root.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA {schema}'))
        engine = create_engine(root.url, connect_args={'options': f'-c search_path={schema},public'}).execution_options(schema_translate_map={None: schema})
        Base.metadata.create_all(engine)
        schemas.append(schema)
        engines.append(engine)
    source, target = engines
    key = Fernet.generate_key()
    cipher = Fernet(key)
    monkeypatch.setattr(transfer.credentials, '_fernet', lambda: cipher)
    settings = {k: None for k in transfer.SETTINGS}
    settings.update(default_learner_id='default', default_level='B2', audio_encryption_key=key.decode(),
                    google_application_credentials=None, azure_api_key=None)
    monkeypatch.setattr(transfer, 'get_settings', lambda: SimpleNamespace(**settings))
    monkeypatch.setattr(transfer, 'reload_settings', lambda: None)
    monkeypatch.setattr(transfer, 'data_dir', lambda: tmp_path / 'destination')
    (tmp_path / 'destination').mkdir()
    audio = tmp_path / 'recording.enc'
    audio.write_bytes(cipher.encrypt(b'kept recording'))
    with source.begin() as conn:
        conn.execute(Learner.__table__.insert().values(id='default'))
        conn.execute(Document.__table__.insert().values(id='doc', owner_id='default', title='Glossar', filename='glossar.pdf', mime='application/pdf'))
        conn.execute(Chunk.__table__.insert().values(id='chunk', document_id='doc', owner_id='default', ord=0, text='Originaltext', embedding=[0.1, 0.2]))
        conn.execute(VocabItem.__table__.insert().values(id='word', owner_id='default', wort='Rechnung', bedeutung='invoice', repetitions=7))
        conn.execute(Exam.__table__.insert().values(id='exam', owner_id='default', title='Test', payload={'questions': [{'passage': 'Der ganze Lesetext'}]}, pdf=b'%PDF-test', audio=b'mp3 bytes'))
        conn.execute(ProviderCredential.__table__.insert().values(id='key', learner_id='default', provider='openai', ciphertext=cipher.encrypt(b'sk-test-transfer-private').decode()))
        conn.execute(ProviderCredential.__table__.insert().values(id='billing-admin', learner_id='default', provider='openai_billing_admin', ciphertext=cipher.encrypt(b'sk-admin-not-portable').decode()))
        conn.execute(AudioFile.__table__.insert().values(id='audio', learner_id='default', path=str(audio), expires_at=datetime.now(UTC) + timedelta(days=30)))
        conn.execute(text("INSERT INTO audit_log (id, ts, caller, tool, arg_digest, outcome, duration_ms, meta) VALUES (50, now(), 'ui', 'test', 'digest', 'ok', 0, '{}')"))
    monkeypatch.setattr(transfer, 'get_engine', lambda: source)
    content, counts = transfer.export_backup('correct-password-123')
    monkeypatch.setattr(transfer, 'get_engine', lambda: target)
    yield content, counts, target, tmp_path / 'destination', cipher
    for engine in engines:
        engine.dispose()
    with root.begin() as conn:
        for schema in schemas:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))


def test_transfer_preserves_assets_vectors_reviews_credentials_and_audio(installation):
    content, counts, target, dest, old_cipher = installation
    archive, manifest = transfer._open(content, 'correct-password-123')
    archive.close()
    assert all(row['provider'] != 'openai_billing_admin' for row in manifest['tables']['provider_credentials'])
    assert 'sk-admin-not-portable' not in json.dumps(manifest)
    assert b'sk-test-transfer-private' not in content
    assert transfer.restore_backup(content, 'correct-password-123') == counts
    with target.begin() as conn:
        exam = conn.execute(select(Exam.__table__)).mappings().one()
        assert exam['pdf'] == b'%PDF-test' and exam['audio'] == b'mp3 bytes'
        assert exam['payload']['questions'][0]['passage'] == 'Der ganze Lesetext'
        assert conn.execute(select(VocabItem.repetitions)).scalar_one() == 7
        assert list(conn.execute(select(Chunk.embedding)).scalar_one()) == pytest.approx([0.1, 0.2])
        assert conn.execute(select(Chunk.tsv)).scalar_one()
        encrypted_key = conn.execute(select(ProviderCredential.ciphertext)).scalar_one()
        if transfer.credentials.native_store_enabled():
            from lernapp_launcher import credential_store
            assert 'CREDENTIAL_ENCRYPTION_KEY' not in (dest / '.env').read_text()
            new_key = credential_store.read(dest).decode()
        else:
            new_key = dotenv_values(dest / '.env')['CREDENTIAL_ENCRYPTION_KEY']
        assert Fernet(new_key.encode()).decrypt(encrypted_key.encode()) == b'sk-test-transfer-private'
        assert new_key.encode() != old_cipher._signing_key
        audio = conn.execute(select(AudioFile.path)).scalar_one()
        from pathlib import Path
        assert Path(audio).is_relative_to(dest)
        assert old_cipher.decrypt(Path(audio).read_bytes()) == b'kept recording'
        next_id = conn.execute(text("INSERT INTO audit_log (ts, caller, tool, arg_digest, outcome, duration_ms, meta) VALUES (now(), 'ui', 'test', 'digest', 'ok', 0, '{}') RETURNING id")).scalar_one()
        assert next_id == 51
    assert 'sk-test' not in (dest / '.env').read_text()
    with pytest.raises(transfer.TransferError, match='bereits eigene Daten'):
        transfer.restore_backup(content, 'correct-password-123')


def test_bad_password_and_tampering_leave_destination_untouched(installation):
    content, _, target, dest, _ = installation
    for blob, password in ((content, 'incorrect-password'), (content[:-10] + b'0000000000', 'correct-password-123')):
        with pytest.raises(transfer.TransferError, match='Passwort'):
            transfer.restore_backup(blob, password)
    assert not list(dest.iterdir())
    with target.connect() as conn:
        assert not conn.execute(select(Learner.id)).all()


def test_bad_schema_rolls_back_all_insertions(installation):
    content, _, target, dest, _ = installation
    archive, manifest = transfer._open(content, 'correct-password-123')
    manifest['tables']['vocab_items'][0]['unknown_column'] = 'bad'
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as altered:
        for item in archive.namelist():
            altered.writestr(item, json.dumps(manifest) if item == 'manifest.json' else archive.read(item))
    salt = b'0123456789abcdef'
    altered_content = transfer.MAGIC + salt + transfer._cipher('correct-password-123', salt).encrypt(stream.getvalue())
    (dest / '.env').write_text('DEFAULT_LEVEL=B1\n')
    with pytest.raises(transfer.TransferError, match='Datenstruktur'):
        transfer.restore_backup(altered_content, 'correct-password-123')
    assert (dest / '.env').read_text() == 'DEFAULT_LEVEL=B1\n'
    assert list(dest.iterdir()) == [dest / '.env']
    with target.connect() as conn:
        assert not conn.execute(select(Learner.id)).all()
