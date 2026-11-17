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
    'TimeoutError',
    'ConnectionError',
    'ConnectionResetError',
    'ConnectionRefusedError',
    'OSError',
    'SSLError',
    'SSLCertVerificationError',
    'URLError',
    'HTTPError',
    'ImportError',
    'ModuleNotFoundError',
    'AttributeError',
    'TypeError',
    'ValueError',
    'RuntimeError',
    'InvalidStatus',
    'InvalidHandshake',
    'InvalidMessage',
    'InvalidURI',
    'ConnectionClosed',
    'ConnectionClosedError',
    'ConnectionClosedOK',
    'InvalidTag',
    'StorageConflict',
    'LeaseBusy',
    'PreconditionFailed',
    'Forbidden',
    'NotFound',
    'ServiceUnavailable',
    'CancelledError',
})
SAFE_STAGES = frozenset({
    'initializing',
    'sdk_import',
    'refreshing',
    'state_persist',
    'state_persist_failed',
    'connecting',
    'registration_timeout',
    'transport_ended',
    'before_send',
    'submitting',
    'acknowledged',
    'connection_failed',
})
SAFE_OUTCOMES = frozenset(
    {'closed', 'auth_rejected', 'forbidden', 'unpaired', 'stopped'})


def safe_exception_type(error: BaseException) -> str:
    """Return an allowlisted class name without retaining exception text."""
    name = type(error).__name__
    return name if name in SAFE_EXCEPTION_TYPES else 'OtherError'


def safe_http_status(error: BaseException) -> int | None:
    """Extract an HTTP status while excluding error messages and responses."""
    for value in (getattr(error, 'status',
                          None), getattr(error, 'status_code', None),
                  getattr(getattr(error, 'response', None), 'status_code',
                          None)):
        if isinstance(value, int) and 100 <= value <= 599:
            return value
    return None


class Diagnostics:
    """Minimal direct status enums; never retain an exception's message/args."""

    def __init__(self) -> None:
        self.stage = 'initializing'
        self.exception_type = ''
        self.http_status = None
        self.outcome = ''

    def exception(self, error: BaseException) -> None:
        """Record only the safe type and status of a transport/storage error."""
        self.exception_type = safe_exception_type(error)
        self.http_status = safe_http_status(error) or self.http_status

    def transport(self, task: asyncio.Task) -> None:
        """Record a finished transport failure or its known outcome."""
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
    """Describe submission state without retaining a message or SDK response."""

    acknowledged: bool
    attempted: bool
    stage: str = ''
    exception_type: str = ''
    http_status: int | None = None
    outcome: str = ''


def safe_diagnostics(result: SendResult) -> dict[str, str | int]:
    """Serialize only allowlisted diagnostic fields for the delivery ledger."""
    diagnostic: dict[str, str | int] = {}
    stage = getattr(result, 'stage', '')
    exception_type = getattr(result, 'exception_type', '')
    http_status = getattr(result, 'http_status', None)
    outcome = getattr(result, 'outcome', '')
    if stage in SAFE_STAGES:
        diagnostic['stage'] = stage
    if exception_type in SAFE_EXCEPTION_TYPES or exception_type == 'OtherError':
        diagnostic['exception_type'] = exception_type
    if isinstance(http_status, int) and 100 <= http_status <= 599:
        diagnostic['http_status'] = http_status
    if outcome in SAFE_OUTCOMES:
        diagnostic['outcome'] = outcome
    return diagnostic


class NoCommands:
    """Advertise no host capabilities and refuse any unsolicited command."""

    def specs(self, **_kwargs) -> dict:
        """Return an empty SDK command registry."""
        return {}

    def run(self, *_args, **_kwargs) -> dict:
        """Refuse commands without executing anything on the host."""
        return {
            'ok': False,
            'error': 'Host commands are not enabled on this bridge'
        }


def validate_bundle(bundle: dict) -> None:
    """Require existing paired credentials before preparing SDK state."""
    if not isinstance(bundle, dict):
        raise ValueError('invalid_state_bundle')
    identity = bundle.get('identity')
    if not isinstance(identity, dict) or not re.fullmatch(
            r'[0-9a-f]{2}(?::[0-9a-f]{2}){5}', str(identity.get('mac', ''))):
        raise ValueError('invalid_identity')
    pairing = bundle.get('pairing')
    if not isinstance(pairing, dict) or any(
            not isinstance(pairing.get(k), str) or not pairing[k]
            for k in ('access_token', 'refresh_token')):
        raise ValueError('pairing_required')
    if not isinstance(bundle.get('sdk_token'),
                      str) or not bundle['sdk_token'].startswith('mgst_'):
        raise ValueError('sdk_token_required')


def send_message(bundle: dict,
                 message: str | None,
                 persist_state: Callable[[dict], None],
                 before_send: Callable[[], None],
                 *,
                 deadline_seconds: float = 55,
                 wav_bytes: bytes | None = None) -> SendResult:
    """Persist state and mark delivery BEFORE sending; never retry a chat send.

    A process death after before_send leaves an uncertain delivery in the ledger.
    The caller must not automatically send that delivery again.
    """
    validate_bundle(bundle)
    if wav_bytes is not None:
        if ((message is not None and not isinstance(message, str)) or
                not isinstance(wav_bytes, bytes) or
                not 44 <= len(wav_bytes) <= 2 * 1024 * 1024 or
                wav_bytes[:4] != b'RIFF' or wav_bytes[8:12] != b'WAVE'):
            raise ValueError('invalid_voice_payload')
    elif not isinstance(message, str) or not message:
        raise ValueError('message_required')
    # The upstream state directory is process-global. Serialize changes to it.
    # Cloud Run also uses concurrency=1 and one worker.
    with _STATE_LOCK, tempfile.TemporaryDirectory(
            prefix='muse-state-') as temporary_directory:
        state_dir = Path(temporary_directory)
        os.chmod(state_dir, 0o700)
        for name, value in (('identity.json', bundle['identity']),
                            ('pairing.json', bundle['pairing'])):
            descriptor = os.open(state_dir / name,
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'w') as file:
                json.dump(value, file)
        descriptor = os.open(state_dir / 'sdk_token',
                             os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as file:
            file.write(bundle['sdk_token'])
        old_dir = os.environ.get('MUSEGADGET_STATE_DIR')
        old_token = os.environ.pop('MUSEGADGET_SDK_TOKEN', None)
        os.environ['MUSEGADGET_STATE_DIR'] = temporary_directory
        saved_pairing = bundle['pairing']
        attempted = False
        before_send_entered = False
        connection_attempts = 0
        acknowledged = False
        diagnostic = Diagnostics()

        def persist() -> None:
            nonlocal saved_pairing
            pairing_path = state_dir / 'pairing.json'
            pairing = json.loads(
                pairing_path.read_text()) if pairing_path.exists() else None
            if pairing != saved_pairing:
                diagnostic.stage = 'state_persist'
                persist_state({**bundle, 'pairing': pairing})
                saved_pairing = pairing

        async def run_once() -> None:
            diagnostic.stage = 'sdk_import'
            # Load the SDK only after its private state directory is prepared.
            # pylint: disable=import-outside-toplevel
            from musegadget import config, identity
            from musegadget.service import Service
            from musegadget.link_client import Outcome, acknowledgement
            from websockets.exceptions import InvalidStatus

            # pylint: enable=import-outside-toplevel

            class OneShot(Service):
                """Use official refresh/reconnect logic for one bounded send."""

                async def _maybe_refresh(self, pairing, force=False):
                    # urllib refresh executes in a thread. Cancellation must not
                    # abandon the returned replacement refresh token.
                    diagnostic.stage = 'refreshing'
                    task = asyncio.create_task(super()._maybe_refresh(
                        pairing, force))
                    try:
                        return await asyncio.shield(task)
                    except asyncio.CancelledError:
                        await task
                        raise
                    finally:
                        if task.done() and not task.cancelled():
                            persist()

                async def serve(self, session, vm, stop):
                    nonlocal attempted, before_send_entered
                    nonlocal connection_attempts, acknowledged
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
                    registration = asyncio.create_task(
                        session.wait_registered(timeout=35))
                    try:
                        done, _ = await asyncio.wait(
                            (transport, registration),
                            timeout=35,
                            return_when=asyncio.FIRST_COMPLETED)
                        if transport in done or registration not in done:
                            diagnostic.transport(transport)
                            if done:
                                diagnostic.stage = 'transport_ended'
                            else:
                                diagnostic.stage = 'registration_timeout'
                            if diagnostic.outcome == Outcome.UNPAIRED.value:
                                # Let the official Service clear pairing.
                                # It handles UNPAIRED before its next loop.
                                return Outcome.UNPAIRED
                            # Retry only an explicit transient HTTP response
                            # before registration; never retry a chat send.
                            failure = (transport.exception()
                                       if transport.done() and
                                       not transport.cancelled() else None)
                            retry_connection = (not attempted and
                                                not before_send_entered and
                                                connection_attempts < 3 and
                                                isinstance(
                                                    failure, InvalidStatus) and
                                                safe_http_status(failure)
                                                in {429, 500, 502, 503, 504})
                            # CLOSED uses official bearer refresh and backoff.
                            if retry_connection:
                                return Outcome.CLOSED
                            return Outcome.STOPPED
                        registration.result()
                        # Service may have refreshed its tokens before serve().
                        # Persist synchronously before any message can leave.
                        persist()
                        diagnostic.stage = 'before_send'
                        before_send_entered = True
                        before_send()
                        attempted = True
                        diagnostic.stage = 'submitting'
                        submission = (session.send_voice(wav_bytes,
                                                         message=message or '',
                                                         output_modality='text')
                                      if wav_bytes is not None else
                                      session.send_chat(message))
                        result = await asyncio.wait_for(submission, 25)
                        acknowledgement(result)
                        acknowledged = True
                        diagnostic.stage = 'acknowledged'
                        return Outcome.STOPPED
                    # pylint: disable-next=broad-exception-caught
                    except Exception as error:
                        # Return safe status fields; exclude SDK error text.
                        diagnostic.exception(error)
                        return Outcome.STOPPED
                    finally:
                        if not retry_connection:
                            self.stop()
                            stop.set()
                        registration.cancel()
                        if not transport.done():
                            transport.cancel()
                        await asyncio.gather(registration,
                                             transport,
                                             return_exceptions=True)

            service = OneShot(identity.load_or_create(),
                              NoCommands(),
                              sdk_token=config.sdk_token(),
                              display_name='Index Ring Bridge',
                              model_id='pebble-ring-bridge',
                              model_name='Pebble Ring Bridge',
                              guide=None)
            await asyncio.wait_for(service.run(), deadline_seconds)

        try:
            asyncio.run(run_once())
        except Exception as error:  # pylint: disable=broad-exception-caught
            # The caller receives status fields without SDK error text.
            diagnostic.exception(error)
        finally:
            try:
                persist()
            except Exception as error:  # pylint: disable=broad-exception-caught
                # Cleanup failure must not erase an already received send ACK.
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
        return SendResult(acknowledged,
                          attempted,
                          stage=diagnostic.stage,
                          exception_type=diagnostic.exception_type,
                          http_status=diagnostic.http_status,
                          outcome=diagnostic.outcome)
