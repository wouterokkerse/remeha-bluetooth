import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import support

from custom_components.elga_ace import config_flow
from custom_components.elga_ace.protocol import PairingError, ProtocolError, ProxyPairingRequired, UnsupportedAdapter
from test_client import device


class FlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.flow = config_flow.ElgaConfigFlow()
        self.flow.hass = support.FakeHass()
        self.flow.context = {}
        self.info = SimpleNamespace(address=device().address, name="GTW-test", connectable=True)
        self.local_patch = patch.object(config_flow, "connectable_device", return_value=device())
        self.scan_patch = patch.object(config_flow.bluetooth, "async_discovered_service_info", return_value=[self.info])
        self.active_patch = patch.object(config_flow.bluetooth, "async_request_active_scan", new=AsyncMock())
        self.local_patch.start()
        self.scan_patch.start()
        self.active_patch.start()
        self.addCleanup(self.local_patch.stop)
        self.addCleanup(self.scan_patch.stop)
        self.addCleanup(self.active_patch.stop)

    async def test_user_selects_from_readable_device_list(self):
        result = await self.flow.async_step_user()
        self.assertEqual(result["type"], "form")
        schema = result["data_schema"].schema
        selection = next(iter(schema.values()))
        self.assertIn("GTW-test", selection.config["options"][0]["label"])
        result = await self.flow.async_step_user({"address": self.info.address})
        self.assertEqual(result["step_id"], "pair")
        self.assertEqual(result["description_placeholders"]["address"], self.info.address)

    async def test_discovery_reuses_pairing_form(self):
        result = await self.flow.async_step_bluetooth(self.info)
        self.assertEqual(result["step_id"], "pair")

    async def test_duplicate_device_is_rejected(self):
        self.flow.configured = {self.info.address}
        with self.assertRaises(support.FlowAbort):
            await self.flow.async_step_bluetooth(self.info)

    async def test_no_devices_message(self):
        with patch.object(config_flow.bluetooth, "async_discovered_service_info", return_value=[]):
            result = await self.flow.async_step_user()
        self.assertEqual(result["reason"], "no_devices_found")

    async def test_unreachable_device_is_not_offered(self):
        with patch.object(config_flow, "connectable_device", return_value=None):
            result = await self.flow.async_step_user()
        self.assertEqual(result["reason"], "no_devices_found")

    async def test_invalid_name_and_pin_do_not_start_pairing(self):
        self.flow._address = self.info.address
        result = await self.flow.async_step_pair({"name": " ", "pin": "000123"})
        self.assertEqual(result["errors"]["name"], "invalid_name")
        result = await self.flow.async_step_pair({"name": "Utility room", "pin": "123abc"})
        self.assertEqual(result["errors"]["pin"], "invalid_pin")
        self.assertFalse(self.flow.hass.tasks)

    async def complete_validation(self, reader):
        self.flow._address = self.info.address
        with patch.object(config_flow, "ElgaClient", return_value=reader):
            result = await self.flow.async_step_pair({"name": "Utility room", "pin": "000123"})
            self.assertEqual(result["type"], "progress")
            await self.flow._connect_task
            result = await self.flow.async_step_connect()
            return result

    def reader(self, error=None):
        return SimpleNamespace(async_connect=AsyncMock(side_effect=error), async_read=AsyncMock(return_value=b"\x62\x0c"), async_close=AsyncMock())

    async def test_entry_created_only_after_successful_authenticated_read(self):
        reader = self.reader()
        result = await self.complete_validation(reader)
        self.assertEqual(result["next_step_id"], "finish")
        reader.async_read.assert_awaited_once_with(1, 0x5031, 0, timeout=5)
        reader.async_close.assert_awaited_once()
        result = await self.flow.async_step_finish()
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["title"], "Utility room")
        self.assertEqual(result["data"]["pin"], "000123")

    async def test_error_is_returned_to_form_without_saving_entry(self):
        for error, expected in ((PairingError(), "invalid_auth"), (ProtocolError(), "handshake_failed"), (UnsupportedAdapter(), "unsupported_adapter")):
            with self.subTest(error=type(error).__name__):
                result = await self.complete_validation(self.reader(error))
                self.assertEqual(result["next_step_id"], "pair")
                result = await self.flow.async_step_pair()
                self.assertEqual(result["errors"]["base"], expected)

    async def test_cancelling_progress_releases_client(self):
        reader = self.reader()
        started = asyncio.Event()
        async def wait_for_cancel():
            started.set()
            await asyncio.Future()
        reader.async_connect.side_effect = wait_for_cancel
        self.flow._address = self.info.address
        self.flow._settings = {"pin": "000123"}
        with patch.object(config_flow, "ElgaClient", return_value=reader):
            await self.flow.async_step_connect()
            await started.wait()
            self.flow._connect_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await self.flow._connect_task
        reader.async_close.assert_awaited_once()

    async def test_reauth_keeps_entity_identity(self):
        entry = support.FakeConfigEntry()
        self.flow.reauth_entry = entry
        result = await self.flow.async_step_reauth(entry.data)
        self.assertEqual(result["step_id"], "pair")
        await self.complete_validation(self.reader())
        result = await self.flow.async_step_finish()
        self.assertEqual(result["reason"], "reauth_successful")
        self.assertEqual(entry.entry_id, "test-entry")
        self.assertEqual(entry.data["pin"], "000123")

    async def test_options_allow_name_and_load_configuration(self):
        options = config_flow.ElgaOptionsFlow()
        options.config_entry = support.FakeConfigEntry()
        form = await options.async_step_init()
        validated = form["data_schema"]({"name": "New name", "scan_interval": 45, "slow_batch_size": 10})
        result = await options.async_step_init(validated)
        self.assertEqual(result["data"]["name"], "New name")
        self.assertEqual(result["data"]["scan_interval"], 45)
        with self.assertRaises(Exception):
            form["data_schema"]({"name": "Test", "scan_interval": 1, "slow_batch_size": 500})

    async def test_proxy_discovery_and_setup_without_home_assistant_pin(self):
        remote = device()
        remote.details = {"source": "esphome-proxy"}
        reader = self.reader()
        with patch.object(config_flow, "connectable_device", return_value=remote):
            result = await self.flow.async_step_user()
            self.assertEqual(result["type"], "form")
            result = await self.flow.async_step_bluetooth(self.info)
            self.assertEqual(result["step_id"], "pair")
            values = result["data_schema"]({"name": "Utility room"})
            with patch.object(config_flow, "ElgaClient", return_value=reader):
                result = await self.flow.async_step_pair(values)
                self.assertEqual(result["type"], "progress")
                await self.flow._connect_task
                await self.flow.async_step_connect()
        result = await self.flow.async_step_finish()
        self.assertEqual(result["data"]["pin"], "")

    async def test_local_adapter_cannot_omit_pin(self):
        self.flow._address = self.info.address
        result = await self.flow.async_step_pair({"name": "Utility room"})
        self.assertEqual(result["errors"]["pin"], "invalid_pin")
