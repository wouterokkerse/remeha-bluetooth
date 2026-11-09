"""Numeric, enumerated and raw-structure sensors; no writable parameters."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import ElgaCoordinator
from .entity import ElgaEntity
from .registers import Register


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Create an enabled entity for every non-binary catalog register."""
    coordinator = entry.runtime_data
    async_add_entities(ElgaSensor(coordinator, register) for register in coordinator.registers if not register.binary)


class ElgaSensor(ElgaEntity, SensorEntity):
    """Represent values exactly according to app datatypes and scaling."""

    def __init__(self, coordinator: ElgaCoordinator, register: Register) -> None:
        super().__init__(coordinator, register)
        if register.choices:
            self._attr_device_class = SensorDeviceClass.ENUM
            self._attr_options = sorted({label[:255] for label in register.choices.values()})
        else:
            self._attr_native_unit_of_measurement = register.unit
            if register.device_class:
                self._attr_device_class = SensorDeviceClass(register.device_class)
            if register.unit and "counter" in register.sections and register.device_class == "energy":
                self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            elif register.unit and not register.diagnostic:
                self._attr_state_class = SensorStateClass.MEASUREMENT
            if register.unit:
                self._attr_suggested_display_precision = register.decimals

    @property
    def native_value(self) -> int | float | str | None:
        if (reading := self.reading) is None:
            return None
        if self.register.choices:
            label = self.register.choices.get(str(reading.value))
            return label[:255] if label is not None else None
        return reading.value
