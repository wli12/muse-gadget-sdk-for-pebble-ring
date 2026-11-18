"""Keep the official Muse SDK's API and Link traffic on approved origins."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from urllib import error as url_error
from urllib.parse import urlsplit
from urllib.request import (
    build_opener,
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
)

from websockets.asyncio.client import connect

API_HOST = 'api.muse.ai'
LINK_HOST = 'hatch.metaaivm.com'


def _check_origin(url: str,
                  scheme: str,
                  host: str,
                  *,
                  root: bool = False) -> None:
    """Reject ambiguous authorities and every unapproved network origin."""
    if not isinstance(url, str) or any(
            char.isspace() or not char.isprintable() for char in url):
        raise ValueError('invalid_muse_destination')
    try:
        parsed = urlsplit(url)
        # An exact authority match also excludes userinfo and other ports.
        valid = (parsed.scheme == scheme and
                 parsed.netloc.lower() in {host, f'{host}:443'} and
                 '#' not in url)
    except ValueError:
        raise ValueError('invalid_muse_destination') from None
    if not valid or (root and (parsed.path not in {'', '/'} or '?' in url)):
        raise ValueError('invalid_muse_destination')


def validate_pairing_destinations(pairing: dict) -> None:
    """Allow defaults or the official API root and bare Link host only."""
    if not isinstance(pairing, dict):
        raise ValueError('invalid_muse_destination')
    api = pairing.get('api_url_v2')
    if api is not None and api != '':
        _check_origin(api, 'https', API_HOST, root=True)
    link = pairing.get('noise_host')
    if link is not None and link != '' and (not isinstance(
            link, str) or link.lower() not in {LINK_HOST, f'{LINK_HOST}:443'}):
        raise ValueError('invalid_muse_destination')


class _NoRedirects(HTTPRedirectHandler):
    """Return no redirected request, so urllib raises its HTTPError."""

    def redirect_request(self, *_args, **_kwargs) -> None:
        return None


def _open_api(request, timeout=15, *, context=None):
    """Use the SDK's TLS context with no environment proxy or redirects."""
    _check_origin(getattr(request, 'full_url', request), 'https', API_HOST)
    if context is None:
        # Keep context creation in the pinned SDK; never weaken TLS here.
        from musegadget import tls  # pylint: disable=import-outside-toplevel

        context = tls.client_context()
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context),
                          _NoRedirects())
    return opener.open(request, timeout=timeout)


@contextmanager
def sdk_api_transport():
    """Constrain SDK HTTP calls without replacing its fetch/refresh protocol.

    The bridge serializes SDK sessions. Replace only this module's urllib
    reference, including during its refresh worker thread; stdlib urllib and
    Google Storage clients remain untouched. Keep this context outside
    asyncio.run() so executor threads finish before the binding is restored.
    """
    from musegadget import muse_api  # pylint: disable=import-outside-toplevel

    previous = muse_api.urllib
    muse_api.urllib = SimpleNamespace(request=SimpleNamespace(
        Request=Request, urlopen=_open_api),
                                      error=url_error)
    try:
        yield
    finally:
        muse_api.urllib = previous


class _NoRedirectConnect(connect):
    """Keep every WebSocket upgrade on its original approved origin."""

    def process_redirect(self, exception: Exception) -> Exception:
        return exception


async def connect_muse(url: str, headers: dict):
    """Open an official Link connection with SDK settings and no proxy."""
    _check_origin(url, 'wss', LINK_HOST)
    # Match SDK 08f89ed LinkSession._open; all protocol work stays upstream.
    # pylint: disable=import-outside-toplevel
    from musegadget import link_client, tls
    from musegadget.muse_api import user_agent

    # pylint: enable=import-outside-toplevel
    return await _NoRedirectConnect(
        url,
        additional_headers=headers,
        user_agent_header=user_agent(),
        compression=None,
        open_timeout=link_client.HANDSHAKE_TIMEOUT_S,
        ping_interval=link_client.PING_INTERVAL_S,
        ping_timeout=link_client.PING_INTERVAL_S,
        max_size=128 * 1024,
        ssl=tls.client_context(),
        proxy=None)


def bind_session_transport(session) -> None:
    """Install the SDK constructor's existing connect callback before run().

    Pinned Service constructs LinkSession without exposing its public connect
    constructor argument. This single private field is the compatibility seam;
    tests cover the exact SDK pin and actual constructor/_open callback path.
    No Service session lifecycle or Noise code is copied or replaced.
    """
    # pylint: disable=protected-access
    if getattr(session, '_connect', False) is not None:
        raise RuntimeError('unsupported_muse_sdk_transport')
    session._connect = connect_muse
