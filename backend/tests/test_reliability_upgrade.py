"""Targeted failure windows; mocked clients are not real Kafka integration tests."""
from app.models import CertificateAnalysis, SessionLocal, User
from app.workers import ocr as worker


def test_ocr_result_and_outbox_are_committed_together(monkeypatch):
    from app.models import OutboxEvent
    db = SessionLocal()
    try:
        db.add(User(id='u', email='ocr-owner@example.com', password_hash='unused'))
        db.add(CertificateAnalysis(id='outbox-ocr', user_id='u', filename='x.png',
                                   content_type='image/png', status='analyzing'))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(worker, 'download_file_bytes', lambda _: b'image bytes')
    monkeypatch.setattr(worker, 'run_ocr', lambda *args: 'Certificate ID: CF-123')
    monkeypatch.setattr(worker, 'extract_fields', lambda _: {'certificate_no': 'CF-123'})
    monkeypatch.setattr(worker, 'produce_event', lambda *args: (_ for _ in ()).throw(AssertionError('no direct publish')), raising=False)
    event = {'analysis_id': 'outbox-ocr', 'file_key': 'u/outbox-ocr/x.png', 'content_type': 'image/png'}
    assert worker.process_ocr(event) == 'handled'
    db = SessionLocal()
    try:
        assert db.query(CertificateAnalysis).filter_by(id='outbox-ocr').one().ocr_text == 'Certificate ID: CF-123'
        out = db.query(OutboxEvent).filter_by(analysis_id='outbox-ocr', topic='ocr_completed').one()
        assert out.sent_at is None
        assert out.payload['analysis_id'] == 'outbox-ocr'
        assert 'ocr_text' not in out.payload
    finally:
        db.close()
    assert worker.process_ocr(event) == 'duplicate'
    db = SessionLocal()
    try:
        assert db.query(OutboxEvent).filter_by(analysis_id='outbox-ocr').count() == 1
    finally:
        db.close()


def test_ocr_field_values_do_not_include_labels_or_other_lines():
    from app.ocr import extract_fields
    text = 'CERTIFICATE OF COMPLETION\nName: Jane A Doe\nCertificate ID: CF-2026-000123\nDate: 2026-09-28\nInstitution: Global Institute of Technology\n'
    assert extract_fields(text) == {'name': 'Jane A Doe', 'certificate_no': 'CF-2026-000123',
                                    'date': '2026-09-28', 'institution': 'Global Institute of Technology'}


def test_dispatch_keeps_pending_on_failure_then_marks_after_ack(monkeypatch):
    from app import outbox
    from app.models import OutboxEvent
    db = SessionLocal()
    try:
        db.add(CertificateAnalysis(id='outbox-retry', user_id=None, filename='x.png', content_type='image/png', status='analyzing'))
        db.add(OutboxEvent(analysis_id='outbox-retry', topic='ocr_completed', payload={'analysis_id': 'outbox-retry'}))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(outbox, 'produce_event', lambda *a: False)
    assert outbox.dispatch_once(object()) == 'pending'
    db = SessionLocal()
    try:
        assert db.query(OutboxEvent).one().sent_at is None
    finally:
        db.close()
    monkeypatch.setattr(outbox, 'produce_event', lambda *a: True)
    assert outbox.dispatch_once(object()) == 'sent'
    assert outbox.dispatch_once(object()) == 'empty'


def test_completed_status_does_not_claim_probability_or_authenticity(client, auth_headers):
    from app.models import CertificateAnalysis
    headers = auth_headers()
    user = client.get('/auth/me', headers=headers).json()
    db = SessionLocal()
    try:
        db.add(CertificateAnalysis(id='legacy-score', user_id=user['id'], filename='x.png', content_type='image/png',
                  status='completed', authenticity_score=91.0, confidence=.91, verdict='Likely Genuine', suspicious_signals=[]))
        db.commit()
    finally:
        db.close()
    result = client.get('/status/legacy-score', headers=headers).json()
    assert result['authenticity_score'] is None and result['confidence'] is None
    assert result['verdict'] == 'Inconclusive'


def test_upload_persists_event_with_analysis_without_direct_kafka(client, auth_headers, monkeypatch):
    from app import api
    from app.models import OutboxEvent
    from tests.conftest import TEST_PNG_BYTES
    headers = auth_headers()
    monkeypatch.setattr(api, 'produce_event', lambda *a: (_ for _ in ()).throw(AssertionError('direct broker call')), raising=False)
    response = client.post('/analyze', headers=headers, files={'file': ('cert.png', TEST_PNG_BYTES, 'image/png')})
    assert response.status_code == 202
    analysis_id = response.json()['analysis_id']
    db = SessionLocal()
    try:
        assert db.query(CertificateAnalysis).filter_by(id=analysis_id).count() == 1
        pending = db.query(OutboxEvent).filter_by(analysis_id=analysis_id, topic='certificate_uploaded').one()
        assert pending.sent_at is None
    finally:
        db.close()


def test_overlong_bcrypt_password_rejected_without_truncation(client):
    password = 'é' * 40
    response = client.post('/auth/register', json={'email': 'long@example.com', 'password': password})
    assert response.status_code == 400
    assert '72' in response.json()['detail']


def test_oversized_upload_returns_413_without_storage(client, auth_headers, fake_storage, monkeypatch):
    from app.config import settings
    from tests.conftest import TEST_PNG_BYTES
    monkeypatch.setattr(settings, 'max_upload_mb', 0)
    response = client.post('/analyze', headers=auth_headers(), files={'file': ('x.png', TEST_PNG_BYTES, 'image/png')})
    assert response.status_code == 413
    assert not fake_storage


def test_ocr_db_failure_rolls_back_result_and_outbox(monkeypatch):
    from app.models import OutboxEvent
    db = SessionLocal()
    try:
        db.add(User(id='u', email='rollback-owner@example.com', password_hash='unused'))
        db.add(CertificateAnalysis(id='rollback-ocr', user_id='u', filename='x.png',
                                   content_type='image/png', status='analyzing'))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(worker, 'download_file_bytes', lambda _: b'image bytes')
    monkeypatch.setattr(worker, 'run_ocr', lambda *args: 'Certificate ID: CF-123')
    monkeypatch.setattr(worker, 'extract_fields', lambda _: {'certificate_no': 'CF-123'})
    original_factory = worker.SessionLocal

    def failing_session():
        session = original_factory()
        session.commit = lambda: (_ for _ in ()).throw(RuntimeError('injected commit failure'))
        return session

    monkeypatch.setattr(worker, 'SessionLocal', failing_session)
    import pytest
    with pytest.raises(RuntimeError, match='injected'):
        worker.process_ocr({'analysis_id': 'rollback-ocr', 'file_key': 'u/rollback-ocr/x.png', 'content_type': 'image/png'})
    db = SessionLocal()
    try:
        assert db.query(CertificateAnalysis).filter_by(id='rollback-ocr').one().ocr_text is None
        assert db.query(OutboxEvent).filter_by(analysis_id='rollback-ocr').count() == 0
    finally:
        db.close()


def test_ocr_retries_rotations_when_first_pass_has_few_fields(tmp_path, monkeypatch):
    from PIL import Image

    from app import ocr
    path = tmp_path / 'portrait.png'
    Image.new('RGB', (120, 80), 'white').save(path)
    calls = []

    def fake_ocr(image):
        calls.append(image.size)
        return ('Name: Jane Doe\nDate: 2026-09-28\n' if len(calls) == 2 else 'unreadable')

    monkeypatch.setattr(ocr, '_ocr_image', fake_ocr)
    text = ocr.run_ocr(str(path), 'image/png')
    assert text.startswith('Name: Jane')
    assert len(calls) == 3
    assert calls[0] == (120, 80) and calls[1] == (80, 120)


def test_forged_event_cannot_point_ocr_at_another_owners_object(monkeypatch):
    import pytest
    db = SessionLocal()
    try:
        db.add(User(id='owner-a', email='owner-a@example.com', password_hash='unused'))
        db.add(CertificateAnalysis(id='safe-analysis', user_id='owner-a', filename='cert.png',
                                   content_type='image/png', status='analyzing'))
        db.commit()
    finally:
        db.close()
    calls = []
    monkeypatch.setattr(worker, 'download_file_bytes', lambda key: calls.append(key) or b'data')
    with pytest.raises(ValueError, match='[Ee]vent'):
        worker.process_ocr({'analysis_id': 'safe-analysis', 'file_key': 'owner-b/other/original.png',
                            'content_type': 'image/png'})
    assert calls == []


def test_forged_event_cannot_point_forensics_at_another_owners_object(monkeypatch):
    import pytest

    from app.workers import forensics
    db = SessionLocal()
    try:
        db.add(User(id='owner-a', email='owner-a@example.com', password_hash='unused'))
        db.add(CertificateAnalysis(id='safe-analysis', user_id='owner-a', filename='cert.png',
                                   content_type='image/png', status='analyzing', ocr_text='Name: Jane'))
        db.commit()
    finally:
        db.close()
    calls = []
    monkeypatch.setattr(forensics, 'download_file_bytes', lambda key: calls.append(key) or b'data')
    with pytest.raises(ValueError, match='[Ee]vent'):
        forensics.process_forensics({'analysis_id': 'safe-analysis', 'file_key': 'owner-b/other/original.png',
                                     'content_type': 'image/png'})
    assert calls == []


def test_invalid_event_does_not_mark_victim_as_failed():
    from app.event_validation import InvalidEventError
    from app.workers import forensics
    db = SessionLocal()
    try:
        db.add(User(id='u', email='victim@example.com', password_hash='unused'))
        db.add(CertificateAnalysis(id='victim', user_id='u', filename='x.png',
                                   content_type='image/png', status='analyzing'))
        db.commit()
    finally:
        db.close()
    worker._mark_failed('victim', InvalidEventError('invalid event'))
    forensics._mark_failed('victim', InvalidEventError('invalid event'))
    db = SessionLocal()
    try:
        assert db.query(CertificateAnalysis).filter_by(id='victim').one().status == 'analyzing'
    finally:
        db.close()


def test_valid_generated_and_legacy_object_keys_are_accepted():
    from types import SimpleNamespace

    import pytest

    from app.event_validation import InvalidEventError, validate_document_reference
    row = SimpleNamespace(id='a1', user_id='user1', filename='certificate.png', content_type='image/png')
    validate_document_reference(row, 'a1', 'user1/a1/original.png', 'image/png')
    validate_document_reference(row, 'a1', 'user1/a1/certificate.png', 'image/png')
    with pytest.raises(InvalidEventError):
        validate_document_reference(row, 'a1', 'user1/a1/../other.png', 'image/png')
    with pytest.raises(InvalidEventError):
        validate_document_reference(row, 'a1', 'user1/a1/original.png', 'application/pdf')


def test_outbox_bound_analysis_rejects_a_forged_same_owner_reference(monkeypatch):
    import pytest

    from app.models import OutboxEvent
    db = SessionLocal()
    try:
        db.add(User(id='u', email='queue@example.com', password_hash='unused'))
        db.add(CertificateAnalysis(id='bound', user_id='u', filename='cert.png',
                                   content_type='image/png', status='analyzing'))
        db.add(OutboxEvent(analysis_id='bound', topic='certificate_uploaded',
            payload={'analysis_id': 'bound', 'file_key': 'u/bound/original.png', 'content_type': 'image/png'}))
        db.commit()
    finally:
        db.close()
    calls = []
    monkeypatch.setattr(worker, 'download_file_bytes', lambda key: calls.append(key) or b'data')
    with pytest.raises(ValueError, match='[Ee]vent'):
        worker.process_ocr({'analysis_id': 'bound', 'file_key': 'u/bound/cert.png', 'content_type': 'image/png'})
    assert not calls


def test_outbox_bound_matching_event_is_processed(monkeypatch):
    from app.models import OutboxEvent
    db = SessionLocal()
    try:
        db.add(User(id='u', email='matching@example.com', password_hash='unused'))
        db.add(CertificateAnalysis(id='bound', user_id='u', filename='cert.png',
                                   content_type='image/png', status='analyzing'))
        event = {'analysis_id': 'bound', 'file_key': 'u/bound/original.png', 'content_type': 'image/png'}
        db.add(OutboxEvent(analysis_id='bound', topic='certificate_uploaded', payload=event))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(worker, 'download_file_bytes', lambda _: b'data')
    monkeypatch.setattr(worker, 'run_ocr', lambda *_: 'Name: Jane')
    monkeypatch.setattr(worker, 'extract_fields', lambda _: {'name': 'Jane'})
    assert worker.process_ocr(event) == 'handled'
    db = SessionLocal()
    try:
        assert db.query(OutboxEvent).filter_by(analysis_id='bound', topic='ocr_completed').count() == 1
    finally:
        db.close()


def test_invalid_missing_reference_never_sets_terminal_failure():
    import pytest

    from app.event_validation import InvalidEventError
    db = SessionLocal()
    try:
        db.add(User(id='u', email='missing@example.com', password_hash='unused'))
        db.add(CertificateAnalysis(id='victim', user_id='u', filename='x.png',
                                   content_type='image/png', status='analyzing'))
        db.commit()
    finally:
        db.close()
    with pytest.raises(InvalidEventError) as caught:
        worker.process_ocr({'analysis_id': 'victim', 'content_type': 'image/png'})
    worker._mark_failed('victim', caught.value)
    db = SessionLocal()
    try:
        assert db.query(CertificateAnalysis).filter_by(id='victim').one().status == 'analyzing'
    finally:
        db.close()


def test_outbox_bound_forensic_event_requires_committed_stage(monkeypatch):
    import pytest

    from app.models import OutboxEvent
    from app.workers import forensics
    db = SessionLocal()
    try:
        db.add(User(id='u', email='stage@example.com', password_hash='unused'))
        db.add(CertificateAnalysis(id='stage', user_id='u', filename='cert.png',
                                   content_type='image/png', status='analyzing', ocr_text='Name: Jane'))
        event = {'analysis_id': 'stage', 'file_key': 'u/stage/original.png', 'content_type': 'image/png'}
        db.add(OutboxEvent(analysis_id='stage', topic='certificate_uploaded', payload=event))
        db.commit()
    finally:
        db.close()
    monkeypatch.setattr(forensics, 'download_file_bytes', lambda _: pytest.fail('storage must not be read'))
    with pytest.raises(ValueError, match='[Ee]vent'):
        forensics.process_forensics(event)
