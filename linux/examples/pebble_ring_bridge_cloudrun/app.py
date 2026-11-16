"""Authenticated Pebble v1 ingress; no credentials or note bodies are logged.

Cloud Run must use request-based CPU, min=0, max=1, concurrency=1 and a single
worker. A GCS CAS lease additionally serializes device sessions across revisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from typing import Callable

from flask import Flask, jsonify, request
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.cloud import storage

from audio import AudioError, normalize_audio
from muse_sender import send_message, SAFE_STAGES, SAFE_EXCEPTION_TYPES, SAFE_OUTCOMES

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
    pass


class LeaseBusy(Exception):
    pass


class GCSStore:
    def __init__(self, bucket_name: str):
        self.bucket = storage.Client().bucket(bucket_name)

    def read(self, name: str):
        blob = self.bucket.blob(name)
        try:
            blob.reload(timeout=8, retry=None)
            generation = int(blob.generation)
            data = blob.download_as_bytes(if_generation_match=generation, timeout=8, retry=None)
        except NotFound:
            return None, 0
        except PreconditionFailed as exc:
            raise StorageConflict from exc
        return json.loads(data), generation

    def write(self, name: str, value: dict, generation: int):
        blob = self.bucket.blob(name)
        try:
            blob.upload_from_string(json.dumps(value, separators=(',', ':')), content_type='application/json',
                                    if_generation_match=generation, timeout=8, retry=None)
        except PreconditionFailed as exc:
            raise StorageConflict from exc
        return int(blob.generation)

    def delete(self, name: str, generation: int):
        try:
            self.bucket.blob(name).delete(if_generation_match=generation, timeout=8, retry=None)
        except NotFound:
            pass
        except PreconditionFailed as exc:
            raise StorageConflict from exc


@dataclass
class Lease:
    store: object
    owner: str
    generation: int
    clock: Callable[[], float]

    @classmethod
    def acquire(cls, store, clock=time.time):
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

    def check(self):
        value, generation = self.store.read(LEASE_OBJECT)
        if (not value or generation != self.generation or value.get('owner') != self.owner
                or float(value.get('expires', 0)) - self.clock() < MIN_LEASE_REMAINING):
            raise LeaseBusy

    def release(self):
        try:
            self.store.delete(LEASE_OBJECT, self.generation)
        except Exception:
            # Expiration allows a future request to recover after a failed
            # release. Never overwrite or remove a successor's lease.
            pass


def parse_multipart(content_type: str, body: bytes) -> dict:
    if not content_type.lower().startswith('multipart/form-data;'):
        raise ValueError('multipart_required')
    message = BytesParser(policy=email_policy).parsebytes(b'Content-Type: ' + content_type.encode('ascii') + b'\r\n\r\n' + body)
    if not message.is_multipart():
        raise ValueError('invalid_multipart')
    fields = {}
    count = 0
    for part in message.iter_parts():
        count += 1
        if count > 10:
            raise ValueError('too_many_parts')
        name = part.get_param('name', header='content-disposition')
        if name not in {'transcription', 'audio', 'test', 'recordedAt', 'client'}:
            continue
        if name in fields:
            raise ValueError('duplicate_field')
        payload = part.get_payload(decode=True)
        if payload is None:
            raise ValueError('invalid_part')
        fields[name] = ({'data': payload, 'content_type': part.get_content_type()}
                        if name == 'audio' else payload.decode('utf-8', errors='strict').strip())
    return fields


def authenticate(headers, body: bytes, auth: dict, now: float) -> None:
    bearer = auth.get('bearer', '')
    secret = auth.get('signing_secret', '')
    if not isinstance(bearer, str) or len(bearer) < 32 or not isinstance(secret, str) or len(secret) < 32:
        raise RuntimeError('auth_configuration_invalid')
    # Constant-time comparison works with non-ASCII untrusted header strings.
    if not hmac.compare_digest(headers.get('Authorization', '').encode(), ('Bearer ' + bearer).encode()):
        raise PermissionError
    if headers.get('X-Index-Webhook-Version') != '1':
        raise ValueError('unsupported_version')
    timestamp = headers.get('X-Index-Timestamp', '')
    delivery = headers.get('X-Index-Delivery', '')
    trigger = headers.get('X-Index-Trigger', '')
    is_test = headers.get('X-Index-Test') == 'true'
    if not re.fullmatch(r'[0-9]{1,14}', timestamp) or abs(now - int(timestamp)) > SIGNATURE_WINDOW:
        raise PermissionError
    if not delivery or len(delivery) > 200 or any(c in delivery for c in '\r\n'):
        raise ValueError('invalid_delivery')
    if trigger not in TRIGGERS:
        raise ValueError('unsupported_trigger')
    prefix = f'v1\n{timestamp}\n{delivery}\n{trigger}\n{1 if is_test else 0}\n'.encode()
    expected = hmac.new(secret.encode(), prefix + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(headers.get('X-Index-Signature', '').encode(), expected.encode()):
        raise PermissionError


def create_app(store=None, sender=send_message, clock=time.time):
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = MAX_BODY
    store_box = [store]
    auth_cache = {'expires': 0, 'value': None}
    local_lock = threading.Lock()

    def get_store():
        if store_box[0] is None:
            store_box[0] = GCSStore(os.environ['BRIDGE_BUCKET'])
        return store_box[0]

    def get_auth():
        if auth_cache['value'] is None or auth_cache['expires'] <= clock():
            value, _ = get_store().read(AUTH_OBJECT)
            if not isinstance(value, dict):
                raise RuntimeError('auth_configuration_missing')
            auth_cache.update(value=value, expires=clock() + 60)
        return auth_cache['value']

    @app.errorhandler(413)
    def too_large(error):
        return jsonify(error='body_too_large'), 413

    @app.get('/health')
    def health():
        # No remote credential, storage or device lookup for routine probes.
        return jsonify(ok=True)

    @app.post('/index')
    def index():
        raw = request.get_data(cache=False)
        try:
            authenticate(request.headers, raw, get_auth(), clock())
            fields = parse_multipart(request.headers.get('Content-Type', ''), raw)
        except PermissionError:
            return jsonify(error='unauthorized'), 401
        except (ValueError, UnicodeError):
            return jsonify(error='invalid_request'), 400
        except Exception:
            return jsonify(error='configuration_or_storage_unavailable'), 503
        if (request.headers.get('X-Index-Test') == 'true' or fields.get('test') == 'true'
                or request.headers.get('X-Index-Trigger') == 'test-event'):
            return jsonify(ok=True, test=True)
        text = fields.get('transcription')
        if text is not None and (not isinstance(text, str) or len(text) > MAX_TEXT):
            return jsonify(error='invalid_transcription'), 422
        voice = None
        if 'audio' in fields:
            try:
                voice = normalize_audio(fields['audio']['data'], fields['audio']['content_type'])
            except AudioError as error:
                # AudioError contains only a fixed local code, never stderr or
                # recording metadata. No SDK connection has been opened.
                return jsonify(error=str(error)), 422
            command = text or None
            if command is None:
                # Preserve existing recording-only delivery fingerprints.
                digest = hashlib.sha256(b'audio/wav\0' + voice).hexdigest()
            else:
                encoded_text = command.encode()
                # Length-prefix the text so both parts identify this delivery.
                digest = hashlib.sha256(b'audio/wav+text\0' + len(encoded_text).to_bytes(4, 'big')
                                        + encoded_text + voice).hexdigest()
        else:
            if not isinstance(text, str) or not text:
                return jsonify(error='transcription_or_audio_required'), 422
            # Text-only fallback retains its original digest and behavior.
            command = text
            digest = hashlib.sha256(command.encode()).hexdigest()
        # No note contents or recordedAt are retained in the ledger.
        key = hashlib.sha256(request.headers['X-Index-Delivery'].encode()).hexdigest()
        ledger_name = f'ledger/{key}.json'
        if not local_lock.acquire(blocking=False):
            return jsonify(error='busy_retry_later'), 503
        lease = None
        armed = False
        ledger_generation = 0
        ledger = None
        try:
            db = get_store()
            lease = Lease.acquire(db, clock)
            ledger, ledger_generation = db.read(ledger_name)
            if ledger:
                if ledger.get('digest') != digest:
                    return jsonify(error='delivery_id_reused'), 409
                if ledger.get('status') == 'delivered':
                    return jsonify(ok=True, duplicate=True, status='delivered')
                if ledger.get('status') in {'sending', 'uncertain'}:
                    return jsonify(ok=True, duplicate=True, status='needs_review'), 202
            ledger = {'digest': digest, 'status': 'preparing', 'updated_at': int(clock())}
            ledger_generation = db.write(ledger_name, ledger, ledger_generation)
            bundle, state_generation = db.read(STATE_OBJECT)
            if not isinstance(bundle, dict):
                raise RuntimeError('state_missing')

            def persist_state(updated):
                nonlocal state_generation
                lease.check()
                state_generation = db.write(STATE_OBJECT, updated, state_generation)

            def before_send():
                nonlocal armed, ledger_generation, ledger
                lease.check()
                ledger = {**ledger, 'status': 'sending', 'updated_at': int(clock())}
                ledger_generation = db.write(ledger_name, ledger, ledger_generation)
                # No external request can be attempted before this durable mark.
                armed = True

            lease.check()
            send_options = {'session_id': os.environ.get('MUSE_SESSION_ID') or None, 'deadline_seconds': 55}
            if voice is not None:
                send_options['wav_bytes'] = voice
            result = sender(bundle, command, persist_state, before_send, **send_options)
            status = 'delivered' if result.acknowledged else ('uncertain' if armed or result.attempted else 'failed_before_send')
            diagnostic = {}
            stage = getattr(result, 'stage', '')
            exception_type = getattr(result, 'exception_type', '')
            http_status = getattr(result, 'http_status', None)
            outcome = getattr(result, 'outcome', '')
            if stage in SAFE_STAGES:
                diagnostic['stage'] = stage
            if exception_type in SAFE_EXCEPTION_TYPES or exception_type == 'OtherError':
                diagnostic['exception_type'] = exception_type
            if type(http_status) is int and 100 <= http_status <= 599:
                diagnostic['http_status'] = http_status
            if outcome in SAFE_OUTCOMES:
                diagnostic['outcome'] = outcome
            ledger = {**ledger, 'status': status, 'updated_at': int(clock()), 'diagnostic': diagnostic}
            ledger_generation = db.write(ledger_name, ledger, ledger_generation)
            if result.acknowledged:
                return jsonify(ok=True, status='delivered')
            if status == 'uncertain':
                return jsonify(ok=True, status='needs_review'), 202
            return jsonify(error='not_delivered_retry_later'), 503
        except LeaseBusy:
            return jsonify(error='busy_retry_later'), 503
        except Exception:
            # The durable 'sending' record, if written, is deliberately kept.
            # Returning 202 suppresses an automatic duplicate send by the phone.
            if armed:
                return jsonify(ok=True, status='needs_review'), 202
            return jsonify(error='temporarily_unavailable'), 503
        finally:
            if lease is not None:
                lease.release()
            local_lock.release()

    return app


app = create_app()
