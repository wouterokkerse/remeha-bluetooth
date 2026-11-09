"""Diagnostics that never include the stored pairing code."""

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_PIN


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data(dict(entry.data), {CONF_PIN}),
        "options": dict(entry.options),
        "connected": coordinator.client.connected,
        "catalog_registers": len(coordinator.registers),
        "readable_registers": len(coordinator.data),
        "read_errors": dict(coordinator.read_errors),
    }
