"""Compatibility tests for helpers shared by the local and Cloud Run bridges."""

import http.server
import importlib.util
from pathlib import Path
import subprocess

import pytest

import pebble_ring_bridge as bridge

CONTENT_TYPE = 'multipart/form-data; boundary=shared-helper-test'


def multipart_body(fields: list[tuple[str | None, bytes]]) -> bytes:
    """Build a synthetic multipart form without reading files or recordings."""
    chunks = []
    for name, payload in fields:
        disposition = 'Content-Disposition: form-data'
        if name is not None:
            disposition += f'; name="{name}"'
        chunks.extend([
            b'--shared-helper-test\r\n',
            disposition.encode() + b'\r\n',
            b'Content-Type: application/octet-stream\r\n\r\n',
            payload,
            b'\r\n',
        ])
    chunks.append(b'--shared-helper-test--\r\n')
    return b''.join(chunks)


def test_default_parser_retains_duplicate_override_and_utf8_replacement():
    raw = multipart_body([
        ('transcription', b'first'),
        ('transcription', b' last\xff '),
        (None, b'unnamed part'),
    ])
    assert bridge.parse_body(CONTENT_TYPE, raw) == {
        'transcription': 'last\ufffd'
    }


def test_default_parser_retains_empty_malformed_multipart_behavior():
    assert list(bridge.multipart_fields(CONTENT_TYPE, b'not multipart')) == []
    assert bridge.parse_body(CONTENT_TYPE, b'not multipart') == {}


@pytest.mark.parametrize('content_type, raw, error', [
    ('application/json', b'{}', ValueError),
    ('multipart/form-data', b'not multipart', ValueError),
    (CONTENT_TYPE, b'not multipart', ValueError),
    ('multipart/form-data; boundary=\u00e9', b'', UnicodeEncodeError),
])
def test_strict_parser_rejects_invalid_envelopes(content_type, raw, error):
    with pytest.raises(error):
        list(bridge.multipart_fields(content_type, raw, strict=True))


def test_shared_fields_preserve_binary_audio_and_unnamed_parts():
    recording = b'\xff\x00\x80synthetic\r\n'
    raw = multipart_body([(None, b'unnamed'), ('audio', recording)])
    assert list(bridge.multipart_fields(CONTENT_TYPE, raw, strict=True)) == [
        (None, b'unnamed', 'application/octet-stream'),
        ('audio', recording, 'application/octet-stream'),
    ]


def test_import_does_not_start_server_or_invoke_muse(monkeypatch):

    def unexpected(*_args, **_kwargs):
        pytest.fail('Import must not start a server or send a message')

    monkeypatch.delenv('PEBBLE_SESSION_ID', raising=False)
    monkeypatch.setattr(http.server, 'ThreadingHTTPServer', unexpected)
    monkeypatch.setattr(subprocess, 'run', unexpected)
    module_path = Path(bridge.__file__)
    spec = importlib.util.spec_from_file_location('bridge_safe_import',
                                                  module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.parse_body)
    assert callable(module.multipart_fields)
