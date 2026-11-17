"""Test ATT batch handling without loading macOS frameworks or pairing state."""

from collections.abc import Mapping
import sys
import types
from unittest import mock

import pytest

import mac_pairing

_RESULTS = types.SimpleNamespace(
    CBATTErrorSuccess=object(),
    CBATTErrorInsufficientAuthorization=object(),
    CBATTErrorInvalidAttributeValueLength=object(),
)


def _request(
    packet: bytes | bytearray | None,
    *,
    central_id: str = "phone",
    uuid: str = mac_pairing.RX_UUID,
    offset: int = 0,
) -> mock.Mock:
    request = mock.Mock()
    request.value.return_value = packet
    request.central.return_value.identifier.return_value = central_id
    characteristic = request.characteristic.return_value
    characteristic.UUID.return_value.UUIDString.return_value = uuid
    request.offset.return_value = offset
    return request


def _central() -> mock.Mock:
    central = mock.Mock()
    central.identifier.return_value = "phone"
    return central


def test_valid_batch_dispatches_in_order_before_one_response() -> None:
    manager = mock.Mock()
    requests = [_request(b"first"), _request(bytearray(b"second"))]
    delivered = []

    def acknowledge(request: mock.Mock, result: object) -> None:
        assert request is requests[0]
        assert result is _RESULTS.CBATTErrorSuccess
        assert delivered == [b"first", b"second"]

    manager.respondToRequest_withResult_.side_effect = acknowledge
    mac_pairing._handle_write_requests(manager, requests, _central(),
                                       delivered.append, _RESULTS)

    manager.respondToRequest_withResult_.assert_called_once_with(
        requests[0], _RESULTS.CBATTErrorSuccess)


@pytest.mark.parametrize(
    ("packet", "options", "expected_result"),
    [
        (b"second", {
            "central_id": "other-phone"
        }, _RESULTS.CBATTErrorInsufficientAuthorization),
        (b"second", {
            "uuid": mac_pairing.TX_UUID
        }, _RESULTS.CBATTErrorInvalidAttributeValueLength),
        (b"second", {
            "offset": 1
        }, _RESULTS.CBATTErrorInvalidAttributeValueLength),
        (None, {}, _RESULTS.CBATTErrorInvalidAttributeValueLength),
        (b"x" * 513, {}, _RESULTS.CBATTErrorInvalidAttributeValueLength),
    ],
    ids=[
        "other-central", "wrong-characteristic", "offset", "missing",
        "oversized"
    ],
)
def test_invalid_request_rejects_whole_batch_without_dispatch(
    packet: bytes | None,
    options: Mapping[str, object],
    expected_result: object,
) -> None:
    manager = mock.Mock()
    requests = [
        _request(b"first"),
        _request(packet, **options),
        _request(b"third"),
    ]
    on_write = mock.Mock()

    mac_pairing._handle_write_requests(manager, requests, _central(), on_write,
                                       _RESULTS)

    on_write.assert_not_called()
    manager.respondToRequest_withResult_.assert_called_once_with(
        requests[0], expected_result)


def test_unsubscribed_central_cannot_dispatch() -> None:
    manager = mock.Mock()
    requests = [_request(b"first"), _request(b"second")]
    on_write = mock.Mock()

    mac_pairing._handle_write_requests(manager, requests, None, on_write,
                                       _RESULTS)

    on_write.assert_not_called()
    manager.respondToRequest_withResult_.assert_called_once_with(
        requests[0], _RESULTS.CBATTErrorInsufficientAuthorization)


def test_help_requires_neither_sdk_nor_mac_frameworks(
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    for name in ("objc", "CoreBluetooth", "musegadget"):
        monkeypatch.setitem(sys.modules, name, None)

    with pytest.raises(SystemExit) as raised:
        mac_pairing.main(["--help"])

    assert raised.value.code == 0
    help_text = capsys.readouterr().out
    assert "--state-dir" in help_text
    assert "--probe" not in help_text
