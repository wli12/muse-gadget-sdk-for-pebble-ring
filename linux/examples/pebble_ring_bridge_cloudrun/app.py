"""Authenticated Pebble v1 ingress; no credentials or note bodies are logged.

Cloud Run must use request-based CPU, min=0, max=1, concurrency=1 and a single
worker. GCS CAS also serializes device sessions across revisions.

Multipart parsing, token extraction and transcription reuse helpers from
../pebble_ring_bridge.py. Request-scoped Cloud Run state and audio are added here.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Any, Callable

from flask import Flask, jsonify, request
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage
import pebble_ring_bridge as ring_bridge

from audio import AudioError, normalize_audio
from muse_sender import safe_diagnostics, send_message

MAX_BODY = 9 * 1024 * 1024
MAX_TEXT = 16000
SIGNATURE_WINDOW = 300
LEASE_SECONDS = 300
MIN_LEASE_REMAINING = 120
TRIGGERS = {'single-click-hold', 'double-click-hold', 'test-event'}
STATE_OBJECT = 'state/muse.json'
AUTH_OBJECT = 'state/webhook.json'
LEASE_OBJECT = 'runtime/lease.json'


class StorageConflict(Exception):
    """A generation-checked state operation lost a concurrent update race."""


class LeaseBusy(Exception):
    """Another request owns the device session, or this lease is expiring."""


class PayloadError(ValueError):
    """A fixed payload validation code safe to return to the caller."""


class GCSStore:
    """Read and update private JSON state using GCS generation preconditions."""

    def __init__(self, bucket_name: str) -> None:
        if any(
                os.environ.get(name)
                for name in ('STORAGE_EMULATOR_HOST', 'API_ENDPOINT_OVERRIDE')):
            raise RuntimeError('storage_endpoint_override_not_allowed')
        self.bucket = storage.Client(client_options={
            'api_endpoint': 'https://storage.googleapis.com'
        }).bucket(bucket_name)

    def read(self, name: str) -> tuple[dict | None, int]:
        """Return the JSON object and generation, or (None, 0) if absent."""
        blob = self.bucket.blob(name)
        try:
            blob.reload(timeout=8, retry=None)
            generation = int(blob.generation)
            data = blob.download_as_bytes(if_generation_match=generation,
                                          timeout=8,
                                          retry=None)
        except NotFound:
            return None, 0
        except PreconditionFailed as exc:
            raise StorageConflict from exc
        return json.loads(data), generation

    def write(self, name: str, value: dict, generation: int) -> int:
        """Write only if the supplied generation matches; return the new one."""
        blob = self.bucket.blob(name)
        try:
            blob.upload_from_string(json.dumps(value, separators=(',', ':')),
                                    content_type='application/json',
                                    if_generation_match=generation,
                                    timeout=8,
                                    retry=None)
        except PreconditionFailed as exc:
            raise StorageConflict from exc
        return int(blob.generation)

    def delete(self, name: str, generation: int) -> None:
        """Delete this generation without removing a successor object."""
        try:
            self.bucket.blob(name).delete(if_generation_match=generation,
                                          timeout=8,
                                          retry=None)
        except NotFound:
            pass
        except PreconditionFailed as exc:
            raise StorageConflict from exc


@dataclass
class Lease:
    """Serialize one device session across instances and rolling revisions."""

    store: Any
    owner: str
    generation: int
    clock: Callable[[], float]

    @classmethod
    def acquire(cls,
                store: Any,
                clock: Callable[[], float] = time.time) -> Lease:
        """Acquire a fresh lease or atomically replace an expired one."""
        owner = secrets.token_hex(24)
        previous, generation = store.read(LEASE_OBJECT)
        if previous and float(previous.get('expires', 0)) > clock():
            raise LeaseBusy
        value = {'owner': owner, 'expires': clock() + LEASE_SECONDS}
        try:
            generation = store.write(LEASE_OBJECT, value, generation)
        except StorageConflict as exc:
            raise LeaseBusy from exc
        return cls(store, owner, generation, clock)

    def check(self) -> None:
        """Require ownership and enough remaining time for a bounded send."""
        value, generation = self.store.read(LEASE_OBJECT)
        if (not value or generation != self.generation or
                value.get('owner') != self.owner or
                float(value.get('expires', 0)) - self.clock()
                < MIN_LEASE_REMAINING):
            raise LeaseBusy

    def release(self) -> None:
        """Best-effort release; expiration recovers a failed cleanup."""
        try:
            self.store.delete(LEASE_OBJECT, self.generation)
        except Exception:  # pylint: disable=broad-exception-caught
            # Preserve delivery status; expiration recovers a failed
            # release without removing a successor's lease.
            pass


def parse_multipart(content_type: str, body: bytes) -> dict[str, Any]:
    """Parse Pebble fields while preserving recording bytes unchanged."""
    fields = {}
    parts = ring_bridge.multipart_fields(content_type, body, strict=True)
    for count, (name, payload, mime_type) in enumerate(parts, start=1):
        if count > 10:
            raise ValueError('too_many_parts')
        if name not in {'transcription', 'audio', 'test'}:
            continue
        if name in fields:
            raise ValueError('duplicate_field')
        if payload is None:
            raise ValueError('invalid_part')
        fields[name] = ({
            'data': payload,
            'content_type': mime_type
        } if name == 'audio' else payload.decode('utf-8',
                                                 errors='strict').strip())
    return fields


def authenticate(headers: Mapping[str, str], body: bytes, auth: dict,
                 now: float) -> None:
    """Validate Bearer authentication, freshness and Pebble v1 HMAC."""
    bearer = auth.get('bearer', '')
    secret = auth.get('signing_secret', '')
    if not isinstance(bearer, str) or len(bearer) < 32 or not isinstance(
            secret, str) or len(secret) < 32:
        raise RuntimeError('auth_configuration_invalid')
    authorization = headers.get('Authorization', '')
    if not authorization.startswith('Bearer '):
        raise PermissionError
    # Restrict the legacy extractor to this header; no alternate token sources.
    token = ring_bridge.presented_token({'Authorization': authorization}, {})
    if not hmac.compare_digest(token.encode(), bearer.encode()):
        raise PermissionError
    if headers.get('X-Index-Webhook-Version') != '1':
        raise ValueError('unsupported_version')
    timestamp = headers.get('X-Index-Timestamp', '')
    delivery = headers.get('X-Index-Delivery', '')
    trigger = headers.get('X-Index-Trigger', '')
    is_test = headers.get('X-Index-Test') == 'true'
    if not re.fullmatch(r'[0-9]{1,14}',
                        timestamp) or abs(now -
                                          int(timestamp)) > SIGNATURE_WINDOW:
        raise PermissionError
    if not delivery or len(delivery) > 200 or any(
            c in delivery for c in '\r\n'):
        raise ValueError('invalid_delivery')
    if trigger not in TRIGGERS:
        raise ValueError('unsupported_trigger')
    prefix = (f'v1\n{timestamp}\n{delivery}\n'
              f'{trigger}\n{1 if is_test else 0}\n').encode()
    expected = hmac.new(secret.encode(), prefix + body,
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(
            headers.get('X-Index-Signature', '').encode(), expected.encode()):
        raise PermissionError


def prepare_message(
        fields: dict[str, Any]) -> tuple[str | None, bytes | None, str]:
    """Validate a note and return text, normalized WAV and its delivery digest.

    Both parts contribute to a combined note's fingerprint. Existing text-only
    and recording-only fingerprints are retained for ledger compatibility.
    Invalid audio is never replaced with its accompanying transcription.
    """
    text = fields.get('transcription')
    if text is not None and (not isinstance(text, str) or len(text) > MAX_TEXT):
        raise PayloadError('invalid_transcription')
    text = ring_bridge.transcription(fields) or None
    if 'audio' not in fields:
        if not text:
            raise PayloadError('transcription_or_audio_required')
        return text, None, hashlib.sha256(text.encode()).hexdigest()

    recording = fields['audio']
    voice = normalize_audio(recording['data'], recording['content_type'])
    if text is None:
        fingerprint = b'audio/wav\0' + voice
    else:
        encoded_text = text.encode()
        fingerprint = (b'audio/wav+text\0' +
                       len(encoded_text).to_bytes(4, 'big') + encoded_text +
                       voice)
    return text, voice, hashlib.sha256(fingerprint).hexdigest()


def create_app(store: Any = None,
               sender: Callable = send_message,
               clock: Callable[[], float] = time.time) -> Flask:
    """Create the webhook with injectable dependencies for tests."""
    application = Flask(__name__)
    application.config['MAX_CONTENT_LENGTH'] = MAX_BODY
    auth_cache = {'expires': 0, 'value': None}

    def get_store() -> Any:
        nonlocal store
        if store is None:
            store = GCSStore(os.environ['BRIDGE_BUCKET'])
        return store

    def get_auth() -> dict:
        if auth_cache['value'] is None or auth_cache['expires'] <= clock():
            value, _ = get_store().read(AUTH_OBJECT)
            if not isinstance(value, dict):
                raise RuntimeError('auth_configuration_missing')
            auth_cache.update(value=value, expires=clock() + 60)
        return auth_cache['value']

    @application.errorhandler(413)
    def too_large(_error):
        return jsonify(error='body_too_large'), 413

    @application.get('/health')
    def health():
        # No remote credential, storage or device lookup for routine probes.
        return jsonify(ok=True)

    @application.post('/index')
    def index():
        raw = request.get_data(cache=False)
        try:
            authenticate(request.headers, raw, get_auth(), clock())
            fields = parse_multipart(request.headers.get('Content-Type', ''),
                                     raw)
        except PermissionError:
            return jsonify(error='unauthorized'), 401
        except (ValueError, UnicodeError):
            return jsonify(error='invalid_request'), 400
        except Exception:  # pylint: disable=broad-exception-caught
            # Keep storage and credential errors out of the public response.
            return jsonify(error='configuration_or_storage_unavailable'), 503
        if (request.headers.get('X-Index-Test') == 'true' or
                fields.get('test') == 'true' or
                request.headers.get('X-Index-Trigger') == 'test-event'):
            return jsonify(ok=True, test=True)
        try:
            message, voice, digest = prepare_message(fields)
        except (PayloadError, AudioError) as error:
            # These domain exceptions contain only fixed local codes.
            return jsonify(error=str(error)), 422
        # No note contents or recordedAt are retained in the ledger.
        key = hashlib.sha256(
            request.headers['X-Index-Delivery'].encode()).hexdigest()
        ledger_name = f'ledger/{key}.json'
        lease = None
        armed = False
        try:
            state_store = get_store()
            lease = Lease.acquire(state_store, clock)
            ledger, ledger_generation = state_store.read(ledger_name)
            if ledger:
                if ledger.get('digest') != digest:
                    return jsonify(error='delivery_id_reused'), 409
                if ledger.get('status') == 'delivered':
                    return jsonify(ok=True, duplicate=True, status='delivered')
                if ledger.get('status') in {'sending', 'uncertain'}:
                    return jsonify(ok=True,
                                   duplicate=True,
                                   status='needs_review'), 202
            ledger = {
                'digest': digest,
                'status': 'preparing',
                'updated_at': int(clock())
            }
            ledger_generation = state_store.write(ledger_name, ledger,
                                                  ledger_generation)
            bundle, state_generation = state_store.read(STATE_OBJECT)
            if not isinstance(bundle, dict):
                raise RuntimeError('state_missing')

            def persist_state(updated):
                nonlocal state_generation
                lease.check()
                state_generation = state_store.write(STATE_OBJECT, updated,
                                                     state_generation)

            def before_send():
                nonlocal armed, ledger_generation, ledger
                lease.check()
                ledger = {
                    **ledger, 'status': 'sending',
                    'updated_at': int(clock())
                }
                ledger_generation = state_store.write(ledger_name, ledger,
                                                      ledger_generation)
                # No external request can be attempted before this durable mark.
                armed = True

            lease.check()
            send_options = {}
            if voice is not None:
                send_options['wav_bytes'] = voice
            result = sender(bundle, message, persist_state, before_send,
                            **send_options)
            status = 'delivered' if result.acknowledged else (
                'uncertain'
                if armed or result.attempted else 'failed_before_send')
            ledger = {
                **ledger, 'status': status,
                'updated_at': int(clock()),
                'diagnostic': safe_diagnostics(result)
            }
            state_store.write(ledger_name, ledger, ledger_generation)
            if result.acknowledged:
                return jsonify(ok=True, status='delivered')
            if status == 'uncertain':
                return jsonify(ok=True, status='needs_review'), 202
            return jsonify(error='not_delivered_retry_later'), 503
        except LeaseBusy:
            return jsonify(error='busy_retry_later'), 503
        except Exception:  # pylint: disable=broad-exception-caught
            # Keep SDK/storage details private and retain the durable send mark.
            # The durable 'sending' record, if written, is deliberately kept.
            # Returning 202 suppresses an automatic duplicate send by the phone.
            if armed:
                return jsonify(ok=True, status='needs_review'), 202
            return jsonify(error='temporarily_unavailable'), 503
        finally:
            if lease is not None:
                lease.release()

    return application


app = create_app()
