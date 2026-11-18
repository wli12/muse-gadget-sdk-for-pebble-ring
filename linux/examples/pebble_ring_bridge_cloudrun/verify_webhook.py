#!/usr/bin/env python3
"""Stdlib-only Cloud Run bridge checks; keys are read only from a private file.

Default checks send synthetic test events, without sending messages to Muse.
--send submits one benign message and repeats its delivery ID to check
deduplication. Network failures are not retried, and redirects are refused.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import pathlib
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

MAX_REPLY = 64 * 1024
TEST_MESSAGE = '请只回复：云端中转成功。'


class VerificationError(Exception):
    """A deliberately generic, credential-free diagnostic."""


class NoRedirects(urllib.request.HTTPRedirectHandler):
    """Keep webhook credentials on their configured origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise VerificationError('endpoint_redirect_refused')


def endpoint(value: str) -> str:
    """Return the HTTPS ingress URL without credentials or query parameters."""
    parts = urllib.parse.urlsplit(value)
    if (parts.scheme != 'https' or not parts.hostname or parts.username or
            parts.password or parts.query or parts.fragment or
            parts.path not in ('', '/', '/index')):
        raise VerificationError(
            'url_must_be_https_service_or_index_without_query')
    return urllib.parse.urlunsplit(
        (parts.scheme, parts.netloc, '/index', '', ''))


def load_keys(path: pathlib.Path) -> dict:
    """Read keys from an owner-only file without printing their values."""
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 8192:
        raise VerificationError('invalid_keys_file')
    if os.name == 'posix' and (metadata.st_uid != os.getuid() or
                               metadata.st_mode & 0o077):
        raise VerificationError('keys_file_must_be_owner_only_chmod_600')
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, UnicodeError):
        raise VerificationError('invalid_keys_json') from None
    if not isinstance(value, dict) or any(
            not isinstance(value.get(k), str) or len(value[k]) < 32
            for k in ('bearer', 'signing_secret')):
        raise VerificationError('invalid_keys_shape')
    return value


def signed_request(url: str,
                   keys: dict,
                   text: str,
                   *,
                   delivery: str,
                   test: bool = False,
                   invalid_auth: bool = False) -> urllib.request.Request:
    """Build a signed Pebble v1 request independently of the server."""
    boundary = 'index-verify-' + uuid.uuid4().hex
    stamp = str(int(time.time()))
    trigger = 'test-event' if test else 'double-click-hold'
    fields = {
        'transcription': text,
        'recordedAt': str(int(time.time() * 1000)),
        'client': 'ring'
    }
    if test:
        fields['test'] = 'true'
    body = b''.join((f'--{boundary}\r\n'
                     f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                     f'{value}\r\n').encode('utf-8')
                    for name, value in fields.items())
    body += f'--{boundary}--\r\n'.encode('ascii')
    prefix = f'v1\n{stamp}\n{delivery}\n{trigger}\n{1 if test else 0}\n'.encode(
        'utf-8')
    signature = hmac.new(keys['signing_secret'].encode(), prefix + body,
                         hashlib.sha256).hexdigest()
    bearer = 'intentionally-invalid-token' if invalid_auth else keys['bearer']
    headers = {
        'Content-Type': f'multipart/form-data; boundary={boundary}',
        'Authorization': 'Bearer ' + bearer,
        'X-Index-Webhook-Version': '1',
        'X-Index-Timestamp': stamp,
        'X-Index-Delivery': delivery,
        'X-Index-Trigger': trigger,
        'X-Index-Signature': signature,
    }
    if test:
        headers['X-Index-Test'] = 'true'
    return urllib.request.Request(url,
                                  data=body,
                                  headers=headers,
                                  method='POST')


def fetch(request: urllib.request.Request,
          timeout: int = 115) -> tuple[int, dict]:
    """Submit once and read bounded JSON, refusing redirects."""
    opener = urllib.request.build_opener(NoRedirects())
    try:
        response = opener.open(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        response = error
    except (urllib.error.URLError, TimeoutError, OSError):
        raise VerificationError('network_failed_no_automatic_retry') from None
    with response:
        raw = response.read(MAX_REPLY + 1)
        code = response.code
    if len(raw) > MAX_REPLY:
        raise VerificationError('response_too_large')
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeError):
        raise VerificationError('non_json_response_http_' + str(code)) from None
    if not isinstance(body, dict):
        raise VerificationError('unexpected_response_shape')
    return code, body


def save_report(path: pathlib.Path | None, report: dict) -> None:
    """Persist verification metadata, including the attempted delivery ID."""
    if path is None:
        return
    # Reports contain only random delivery IDs and check results, never keys,
    # note content, response bodies, account IDs or signed request bytes.
    fd = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, 'O_NOFOLLOW', 0),
        0o600)
    os.chmod(path, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as output:
        json.dump(report, output, indent=2)
        output.write('\n')
        output.flush()
        os.fsync(output.fileno())


def run(url: str,
        keys: dict,
        *,
        send: bool = False,
        report_path: pathlib.Path | None = None,
        transport=fetch) -> dict:
    """Check authentication and optionally submit a message and duplicate."""
    report = {'checks': [], 'send_requested': send}

    def check(name: str, code: int, passed: bool):
        report['checks'].append({
            'check': name,
            'http_status': code,
            'passed': bool(passed)
        })
        save_report(report_path, report)
        if not passed:
            raise VerificationError(name + '_failed')
        print('PASS:', name, flush=True)

    code, result = transport(
        signed_request(url,
                       keys,
                       'Webhook connectivity check',
                       delivery=str(uuid.uuid4()),
                       test=True))
    check('signed_test_event_not_forwarded', code, code == 200 and
          result.get('test') is True)
    code, result = transport(
        signed_request(url,
                       keys,
                       'Webhook connectivity check',
                       delivery=str(uuid.uuid4()),
                       test=True,
                       invalid_auth=True))
    check('invalid_auth_rejected', code, code == 401)
    if not send:
        return report

    delivery = str(uuid.uuid4())
    report.update(delivery_id=delivery, delivery_state='attempting')
    save_report(report_path, report)
    # No retry is allowed here. On timeout/202 this delivery ID is retained in
    # the optional private report for operator review instead of sending again.
    code, result = transport(
        signed_request(url, keys, TEST_MESSAGE, delivery=delivery))
    acknowledged = code == 200 and result.get('status') == 'delivered'
    report['delivery_state'] = 'delivered' if acknowledged else 'unconfirmed'
    save_report(report_path, report)
    check('one_message_acknowledged', code,
          report['delivery_state'] == 'delivered')
    code, result = transport(
        signed_request(url, keys, TEST_MESSAGE, delivery=delivery))
    check(
        'same_delivery_deduplicated', code, code == 200 and
        result.get('duplicate') is True and result.get('status') == 'delivered')
    return report


def main(argv=None) -> int:
    """Run explicit CLI checks and report fixed, credential-free errors."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url',
                        required=True,
                        help='HTTPS Cloud Run service URL or /index URL')
    parser.add_argument(
        '--keys',
        required=True,
        type=pathlib.Path,
        help='Owner-only local webhook.json; never pass raw tokens')
    parser.add_argument(
        '--send',
        action='store_true',
        help='Send ONE benign Muse test message, then check its duplicate ID')
    parser.add_argument(
        '--report',
        type=pathlib.Path,
        help='Optional metadata report, e.g. work/webhook-report.json')
    args = parser.parse_args(argv)
    try:
        if args.report and args.report.resolve() == args.keys.resolve():
            raise VerificationError('report_must_not_overwrite_keys')
        run(endpoint(args.url),
            load_keys(args.keys),
            send=args.send,
            report_path=args.report)
    except VerificationError as error:
        print('FAIL:', str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError, TypeError):
        print('FAIL: local_configuration_or_transport_error', file=sys.stderr)
        return 2
    print('PASS: webhook_checks_complete', flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
