#!/usr/bin/env python3
"""Experimental macOS BLE pairing transport for the Muse Gadget SDK.

This community example is not official Meta support for macOS. It replaces
only the Linux Bluetooth peripheral transport with CoreBluetooth. The SDK's
pairing protocol, credential verification, encryption and persistence remain
unchanged. It never starts the SDK command-execution service.

Use Python 3.11+ and install requirements-macos.txt and the musegadget package.

    python mac_pairing.py --state-dir /path/to/private/pairing-state

The SDK token is entered at a hidden interactive prompt, never as an argument.
--help requires neither macOS frameworks nor an installed SDK.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
import getpass
import os
from pathlib import Path
import stat
import sys
import threading
import time
import types
from typing import Any
import warnings

SERVICE_UUID = "7fdd3d1c-38ea-46cf-8b46-314ecf5f240c"
RX_UUID = "4d593029-28a2-4a6e-a1f0-3c2d5e8f9b01"
TX_UUID = "d75dc4ca-7b2b-4e9c-8f0a-1d2e3f4a5b6c"
DEFAULT_STATE_DIR = "~/Library/Application Support/MusePairingMac"


class SetupError(Exception):
    """A local setup problem whose message contains no credential values."""


def _handle_write_requests(
    manager: Any,
    requests: Sequence[Any],
    central: Any,
    on_write: Callable[[bytes], None],
    bluetooth: Any,
) -> None:
    """Validate an entire ATT batch before dispatching and acknowledge once."""
    if not requests:
        return
    packets = []
    result = bluetooth.CBATTErrorSuccess
    for request in requests:
        value = request.value()
        if (central is None or
                central.identifier() != request.central().identifier()):
            result = bluetooth.CBATTErrorInsufficientAuthorization
            break
        if (str(request.characteristic().UUID().UUIDString()).lower() != RX_UUID
                or request.offset() != 0 or value is None or len(value) > 512):
            result = bluetooth.CBATTErrorInvalidAttributeValueLength
            break
        packets.append(bytes(value))
    if result == bluetooth.CBATTErrorSuccess:
        for packet in packets:
            on_write(packet)
    # CoreBluetooth treats the batch as one request, represented by its first
    # element. A rejected batch must not partially reach the SDK controller.
    manager.respondToRequest_withResult_(requests[0], result)


def _mac_transport() -> type:
    """Build the CoreBluetooth adapter only when pairing is requested."""
    # Keep platform imports lazy: importing the example or asking for --help
    # must not load CoreBluetooth, request permission, or touch device state.
    # PyObjC exposes framework symbols dynamically, beyond static lookup.
    # pylint: disable=import-outside-toplevel,no-name-in-module,no-member
    import objc
    import CoreBluetooth as bluetooth
    from Foundation import NSData, NSObject
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSEvent,
        NSEventTypeApplicationDefined,
    )
    from musegadget.ble_framing import (
        CHUNK_STAGGER_S,
        DEFAULT_ATT_MTU,
        MAX_PACKET_BYTES,
    )

    class MuseMacPairingDelegate(
            NSObject,
            protocols=[objc.protocolNamed("CBPeripheralManagerDelegate")]):
        """Translate CoreBluetooth events into SDK transport callbacks."""

        # Apple fixes selector names. PyObjC objects are wired after alloc/init.
        # pylint: disable=invalid-name,attribute-defined-outside-init

        def peripheralManagerDidUpdateState_(self, manager):
            """Publish the setup service once Bluetooth becomes available."""
            state = int(manager.state())
            messages = {
                5: "Bluetooth is on.",
                4: "Turn on Bluetooth in macOS Settings.",
                3: "Allow Bluetooth for Terminal/Python in macOS Settings.",
                2: "This Mac does not support Bluetooth peripheral mode.",
            }
            print(messages.get(state, "Initializing Bluetooth..."), flush=True)
            if state in (2, 3):
                self.owner.stop()
            if state != 5:
                return
            rx = bluetooth.CBMutableCharacteristic.alloc(
            ).initWithType_properties_value_permissions_(
                bluetooth.CBUUID.UUIDWithString_(RX_UUID),
                bluetooth.CBCharacteristicPropertyWrite |
                bluetooth.CBCharacteristicPropertyWriteWithoutResponse,
                None,
                bluetooth.CBAttributePermissionsWriteable,
            )
            self.tx = bluetooth.CBMutableCharacteristic.alloc(
            ).initWithType_properties_value_permissions_(
                bluetooth.CBUUID.UUIDWithString_(TX_UUID),
                bluetooth.CBCharacteristicPropertyNotify |
                bluetooth.CBCharacteristicPropertyRead,
                None,
                bluetooth.CBAttributePermissionsReadable,
            )
            service = bluetooth.CBMutableService.alloc().initWithType_primary_(
                bluetooth.CBUUID.UUIDWithString_(SERVICE_UUID), True)
            service.setCharacteristics_([rx, self.tx])
            manager.addService_(service)

        def peripheralManager_didAddService_error_(self, manager, _service,
                                                   error):
            """Advertise the SDK device name after service registration."""
            if error is not None:
                print("Could not publish the Bluetooth service.", flush=True)
                self.owner.stop()
                return
            # Name-only advertising preserves the full MuseGadgetXXXXXX name
            # on macOS. Its GATT service is discoverable after connecting.
            manager.startAdvertising_(
                {bluetooth.CBAdvertisementDataLocalNameKey: self.owner.name})

        def peripheralManagerDidStartAdvertising_error_(self, _manager, error):
            """Report advertising status without printing framework errors."""
            if error is None:
                print("Advertising: " + self.owner.name, flush=True)
            else:
                print("Bluetooth advertising failed.", flush=True)
                self.owner.stop()

        def peripheralManager_central_didSubscribeToCharacteristic_(
                self, _manager, central, _characteristic):
            """Remember one subscribing phone and its notification capacity."""
            if self.central is not None and self.central.identifier(
            ) != central.identifier():
                return
            self.central = central
            self.owner.packet_mtu = (
                min(MAX_PACKET_BYTES, int(central.maximumUpdateValueLength())) +
                3)
            print("Phone connected to the Bluetooth service.", flush=True)
            self.drain_(None)

        def peripheralManager_central_didUnsubscribeFromCharacteristic_(
                self, _manager, central, _characteristic):
            """Clear pending packets and reset the disconnected SDK session."""
            if self.central is None or self.central.identifier(
            ) != central.identifier():
                return
            self.central = None
            with self.owner.lock:
                self.owner.pending.clear()
            self.owner.packet_mtu = DEFAULT_ATT_MTU
            self.owner.on_disconnect()

        def peripheralManager_didReceiveWriteRequests_(self, manager, requests):
            """Validate and deliver one atomic ATT write batch."""
            _handle_write_requests(manager, requests, self.central,
                                   self.owner.on_write, bluetooth)

        def peripheralManager_didReceiveReadRequest_(self, manager, request):
            """Return the empty readable value of the notification channel."""
            request.setValue_(NSData.data())
            result = (bluetooth.CBATTErrorSuccess if request.offset() == 0 else
                      bluetooth.CBATTErrorInvalidOffset)
            manager.respondToRequest_withResult_(request, result)

        def peripheralManagerIsReadyToUpdateSubscribers_(self, _manager):
            """Resume notifications after CoreBluetooth backpressure clears."""
            self.drain_(None)

        @objc.typedSelector(b"v@:@")
        def drain_(self, _ignored):
            """Drain queued notifications on the CoreBluetooth main thread."""
            if self.central is None:
                return
            with self.owner.lock:
                while self.owner.pending:
                    packet = self.owner.pending[0]
                    # Apple fixes this Objective-C selector's spelling.
                    # pylint: disable-next=line-too-long
                    sent = self.manager.updateValue_forCharacteristic_onSubscribedCentrals_(
                        NSData.dataWithBytes_length_(packet, len(packet)),
                        self.tx, [self.central])
                    if not sent:
                        return
                    self.owner.pending.pop(0)

        @objc.typedSelector(b"v@:@")
        def stop_(self, _ignored):
            """Stop BLE and wake AppKit so the pairing process can exit."""
            self.manager.stopAdvertising()
            self.manager.removeAllServices()
            app = NSApplication.sharedApplication()
            app.stop_(None)
            # Wake AppKit so stop takes effect without another UI event.
            # Apple fixes this Objective-C selector's spelling.
            # pylint: disable-next=line-too-long
            event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                NSEventTypeApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0)
            app.postEvent_atStart_(event, True)

    class MacBleServer:
        """Expose CoreBluetooth through the official SDK transport interface."""

        def __init__(
            self,
            local_name: str,
            on_write: Callable[[bytes], None],
            on_disconnect: Callable[[], None],
        ) -> None:
            self.name = local_name
            self.on_write = on_write
            self.on_disconnect = on_disconnect
            self.packet_mtu = DEFAULT_ATT_MTU
            self.pending = []
            self.lock = threading.Lock()
            self.delegate = None

        def mtu(self) -> int:
            """Return the phone's current ATT MTU."""
            return self.packet_mtu

        def send_packets(self, packets: list[bytes]) -> None:
            """Queue SDK-framed packets in order using upstream pacing."""
            for packet in packets:
                with self.lock:
                    if len(self.pending) >= 512:
                        raise ConnectionError(
                            "Bluetooth notification queue full")
                    self.pending.append(packet)
                # Apple fixes this Objective-C selector's spelling.
                # pylint: disable-next=line-too-long
                self.delegate.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "drain:", None, False)
                time.sleep(CHUNK_STAGGER_S)

        def disconnect(self, delay: float = 0) -> None:
            """Stop BLE after the SDK's protocol error response has drained."""
            timer = threading.Timer(delay, self.stop)
            timer.daemon = True
            timer.start()

        def stop(self) -> None:
            """Schedule shutdown on the main thread."""
            if self.delegate is not None:
                # Apple fixes this Objective-C selector's spelling.
                # pylint: disable-next=line-too-long
                self.delegate.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "stop:", None, False)

        def run(self) -> None:
            """Serve setup requests until the SDK stops pairing."""
            app = NSApplication.sharedApplication()
            app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            self.delegate = MuseMacPairingDelegate.alloc().init()
            self.delegate.owner = self
            self.delegate.central = None
            self.delegate.manager = bluetooth.CBPeripheralManager.alloc(
            ).initWithDelegate_queue_options_(
                self.delegate, None,
                {bluetooth.CBPeripheralManagerOptionShowPowerAlertKey: False})
            try:
                app.run()
            finally:
                self.delegate.manager.stopAdvertising()
                self.delegate.manager.removeAllServices()

    return MacBleServer


def _private_state_directory(directory: Path) -> None:
    """Require an owner-only real directory for SDK credential files."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid(
    ) or info.st_mode & 0o077:
        raise SetupError("Use a state directory owned by you with mode 0700.")


def _ensure_sdk_token(config: types.ModuleType) -> None:
    """Read or privately prompt for a token without replacing existing files."""
    path = config.state_dir() / config.SDK_TOKEN_FILE
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid(
        ) or info.st_mode & 0o077:
            raise SetupError("The token must be your regular file, mode 0600.")
        try:
            if config.sdk_token():
                return
        except ValueError:
            pass
        raise SetupError("Existing SDK token is invalid; no file was changed.")
    if not sys.stdin.isatty():
        raise SetupError("Use an interactive terminal for hidden token input.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass(
                "Muse SDK token from gadgets.muse.ai (hidden): ").strip()
    except (getpass.GetPassWarning, EOFError):
        raise SetupError(
            "Hidden input is unavailable; no token saved.") from None
    os.environ[config.SDK_TOKEN_ENV] = token
    try:
        if not config.sdk_token():
            raise ValueError
    except ValueError:
        raise SetupError("Invalid SDK token; no token saved.") from None
    finally:
        os.environ.pop(config.SDK_TOKEN_ENV, None)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(token)
        stream.flush()
        os.fsync(stream.fileno())
    del token


def _pair_device(state_directory: Path, transport: type) -> int:
    """Run official pairing with only its platform transport substituted."""
    # Delayed SDK imports let --help work without the package installed.
    # pylint: disable=import-outside-toplevel
    from musegadget import config

    os.environ[config.STATE_DIR_ENV] = str(state_directory)
    # This helper deliberately uses a durable private token file, not a token
    # accidentally inherited from another SDK invocation in the shell.
    inherited_token = os.environ.pop(config.SDK_TOKEN_ENV, None)
    try:
        _private_state_directory(state_directory)
        pairing_path = state_directory / config.PAIRING_FILE
        if pairing_path.exists() or pairing_path.is_symlink():
            print("Existing pairing found; no changes were made.")
            return 0
        adapter = types.ModuleType("musegadget.ble_server")
        adapter.BleServer = transport
        sys.modules["musegadget.ble_server"] = adapter
        from musegadget.pair import PairOutcome, pair

        _ensure_sdk_token(config)

        def opened(window):
            print(
                "In Muse, open Settings > Devices, enable Developer mode, "
                "and add: " + window.ble_name,
                flush=True,
            )
            print("Choose Use current connection. Window: 10 minutes.",
                  flush=True)

        result = pair(on_open=opened)
        if result.outcome is PairOutcome.PAIRED:
            print("Paired. State saved; no command service was started.")
            return 0
        print("Pairing did not complete.")
        return 1
    finally:
        if inherited_token is not None:
            os.environ[config.SDK_TOKEN_ENV] = inherited_token


def main(argv: Sequence[str] | None = None) -> int:
    """Run one macOS pairing session and report only safe local errors."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=DEFAULT_STATE_DIR,
        help="Private pairing-state directory (default: %(default)s).",
    )
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("Pairing requires macOS; --help works on any platform.")
    if sys.version_info < (3, 11):
        parser.error("PyObjC 13.0 requires Python 3.11 or later.")
    try:
        transport = _mac_transport()
        os.umask(0o077)
        return _pair_device(args.state_dir.expanduser().absolute(), transport)
    except ImportError:
        print(
            "Install musegadget and requirements-macos.txt first.",
            file=sys.stderr,
        )
        return 2
    except SetupError as error:
        print(str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Pairing cancelled.", file=sys.stderr)
        return 130
    except Exception as error:  # pylint: disable=broad-exception-caught
        # Framework/network error messages may contain private data.
        print("Pairing failed: " + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
