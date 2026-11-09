"""Read-only Remeha Elga Ace Bluetooth integration."""

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant

from .coordinator import ElgaCoordinator
from .registers import load_registers

PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one named gateway and all dictionary-defined read-only entities."""
    registers = await hass.async_add_executor_job(load_registers)
    coordinator = ElgaCoordinator(hass, entry, registers)
    entry.runtime_data = coordinator
    try:
        await coordinator.async_config_entry_first_refresh()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await coordinator.async_shutdown()
        raise
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply naming and polling changes with a clean reconnection."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload entities and release the adapter connection."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_shutdown()
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Allow rediscovery after removal without deleting the host's bond."""
    bluetooth.async_rediscover_address(hass, entry.data[CONF_ADDRESS])
