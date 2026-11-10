"""Minimal HA contracts for offline tests, not a simulated Home Assistant server."""

import asyncio
import sys
from enum import StrEnum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "custom_components/elga_ace"


def module(name, **attributes):
    result = ModuleType(name)
    result.__dict__.update(attributes)
    sys.modules[name] = result
    parent, _, child = name.rpartition(".")
    if parent in sys.modules:
        setattr(sys.modules[parent], child, result)
    return result


class FlowAbort(Exception):
    pass


class FakeFlow:
    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _async_current_ids(self, include_ignore=False):
        return getattr(self, "configured", set())

    def _abort_if_unique_id_configured(self):
        if getattr(self, "unique_id", None) in self._async_current_ids():
            raise FlowAbort("already_configured")

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_abort(self, **kwargs):
        return {"type": "abort", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_show_progress(self, **kwargs):
        return {"type": "progress", **kwargs}

    def async_show_progress_done(self, **kwargs):
        return {"type": "progress_done", **kwargs}

    def async_update_and_abort(self, entry, title, data_updates):
        entry.data = {**entry.data, **data_updates}
        entry.title = title
        return self.async_abort(reason="reauth_successful")

    def _get_reauth_entry(self):
        return self.reauth_entry


class FakeConfigEntry:
    def __init__(self, title="Utility room", data=None, options=None):
        self.title = title
        self.data = data or {"address": "00:80:E1:26:5E:9C", "pin": "000123", "name": title}
        self.options = options or {}
        self.entry_id = "test-entry"
        self.unload_callbacks = []
        self.listeners = []

    def async_on_unload(self, callback):
        self.unload_callbacks.append(callback)

    def add_update_listener(self, callback):
        self.listeners.append(callback)
        return lambda: None


class FakeHass:
    def __init__(self):
        self.tasks = []
        self.config_entries = SimpleNamespace(
            async_reload=AsyncMock(),
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
            async_loaded_entries=lambda domain: [],
        )

    def async_create_task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    async def async_add_executor_job(self, function, *args):
        return function(*args)


class FakeCoordinator:
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, hass, logger, **kwargs):
        self.hass = hass
        self.logger = logger
        self.name = kwargs["name"]
        self.update_interval = kwargs.get("update_interval")
        self.data = {}
        self.last_update_success = True
        self._retry_after = None
        self.shutdown = False

    async def async_config_entry_first_refresh(self):
        self.data = await self._async_update_data()

    async def async_shutdown(self):
        self.shutdown = True


class FakeEntity:
    _attr_entity_category = None
    _attr_device_class = None
    _attr_state_class = None
    _attr_native_unit_of_measurement = None


class FakeCoordinatorEntity(FakeEntity):
    def __class_getitem__(cls, item):
        return cls

    def __init__(self, coordinator, context=None):
        self.coordinator = coordinator
        self.context = context

    @property
    def available(self):
        return self.coordinator.last_update_success


class Selector:
    def __init__(self, config=None):
        self.config = config or {}

    def __call__(self, value):
        if not isinstance(value, str):
            raise ValueError("Expected text")
        if "options" in self.config and value not in {option["value"] for option in self.config["options"]}:
            raise ValueError("Unknown selection")
        return value


def install():
    if "custom_components.elga_ace" in sys.modules:
        return
    module("custom_components").__path__ = [str(ROOT / "custom_components")]
    package = module("custom_components.elga_ace")
    package.__path__ = [str(PACKAGE)]
    module("homeassistant").__path__ = []
    module("homeassistant.components").__path__ = []
    module("homeassistant.helpers").__path__ = []
    module("homeassistant.core", HomeAssistant=FakeHass, callback=lambda function: function)
    platform = StrEnum("Platform", {"SENSOR": "sensor", "BINARY_SENSOR": "binary_sensor"})
    module("homeassistant.const", CONF_ADDRESS="address", CONF_NAME="name", CONF_SCAN_INTERVAL="scan_interval", Platform=platform)
    module("homeassistant.config_entries", ConfigEntry=FakeConfigEntry, ConfigFlow=FakeFlow, ConfigFlowResult=dict, OptionsFlow=FakeFlow)
    module("homeassistant.exceptions", ConfigEntryAuthFailed=type("ConfigEntryAuthFailed", (Exception,), {}))
    module("homeassistant.components.bluetooth",
           BluetoothServiceInfoBleak=SimpleNamespace,
           async_discovered_service_info=lambda *args, **kwargs: [],
           async_ble_device_from_address=lambda *args, **kwargs: None,
           async_scanner_devices_by_address=lambda *args, **kwargs: [],
           async_rediscover_address=lambda *args, **kwargs: None,
           async_request_active_scan=AsyncMock())
    classes = StrEnum("SensorDeviceClass", {
        "ENUM": "enum", "TEMPERATURE": "temperature", "PRESSURE": "pressure",
        "POWER": "power", "VOLTAGE": "voltage", "ENERGY": "energy",
        "FREQUENCY": "frequency", "CURRENT": "current",
    })
    states = StrEnum("SensorStateClass", {"TOTAL_INCREASING": "total_increasing", "MEASUREMENT": "measurement"})
    module("homeassistant.components.sensor", SensorDeviceClass=classes, SensorStateClass=states, SensorEntity=type("SensorEntity", (FakeEntity,), {}))
    module("homeassistant.components.binary_sensor", BinarySensorEntity=type("BinarySensorEntity", (FakeEntity,), {}))
    module("homeassistant.helpers.entity", EntityCategory=StrEnum("EntityCategory", {"DIAGNOSTIC": "diagnostic"}))
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    module("homeassistant.helpers.device_registry", DeviceInfo=dict, CONNECTION_BLUETOOTH="bluetooth")
    module("homeassistant.helpers.update_coordinator", DataUpdateCoordinator=FakeCoordinator, CoordinatorEntity=FakeCoordinatorEntity, UpdateFailed=type("UpdateFailed", (Exception,), {}))
    module("homeassistant.helpers.selector",
           SelectSelector=Selector, TextSelector=Selector,
           SelectSelectorConfig=lambda **kwargs: kwargs, TextSelectorConfig=lambda **kwargs: kwargs,
           SelectSelectorMode=SimpleNamespace(DROPDOWN="dropdown"), TextSelectorType=SimpleNamespace(PASSWORD="password"))
    module("homeassistant.components.diagnostics", async_redact_data=lambda data, keys: {key: "**REDACTED**" if key in keys else value for key, value in data.items()})


install()
