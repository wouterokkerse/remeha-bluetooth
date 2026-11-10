import asyncio
import unittest
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import support

from bleak.exc import BleakError
from custom_components.elga_ace import client, pairing, protocol, transport
from test_client import CAPTURE, FakeGatt, device

SOURCE = "11:22:33:44:55:66"


def remote_device(source=SOURCE):
    remote = device()
    remote.details = {"source": source, "address_type": 0}
    return remote


def proxy_entry(source=SOURCE):
    action = SimpleNamespace(name=transport.PAIRING_ACTION, args=[SimpleNamespace(name="address"), SimpleNamespace(name="pin")])
    entry = support.FakeConfigEntry(data={"bluetooth_mac_address": source, "noise_psk": "offline-test-only"})
    entry.runtime_data = SimpleNamespace(
        available=True,
        services={42: action},
        client=SimpleNamespace(execute_service=AsyncMock(), noise_psk="offline-test-only"),
    )
    return entry


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = support.FakeHass()
        self.entry = proxy_entry()
        self.hass.config_entries.async_loaded_entries = lambda domain: [self.entry] if domain == "esphome" else []
        self.runtime = self.entry.runtime_data
        self.gatt = SimpleNamespace(pair=AsyncMock())

    def codes_sent(self):
        return [call.args[1] for call in self.runtime.client.execute_service.await_args_list]

    async def test_pin_delivered_and_cleared_on_exact_proxy(self):
        with patch.object(transport, "async_pair", new=AsyncMock()) as local_pair:
            await transport.async_pair_gateway(self.hass, self.gatt, remote_device(), "000123")
        self.assertEqual(self.codes_sent(), [
            {"address": device().address, "pin": "000123"},
            {"address": device().address, "pin": ""},
        ])
        self.gatt.pair.assert_awaited_once_with()
        local_pair.assert_not_awaited()

    async def test_local_pairing_keeps_scoped_bluez_agent(self):
        with patch.object(transport, "async_pair", new=AsyncMock()) as local_pair:
            await transport.async_pair_gateway(self.hass, self.gatt, device(), "000123")
        self.assertEqual(local_pair.await_args.args[1], "000123")
        self.gatt.pair.assert_not_awaited()
        self.runtime.client.execute_service.assert_not_awaited()

    async def test_stock_proxy_fails_clearly_without_ignoring_pin(self):
        self.runtime.services.clear()
        with self.assertRaises(protocol.ProxyPairingRequired):
            await transport.async_pair_gateway(self.hass, self.gatt, remote_device(), "000123")
        self.gatt.pair.assert_not_awaited()
        self.runtime.client.execute_service.assert_not_awaited()

    async def test_unencrypted_proxy_never_receives_credential(self):
        self.runtime.client.noise_psk = None
        with self.assertRaises(protocol.ProxyPairingRequired):
            await transport.async_pair_gateway(self.hass, self.gatt, remote_device(), "000123")
        self.runtime.client.execute_service.assert_not_awaited()

    async def test_runtime_provisioned_encryption_key_is_supported(self):
        self.entry.data.pop("noise_psk")
        await transport.async_pair_gateway(self.hass, self.gatt, remote_device(), "000123")
        self.assertEqual(self.codes_sent()[0]["pin"], "000123")

    async def test_wrong_action_signature_is_not_used(self):
        self.runtime.services[42].args = [SimpleNamespace(name="pin")]
        with self.assertRaises(protocol.ProxyPairingRequired):
            await transport.async_pair_gateway(self.hass, self.gatt, remote_device(), "000123")
        self.runtime.client.execute_service.assert_not_awaited()

    async def test_different_proxy_never_receives_pin(self):
        with self.assertRaises(protocol.ProxyPairingRequired):
            await transport.async_pair_gateway(self.hass, self.gatt, remote_device("22:33:44:55:66:77"), "000123")
        self.runtime.client.execute_service.assert_not_awaited()

    async def test_offline_proxy_is_reported_without_pairing(self):
        self.runtime.available = False
        with self.assertRaises(protocol.DeviceUnavailable):
            await transport.async_pair_gateway(self.hass, self.gatt, remote_device(), "000123")
        self.gatt.pair.assert_not_awaited()

    async def test_pairing_rejection_timeout_and_old_firmware_clear_code(self):
        for error, expected in (
            (BleakError("Pairing failed"), protocol.PairingError),
            (TimeoutError(), protocol.DeviceUnavailable),
            (NotImplementedError(), protocol.UnsupportedAdapter),
        ):
            with self.subTest(error=type(error).__name__):
                self.runtime.client.execute_service.reset_mock()
                self.gatt.pair.side_effect = error
                with self.assertRaises(expected):
                    await transport.async_pair_gateway(self.hass, self.gatt, remote_device(), "000123")
                self.assertEqual(self.codes_sent()[-1]["pin"], "")

    async def test_cancellation_clears_remote_code(self):
        started = asyncio.Event()
        async def wait():
            started.set()
            await asyncio.Future()
        self.gatt.pair.side_effect = wait
        task = asyncio.create_task(transport.async_pair_gateway(self.hass, self.gatt, remote_device(), "000123"))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.codes_sent()[-1]["pin"], "")

    async def test_api_failure_is_actionable_and_does_not_pair(self):
        self.runtime.client.execute_service.side_effect = OSError("Offline")
        with self.assertRaises(protocol.DeviceUnavailable):
            await transport.async_pair_gateway(self.hass, self.gatt, remote_device(), "000123")
        self.gatt.pair.assert_not_awaited()

    async def test_cleanup_failure_does_not_hide_pairing_failure(self):
        self.runtime.client.execute_service.side_effect = [None, OSError("Offline")]
        self.gatt.pair.side_effect = BleakError("Pairing failed")
        with self.assertRaises(protocol.PairingError):
            await transport.async_pair_gateway(self.hass, self.gatt, remote_device(), "000123")

    async def test_actual_ha_route_controls_pin_delivery_and_handshake(self):
        gateway = FakeGatt()
        gateway._connected_device = remote_device()
        gateway.pair = AsyncMock()
        reader = client.ElgaClient(device, "000123", pair_callback=partial(transport.async_pair_gateway, self.hass))
        self.addAsyncCleanup(reader.async_close)
        with patch.object(client, "establish_connection", new=AsyncMock(return_value=gateway)):
            await reader.async_connect()
            self.assertTrue(reader.connected)
            self.assertTrue(reader.is_remote)
            self.assertEqual(reader.connection_source, SOURCE)
            self.assertEqual([packet.hex() for _, packet in gateway.writes], [row[1] for row in CAPTURE])
            self.assertEqual(await reader.async_read(1, 0x5404, 1), bytes.fromhex("d700"))
        self.assertEqual(self.codes_sent()[0]["pin"], "000123")
        gateway.pair.assert_awaited_once()

    async def test_actual_local_route_overrides_proxy_advertisement(self):
        gateway = FakeGatt()
        gateway._connected_device = device()
        reader = client.ElgaClient(lambda: remote_device(), "000123", pair_callback=partial(transport.async_pair_gateway, self.hass))
        self.addAsyncCleanup(reader.async_close)
        with patch.object(client, "establish_connection", new=AsyncMock(return_value=gateway)):
            with patch.object(transport, "async_pair", new=AsyncMock()) as local_pair:
                await reader.async_connect()
                local_pair.assert_awaited_once()
        self.assertFalse(reader.is_remote)
        self.runtime.client.execute_service.assert_not_awaited()

    async def test_reconnect_on_another_proxy_prepares_its_own_code(self):
        other = proxy_entry("22:33:44:55:66:77")
        self.hass.config_entries.async_loaded_entries = lambda domain: [self.entry, other]
        for entry in (self.entry, other):
            gateway = FakeGatt()
            gateway._connected_device = remote_device(entry.data["bluetooth_mac_address"])
            gateway.pair = AsyncMock()
            reader = client.ElgaClient(device, "000123", pair_callback=partial(transport.async_pair_gateway, self.hass))
            with patch.object(client, "establish_connection", new=AsyncMock(return_value=gateway)):
                await reader.async_connect()
                await reader.async_close()
            self.assertEqual(entry.runtime_data.client.execute_service.await_count, 2)

    def test_actual_device_fallbacks(self):
        remote = remote_device()
        self.assertIs(pairing.connected_device(SimpleNamespace(_backend=SimpleNamespace(_ble_device=remote)), device()), remote)
        backend = SimpleNamespace(_device_path="/org/bluez/hci1/dev_00_80_E1_26_5E_9C")
        self.assertIn("hci1", pairing.connected_device(SimpleNamespace(_backend=backend), remote).details["path"])
