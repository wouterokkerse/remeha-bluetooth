import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import support

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from custom_components.elga_ace import binary_sensor, coordinator, diagnostics, sensor
from custom_components.elga_ace.const import FAST_REGISTERS
from custom_components.elga_ace.protocol import DeviceUnavailable, PairingError, RegisterReadError
from custom_components.elga_ace.registers import INTEGER_LENGTHS, load_registers
from test_client import device


class CoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.registers = load_registers()
        self.by_address = {(register.index, register.subindex): register for register in self.registers}
        self.entry = support.FakeConfigEntry()
        self.hass = support.FakeHass()
        self.coordinator = coordinator.ElgaCoordinator(self.hass, self.entry, self.registers)
        self.client = SimpleNamespace(
            async_connect=AsyncMock(), async_close=AsyncMock(),
            async_read=AsyncMock(side_effect=self.read), connected=True,
        )
        self.coordinator.client = self.client
        self.entry.runtime_data = self.coordinator
        self.time_patch = patch.object(coordinator, "monotonic", return_value=1000.0)
        self.sleep_patch = patch.object(coordinator.asyncio, "sleep", new=AsyncMock())
        self.time_patch.start()
        self.sleep_patch.start()
        self.addCleanup(self.time_patch.stop)
        self.addCleanup(self.sleep_patch.stop)

    async def read(self, node, index, subindex, timeout):
        register = self.by_address[index, subindex]
        if register.datatype in INTEGER_LENGTHS:
            return (215 if index == 0x5404 else 1).to_bytes(INTEGER_LENGTHS[register.datatype], "little")
        return b"test" if register.datatype == "string" else b"\x01\x02"

    async def test_fast_and_background_reads_bounded_and_rotate(self):
        data = await self.coordinator._async_update_data()
        self.coordinator.data = data
        self.assertEqual(self.client.async_read.await_count, 30)
        addresses = [(call.args[1], call.args[2]) for call in self.client.async_read.await_args_list[:10]]
        self.assertEqual(addresses, list(FAST_REGISTERS))
        initial_background = set(data) - {register.key for register in self.coordinator._fast}
        self.client.async_read.reset_mock()
        data = await self.coordinator._async_update_data()
        self.assertEqual(self.client.async_read.await_count, 30)
        self.assertGreater(len(data), 30)
        next_background = {f"1_{call.args[1]:04x}_{call.args[2]}" for call in self.client.async_read.await_args_list[10:]}
        self.assertFalse(initial_background & next_background)
        self.assertIsNone(self.coordinator._retry_after, "Do not overwrite HA's retry scheduling field")

    async def test_all_487_catalog_addresses_are_eventually_visited(self):
        for _ in range(25):
            self.coordinator.data = await self.coordinator._async_update_data()
        self.assertEqual(len(self.coordinator.data), 487)

    async def test_missing_register_is_unavailable_but_others_continue(self):
        async def one_missing(node, index, subindex, timeout):
            if index == 0x4350:
                raise RegisterReadError("Absent register")
            return await self.read(node, index, subindex, timeout)
        self.client.async_read.side_effect = one_missing
        data = await self.coordinator._async_update_data()
        self.assertNotIn("1_4350_0", data)
        self.assertIn("1_5404_1", data)
        self.assertIn("1_4350_0", self.coordinator.read_errors)
        self.coordinator.data = data
        self.client.async_read.reset_mock()
        await self.coordinator._async_update_data()
        self.assertNotIn(0x4350, [call.args[1] for call in self.client.async_read.await_args_list])

    async def test_bad_datatype_length_is_local_to_one_register(self):
        self.client.async_read.side_effect = None
        self.client.async_read.return_value = b"\x01"
        data = await self.coordinator._async_update_data()
        self.assertNotIn("1_5031_0", data)
        self.assertIn("1_5031_0", self.coordinator.read_errors)

    async def test_disconnect_closes_client_and_marks_update_failed(self):
        self.client.async_read.side_effect = DeviceUnavailable("Disconnected")
        with self.assertRaises(UpdateFailed):
            await self.coordinator._async_update_data()
        self.client.async_close.assert_awaited_once()

    async def test_bad_pin_requests_reauthentication(self):
        self.client.async_connect.side_effect = PairingError("Rejected")
        with self.assertRaises(ConfigEntryAuthFailed):
            await self.coordinator._async_update_data()
        self.client.async_close.assert_awaited_once()

    async def test_budget_stops_background_work(self):
        clock = [1000.0]
        async def timed_read(*args, **kwargs):
            clock[0] += 2
            return await self.read(*args, **kwargs)
        self.client.async_read.side_effect = timed_read
        with patch.object(coordinator, "monotonic", side_effect=lambda: clock[0]):
            await self.coordinator._async_update_data()
        self.assertLessEqual(self.client.async_read.await_count, 8)

    async def test_shutdown_releases_connection(self):
        await self.coordinator.async_shutdown()
        self.assertTrue(self.coordinator.shutdown)
        self.client.async_close.assert_awaited_once()

    async def test_every_register_becomes_exactly_one_read_only_entity(self):
        entities = []
        await sensor.async_setup_entry(self.hass, self.entry, lambda values: entities.extend(values))
        await binary_sensor.async_setup_entry(self.hass, self.entry, lambda values: entities.extend(values))
        self.assertEqual(len(entities), 487)
        self.assertEqual(len({entity._attr_unique_id for entity in entities}), 487)
        self.assertEqual(sum(isinstance(entity, binary_sensor.ElgaBinarySensor) for entity in entities), 48)
        for entity in entities:
            self.assertEqual(entity._attr_device_info["name"], self.entry.title)
            self.assertTrue(entity.extra_state_attributes["read_only"])
            self.assertFalse(entity.available)
            for command in ("async_set_value", "async_select_option", "async_turn_on", "async_turn_off", "async_write"):
                self.assertFalse(hasattr(entity, command), (entity.register.key, command))

    async def test_temperature_entity_state_units_and_availability(self):
        register = self.by_address[0x5404, 1]
        entity = sensor.ElgaSensor(self.coordinator, register)
        self.coordinator.data = {register.key: coordinator.Reading(21.5, bytes.fromhex("d700"), datetime.now(timezone.utc))}
        self.assertTrue(entity.available)
        self.assertEqual(entity.native_value, 21.5)
        self.assertEqual(entity._attr_device_class, "temperature")
        self.assertEqual(entity._attr_native_unit_of_measurement, "°C")
        self.assertEqual(entity.extra_state_attributes["raw_hex"], "d700")
        self.assertIn("last_read", entity.extra_state_attributes)
        self.coordinator.last_update_success = False
        self.assertFalse(entity.available)

    async def test_enum_and_binary_states_keep_app_meaning(self):
        enum_register = next(register for register in self.registers if register.choices and not register.binary)
        value = int(next(iter(enum_register.choices)))
        self.coordinator.data[enum_register.key] = coordinator.Reading(value, b"\x00", datetime.now(timezone.utc))
        entity = sensor.ElgaSensor(self.coordinator, enum_register)
        self.assertEqual(entity.native_value, enum_register.choices[str(value)][:255])
        self.assertIn(entity.native_value, entity._attr_options)
        binary = next(register for register in self.registers if register.binary)
        self.coordinator.data[binary.key] = coordinator.Reading(1, b"\x01", datetime.now(timezone.utc))
        binary_entity = binary_sensor.ElgaBinarySensor(self.coordinator, binary)
        self.assertIs(binary_entity.is_on, True)

    async def test_diagnostics_redact_only_secret(self):
        diagnostic = await diagnostics.async_get_config_entry_diagnostics(self.hass, self.entry)
        self.assertEqual(diagnostic["entry"]["pin"], "**REDACTED**")
        self.assertNotIn("000123", str(diagnostic))
        self.assertEqual(diagnostic["catalog_registers"], 487)

    async def test_local_adapter_preferred_over_remote_proxy(self):
        local = device()
        remote = device()
        remote.details = {"source": "proxy"}
        candidates = [SimpleNamespace(ble_device=remote), SimpleNamespace(ble_device=local)]
        with patch.object(coordinator.bluetooth, "async_scanner_devices_by_address", return_value=candidates):
            self.assertIs(coordinator.local_device(self.hass, local.address), local)
