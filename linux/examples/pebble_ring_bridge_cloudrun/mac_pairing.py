#!/usr/bin/env python3
"""Experimental macOS BLE pairing transport for the Muse Gadget SDK.

This community example is not official Meta support for macOS. It replaces
only the Linux Bluetooth peripheral transport with CoreBluetooth. The SDK's
pairing protocol, credential verification, encryption and persistence remain
unchanged. It never starts the SDK command-execution service.

Use Python 3.11+ and install requirements-macos.txt plus the Linux SDK's Python
dependencies. In an SDK checkout, this script finds linux/src automatically;
when copied elsewhere, install the musegadget package first.

    python mac_pairing.py --state-dir /path/to/private/pairing-state
    python mac_pairing.py --probe

The SDK token is entered at a hidden interactive prompt, never as an argument.
State is created only when pairing is invoked. --probe advertises for 45 seconds
without reading tokens, creating an identity, or completing pairing. --help
requires neither macOS frameworks nor an installed SDK.
"""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path
import stat
import sys
import threading
import time
import types
import warnings


SERVICE_UUID = "7fdd3d1c-38ea-46cf-8b46-314ecf5f240c"
RX_UUID = "4d593029-28a2-4a6e-a1f0-3c2d5e8f9b01"
TX_UUID = "d75dc4ca-7b2b-4e9c-8f0a-1d2e3f4a5b6c"
DEFAULT_STATE_DIR = "~/Library/Application Support/MusePairingMac"


class SetupError(Exception):
    """A local setup problem whose message contains no credential values."""


def _find_sdk() -> None:
    for directory in Path(__file__).resolve().parents:
        source = directory / "src"
        if (source / "musegadget" / "pair.py").is_file():
            sys.path.insert(0, str(source))
            return
    # A standalone copy can use an installed package instead.


def _mac_transport():
    # Keep platform imports lazy: importing the example or asking for --help
    # must not load CoreBluetooth, request permission, or touch device state.
    import objc
    import CoreBluetooth as bluetooth
    from Foundation import NSData, NSObject
    from AppKit import (
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSEvent,
        NSEventTypeApplicationDefined,
    )

    class MuseMacPairingDelegate(
        NSObject, protocols=[objc.protocolNamed("CBPeripheralManagerDelegate")]
    ):
        def peripheralManagerDidUpdateState_(self, manager):
            state = int(manager.state())
            messages = {
                5: "Bluetooth is on.",
                4: "Turn on Bluetooth in macOS Settings.",
                3: "Allow Bluetooth access for this terminal or Python in macOS Settings.",
                2: "This Mac does not support Bluetooth peripheral mode.",
            }
            print(messages.get(state, "Initializing Bluetooth..."), flush=True)
            if state in (2, 3):
                self.owner.stop()
            if state != 5:
                return
            rx = bluetooth.CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
                bluetooth.CBUUID.UUIDWithString_(RX_UUID),
                bluetooth.CBCharacteristicPropertyWrite
                | bluetooth.CBCharacteristicPropertyWriteWithoutResponse,
                None,
                bluetooth.CBAttributePermissionsWriteable,
            )
            self.tx = bluetooth.CBMutableCharacteristic.alloc().initWithType_properties_value_permissions_(
                bluetooth.CBUUID.UUIDWithString_(TX_UUID),
                bluetooth.CBCharacteristicPropertyNotify | bluetooth.CBCharacteristicPropertyRead,
                None,
                bluetooth.CBAttributePermissionsReadable,
            )
            service = bluetooth.CBMutableService.alloc().initWithType_primary_(
                bluetooth.CBUUID.UUIDWithString_(SERVICE_UUID), True
            )
            service.setCharacteristics_([rx, self.tx])
            manager.addService_(service)

        def peripheralManager_didAddService_error_(self, manager, service, error):
            if error is not None:
                print("Could not publish the Bluetooth service.", flush=True)
                self.owner.stop()
                return
            # Name-only advertising preserves the full MuseGadgetXXXXXX name
            # on macOS. Its GATT service is discoverable after connecting.
            manager.startAdvertising_(
                {bluetooth.CBAdvertisementDataLocalNameKey: self.owner.name}
            )

        def peripheralManagerDidStartAdvertising_error_(self, manager, error):
            if error is None:
                print("Advertising: " + self.owner.name, flush=True)
            else:
                print("Bluetooth advertising failed.", flush=True)
                self.owner.stop()

        def peripheralManager_central_didSubscribeToCharacteristic_(
            self, manager, central, characteristic
        ):
            if self.central is not None and self.central.identifier() != central.identifier():
                return
            self.central = central
            self.owner.packet_mtu = min(160, int(central.maximumUpdateValueLength())) + 3
            print("Phone connected to the Bluetooth service.", flush=True)
            self.drain_(None)

        def peripheralManager_central_didUnsubscribeFromCharacteristic_(
            self, manager, central, characteristic
        ):
            if self.central is None or self.central.identifier() != central.identifier():
                return
            self.central = None
            with self.owner.lock:
                self.owner.pending.clear()
            self.owner.packet_mtu = 23
            self.owner.on_disconnect()

        def peripheralManager_didReceiveWriteRequests_(self, manager, requests):
            for request in requests:
                value = request.value()
                if self.central is None or self.central.identifier() != request.central().identifier():
                    result = bluetooth.CBATTErrorInsufficientAuthorization
                elif (
                    str(request.characteristic().UUID().UUIDString()).lower() != RX_UUID
                    or request.offset() != 0
                    or value is None
                    or len(value) > 512
                ):
                    result = bluetooth.CBATTErrorInvalidAttributeValueLength
                else:
                    self.owner.on_write(bytes(value))
                    result = bluetooth.CBATTErrorSuccess
                manager.respondToRequest_withResult_(request, result)

        def peripheralManager_didReceiveReadRequest_(self, manager, request):
            request.setValue_(NSData.data())
            result = (
                bluetooth.CBATTErrorSuccess
                if request.offset() == 0
                else bluetooth.CBATTErrorInvalidOffset
            )
            manager.respondToRequest_withResult_(request, result)

        def peripheralManagerIsReadyToUpdateSubscribers_(self, manager):
            self.drain_(None)

        @objc.typedSelector(b"v@:@")
        def drain_(self, ignored):
            if self.central is None:
                return
            with self.owner.lock:
                while self.owner.pending:
                    packet = self.owner.pending[0]
                    sent = self.manager.updateValue_forCharacteristic_onSubscribedCentrals_(
                        NSData.dataWithBytes_length_(packet, len(packet)), self.tx, [self.central]
                    )
                    if not sent:
                        return
                    self.owner.pending.pop(0)

        @objc.typedSelector(b"v@:@")
        def stop_(self, ignored):
            self.manager.stopAdvertising()
            self.manager.removeAllServices()
            app = NSApplication.sharedApplication()
            app.stop_(None)
            # Wake AppKit's event loop so stop takes effect without another UI event.
            event = NSEvent.otherEventWithType_location_modifierFlags_timestamp_windowNumber_context_subtype_data1_data2_(
                NSEventTypeApplicationDefined, (0, 0), 0, 0, 0, None, 0, 0, 0
            )
            app.postEvent_atStart_(event, True)

    class MacBleServer:
        def __init__(self, local_name, on_write, on_disconnect):
            self.name = local_name
            self.on_write = on_write
            self.on_disconnect = on_disconnect
            self.packet_mtu = 23
            self.pending = []
            self.lock = threading.Lock()
            self.delegate = None

        def mtu(self):
            return self.packet_mtu

        def send_packets(self, packets):
            for packet in packets:
                with self.lock:
                    if len(self.pending) >= 512:
                        raise ConnectionError("Bluetooth notification queue full")
                    self.pending.append(packet)
                self.delegate.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "drain:", None, False
                )
                time.sleep(0.05)

        def disconnect(self, delay=0):
            timer = threading.Timer(delay, self.stop)
            timer.daemon = True
            timer.start()

        def stop(self):
            if self.delegate is not None:
                self.delegate.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "stop:", None, False
                )

        def run(self):
            app = NSApplication.sharedApplication()
            app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            self.delegate = MuseMacPairingDelegate.alloc().init()
            self.delegate.owner = self
            self.delegate.central = None
            self.delegate.manager = bluetooth.CBPeripheralManager.alloc().initWithDelegate_queue_options_(
                self.delegate, None, {bluetooth.CBPeripheralManagerOptionShowPowerAlertKey: False}
            )
            try:
                app.run()
            finally:
                self.delegate.manager.stopAdvertising()
                self.delegate.manager.removeAllServices()

    return MacBleServer


def _private_state_directory(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise SetupError("Use an owner-only state directory (mode 0700) owned by your account.")


def _ensure_sdk_token(config) -> None:
    path = config.state_dir() / config.SDK_TOKEN_FILE
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise SetupError("The SDK token must be an owner-only regular file (mode 0600).")
        try:
            if config.sdk_token():
                return
        except ValueError:
            pass
        raise SetupError("The existing SDK token is invalid; no file was changed.")
    if not sys.stdin.isatty():
        raise SetupError("Run pairing in an interactive terminal so the SDK token can be entered privately.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass("Muse SDK token from gadgets.muse.ai (hidden): ").strip()
    except (getpass.GetPassWarning, EOFError):
        raise SetupError("A hidden token prompt is unavailable; no token was saved.") from None
    os.environ[config.SDK_TOKEN_ENV] = token
    try:
        if not config.sdk_token():
            raise ValueError
    except ValueError:
        raise SetupError("Invalid SDK token; copy it again from gadgets.muse.ai. No token was saved.") from None
    finally:
        os.environ.pop(config.SDK_TOKEN_ENV, None)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(token)
        stream.flush()
        os.fsync(stream.fileno())
    del token


def _pair_device(state_directory: Path, transport) -> int:
    _find_sdk()
    from musegadget import config

    os.environ[config.STATE_DIR_ENV] = str(state_directory)
    # This helper deliberately uses a durable private token file, not a token
    # accidentally inherited from another SDK invocation in the shell.
    inherited_token = os.environ.pop(config.SDK_TOKEN_ENV, None)
    try:
        _private_state_directory(state_directory)
        pairing_path = state_directory / config.PAIRING_FILE
        if pairing_path.exists() or pairing_path.is_symlink():
            print("Existing pairing found; it was not read, replaced, or paired again.")
            return 0
        adapter = types.ModuleType("musegadget.ble_server")
        adapter.BleServer = transport
        sys.modules["musegadget.ble_server"] = adapter
        from musegadget.pair import PairOutcome, pair

        _ensure_sdk_token(config)

        def opened(window):
            print(
                "In the Muse phone app, open Settings > Devices, enable Developer mode, "
                "and add: " + window.ble_name,
                flush=True,
            )
            print("Choose Use current connection. The pairing window lasts 10 minutes.", flush=True)

        result = pair(on_open=opened)
        if result.outcome is PairOutcome.PAIRED:
            print("Paired. Local state saved; no cloud bridge or command-execution service was started.")
            return 0
        print("Pairing did not complete. No command-execution service was started.")
        return 1
    finally:
        if inherited_token is not None:
            os.environ[config.SDK_TOKEN_ENV] = inherited_token


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--state-dir", type=Path, default=DEFAULT_STATE_DIR,
        help="Private pairing-state directory (default: %(default)s). Ignored by --probe.",
    )
    parser.add_argument(
        "--probe", action="store_true",
        help="Advertise a test BLE name for 45 seconds; do not pair or read/write credentials.",
    )
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("This experimental Bluetooth transport requires macOS; --help works on any platform.")
    if sys.version_info < (3, 11):
        parser.error("The pinned PyObjC 13.0 dependencies require Python 3.11 or later.")
    try:
        transport = _mac_transport()
        if args.probe:
            server = transport("MuseGadgetA1B2C3", lambda packet: None, lambda: None)
            timer = threading.Timer(45, server.stop)
            timer.daemon = True
            timer.start()
            try:
                server.run()
            finally:
                timer.cancel()
            return 0
        os.umask(0o077)
        return _pair_device(args.state_dir.expanduser().absolute(), transport)
    except ImportError:
        print("Install requirements-macos.txt and the musegadget SDK's Python dependencies first.", file=sys.stderr)
        return 2
    except SetupError as error:
        print(str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Pairing cancelled.", file=sys.stderr)
        return 130
    except Exception as error:
        # Framework/network errors may contain private data; never print their arguments.
        print("Pairing stopped unexpectedly (" + type(error).__name__ + ").", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
