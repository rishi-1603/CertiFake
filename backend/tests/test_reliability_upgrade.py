"""Targeted failure windows; mocked clients are not real Kafka integration tests."""
from app.models import CertificateAnalysis, SessionLocal
from app.workers import ocr as worker


def test_ocr_result_and_outbox_are_committed_together(monkeypatch):
    from app.models import OutboxEvent
    db = SessionLocal()
    try:
        db.add(CertificateAnalysis(id='outbox-ocr', user_id=None, filename='x.png',
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
        db.add(CertificateAnalysis(id='rollback-ocr', user_id=None, filename='x.png',
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
        worker.process_ocr({'analysis_id': 'rollback-ocr', 'file_key': 'u/rollback/x.png', 'content_type': 'image/png'})
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
