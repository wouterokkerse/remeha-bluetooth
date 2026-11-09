import importlib.util
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import support

def integration_module():
    name = "custom_components.elga_ace._lifecycle_test"
    spec = importlib.util.spec_from_file_location(name, support.PACKAGE / "__init__.py")
    spec.submodule_search_locations = None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.integration = integration_module()
        self.hass = support.FakeHass()
        self.entry = support.FakeConfigEntry()
        self.coordinator = SimpleNamespace(
            async_config_entry_first_refresh=AsyncMock(), async_shutdown=AsyncMock(),
        )

    async def test_setup_forwards_only_read_only_platforms(self):
        with patch.object(self.integration, "ElgaCoordinator", return_value=self.coordinator):
            self.assertTrue(await self.integration.async_setup_entry(self.hass, self.entry))
        self.coordinator.async_config_entry_first_refresh.assert_awaited_once()
        platforms = self.hass.config_entries.async_forward_entry_setups.await_args.args[1]
        self.assertEqual(set(platforms), {"sensor", "binary_sensor"})
        self.assertIs(self.entry.runtime_data, self.coordinator)

    async def test_setup_failure_does_not_leak_client(self):
        self.coordinator.async_config_entry_first_refresh.side_effect = RuntimeError("Setup failed")
        with patch.object(self.integration, "ElgaCoordinator", return_value=self.coordinator):
            with self.assertRaises(RuntimeError):
                await self.integration.async_setup_entry(self.hass, self.entry)
        self.coordinator.async_shutdown.assert_awaited_once()
        self.hass.config_entries.async_forward_entry_setups.assert_not_awaited()

    async def test_unload_shuts_down_client(self):
        self.entry.runtime_data = self.coordinator
        self.assertTrue(await self.integration.async_unload_entry(self.hass, self.entry))
        self.coordinator.async_shutdown.assert_awaited_once()

    async def test_unload_failure_keeps_working_connection(self):
        self.entry.runtime_data = self.coordinator
        self.hass.config_entries.async_unload_platforms.return_value = False
        self.assertFalse(await self.integration.async_unload_entry(self.hass, self.entry))
        self.coordinator.async_shutdown.assert_not_awaited()

    async def test_options_reload(self):
        await self.integration.async_update_options(self.hass, self.entry)
        self.hass.config_entries.async_reload.assert_awaited_once_with(self.entry.entry_id)

    async def test_remove_triggers_rediscovery_without_unpair(self):
        with patch.object(self.integration.bluetooth, "async_rediscover_address") as rediscover:
            await self.integration.async_remove_entry(self.hass, self.entry)
        rediscover.assert_called_once_with(self.hass, self.entry.data["address"])

    async def test_manifest_and_metadata_are_self_contained(self):
        manifest = json.loads((support.PACKAGE / "manifest.json").read_text())
        self.assertEqual(manifest["dependencies"], ["bluetooth"])
        self.assertEqual(manifest["iot_class"], "local_polling")
        source_files = list(support.PACKAGE.rglob("*.py"))
        forbidden = ("create_subprocess", "import subprocess", "write_register", "async_set_value")
        for path in source_files:
            for token in forbidden:
                self.assertNotIn(token, path.read_text(), str(path))
