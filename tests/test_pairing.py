import asyncio
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import support

from dbus_fast import MessageType, Variant
from dbus_fast.errors import DBusError
from dbus_fast.service import ServiceInterface
from custom_components.elga_ace import pairing, protocol
from test_client import device


class FakeBus:
    def __init__(self, paired=False, error=None):
        self.paired = paired
        self.error = error
        self.calls = []
        self.agent = None
        self.disconnected = False
        self.unexported = False
        self.hang = False
        self.pair_started = asyncio.Event()

    async def connect(self):
        return self

    def export(self, path, agent):
        self.agent = agent

    def unexport(self, path):
        self.unexported = True

    def disconnect(self):
        self.disconnected = True

    async def call(self, message):
        self.calls.append(message)
        if message.member == "Get":
            return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[Variant("b", self.paired)])
        if message.member == "Pair":
            self.pair_started.set()
            if self.hang:
                await asyncio.Future()
            if self.error:
                return SimpleNamespace(message_type=MessageType.ERROR, error_name=self.error, body=[])
        return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[])

    def invoke(self, name, *args):
        function = next(method.fn for method in ServiceInterface._get_methods(self.agent) if method.name == name)
        return function(self.agent, *args)


class PairingTests(unittest.IsolatedAsyncioTestCase):
    def setup_bus(self, **kwargs):
        bus = FakeBus(**kwargs)
        platform = patch.object(pairing, "sys", SimpleNamespace(platform="linux"))
        fake_aio = ModuleType("dbus_fast.aio")
        fake_aio.MessageBus = lambda **kwargs: bus
        constructor = patch.dict(sys.modules, {"dbus_fast.aio": fake_aio})
        platform.start()
        constructor.start()
        self.addCleanup(platform.stop)
        self.addCleanup(constructor.stop)
        return bus

    def test_pin_validation_keeps_zeroes(self):
        for value in ("000123", "1", "999999"):
            self.assertTrue(pairing.validate_pin(value))
        for value in ("", "1234567", "-123", "12 34", "１２３４５６"):
            self.assertFalse(pairing.validate_pin(value))

    async def test_scoped_agent_uses_same_bus_and_is_removed(self):
        bus = self.setup_bus()
        await pairing.async_pair(device(), "000123")
        methods = [message.member for message in bus.calls]
        self.assertEqual(methods, ["Get", "RegisterAgent", "Pair", "UnregisterAgent"])
        self.assertNotIn("RequestDefaultAgent", methods)
        self.assertTrue(bus.disconnected)
        self.assertTrue(bus.unexported)
        path = device().details["path"]
        self.assertEqual(bus.invoke("RequestPinCode", path), "000123")
        self.assertEqual(bus.invoke("RequestPasskey", path), 123)
        self.assertIsNone(bus.invoke("RequestConfirmation", path, 123))
        with self.assertRaises(DBusError):
            bus.invoke("RequestPasskey", "/org/bluez/hci0/dev_UNRELATED")
        with self.assertRaises(DBusError):
            bus.invoke("RequestConfirmation", path, 456)

    async def test_existing_bond_does_not_pair_again(self):
        bus = self.setup_bus(paired=True)
        await pairing.async_pair(device(), "000123")
        self.assertEqual([message.member for message in bus.calls], ["Get"])
        self.assertIsNone(bus.agent)
        self.assertTrue(bus.disconnected)

    async def test_incorrect_pin_is_not_silently_accepted(self):
        bus = self.setup_bus(error="org.bluez.Error.AuthenticationFailed")
        with self.assertRaises(protocol.PairingError):
            await pairing.async_pair(device(), "000123")
        self.assertTrue(bus.disconnected)
        self.assertIn("UnregisterAgent", [message.member for message in bus.calls])

    async def test_access_denied_explains_adapter_requirement(self):
        self.setup_bus(error="org.freedesktop.DBus.Error.AccessDenied")
        with self.assertRaises(protocol.UnsupportedAdapter):
            await pairing.async_pair(device(), "000123")

    async def test_cancellation_cancels_bluez_pair_and_cleans_up(self):
        bus = self.setup_bus()
        bus.hang = True
        task = asyncio.create_task(pairing.async_pair(device(), "000123"))
        await bus.pair_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        methods = [message.member for message in bus.calls]
        self.assertIn("CancelPairing", methods)
        self.assertIn("UnregisterAgent", methods)
        self.assertTrue(bus.disconnected)

    async def test_proxy_is_rejected_without_dbus_access(self):
        remote = device()
        remote.details = {"source": "esphome-proxy"}
        with self.assertRaises(protocol.UnsupportedAdapter):
            await pairing.async_pair(remote, "000123")
