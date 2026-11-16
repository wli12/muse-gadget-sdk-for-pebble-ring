"""One bounded official Muse SDK connection, with no host commands advertised.

No pairing, Noise or encryption code is replaced. The pinned upstream Service
handles device-token refresh; all rotated state is saved before chat submission
and again on every exit path. Never log SDK responses or exceptions.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Callable

# Silence SDK messages, which may contain account identifiers or API errors.
_sdk_log = logging.getLogger('musegadget')
_sdk_log.addHandler(logging.NullHandler())
_sdk_log.propagate = False
_STATE_LOCK = threading.Lock()
SAFE_EXCEPTION_TYPES = frozenset({
    'TimeoutError', 'ConnectionError', 'ConnectionResetError', 'ConnectionRefusedError',
    'OSError', 'SSLError', 'SSLCertVerificationError', 'URLError', 'HTTPError',
    'ImportError', 'ModuleNotFoundError', 'AttributeError', 'TypeError', 'ValueError',
    'RuntimeError', 'InvalidStatus', 'InvalidHandshake', 'InvalidMessage',
    'InvalidURI', 'ConnectionClosed', 'ConnectionClosedError', 'ConnectionClosedOK',
    'InvalidTag', 'StorageConflict', 'LeaseBusy', 'PreconditionFailed', 'Forbidden',
    'NotFound', 'ServiceUnavailable', 'CancelledError',
})
SAFE_STAGES = frozenset({
    'initializing', 'sdk_import', 'refreshing', 'state_persist', 'state_persist_failed',
    'connecting', 'registration_timeout', 'transport_ended', 'before_send',
    'submitting', 'acknowledged', 'connection_failed',
})
SAFE_OUTCOMES = frozenset({'closed', 'auth_rejected', 'forbidden', 'unpaired', 'stopped'})


def safe_exception_type(error):
    name = type(error).__name__
    return name if name in SAFE_EXCEPTION_TYPES else 'OtherError'


def safe_http_status(error):
    for value in (getattr(error, 'status', None), getattr(error, 'status_code', None),
                  getattr(getattr(error, 'response', None), 'status_code', None)):
        if type(value) is int and 100 <= value <= 599:
            return value
    return None


class Diagnostics:
    """Minimal direct status enums; never retain an exception's message/args."""
    def __init__(self):
        self.stage = 'initializing'
        self.exception_type = ''
        self.http_status = None
        self.outcome = ''

    def exception(self, error):
        self.exception_type = safe_exception_type(error)
        self.http_status = safe_http_status(error) or self.http_status

    def transport(self, task):
        if not task.done() or task.cancelled():
            return
        error = task.exception()
        if error is not None:
            self.exception(error)
            return
        value = getattr(task.result(), 'value', '')
        if value in SAFE_OUTCOMES:
            self.outcome = value



@dataclass(frozen=True)
class SendResult:
    acknowledged: bool
    attempted: bool
    stage: str = ''
    exception_type: str = ''
    http_status: int | None = None
    outcome: str = ''


class NoCommands:
    def specs(self, **kwargs):
        return {}

    def run(self, *args, **kwargs):
        return {'ok': False, 'error': 'Host commands are not enabled on this bridge'}


def validate_bundle(bundle: dict) -> None:
    if not isinstance(bundle, dict):
        raise ValueError('invalid_state_bundle')
    identity = bundle.get('identity')
    if not isinstance(identity, dict) or not re.fullmatch(r'[0-9a-f]{2}(?::[0-9a-f]{2}){5}', str(identity.get('mac', ''))):
        raise ValueError('invalid_identity')
    pairing = bundle.get('pairing')
    if not isinstance(pairing, dict) or any(not isinstance(pairing.get(k), str) or not pairing[k] for k in ('access_token', 'refresh_token')):
        raise ValueError('pairing_required')
    if not isinstance(bundle.get('sdk_token'), str) or not bundle['sdk_token'].startswith('mgst_'):
        raise ValueError('sdk_token_required')


def send_message(bundle: dict, message: str | None, persist_state: Callable[[dict], None],
                 before_send: Callable[[], None], *, session_id: str | None = None,
                 deadline_seconds: float = 55, wav_bytes: bytes | None = None) -> SendResult:
    """Persist state and mark delivery BEFORE sending; never retry a chat send.

    If a process dies after before_send, the durable delivery ledger intentionally
    remains uncertain. A caller must not automatically send that delivery again.
    """
    validate_bundle(bundle)
    if wav_bytes is not None:
        if ((message is not None and not isinstance(message, str)) or not isinstance(wav_bytes, bytes)
                or not 44 <= len(wav_bytes) <= 2 * 1024 * 1024
                or wav_bytes[:4] != b'RIFF' or wav_bytes[8:12] != b'WAVE'):
            raise ValueError('invalid_voice_payload')
    elif not isinstance(message, str) or not message:
        raise ValueError('message_required')
    # The upstream state directory is process-global; concurrent threads must not
    # swap it. Cloud Run should additionally use concurrency=1, one worker.
    with _STATE_LOCK, tempfile.TemporaryDirectory(prefix='muse-state-') as tmp:
        state_dir = Path(tmp)
        os.chmod(state_dir, 0o700)
        for name, value in (('identity.json', bundle['identity']), ('pairing.json', bundle['pairing'])):
            fd = os.open(state_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'w') as file:
                json.dump(value, file)
        fd = os.open(state_dir / 'sdk_token', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as file:
            file.write(bundle['sdk_token'])
        old_dir = os.environ.get('MUSEGADGET_STATE_DIR')
        old_token = os.environ.pop('MUSEGADGET_SDK_TOKEN', None)
        os.environ['MUSEGADGET_STATE_DIR'] = tmp
        last_saved = json.dumps(bundle, sort_keys=True)
        attempted = False
        before_send_entered = False
        connection_attempts = 0
        acknowledged = False
        diagnostic = Diagnostics()

        def persist() -> None:
            nonlocal last_saved
            pairing_path = state_dir / 'pairing.json'
            updated = {**bundle, 'pairing': json.loads(pairing_path.read_text()) if pairing_path.exists() else None}
            current = json.dumps(updated, sort_keys=True)
            if current != last_saved:
                diagnostic.stage = 'state_persist'
                persist_state(updated)
                last_saved = current

        async def once() -> None:
            diagnostic.stage = 'sdk_import'
            from musegadget import config, identity
            from musegadget.service import Service
            from musegadget.link_client import Outcome, acknowledgement
            from websockets.exceptions import InvalidStatus

            class OneShot(Service):
                async def _maybe_refresh(self, pairing, force=False):
                    # urllib refresh executes in a thread. Cancellation must not
                    # abandon the returned replacement refresh token.
                    diagnostic.stage = 'refreshing'
                    task = asyncio.create_task(super()._maybe_refresh(pairing, force))
                    try:
                        return await asyncio.shield(task)
                    except asyncio.CancelledError:
                        try:
                            await task
                        finally:
                            persist()
                        raise
                    finally:
                        if task.done() and not task.cancelled():
                            persist()

                async def serve(self, session, vm, stop):
                    nonlocal attempted, before_send_entered, connection_attempts, acknowledged
                    if attempted or before_send_entered:
                        self.stop()
                        return Outcome.STOPPED
                    connection_attempts += 1
                    diagnostic.stage = 'connecting'
                    diagnostic.exception_type = ''
                    diagnostic.http_status = None
                    diagnostic.outcome = ''
                    retry_connection = False
                    transport = asyncio.create_task(session.run(stop))
                    registration = asyncio.create_task(session.registered.wait())
                    try:
                        done, _ = await asyncio.wait((transport, registration), timeout=35,
                                                     return_when=asyncio.FIRST_COMPLETED)
                        if registration not in done or not registration.result():
                            diagnostic.transport(transport)
                            registration_error = getattr(session, '_registration_error', None)
                            if registration_error is not None:
                                diagnostic.exception(registration_error)
                            if diagnostic.stage == 'connecting':
                                diagnostic.stage = 'registration_timeout' if not done else 'transport_ended'
                            # Retry only an explicit transient HTTP response
                            # before Noise/registration, never a chat submission.
                            failure = (transport.exception() if transport.done() and not transport.cancelled() else None)
                            retry_connection = (
                                not attempted and not before_send_entered
                                and connection_attempts < 3
                                and isinstance(failure, InvalidStatus)
                                and safe_http_status(failure) in {429, 500, 502, 503, 504}
                            )
                            # CLOSED lets the unchanged official Service re-fetch
                            # the VM bearer and apply its bounded backoff.
                            return Outcome.CLOSED if retry_connection else Outcome.STOPPED
                        # Service may have refreshed its tokens before serve().
                        # Persist synchronously before any message can leave.
                        persist()
                        diagnostic.stage = 'before_send'
                        before_send_entered = True
                        before_send()
                        attempted = True
                        diagnostic.stage = 'submitting'
                        submission = (session.send_voice(wav_bytes, session_id=session_id, message=message or '',
                                                         output_modality='text')
                                      if wav_bytes is not None else session.send_chat(message, session_id=session_id))
                        result = await asyncio.wait_for(submission, 25)
                        acknowledgement(result)
                        acknowledged = True
                        diagnostic.stage = 'acknowledged'
                        return Outcome.STOPPED
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        diagnostic.exception(error)
                        return Outcome.STOPPED
                    finally:
                        if not retry_connection:
                            self.stop()
                            stop.set()
                        registration.cancel()
                        if not transport.done():
                            transport.cancel()
                        await asyncio.gather(registration, transport, return_exceptions=True)

            service = OneShot(identity.load_or_create(), NoCommands(), sdk_token=config.sdk_token(),
                              display_name='Index Ring Bridge', model_id='pebble-ring-bridge',
                              model_name='Pebble Ring Bridge', guide=None)
            await asyncio.wait_for(service.run(), deadline_seconds)

        try:
            asyncio.run(once())
        except Exception as error:
            diagnostic.exception(error)
        finally:
            try:
                persist()
            except Exception as error:
                diagnostic.stage = 'state_persist_failed'
                diagnostic.exception(error)
                # Even a confirmed message must remain non-retryable. The
                # transport acknowledgement is retained in the result below.
            if old_dir is None:
                os.environ.pop('MUSEGADGET_STATE_DIR', None)
            else:
                os.environ['MUSEGADGET_STATE_DIR'] = old_dir
            if old_token is not None:
                os.environ['MUSEGADGET_SDK_TOKEN'] = old_token
        return SendResult(acknowledged, attempted,
                          stage=diagnostic.stage if diagnostic.stage in SAFE_STAGES else 'connection_failed',
                          exception_type=diagnostic.exception_type,
                          http_status=diagnostic.http_status, outcome=diagnostic.outcome)
