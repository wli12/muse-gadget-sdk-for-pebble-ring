"""Test bounded SDK sends, token persistence and connection outcomes."""
import asyncio
import copy

import pytest
from muse_sender import send_message
from app_test import BUNDLE


@pytest.fixture
def sdk(monkeypatch):
    import musegadget.service as service
    from musegadget import config
    calls = []

    class Session:

        def __init__(self):
            self.registered = asyncio.Event()
            self._connect = None

        async def wait_registered(self, timeout=35):
            await asyncio.wait_for(self.registered.wait(), timeout)

        async def run(self, stop):
            self.registered.set()
            await stop.wait()

        async def send_chat(self, message, session_id=None):
            calls.append(message)
            if getattr(self, 'fail', False):
                raise TimeoutError
            return {'ok': True, 'response': {'message_id': 'synthetic'}}

        async def send_voice(self,
                             wav_bytes,
                             session_id=None,
                             *,
                             message='',
                             output_modality=None):
            calls.append(
                ('voice', wav_bytes, session_id, output_modality, message))
            if getattr(self, 'fail', False):
                raise TimeoutError
            return {'ok': True, 'response': {'message_id': 'synthetic'}}

    class FakeService:
        refresh_delay = 0
        fail_send = False

        def __init__(self, *args, **kwargs):
            self._stop = asyncio.Event()

        def stop(self):
            self._stop.set()

        async def _maybe_refresh(self, pairing, force=False):
            await asyncio.sleep(self.refresh_delay)
            updated = {**pairing, 'refresh_token': 'rotated-in-test'}
            config.save_json(config.PAIRING_FILE, updated)
            return updated

        async def run(self):
            await self._maybe_refresh(config.load_json(config.PAIRING_FILE))
            session = Session()
            session.fail = self.fail_send
            await self.serve(session, {}, self._stop)
            # Real Service would reconnect if not stopped. This verifies that
            # every serve exit stopped, including errors.
            assert self._stop.is_set()

    monkeypatch.setattr(service, 'Service', FakeService)
    return FakeService, calls


def test_rotated_pairing_persisted_before_send_and_no_commands(sdk):
    _, calls = sdk
    saved = []

    def before():
        assert saved[-1]['pairing']['refresh_token'] == 'rotated-in-test'

    result = send_message(copy.deepcopy(BUNDLE), 'synthetic command',
                          lambda x: saved.append(copy.deepcopy(x)), before)
    assert result.acknowledged and result.attempted
    assert calls == ['synthetic command']


def test_cancelled_refresh_still_persists_returned_token(sdk):
    service, calls = sdk
    service.refresh_delay = .03
    saved = []
    result = send_message(copy.deepcopy(BUNDLE),
                          'must not send',
                          lambda x: saved.append(copy.deepcopy(x)),
                          lambda: None,
                          deadline_seconds=.001)
    assert not result.attempted
    assert not calls
    assert saved[-1]['pairing']['refresh_token'] == 'rotated-in-test'


def test_send_timeout_is_not_retried(sdk):
    service, calls = sdk
    service.fail_send = True
    result = send_message(copy.deepcopy(BUNDLE), 'synthetic', lambda x: None,
                          lambda: None)
    assert result.attempted and not result.acknowledged
    assert len(calls) == 1


def synthetic_wav():
    import io
    import wave
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\0\0' * 160)
    return output.getvalue()


def test_voice_uses_official_send_voice_without_transcript(sdk):
    _, calls = sdk
    armed = []
    saved = []
    wav = synthetic_wav()

    def before():
        assert saved[-1]['pairing']['refresh_token'] == 'rotated-in-test'
        armed.append(True)

    result = send_message(copy.deepcopy(BUNDLE),
                          None,
                          lambda x: saved.append(x),
                          before,
                          wav_bytes=wav)
    assert result.acknowledged and result.attempted
    assert calls == [('voice', wav, None, 'text', '')]
    assert armed == [True]


def test_combined_note_uses_one_official_send_voice_with_pebble_text(sdk):
    _, calls = sdk
    armed = []
    wav = synthetic_wav()
    text = '请用中文回复：早上好，Muse。'
    result = send_message(copy.deepcopy(BUNDLE),
                          text,
                          lambda x: None,
                          lambda: armed.append(True),
                          wav_bytes=wav)
    assert result.acknowledged and result.attempted
    assert calls == [('voice', wav, None, 'text', text)]
    assert armed == [True]


def test_official_sdk_serializes_combined_note_as_text_and_wav_items():
    import base64
    from types import SimpleNamespace
    from musegadget.link_client import LinkSession
    requests = []
    wav = synthetic_wav()
    text = '请用中文回复：早上好，Muse。'

    class Capture:
        _device = SimpleNamespace(node_id='synthetic-device')

        async def _post_chat(self, body):
            requests.append(body)
            return {'ok': True}

    result = asyncio.run(
        LinkSession.send_voice(Capture(),
                               wav,
                               session_id='same-fixed-chat',
                               message=text,
                               output_modality='text'))
    assert result['ok'] and len(requests) == 1
    body = requests[0]
    assert body['message'] == text and body['session_id'] == 'same-fixed-chat'
    assert body['output_modality'] == 'text'
    text_item, voice_item = body['items']
    assert text_item == {'type': 'text', 'text': text}
    assert (voice_item['type'], voice_item['mime_type']) == ('file',
                                                             'audio/wav')
    assert base64.b64decode(voice_item['data_base64']) == wav


def test_empty_transcript_remains_a_recording_only_note(sdk):
    _, calls = sdk
    wav = synthetic_wav()
    result = send_message(copy.deepcopy(BUNDLE),
                          '',
                          lambda x: None,
                          lambda: None,
                          wav_bytes=wav)
    assert result.acknowledged
    assert calls == [('voice', wav, None, 'text', '')]


def test_voice_timeout_never_resends(sdk):
    service, calls = sdk
    service.fail_send = True
    result = send_message(copy.deepcopy(BUNDLE),
                          None,
                          lambda x: None,
                          lambda: None,
                          wav_bytes=synthetic_wav())
    assert result.attempted and not result.acknowledged
    assert len(calls) == 1 and calls[0][0] == 'voice'


def test_invalid_voice_payload_is_rejected_before_sdk(sdk):
    _, calls = sdk
    with pytest.raises(ValueError):
        send_message(copy.deepcopy(BUNDLE), {'invalid': 'text'},
                     lambda x: None,
                     lambda: None,
                     wav_bytes=synthetic_wav())
    assert not calls


def test_failed_credential_persistence_prevents_send(sdk):
    _, calls = sdk

    def fail(state):
        raise OSError('synthetic storage outage')

    result = send_message(copy.deepcopy(BUNDLE), 'must not send', fail,
                          lambda: pytest.fail('should not arm'))
    assert not result.attempted and not calls


def test_transport_exception_diagnostics_are_type_only():
    from muse_sender import Diagnostics

    async def run():
        diagnostic = Diagnostics()

        async def fail():
            raise ValueError('must never retain this private payload')

        task = asyncio.create_task(fail())
        await asyncio.gather(task, return_exceptions=True)
        diagnostic.transport(task)
        assert diagnostic.exception_type == 'ValueError'
        assert 'private payload' not in repr(vars(diagnostic))

    asyncio.run(run())


@pytest.fixture
def reconnecting_sdk(monkeypatch):
    """Exercise real Service.run/backoff using synthetic VM/session objects."""
    from musegadget import service as module, muse_api
    from musegadget.link_client import Outcome
    from websockets.exceptions import InvalidStatus
    from websockets.http11 import Response
    from websockets.datastructures import Headers
    calls = []
    connections = []
    delays = []
    statuses = [503, None]

    class FakeSession:
        registered_at = None
        _registration_error = None
        _connect = None

        def __init__(self, **kwargs):
            self.registered = asyncio.Event()
            self.failure = statuses[min(len(connections), len(statuses) - 1)]
            connections.append(self)

        async def wait_registered(self, timeout=35):
            await asyncio.wait_for(self.registered.wait(), timeout)

        async def run(self, stop):
            if self.failure is not None:
                raise InvalidStatus(
                    Response(self.failure, 'synthetic', Headers()))
            self.registered_at = 1.0
            self.registered.set()
            await stop.wait()
            return Outcome.STOPPED

        async def send_chat(self, message, session_id=None):
            calls.append(message)
            if send_failure[0]:
                raise InvalidStatus(Response(503, 'synthetic', Headers()))
            return {'ok': True, 'response': {'message_id': 'synthetic'}}

    async def no_sleep(self, delay):
        delays.append(delay)

    monkeypatch.setattr(module, 'LinkSession', FakeSession)
    monkeypatch.setattr(module.Service, '_sleep', no_sleep)
    monkeypatch.setattr(
        muse_api, 'fetch_vms_with_status', lambda *a: ([{
            'is_default': True,
            'vm_id': 'synthetic',
            'vm_name': 'synthetic',
            'vm_auth_token': 'synthetic'
        }], 200))
    monkeypatch.setattr(
        muse_api, 'refresh_device_token', lambda *a: ({
            'access_token': 'synthetic',
            'refresh_token': 'synthetic'
        }, 200))
    send_failure = [False]
    return statuses, calls, connections, delays, send_failure


def test_transient_upgrade_503_reconnects_then_sends_once(reconnecting_sdk):
    statuses, calls, connections, delays, _ = reconnecting_sdk
    armed = []
    result = send_message(copy.deepcopy(BUNDLE), 'synthetic', lambda x: None,
                          lambda: armed.append(True))
    assert result.acknowledged and result.attempted
    assert len(connections) == 2 and delays == [2.0]
    assert calls == ['synthetic'] and armed == [True]
    assert result.stage == 'acknowledged'


def test_transient_upgrade_stops_after_three_total_attempts(reconnecting_sdk):
    statuses, calls, connections, delays, _ = reconnecting_sdk
    statuses[:] = [502]
    result = send_message(copy.deepcopy(BUNDLE), 'must not send',
                          lambda x: None, lambda: pytest.fail('must not arm'))
    assert not result.attempted and not result.acknowledged
    assert len(connections) == 3 and delays == [2.0, 4.0]
    assert not calls and result.http_status == 502


@pytest.mark.parametrize('status,outcome', [(401, 'auth_rejected'),
                                            (403, 'forbidden')])
def test_nontransient_upgrade_auth_rejection_is_not_retried(
        reconnecting_sdk, status, outcome):
    statuses, calls, connections, delays, _ = reconnecting_sdk
    statuses[:] = [status]
    result = send_message(copy.deepcopy(BUNDLE), 'must not send',
                          lambda x: None, lambda: pytest.fail('must not arm'))
    assert not result.attempted
    assert len(connections) == 1 and not delays and not calls
    assert result.outcome == outcome


@pytest.mark.parametrize('field,value', [
    ('api_url_v2', 'https://collector.invalid'),
    ('noise_host', 'collector.invalid'),
])
def test_unapproved_destination_is_rejected_before_sdk(sdk, field, value):
    _, calls = sdk
    bundle = copy.deepcopy(BUNDLE)
    bundle['pairing'][field] = value
    with pytest.raises(ValueError, match='invalid_muse_destination'):
        send_message(bundle, 'synthetic',
                     lambda _: pytest.fail('must not persist'),
                     lambda: pytest.fail('must not arm'))
    assert not calls


def test_postattempt_http_503_is_never_retried(reconnecting_sdk):
    statuses, calls, connections, delays, send_failure = reconnecting_sdk
    statuses[:] = [None]
    send_failure[0] = True
    result = send_message(copy.deepcopy(BUNDLE), 'synthetic', lambda x: None,
                          lambda: None)
    assert result.attempted and not result.acknowledged
    assert len(connections) == 1 and len(calls) == 1 and not delays


def test_before_send_callback_failure_does_not_reconnect(reconnecting_sdk):
    statuses, calls, connections, delays, _ = reconnecting_sdk
    statuses[:] = [None]

    def fail():
        raise RuntimeError('synthetic ledger failure')

    result = send_message(copy.deepcopy(BUNDLE), 'must not send',
                          lambda x: None, fail)
    assert not result.attempted
    assert len(connections) == 1 and not calls and not delays


def test_unpair_event_uses_official_cleanup_and_persists_revocation(
        reconnecting_sdk, monkeypatch):
    from app import create_app, STATE_OBJECT
    from app_test import MemoryStore, NOW, post
    from musegadget import service as module
    from musegadget.link_client import Outcome

    _, calls, connections, delays, _ = reconnecting_sdk
    saved = []

    async def removed(self, stop):
        return Outcome.UNPAIRED

    monkeypatch.setattr(module.LinkSession, 'run', removed)
    result = send_message(copy.deepcopy(BUNDLE), 'must not send',
                          lambda state: saved.append(copy.deepcopy(state)),
                          lambda: pytest.fail('must not arm'))
    assert not result.attempted and not result.acknowledged
    assert result.outcome == 'unpaired'
    assert saved[-1]['pairing'] is None
    assert len(connections) == 1 and not calls and not delays

    # Later webhook requests fail safely instead of reusing revoked credentials.
    store = MemoryStore()
    _, generation = store.read(STATE_OBJECT)
    store.write(STATE_OBJECT, saved[-1], generation)
    client = create_app(store, clock=lambda: NOW).test_client()
    response = post(client)
    assert response.status_code == 503
    assert response.json == {'error': 'temporarily_unavailable'}
    assert len(connections) == 1 and not calls


def test_closed_transport_wins_over_simultaneous_registration(
        reconnecting_sdk, monkeypatch):
    from musegadget import service as module
    from musegadget.link_client import Outcome

    _, calls, connections, delays, _ = reconnecting_sdk

    async def closes_after_registration(self, stop):
        self.registered.set()
        await asyncio.sleep(0)
        self.registered.clear()
        return Outcome.CLOSED

    monkeypatch.setattr(module.LinkSession, 'run', closes_after_registration)
    result = send_message(copy.deepcopy(BUNDLE), 'must not send',
                          lambda state: None,
                          lambda: pytest.fail('closed transport must not arm'))
    assert not result.attempted and not result.acknowledged
    assert len(connections) == 1 and not calls and not delays


def test_cancelled_sdk_worker_stays_guarded_until_executor_shutdown(
        monkeypatch, tmp_path):
    """Keep a cancelled SDK fetch guarded until its worker thread exits."""
    from email.message import Message
    import io
    import os
    import socket
    import threading
    import urllib.request
    from urllib.response import addinfourl

    from musegadget import muse_api, service, tls
    import muse_transport

    def no_network(*_args, **_kwargs):
        raise AssertionError('External network is forbidden in this test')

    monkeypatch.setattr(socket.socket, 'connect', no_network)
    monkeypatch.setattr(socket, 'getaddrinfo', no_network)
    original_namespace = muse_api.urllib
    original_urlopen = urllib.request.urlopen
    original_request = urllib.request.Request
    original_wait_for = asyncio.wait_for
    original_shutdown = asyncio.BaseEventLoop.shutdown_default_executor
    state_before = str(tmp_path / 'previous-sdk-state')
    monkeypatch.setenv('MUSEGADGET_STATE_DIR', state_before)
    monkeypatch.setenv('MUSEGADGET_SDK_TOKEN', 'synthetic-previous-token')
    release_worker = threading.Event()
    worker_finished = threading.Event()
    worker_started = asyncio.Event()
    loop_holder = []
    opened, worker_errors, worker_results = [], [], []
    guarded_after_cancel = []
    cancelled = []

    def paused_request(*args, **kwargs):
        request = original_request(*args, **kwargs)
        loop_holder[0].call_soon_threadsafe(worker_started.set)
        if not release_worker.wait(timeout=5):
            raise AssertionError('Executor shutdown never released the worker')
        guarded = (muse_api.urllib is not original_namespace and
                   muse_api.urllib.request.urlopen is muse_transport._open_api)
        guarded_after_cancel.append(guarded)
        # Stop before urlopen if the original namespace was restored early.
        assert guarded, 'SDK transport was restored before its worker finished'
        assert urllib.request.urlopen is original_urlopen
        assert os.environ['MUSEGADGET_STATE_DIR'] != state_before
        assert os.path.isdir(os.environ['MUSEGADGET_STATE_DIR'])
        assert 'MUSEGADGET_SDK_TOKEN' not in os.environ
        return request

    def fetch():
        try:
            worker_results.append(
                muse_api.fetch_vms_with_status('synthetic-worker-access'))
        except BaseException as error:
            # Record failures in the cancelled worker for the main test.
            worker_errors.append(error)
        finally:
            worker_finished.set()

    class Opener:

        def open(self, request, timeout):
            opened.append((request.full_url, timeout))
            assert request.get_header('Authorization') == (
                'Bearer synthetic-worker-access')
            return addinfourl(io.BytesIO(b'{"vm_list": []}'),
                              Message(),
                              request.full_url,
                              code=200)

    async def background_fetch(self):
        loop_holder.append(asyncio.get_running_loop())
        await asyncio.to_thread(fetch)

    async def cancel_after_worker_started(awaitable, timeout):
        assert timeout == 5
        task = asyncio.create_task(awaitable)
        await original_wait_for(worker_started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        cancelled.append(True)
        # Model wait_for's completed cancellation; the executor still has work.
        raise TimeoutError

    async def release_during_shutdown(self, *args, **kwargs):
        assert cancelled == [True]
        assert not worker_finished.is_set()
        release_worker.set()
        return await original_shutdown(self, *args, **kwargs)

    monkeypatch.setattr(muse_transport, 'Request', paused_request)
    monkeypatch.setattr(muse_transport, 'build_opener', lambda *_args: Opener())
    monkeypatch.setattr(tls, 'client_context', object)
    monkeypatch.setattr(service.Service, 'run', background_fetch)
    monkeypatch.setattr(asyncio, 'wait_for', cancel_after_worker_started)
    monkeypatch.setattr(asyncio.BaseEventLoop, 'shutdown_default_executor',
                        release_during_shutdown)
    result = send_message(copy.deepcopy(BUNDLE),
                          'must not send',
                          lambda _: pytest.fail('must not persist'),
                          lambda: pytest.fail('must not arm'),
                          deadline_seconds=5)
    assert not result.attempted and not result.acknowledged
    assert worker_finished.is_set() and not worker_errors
    assert guarded_after_cancel == [True]
    assert worker_results == [([], 200)]
    assert opened == [('https://api.muse.ai/fetch_vms', 15)]
    assert muse_api.urllib is original_namespace
    assert urllib.request.urlopen is original_urlopen
    assert os.environ['MUSEGADGET_STATE_DIR'] == state_before
    assert os.environ['MUSEGADGET_SDK_TOKEN'] == 'synthetic-previous-token'
