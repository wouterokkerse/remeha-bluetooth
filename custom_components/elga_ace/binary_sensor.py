"""Read-only binary states for dictionary-defined 0/1 selections."""

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ElgaEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Create all boolean sensor entities, never switches."""
    coordinator = entry.runtime_data
    async_add_entities(ElgaBinarySensor(coordinator, register) for register in coordinator.registers if register.binary)


class ElgaBinarySensor(ElgaEntity, BinarySensorEntity):
    """Expose Yes/No and Off/On choices without a setting operation."""

    @property
    def is_on(self) -> bool | None:
        if (reading := self.reading) is None or reading.value not in (0, 1):
            return None
        return bool(reading.value)
