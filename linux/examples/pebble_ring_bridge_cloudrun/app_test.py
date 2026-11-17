"""Test webhook delivery with synthetic state and payloads."""
import copy
import hashlib
import hmac
import json
import threading
from types import SimpleNamespace

import pytest
from app import (AUTH_OBJECT, LEASE_OBJECT, STATE_OBJECT, Lease,
                 StorageConflict, create_app)
from muse_sender import SendResult

NOW = 1_800_000_000
AUTH = {'bearer': 'b' * 64, 'signing_secret': 's' * 64}
BUNDLE = {
    'identity': {
        'mac': '02:00:00:00:00:01'
    },
    'pairing': {
        'access_token': 'synthetic-access',
        'refresh_token': 'synthetic-refresh'
    },
    'sdk_token': 'mgst_' + 'A' * 43
}


class MemoryStore:

    def __init__(self):
        self.values = {}
        self.next = 1
        self.lock = threading.Lock()
        self.write(AUTH_OBJECT, AUTH, 0)
        self.write(STATE_OBJECT, BUNDLE, 0)

    def read(self, name):
        with self.lock:
            return copy.deepcopy(self.values.get(name, (None, 0)))

    def write(self, name, value, generation):
        with self.lock:
            previous = self.values.get(name, (None, 0))[1]
            if previous != generation:
                raise StorageConflict
            self.next += 1
            self.values[name] = (copy.deepcopy(value), self.next)
            return self.next

    def delete(self, name, generation):
        with self.lock:
            if self.values.get(name, (None, 0))[1] != generation:
                raise StorageConflict
            del self.values[name]


def signed(text='Muse，帮我测试',
           delivery='delivery-1',
           test=False,
           timestamp=NOW,
           audio=None,
           audio_type='audio/mp4',
           token=None):
    boundary = 'synthetic-test-boundary'
    fields = {'client': 'ring', 'recordedAt': str(NOW * 1000)}
    if text is not None:
        fields['transcription'] = text
    if test:
        fields['test'] = 'true'
    if token is not None:
        fields['token'] = token
    body = b''
    for key, value in fields.items():
        body += (f'--{boundary}\r\n'
                 f'Content-Disposition: form-data; name="{key}"\r\n\r\n'
                 f'{value}\r\n').encode()
    if audio is not None:
        body += (
            f'--{boundary}\r\n'
            'Content-Disposition: form-data; name="audio"; '
            'filename="recording.m4a"\r\n'
            f'Content-Type: {audio_type}\r\n\r\n').encode() + audio + b'\r\n'
    body += f'--{boundary}--\r\n'.encode()
    trigger = 'test-event' if test else 'double-click-hold'
    prefix = (f'v1\n{timestamp}\n{delivery}\n'
              f'{trigger}\n{1 if test else 0}\n').encode()
    headers = {
        'Content-Type':
            f'multipart/form-data; boundary={boundary}',
        'Authorization':
            'Bearer ' + AUTH['bearer'],
        'X-Index-Webhook-Version':
            '1',
        'X-Index-Timestamp':
            str(timestamp),
        'X-Index-Delivery':
            delivery,
        'X-Index-Trigger':
            trigger,
        'X-Index-Signature':
            hmac.new(AUTH['signing_secret'].encode(), prefix + body,
                     hashlib.sha256).hexdigest()
    }
    if test:
        headers['X-Index-Test'] = 'true'
    return body, headers


@pytest.fixture
def setup():
    db = MemoryStore()
    calls = []

    def sender(bundle, command, persist, before, **kwargs):
        before()
        calls.append(command)
        return SendResult(True, True)

    app = create_app(db, sender, lambda: NOW)
    return db, calls, app.test_client()


def post(client, **kwargs):
    raw, headers = signed(**kwargs)
    return client.post('/index', data=raw, headers=headers)


def test_valid_signed_chinese_note_and_duplicate(setup):
    db, calls, client = setup
    assert post(client).json == {'ok': True, 'status': 'delivered'}
    assert post(client).json['duplicate'] is True
    assert calls == ['Muse，帮我测试']
    ledger = next(v[0] for k, v in db.values.items() if k.startswith('ledger/'))
    assert ledger['status'] == 'delivered'
    assert '帮我测试' not in json.dumps(ledger, ensure_ascii=False)


@pytest.mark.parametrize('change', [
    'auth', 'signature', 'body', 'version', 'timestamp', 'delivery', 'trigger'
])
def test_forged_or_expired_input_never_calls_sdk(setup, change):
    db, calls, client = setup
    raw, headers = signed()
    if change == 'auth':
        headers['Authorization'] = 'Bearer wrong'
    elif change == 'signature':
        headers['X-Index-Signature'] = 'x'
    elif change == 'body':
        raw += b'changed'
    elif change == 'version':
        headers['X-Index-Webhook-Version'] = '2'
    elif change == 'timestamp':
        raw, headers = signed(timestamp=NOW - 301)
    elif change == 'delivery':
        headers['X-Index-Delivery'] = ''
    elif change == 'trigger':
        headers['X-Index-Trigger'] = 'single-click'
    assert client.post('/index', data=raw,
                       headers=headers).status_code in (400, 401)
    assert not calls
    assert not any(k.startswith('ledger/') for k in db.values)


def test_test_event_does_not_forward(setup):
    db, calls, client = setup
    assert post(client, test=True).json['test'] is True
    assert not calls
    assert not any(k.startswith('ledger/') for k in db.values)


@pytest.mark.parametrize(
    'text', ['普通中文笔记', 'Hey Muse 今天天气怎么样', 'museum opening hours', '我想知道天气'])
def test_all_real_transcriptions_forward_verbatim_without_keyword_filter(
        setup, text):
    db, calls, client = setup
    assert post(client, text=text).json['status'] == 'delivered'
    assert calls == [text]


def test_delivery_id_cannot_be_reused_for_different_command(setup):
    db, calls, client = setup
    assert post(client).status_code == 200
    assert post(client, text='Muse 另一条命令').status_code == 409
    assert len(calls) == 1


def test_ambiguous_send_is_durable_and_never_retried():
    db = MemoryStore()
    calls = []

    def sender(bundle, text, persist, before, **kwargs):
        before()
        calls.append(text)
        raise TimeoutError('must not appear in HTTP response')

    client = create_app(db, sender, lambda: NOW).test_client()
    assert post(client).json['status'] == 'needs_review'
    assert post(client).json['status'] == 'needs_review'
    assert len(calls) == 1


def test_failed_before_send_is_retryable():
    db = MemoryStore()
    calls = []

    def sender(bundle, text, persist, before, **kwargs):
        calls.append(text)
        if len(calls) == 1:
            return SendResult(False, False, stage='connecting')
        before()
        return SendResult(True, True)

    client = create_app(db, sender, lambda: NOW).test_client()
    assert post(client).status_code == 503
    assert post(client).json['status'] == 'delivered'


def test_lease_blocks_cross_revision_and_cannot_delete_successor():
    db = MemoryStore()
    lease = Lease.acquire(db, lambda: NOW)
    client = create_app(db, lambda *a, **k: pytest.fail('must not send'),
                        lambda: NOW).test_client()
    assert post(client).status_code == 503
    # An expired holder cannot release or use its successor's lock.
    successor = Lease.acquire(db, lambda: NOW + 301)
    lease.release()
    assert db.read(LEASE_OBJECT)[0]['owner'] == successor.owner
    with pytest.raises(Exception):
        lease.check()


def test_token_changes_persist_before_send():
    db = MemoryStore()

    def sender(bundle, text, persist, before, **kwargs):
        bundle['pairing']['refresh_token'] = 'rotated-synthetic'
        persist(bundle)
        assert db.read(
            STATE_OBJECT)[0]['pairing']['refresh_token'] == 'rotated-synthetic'
        before()
        return SendResult(True, True)

    client = create_app(db, sender, lambda: NOW).test_client()
    assert post(client).status_code == 200


def test_health_does_not_read_or_initialize_cloud_storage():
    client = create_app().test_client()
    assert client.get('/health').json == {'ok': True}


def test_oversize_body_rejected_before_authentication(setup):
    _, calls, client = setup
    assert client.post('/index',
                       data=b'a' * (9 * 1024 * 1024 + 1)).status_code == 413
    assert not calls


def test_ledger_stores_only_whitelisted_sender_diagnostics():
    db = MemoryStore()

    def sender(bundle, text, persist, before, **kwargs):
        return SimpleNamespace(acknowledged=False,
                               attempted=False,
                               unexpected='private-exception-message',
                               stage='transport_ended',
                               exception_type='ValueError',
                               http_status=403,
                               outcome='forbidden')

    client = create_app(db, sender, lambda: NOW).test_client()
    assert post(client).status_code == 503
    ledger = next(v[0] for k, v in db.values.items() if k.startswith('ledger/'))
    assert ledger['diagnostic'] == {
        'stage': 'transport_ended',
        'exception_type': 'ValueError',
        'http_status': 403,
        'outcome': 'forbidden'
    }
    assert 'private-exception' not in json.dumps(ledger)


def test_ledger_rejects_nonwhitelisted_diagnostic_content():
    db = MemoryStore()

    def sender(bundle, text, persist, before, **kwargs):
        return SendResult(False,
                          False,
                          stage='private-message',
                          exception_type='private-token',
                          http_status='secret',
                          outcome='private-response')

    client = create_app(db, sender, lambda: NOW).test_client()
    assert post(client).status_code == 503
    ledger = next(v[0] for k, v in db.values.items() if k.startswith('ledger/'))
    assert ledger['diagnostic'] == {}


def test_recording_only_is_forwarded_and_deduplicated(monkeypatch):
    import app as module
    db = MemoryStore()
    calls = []
    decoded = []
    canonical = b'canonical-synthetic-wav'
    binary_audio = b'\x00\x00\x00\x18ftypM4A ' + bytes(range(256)) + b'\r\n'

    def normalize(raw, mime):
        decoded.append((raw, mime))
        return canonical

    monkeypatch.setattr(module, 'normalize_audio', normalize)

    def sender(bundle, text, persist, before, **kwargs):
        before()
        calls.append((text, kwargs['wav_bytes']))
        return SendResult(True, True)

    client = create_app(db, sender, lambda: NOW).test_client()
    assert post(client, audio=binary_audio,
                text=None).json['status'] == 'delivered'
    # Different container bytes that decode to the same samples share a digest.
    assert post(client, audio=b'another-container',
                text=None).json['duplicate'] is True
    assert calls == [(None, canonical)]
    assert decoded == [(binary_audio, 'audio/mp4'),
                       (b'another-container', 'audio/mp4')]
    ledger = next(v[0] for k, v in db.values.items() if k.startswith('ledger/'))
    assert ledger['digest'] == hashlib.sha256(b'audio/wav\0' +
                                              canonical).hexdigest()
    assert 'synthetic' not in json.dumps(ledger)


def test_both_preserves_transcription_and_audio_in_one_delivery(monkeypatch):
    import app as module
    db = MemoryStore()
    calls = []
    canonical = b'canonical-synthetic-wav'
    monkeypatch.setattr(
        module, 'normalize_audio', lambda raw, mime: canonical if raw in
        {b'first-container', b'another-container'} else b'different-wav')

    def sender(bundle, text, persist, before, **kwargs):
        before()
        calls.append((text, kwargs['wav_bytes']))
        return SendResult(True, True)

    client = create_app(db, sender, lambda: NOW).test_client()
    text = '请用中文回复：早上好，Muse。'
    assert post(client, audio=b'first-container',
                text=text).json['status'] == 'delivered'
    assert post(client, audio=b'another-container',
                text=text).json['duplicate'] is True
    assert calls == [(text, canonical)]
    # Changing either logical part under the same delivery ID is a conflict.
    assert post(client, audio=b'first-container',
                text='另一份转写').status_code == 409
    assert post(client, audio=b'first-container', text=None).status_code == 409
    assert post(client, audio=b'changed-recording',
                text=text).status_code == 409
    assert len(calls) == 1
    ledger = next(v[0] for k, v in db.values.items() if k.startswith('ledger/'))
    encoded_text = text.encode()
    assert ledger['digest'] == hashlib.sha256(
        b'audio/wav+text\0' + len(encoded_text).to_bytes(4, 'big') +
        encoded_text + canonical).hexdigest()
    assert text not in json.dumps(ledger, ensure_ascii=False)


@pytest.mark.parametrize('text', ['', '  \n'])
def test_empty_transcription_with_audio_remains_recording_only(
        monkeypatch, text):
    import app as module
    db = MemoryStore()
    calls = []
    canonical = b'canonical'
    monkeypatch.setattr(module, 'normalize_audio', lambda raw, mime: canonical)

    def sender(bundle, command, persist, before, **kwargs):
        before()
        calls.append((command, kwargs['wav_bytes']))
        return SendResult(True, True)

    client = create_app(db, sender, lambda: NOW).test_client()
    assert post(client, audio=b'container',
                text=text).json['status'] == 'delivered'
    assert post(client, audio=b'container', text=None).json['duplicate'] is True
    assert calls == [(None, canonical)]


def test_oversized_combined_transcript_rejected_before_decode_or_lease(
        monkeypatch):
    import app as module
    db = MemoryStore()
    monkeypatch.setattr(module, 'normalize_audio',
                        lambda *a: pytest.fail('must not decode'))
    client = create_app(db, lambda *a, **k: pytest.fail('must not send'),
                        lambda: NOW).test_client()
    result = post(client, audio=b'container', text='中' * (module.MAX_TEXT + 1))
    assert result.status_code == 422 and result.json[
        'error'] == 'invalid_transcription'
    assert not any(
        k.startswith('ledger/') or k == LEASE_OBJECT for k in db.values)


def test_failed_audio_decode_never_opens_sdk_and_valid_retry_can_send(
        monkeypatch):
    import app as module
    from audio import AudioError
    db = MemoryStore()
    calls = []

    def normalize(raw, mime):
        if raw == b'invalid':
            raise AudioError('audio_decode_failed')
        return b'canonical'

    monkeypatch.setattr(module, 'normalize_audio', normalize)

    def sender(bundle, text, persist, before, **kwargs):
        before()
        calls.append(kwargs['wav_bytes'])
        return SendResult(True, True)

    client = create_app(db, sender, lambda: NOW).test_client()
    assert post(client, audio=b'invalid', text=None).status_code == 422
    assert not calls and not any(k.startswith('ledger/') for k in db.values)
    assert post(client, audio=b'valid', text=None).json['status'] == 'delivered'
    assert calls == [b'canonical']


def test_audio_authentication_and_synthetic_test_precede_decode(monkeypatch):
    import app as module
    monkeypatch.setattr(module, 'normalize_audio',
                        lambda *a: pytest.fail('must not decode'))
    client = create_app(MemoryStore(),
                        lambda *a, **k: pytest.fail('must not send'),
                        lambda: NOW).test_client()
    assert post(client, audio=b'invalid', test=True).json['test'] is True
    raw, headers = signed(audio=b'invalid')
    headers['Authorization'] = 'Bearer invalid'
    assert client.post('/index', data=raw, headers=headers).status_code == 401


def test_audio_ambiguous_send_is_not_repeated_or_replaced(monkeypatch):
    import app as module
    monkeypatch.setattr(module, 'normalize_audio', lambda raw, mime: raw)
    calls = []
    db = MemoryStore()

    def sender(bundle, text, persist, before, **kwargs):
        before()
        calls.append(kwargs['wav_bytes'])
        raise TimeoutError

    client = create_app(db, sender, lambda: NOW).test_client()
    assert post(client, audio=b'first', text=None).status_code == 202
    assert post(client, audio=b'first', text=None).status_code == 202
    assert post(client, audio=b'different', text=None).status_code == 409
    assert calls == [b'first']


@pytest.mark.parametrize(
    ('authorization', 'alternate_token', 'body_token', 'expected_status'), [
        ('Bearer wrong', AUTH['bearer'], None, 401),
        (None, AUTH['bearer'], None, 401),
        (None, None, AUTH['bearer'], 401),
        ('bearer ' + AUTH['bearer'], None, None, 401),
        ('Bearer ' + AUTH['bearer'], 'ignored alternate token', None, 200),
    ])
def test_cloud_requires_exact_bearer_without_legacy_token_fallback(
        setup, authorization, alternate_token, body_token, expected_status):
    _, calls, client = setup
    raw, headers = signed(token=body_token)
    if authorization is None:
        headers.pop('Authorization')
    else:
        headers['Authorization'] = authorization
    if alternate_token is not None:
        headers['X-Pebble-Token'] = alternate_token
    assert client.post('/index', data=raw,
                       headers=headers).status_code == expected_status
    assert len(calls) == (1 if expected_status == 200 else 0)
