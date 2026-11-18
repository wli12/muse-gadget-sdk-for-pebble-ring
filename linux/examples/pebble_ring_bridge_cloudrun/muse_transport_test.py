"""Exercise destination and redirect boundaries without network calls."""
import asyncio
from email.message import Message
import io
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import urllib.request
from urllib.response import addinfourl

import pytest
from websockets.datastructures import Headers
from websockets.exceptions import InvalidStatus
from websockets.http11 import Response

from musegadget import link_client, muse_api, tls
import muse_transport as transport


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any accidental socket or DNS use, including during SDK tests."""

    def blocked(*_args, **_kwargs):
        raise AssertionError('External network is forbidden in these tests')

    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'getaddrinfo', blocked)


@pytest.mark.parametrize('pairing', [
    {},
    {
        'api_url_v2': None,
        'noise_host': None
    },
    {
        'api_url_v2': '',
        'noise_host': ''
    },
    {
        'api_url_v2': 'https://api.muse.ai',
        'noise_host': 'hatch.metaaivm.com'
    },
    {
        'api_url_v2': 'https://api.muse.ai:443/',
        'noise_host': 'hatch.metaaivm.com:443'
    },
    {
        'api_url_v2': 'HTTPS://API.MUSE.AI/',
        'noise_host': 'HATCH.METAAIVM.COM'
    },
])
def test_only_default_or_official_pairing_origins(pairing):
    transport.validate_pairing_destinations(pairing)


@pytest.mark.parametrize('url', [
    'http://api.muse.ai',
    'https://127.0.0.1',
    'https://localhost',
    'https://api.muse.ai.evil.invalid',
    'https://api.muse.ai.',
    'https://api.muse.a\u0456',
    'https://%61pi.muse.ai',
    'https://api.muse.ai@evil.invalid',
    'https://user@api.muse.ai',
    'https://api.muse.ai:444',
    'https://api.muse.ai:',
    'https://api.muse.ai/v2',
    'https://api.muse.ai?other=1',
    'https://api.muse.ai#other',
    'https://api.muse.ai#',
    'https://api.muse.ai?',
    ' https://api.muse.ai',
    'https://api.muse.ai\n',
    'https://api.muse.ai\t',
    'https://api.muse.ai/\x00',
    [],
    {},
    7,
])
def test_rejects_unapproved_api_pairing(url):
    with pytest.raises(ValueError, match='invalid_muse_destination'):
        transport.validate_pairing_destinations({'api_url_v2': url})


@pytest.mark.parametrize('host', [
    'localhost',
    '127.0.0.1',
    'hatch.metaaivm.com.evil.invalid',
    'hatch.metaaivm.com.',
    'hatch.metaaivm.c\u043em',
    'hatch.metaaivm.com@evil.invalid',
    'user@hatch.metaaivm.com',
    'hatch.metaaivm.com:80',
    'hatch.metaaivm.com:444',
    'wss://hatch.metaaivm.com',
    'hatch.metaaivm.com/',
    'hatch.metaaivm.com?other=1',
    'hatch.metaaivm.com#other',
    'hatch.metaaivm.com\n',
    'hatch.metaaivm.com\x00',
    [],
    {},
    7,
])
def test_rejects_unapproved_link_pairing(host):
    with pytest.raises(ValueError, match='invalid_muse_destination'):
        transport.validate_pairing_destinations({'noise_host': host})


def test_sdk_namespace_is_narrow_and_restored_after_exception():
    original = muse_api.urllib
    original_urlopen = urllib.request.urlopen
    with pytest.raises(RuntimeError, match='synthetic failure'):
        with transport.sdk_api_transport():
            assert muse_api.urllib is not original
            assert muse_api.urllib.request.Request is urllib.request.Request
            assert muse_api.urllib.error is original.error
            assert not hasattr(muse_api.urllib.request, 'urlretrieve')
            assert urllib.request.urlopen is original_urlopen
            raise RuntimeError('synthetic failure')
    assert muse_api.urllib is original
    assert urllib.request.urlopen is original_urlopen


def test_official_fetch_and_refresh_use_guarded_transport(monkeypatch):
    """Exercise real SDK request construction, not a replacement protocol."""
    calls = []
    contexts = []
    context = object()
    monkeypatch.setattr(tls, 'client_context', lambda: context)
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setenv('HTTP_PROXY', 'http://127.0.0.1:1')

    class Opener:

        def open(self, request, timeout):
            calls.append((request, timeout))
            if request.full_url.endswith('/fetch_vms'):
                payload = {
                    'vm_list': [{
                        'vm_ws_url': 'wss://unused.invalid',
                        'vm_auth_token': 'synthetic-vm',
                        'vm_id': 'synthetic-id',
                        'default': True,
                    }]
                }
            else:
                payload = {
                    'access_token': 'synthetic-new-access',
                    'refresh_token': 'synthetic-new-refresh'
                }
            return addinfourl(io.BytesIO(json.dumps(payload).encode()),
                              Message(),
                              request.full_url,
                              code=200)

    def build(*handlers):
        assert any(
            isinstance(h, urllib.request.ProxyHandler) and h.proxies == {}
            for h in handlers)
        contexts.extend(h._context
                        for h in handlers
                        if isinstance(h, urllib.request.HTTPSHandler))
        assert any(isinstance(h, transport._NoRedirects) for h in handlers)
        return Opener()

    monkeypatch.setattr(transport, 'build_opener', build)
    with transport.sdk_api_transport():
        vms, status = muse_api.fetch_vms_with_status('synthetic-access')
        tokens, refresh_status = muse_api.refresh_device_token(
            'synthetic-refresh', 'synthetic-device', sdk_token='synthetic-sdk')
    assert status == refresh_status == 200
    assert vms[0]['vm_id'] == 'synthetic-id'
    assert tokens['access_token'] == 'synthetic-new-access'
    assert contexts == [context, context]
    fetch, refresh = (entry[0] for entry in calls)
    assert fetch.full_url == 'https://api.muse.ai/fetch_vms'
    assert fetch.get_header('Authorization') == 'Bearer synthetic-access'
    assert refresh.full_url == 'https://api.muse.ai/device_token/refresh'
    assert refresh.get_header('Authorization') == (
        'Bearer hatch_refresh:synthetic-refresh')
    assert json.loads(refresh.data) == {
        'device_id': 'synthetic-device',
        'sdk_token': 'synthetic-sdk'
    }
    assert [timeout for _, timeout in calls] == [15, 15]


@pytest.mark.parametrize('operation', ['fetch', 'refresh'])
def test_real_sdk_calls_reject_unapproved_origin_before_open(
        monkeypatch, operation):

    def forbidden(*_args, **_kwargs):
        raise AssertionError('An unapproved destination reached the opener')

    monkeypatch.setattr(transport, 'build_opener', forbidden)
    with transport.sdk_api_transport(), pytest.raises(
            ValueError, match='invalid_muse_destination'):
        if operation == 'fetch':
            muse_api.fetch_vms_with_status('synthetic', 'https://localhost')
        else:
            muse_api.refresh_device_token('synthetic', 'synthetic',
                                          'https://localhost')


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
@pytest.mark.parametrize('destination', [
    'https://api.muse.ai/another-path',
    'https://other.invalid/capture',
    'http://127.0.0.1/capture',
])
def test_http_redirect_cannot_send_a_second_request(monkeypatch, status,
                                                    destination):
    """Use urllib's actual redirect machinery with a synthetic HTTPS handler."""
    requests = []

    class SyntheticHttps(urllib.request.HTTPSHandler):

        def https_open(self, request):
            requests.append(request)
            headers = Message()
            headers['Location'] = destination
            response = addinfourl(io.BytesIO(b''),
                                  headers,
                                  request.full_url,
                                  code=status)
            response.msg = 'Synthetic redirect'
            return response

    monkeypatch.setattr(transport, 'HTTPSHandler', SyntheticHttps)
    with transport.sdk_api_transport():
        # The real SDK consumes HTTPError as a failed fetch; no retry follows.
        vms, received = muse_api.fetch_vms_with_status('synthetic-access')
    assert (vms, received) == ([], status)
    assert len(requests) == 1
    assert requests[0].full_url == 'https://api.muse.ai/fetch_vms'


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308])
@pytest.mark.parametrize('destination', [
    'wss://hatch.metaaivm.com/another-path',
    'wss://other.invalid/capture',
    'ws://127.0.0.1/capture',
])
def test_websocket_redirect_cannot_open_a_second_connection(
        status, destination):
    """Use websockets 17.4's connection loop, without a socket or DNS lookup."""

    async def scenario():
        attempts = []
        aborts = []
        refusal = InvalidStatus(
            Response(status, 'Synthetic redirect',
                     Headers({'Location': destination})))
        connector = transport._NoRedirectConnect(
            'wss://hatch.metaaivm.com/v1/noise?vm_id=synthetic', proxy=None)

        class Connection:
            transport = SimpleNamespace(abort=lambda: aborts.append(True))

            async def handshake(self, *_args):
                raise refusal

        async def open_connection():
            attempts.append(True)
            return Connection()

        connector.open_tcp_connection = open_connection
        with pytest.raises(InvalidStatus) as error:
            await connector
        assert error.value is refusal
        assert attempts == aborts == [True]

    asyncio.run(scenario())


def test_link_connect_preserves_sdk_settings_and_disables_proxy(monkeypatch):

    async def scenario():
        calls = []
        context, websocket = object(), object()
        monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
        monkeypatch.setenv('ALL_PROXY', 'http://127.0.0.1:1')
        monkeypatch.setattr(tls, 'client_context', lambda: context)
        monkeypatch.setattr(muse_api, 'user_agent', lambda: 'synthetic-agent')

        async def connect(url, **kwargs):
            calls.append((url, kwargs))
            return websocket

        monkeypatch.setattr(transport, '_NoRedirectConnect', connect)
        url = 'wss://hatch.metaaivm.com/v1/noise?vm_id=synthetic'
        headers = {'Authorization': 'Bearer synthetic-vm'}
        assert await transport.connect_muse(url, headers) is websocket
        assert calls == [(url, {
            'additional_headers': headers,
            'user_agent_header': 'synthetic-agent',
            'compression': None,
            'open_timeout': link_client.HANDSHAKE_TIMEOUT_S,
            'ping_interval': link_client.PING_INTERVAL_S,
            'ping_timeout': link_client.PING_INTERVAL_S,
            'max_size': 128 * 1024,
            'ssl': context,
            'proxy': None,
        })]

    asyncio.run(scenario())


@pytest.mark.parametrize('url', [
    'ws://hatch.metaaivm.com/v1/noise',
    'wss://hatch.metaaivm.com.evil.invalid/v1/noise',
    'wss://localhost/v1/noise',
    'wss://127.0.0.1/v1/noise',
    'wss://user@hatch.metaaivm.com/v1/noise',
    'wss://hatch.metaaivm.com:444/v1/noise',
    'wss://hatch.metaaivm.com/v1/noise#fragment',
])
def test_link_validates_destination_before_connect(monkeypatch, url):

    def forbidden(*_args, **_kwargs):
        raise AssertionError('An unapproved destination reached connect')

    monkeypatch.setattr(transport, '_NoRedirectConnect', forbidden)
    with pytest.raises(ValueError, match='invalid_muse_destination'):
        asyncio.run(transport.connect_muse(url, {}))


def test_binding_uses_exact_pinned_sdk_constructor_hook(monkeypatch):
    """Guard the one private field with the actual pinned SDK open path."""
    docker = Path(__file__).with_name('Dockerfile').read_text()
    assert ('ARG MUSE_SDK_COMMIT=' +
            '08f89ed329918c27d53b11192065ad6b2cb0e868') in docker
    device = link_client.DeviceDescription('homelink-aabbcc', 'Synthetic',
                                           'synthetic', {})
    session = link_client.LinkSession(noise_host='hatch.metaaivm.com',
                                      vm_id='synthetic-vm',
                                      vm_auth_token='synthetic-token',
                                      device=device,
                                      run_command=lambda *_args: {'ok': False})
    seen, websocket = [], object()

    async def guarded(url, headers):
        seen.append((url, headers))
        return websocket

    monkeypatch.setattr(transport, 'connect_muse', guarded)
    transport.bind_session_transport(session)
    assert asyncio.run(session._open()) is websocket
    assert seen == [('wss://hatch.metaaivm.com/v1/noise?vm_id=synthetic-vm', {
        'Authorization': 'Bearer synthetic-token'
    })]
    with pytest.raises(RuntimeError, match='unsupported_muse_sdk_transport'):
        transport.bind_session_transport(session)
    with pytest.raises(RuntimeError, match='unsupported_muse_sdk_transport'):
        transport.bind_session_transport(SimpleNamespace())
