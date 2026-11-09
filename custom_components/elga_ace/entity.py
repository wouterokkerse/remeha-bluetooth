"""Shared read-only entity metadata and availability."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ElgaCoordinator, Reading
from .registers import Register


class ElgaEntity(CoordinatorEntity[ElgaCoordinator]):
    """One register belonging to the named appliance, not a writable control."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ElgaCoordinator, register: Register) -> None:
        super().__init__(coordinator, context=register.key)
        self.register = register
        self._attr_unique_id = f"{coordinator.address.replace(':', '').lower()}_{register.key}"
        self._attr_name = register.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address.upper())},
            connections={(CONNECTION_BLUETOOTH, coordinator.address.upper())},
            name=coordinator.name,
            manufacturer="Remeha",
            model="Elga Ace / EHC-07",
        )
        if register.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def reading(self) -> Reading | None:
        return self.coordinator.data.get(self.register.key)

    @property
    def available(self) -> bool:
        return super().available and self.reading is not None and self.reading.value is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = self.register.attributes()
        if (reading := self.reading) is not None:
            attributes["raw_hex"] = reading.raw.hex()
            attributes["last_read"] = reading.observed_at.isoformat()
            if self.register.choices:
                attributes["raw_value"] = reading.value
        if error := self.coordinator.read_errors.get(self.register.key):
            attributes["read_error"] = error
        return attributes
