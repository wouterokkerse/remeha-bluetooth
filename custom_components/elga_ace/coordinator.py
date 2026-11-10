"""Bounded, staggered polling for all app-defined registers."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from time import monotonic

from bleak.exc import BleakError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import ElgaClient
from .const import (
    CONF_PIN, CONF_SLOW_BATCH_SIZE, DEFAULT_SCAN_INTERVAL, DEFAULT_SLOW_BATCH_SIZE,
    FAST_REGISTERS, POLL_BUDGET, REGISTER_RETRY_DELAY, REGISTER_TIMEOUT,
    SLOW_REFRESH_INTERVAL,
)
from .protocol import DeviceUnavailable, ElgaError, PairingError, RegisterReadError
from .registers import Register
from .transport import async_pair_gateway, connectable_device

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Reading:
    """Value plus its actual observation time, not the latest poll time."""

    value: int | float | str | None
    raw: bytes
    observed_at: datetime


class ElgaCoordinator(DataUpdateCoordinator[dict[str, Reading]]):
    """Refresh core measurements frequently and rotate through the full catalog."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, registers: tuple[Register, ...]) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=entry.options.get(CONF_NAME, entry.title),
            update_interval=timedelta(seconds=interval),
        )
        self.address = entry.data[CONF_ADDRESS]
        self.registers = registers
        self.client = ElgaClient(lambda: connectable_device(hass, self.address), entry.data.get(CONF_PIN, ""))
        self._batch_size = entry.options.get(CONF_SLOW_BATCH_SIZE, DEFAULT_SLOW_BATCH_SIZE)
        by_address = {(register.index, register.subindex): register for register in registers}
        self._fast = tuple(by_address[key] for key in FAST_REGISTERS)
        fast_keys = {register.key for register in self._fast}
        self._slow = tuple(register for register in registers if register.key not in fast_keys)
        self._cursor = 0
        self._register_retry_after: dict[str, float] = {}
        self._read_at: dict[str, float] = {}
        self.read_errors: dict[str, str] = {}
        self.data = {}

    async def _read(self, register: Register, data: dict[str, Reading], timeout: float) -> bool:
        now = monotonic()
        if self._register_retry_after.get(register.key, 0) > now:
            return False
        try:
            raw = await self.client.async_read(register.node, register.index, register.subindex, timeout=timeout)
            value = register.decode(raw)
        except (RegisterReadError, ValueError) as error:
            data.pop(register.key, None)
            self.read_errors[register.key] = str(error)
            self._register_retry_after[register.key] = now + REGISTER_RETRY_DELAY
            _LOGGER.debug("Register %s not readable: %s", register.key, error)
            return False
        data[register.key] = Reading(value, raw, datetime.now(timezone.utc))
        self._read_at[register.key] = monotonic()
        self._register_retry_after.pop(register.key, None)
        self.read_errors.pop(register.key, None)
        await asyncio.sleep(0.05)
        return True

    async def _async_update_data(self) -> dict[str, Reading]:
        try:
            await self.client.async_connect()
            deadline = monotonic() + POLL_BUDGET
            data = dict(self.data)
            read_timeout = 5.0 if getattr(self.client, "is_remote", False) else REGISTER_TIMEOUT
            attempted = 0
            successful = 0
            for register in self._fast:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                attempted += 1
                successful += await self._read(register, data, min(read_timeout, remaining))
            visited = 0
            slow_reads = 0
            while self._slow and visited < len(self._slow) and slow_reads < self._batch_size:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                register = self._slow[self._cursor]
                self._cursor = (self._cursor + 1) % len(self._slow)
                visited += 1
                if self._read_at.get(register.key, float("-inf")) + SLOW_REFRESH_INTERVAL > monotonic():
                    continue
                if self._register_retry_after.get(register.key, 0) > monotonic():
                    continue
                attempted += 1
                slow_reads += 1
                successful += await self._read(register, data, min(read_timeout, remaining))
            if not self.client.connected:
                raise DeviceUnavailable("Gateway disconnected during polling")
            if attempted and not successful and not data:
                raise DeviceUnavailable("Gateway did not return any readable registers")
            return data
        except PairingError as error:
            await self.client.async_close()
            raise ConfigEntryAuthFailed("Bluetooth pairing failed; enter the pairing code again") from error
        except (ElgaError, BleakError, OSError, TimeoutError) as error:
            await self.client.async_close()
            raise UpdateFailed(str(error) or "Bluetooth connection failed") from error

    async def async_shutdown(self) -> None:
        """Close both the coordinator timer and the persistent BLE connection."""
        await super().async_shutdown()
        await self.client.async_close()
