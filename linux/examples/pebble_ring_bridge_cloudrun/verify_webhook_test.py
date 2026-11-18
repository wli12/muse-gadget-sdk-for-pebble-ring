"""Tests for explicit, credential-safe webhook verification."""

import json

import pytest

import app
import app_test
import muse_sender
import verify_webhook


def client_transport(client):
    """Adapt the verifier's HTTP calls to the local Flask application."""

    def transport(request):
        response = client.post('/index',
                               data=request.data,
                               headers=dict(request.header_items()))
        return response.status_code, response.json

    return transport


def test_default_checks_do_not_submit_chat_or_write_delivery():
    calls = []
    client = app.create_app(app_test.MemoryStore(),
                            lambda *a, **k: calls.append(a)).test_client()
    report = verify_webhook.run('https://example.invalid/index',
                                app_test.AUTH,
                                transport=client_transport(client))
    assert report['send_requested'] is False
    assert 'delivery_id' not in report
    assert not calls
    assert len(report['checks']) == 2


def test_explicit_send_submits_once_and_report_contains_only_metadata(tmp_path):
    calls = []

    def sender(bundle, command, persist, before, **kwargs):
        before()
        calls.append(command)
        return muse_sender.SendResult(True, True)

    client = app.create_app(app_test.MemoryStore(), sender).test_client()
    report_path = tmp_path / 'report.json'
    report = verify_webhook.run('https://example.invalid/index',
                                app_test.AUTH,
                                send=True,
                                report_path=report_path,
                                transport=client_transport(client))
    assert calls == [verify_webhook.TEST_MESSAGE]
    assert report['delivery_state'] == 'delivered'
    stored_report = report_path.read_text()
    assert json.loads(stored_report) == report
    assert app_test.AUTH['bearer'] not in stored_report
    assert app_test.AUTH['signing_secret'] not in stored_report
    assert verify_webhook.TEST_MESSAGE not in stored_report
    assert report_path.stat().st_mode & 0o777 == 0o600


def test_uncertain_submission_stops_without_duplicate_attempt():
    calls = []

    def sender(bundle, command, persist, before, **kwargs):
        before()
        calls.append(command)
        return muse_sender.SendResult(False, True)

    client = app.create_app(app_test.MemoryStore(), sender).test_client()
    with pytest.raises(verify_webhook.VerificationError,
                       match='one_message_acknowledged_failed'):
        verify_webhook.run('https://example.invalid/index',
                           app_test.AUTH,
                           send=True,
                           transport=client_transport(client))
    assert len(calls) == 1


@pytest.mark.parametrize('value', [
    'http://example.com',
    'https://user:secret@example.com',
    'https://example.com/?token=secret',
    'https://example.com/wrong',
])
def test_unsafe_url_is_rejected(value):
    with pytest.raises(verify_webhook.VerificationError):
        verify_webhook.endpoint(value)


def test_redirect_is_refused():
    with pytest.raises(verify_webhook.VerificationError, match='redirect'):
        verify_webhook.NoRedirects().redirect_request(None, None, 302, '', {},
                                                      'https://other.invalid')
