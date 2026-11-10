"""Scoped BlueZ passkey agent; never replace the system default agent."""

import asyncio
import re
import sys
from contextlib import suppress
from uuid import uuid4

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError

from .protocol import DeviceUnavailable, PairingError, UnsupportedAdapter


def bluez_path(device: BLEDevice) -> str | None:
    """Identify devices backed by a local Linux Bluetooth adapter."""
    details = device.details
    path = details.get("path") if isinstance(details, dict) else None
    if isinstance(path, str) and path.startswith("/org/bluez/"):
        return path
    return None


def validate_pin(pin: str) -> bool:
    """Keep leading zeroes while validating a Bluetooth numeric passkey."""
    return bool(re.fullmatch(r"[0-9]{1,6}", pin))


def is_proxy(device: BLEDevice | None) -> bool:
    """Recognize a remote route without requiring a local Bluetooth adapter."""
    details = getattr(device, "details", None)
    return isinstance(details, dict) and bool(details.get("source")) and not bluez_path(device)


async def async_pair(device: BLEDevice, pin: str, client=None) -> None:
    """Use standard proxy pairing or the scoped local BlueZ passkey agent."""
    if is_proxy(device):
        if client is None:
            raise UnsupportedAdapter("Connect to the proxy before pairing")
        try:
            async with asyncio.timeout(60):
                await client.pair()
        except NotImplementedError as error:
            raise UnsupportedAdapter("Update the ESPHome proxy firmware to enable pairing") from error
        except BleakError as error:
            raise DeviceUnavailable("Proxy pairing failed; check the PIN in ESPHome secrets.yaml and service mode") from error
        return
    if not validate_pin(pin):
        raise PairingError("Invalid pairing code format")
    path = bluez_path(device)
    if sys.platform != "linux" or path is None:
        raise UnsupportedAdapter("Passkey pairing requires a local Linux/BlueZ adapter")

    from dbus_fast import BusType, Message, MessageType
    from dbus_fast.aio import MessageBus
    from dbus_fast.annotations import DBusObjectPath, DBusStr, DBusUInt16, DBusUInt32
    from dbus_fast.errors import DBusError
    from dbus_fast.service import ServiceInterface, dbus_method

    class PasskeyAgent(ServiceInterface):
        """Only answer authentication requests for the selected device."""

        def __init__(self):
            super().__init__("org.bluez.Agent1")

        def _check(self, device_path):
            if device_path != path:
                raise DBusError("org.bluez.Error.Rejected", "Unrelated device")

        @dbus_method()
        def Release(self):
            pass

        @dbus_method()
        def RequestPinCode(self, device_path: DBusObjectPath) -> DBusStr:
            self._check(device_path)
            return pin

        @dbus_method()
        def RequestPasskey(self, device_path: DBusObjectPath) -> DBusUInt32:
            self._check(device_path)
            return int(pin)

        @dbus_method()
        def DisplayPinCode(self, device_path: DBusObjectPath, pincode: DBusStr):
            self._check(device_path)
            if pincode != pin:
                raise DBusError("org.bluez.Error.Rejected", "Unexpected pairing method")

        @dbus_method()
        def DisplayPasskey(self, device_path: DBusObjectPath, passkey: DBusUInt32, entered: DBusUInt16):
            self._check(device_path)
            if passkey != int(pin):
                raise DBusError("org.bluez.Error.Rejected", "Unexpected passkey")

        @dbus_method()
        def RequestConfirmation(self, device_path: DBusObjectPath, passkey: DBusUInt32):
            self._check(device_path)
            if passkey != int(pin):
                raise DBusError("org.bluez.Error.Rejected", "Passkey mismatch")

        @dbus_method()
        def RequestAuthorization(self, device_path: DBusObjectPath):
            self._check(device_path)
            raise DBusError("org.bluez.Error.Rejected", "A passkey is required")

        @dbus_method()
        def AuthorizeService(self, device_path: DBusObjectPath, service_uuid: DBusStr):
            self._check(device_path)

        @dbus_method()
        def Cancel(self):
            pass

    async def call(bus, object_path, interface, member, signature="", body=None):
        reply = await bus.call(Message(
            destination="org.bluez", path=object_path, interface=interface,
            member=member, signature=signature, body=body or [],
        ))
        if reply is None:
            raise DeviceUnavailable("No response from BlueZ")
        if reply.message_type == MessageType.ERROR:
            if reply.error_name == "org.bluez.Error.AlreadyExists":
                return reply
            if reply.error_name in {
                "org.bluez.Error.AuthenticationFailed",
                "org.bluez.Error.AuthenticationRejected",
                "org.bluez.Error.AuthenticationCanceled",
            }:
                raise PairingError("Bluetooth authentication was rejected")
            if reply.error_name in {
                "org.freedesktop.DBus.Error.AccessDenied",
                "org.bluez.Error.NotAuthorized",
            }:
                raise UnsupportedAdapter("BlueZ D-Bus access was denied")
            raise DeviceUnavailable(f"BlueZ operation failed: {reply.error_name}")
        return reply

    bus = None
    registered = False
    pairing_started = False
    agent_path = f"/org/homeassistant/elga_ace/agent_{uuid4().hex}"
    try:
        async with asyncio.timeout(90):
            bus = MessageBus(bus_type=BusType.SYSTEM)
            await bus.connect()
            paired = await call(bus, path, "org.freedesktop.DBus.Properties", "Get", "ss", ["org.bluez.Device1", "Paired"])
            if paired.body and paired.body[0].value:
                return
            bus.export(agent_path, PasskeyAgent())
            await call(bus, "/org/bluez", "org.bluez.AgentManager1", "RegisterAgent", "os", [agent_path, "KeyboardOnly"])
            registered = True
            pairing_started = True
            await call(bus, path, "org.bluez.Device1", "Pair")
            pairing_started = False
    except TimeoutError as error:
        raise DeviceUnavailable("Bluetooth pairing timed out") from error
    except (OSError, DBusError) as error:
        raise DeviceUnavailable("Could not communicate with BlueZ") from error
    finally:
        if bus is not None:
            if pairing_started:
                with suppress(Exception):
                    async with asyncio.timeout(3):
                        await call(bus, path, "org.bluez.Device1", "CancelPairing")
            if registered:
                with suppress(Exception):
                    async with asyncio.timeout(3):
                        await call(bus, "/org/bluez", "org.bluez.AgentManager1", "UnregisterAgent", "o", [agent_path])
                bus.unexport(agent_path)
            bus.disconnect()
