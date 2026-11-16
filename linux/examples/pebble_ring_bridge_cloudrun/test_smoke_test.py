import importlib.util
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).parent))
import smoke_test
from app import create_app
from test_bridge import MemoryStore, AUTH
from muse_sender import SendResult


def client_transport(client):
    def transport(request):
        response = client.post('/index', data=request.data, headers=dict(request.header_items()))
        return response.status_code, response.json
    return transport


def test_default_smoke_does_not_send_or_write_delivery(tmp_path):
    calls = []
    client = create_app(MemoryStore(), lambda *a, **k: calls.append(a)).test_client()
    report = smoke_test.run('https://example.invalid/index', AUTH, transport=client_transport(client))
    assert report['send_requested'] is False
    assert 'delivery_id' not in report
    assert not calls
    assert len(report['checks']) == 2


def test_explicit_send_sends_once_and_report_is_metadata_only(tmp_path):
    calls = []
    def sender(bundle, command, persist, before, **kwargs):
        before(); calls.append(command); return SendResult(True, True)
    client = create_app(MemoryStore(), sender).test_client()
    report_path = tmp_path/'report.json'
    report = smoke_test.run('https://example.invalid/index', AUTH, send=True, report_path=report_path,
                            transport=client_transport(client))
    assert calls == ['Muse，云端戒指中转测试，请只回复：云端中转成功']
    assert report['delivery_state'] == 'delivered'
    text = report_path.read_text()
    assert AUTH['bearer'] not in text and AUTH['signing_secret'] not in text
    assert '云端戒指' not in text
    assert report_path.stat().st_mode & 0o777 == 0o600


def test_uncertain_send_stops_without_duplicate_attempt():
    calls = []
    def sender(bundle, command, persist, before, **kwargs):
        before(); calls.append(command); return SendResult(False, True)
    client = create_app(MemoryStore(), sender).test_client()
    with pytest.raises(smoke_test.SmokeFailure, match='one_message_acknowledged_failed'):
        smoke_test.run('https://example.invalid/index', AUTH, send=True, transport=client_transport(client))
    assert len(calls) == 1


@pytest.mark.parametrize('value', ['http://example.com', 'https://user:secret@example.com',
                                  'https://example.com/?token=secret', 'https://example.com/wrong'])
def test_unsafe_url_rejected(value):
    with pytest.raises(smoke_test.SmokeFailure):smoke_test.endpoint(value)


def test_redirect_refused():
    with pytest.raises(smoke_test.SmokeFailure, match='redirect'):
        smoke_test.NoRedirects().redirect_request(None, None, 302, '', {}, 'https://other.invalid')
