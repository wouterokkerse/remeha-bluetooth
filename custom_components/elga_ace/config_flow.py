"""Bluetooth device selection, named setup and real passkey pairing."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    import probatio as vol
except ImportError:
    import voluptuous as vol

from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS, CONF_NAME, CONF_SCAN_INTERVAL
from homeassistant.core import callback
from homeassistant.helpers import selector

from .client import ElgaClient
from .const import (
    CONF_PIN, CONF_SLOW_BATCH_SIZE, DEFAULT_NAME, DEFAULT_SCAN_INTERVAL,
    DEFAULT_SLOW_BATCH_SIZE, DOMAIN,
)
from .coordinator import connectable_device
from .pairing import is_proxy, validate_pin
from .protocol import DeviceUnavailable, PairingError, ProtocolError, RegisterReadError, UnsupportedAdapter

_LOGGER = logging.getLogger(__name__)


def supported_name(name: str) -> bool:
    """Only offer known GTW/EHC gateway advertisements."""
    name = name.upper()
    return name.startswith(("GTW", "EHC-"))


class ElgaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle manual selection and Bluetooth discovery with the same wizard."""

    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._devices: dict[str, str] = {}
        self._settings: dict[str, str] = {}
        self._connect_task: asyncio.Task[str | None] | None = None
        self._connect_error: str | None = None
        self._existing_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Select a nearby device from HA's shared scanner, not a private scan."""
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            if address in self._devices:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                self._address = address
                return await self.async_step_pair()
        if active_scan := getattr(bluetooth, "async_request_active_scan", None):
            await active_scan(self.hass, duration=5)
        configured = self._async_current_ids(include_ignore=False)
        self._devices.clear()
        for info in bluetooth.async_discovered_service_info(self.hass, connectable=True):
            address = info.address.upper()
            if not supported_name(info.name or "") or address in configured:
                continue
            if connectable_device(self.hass, address) is None:
                continue
            self._devices[address] = f"{info.name} ({address})"
        if not self._devices:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[{"value": address, "label": label} for address, label in sorted(self._devices.items())],
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )}),
        )

    async def async_step_bluetooth(self, discovery_info: bluetooth.BluetoothServiceInfoBleak) -> ConfigFlowResult:
        """Offer the same named pairing form for automatic discovery."""
        self._address = discovery_info.address.upper()
        await self.async_set_unique_id(self._address)
        self._abort_if_unique_id_configured()
        if not discovery_info.connectable or not supported_name(discovery_info.name or ""):
            return self.async_abort(reason="not_supported")
        if connectable_device(self.hass, self._address) is None:
            return self.async_abort(reason="no_devices_found")
        self.context["title_placeholders"] = {"name": discovery_info.name or DEFAULT_NAME}
        return await self.async_step_pair()

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for a name and, for local adapters only, a pairing code."""
        errors = {}
        if self._connect_error:
            errors["base"], self._connect_error = self._connect_error, None
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            pin = user_input.get(CONF_PIN, "").strip()
            if not name or len(name) > 80:
                errors[CONF_NAME] = "invalid_name"
            elif (pin or not is_proxy(connectable_device(self.hass, self._address))) and not validate_pin(pin):
                errors[CONF_PIN] = "invalid_pin"
            else:
                self._settings = {CONF_ADDRESS: self._address, CONF_NAME: name, CONF_PIN: pin}
                self.context["title_placeholders"] = {"name": name}
                return await self.async_step_connect()
        name = self._settings.get(CONF_NAME, self._existing_entry.title if self._existing_entry else DEFAULT_NAME)
        return self.async_show_form(
            step_id="pair", errors=errors,
            description_placeholders={"address": self._address or ""},
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=name): selector.TextSelector(),
                vol.Optional(CONF_PIN): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            }),
        )

    async def _async_validate(self) -> str | None:
        client = ElgaClient(lambda: connectable_device(self.hass, self._address), self._settings[CONF_PIN])
        try:
            if connectable_device(self.hass, self._address) is None:
                return "cannot_connect"
            await client.async_connect()
            raw = await client.async_read(1, 0x5031, 0, timeout=5)
            if len(raw) != 2:
                return "cannot_read"
        except UnsupportedAdapter:
            return "unsupported_adapter"
        except PairingError:
            return "invalid_auth"
        except RegisterReadError:
            return "cannot_read"
        except ProtocolError:
            return "handshake_failed"
        except (DeviceUnavailable, BleakError, OSError, TimeoutError):
            return "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected Elga Ace setup failure")
            return "unknown"
        finally:
            await client.async_close()
        return None

    async def async_step_connect(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show cancellable progress during pairing and the three handshakes."""
        if self._connect_task is None:
            self._connect_task = self.hass.async_create_task(self._async_validate())
        if not self._connect_task.done():
            return self.async_show_progress(step_id="connect", progress_action="connecting", progress_task=self._connect_task)
        self._connect_error = self._connect_task.result()
        self._connect_task = None
        return self.async_show_progress_done(next_step_id="pair" if self._connect_error else "finish")

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Only save configuration after an authenticated register read."""
        if self._existing_entry:
            return self.async_update_and_abort(self._existing_entry, title=self._settings[CONF_NAME], data_updates=self._settings)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=self._settings[CONF_NAME], data=self._settings)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Replace a rejected pairing code without creating duplicate entities."""
        self._existing_entry = self._get_reauth_entry()
        self._address = self._existing_entry.data[CONF_ADDRESS]
        return await self.async_step_pair()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> ElgaOptionsFlow:
        return ElgaOptionsFlow()


class ElgaOptionsFlow(OptionsFlow):
    """Adjust the appliance display name and polling load."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            name = user_input[CONF_NAME].strip()
            if not name or len(name) > 80:
                errors[CONF_NAME] = "invalid_name"
            else:
                return self.async_create_entry(title="", data={**user_input, CONF_NAME: name})
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init", errors=errors,
            data_schema=vol.Schema({
                vol.Required(CONF_NAME, default=options.get(CONF_NAME, self.config_entry.title)): selector.TextSelector(),
                vol.Required(CONF_SCAN_INTERVAL, default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): vol.All(vol.Coerce(int), vol.Range(min=20, max=300)),
                vol.Required(CONF_SLOW_BATCH_SIZE, default=options.get(CONF_SLOW_BATCH_SIZE, DEFAULT_SLOW_BATCH_SIZE)): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
            }),
        )
