"""Home Assistant local/proxy routing and transport-specific passkey pairing."""

from __future__ import annotations

import asyncio
import inspect
from contextlib import suppress

from bleak.exc import BleakError
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .pairing import async_pair, bluez_path, proxy_source
from .protocol import DeviceUnavailable, PairingError, ProxyPairingRequired, UnsupportedAdapter

PAIRING_ACTION = "elga_ace_set_pairing_code"


def connectable_device(hass: HomeAssistant, address: str):
    """Let HA choose among connectable local adapters and active proxies."""
    return bluetooth.async_ble_device_from_address(hass, address, connectable=True)


def proxy_runtime(hass: HomeAssistant, source: str):
    """Match the actual proxy by its Bluetooth MAC, never by a guessed name."""
    for entry in hass.config_entries.async_loaded_entries("esphome"):
        address = entry.data.get("bluetooth_mac_address", "")
        if address.upper() == source.upper():
            runtime = entry.runtime_data
            if not getattr(runtime.client, "noise_psk", None):
                raise ProxyPairingRequired("Enable native API encryption on the ESPHome proxy before sending a passkey")
            return runtime
    raise ProxyPairingRequired("The selected ESPHome proxy is not loaded")


def pairing_action(runtime):
    """Only send credentials to the explicitly named companion action."""
    for action in runtime.services.values():
        if action.name == PAIRING_ACTION and {argument.name for argument in action.args} == {"address", "pin"}:
            return action
    raise ProxyPairingRequired("Install the Elga Ace pairing package on this ESPHome proxy")


async def _send_code(runtime, action, address: str, pin: str) -> None:
    """Use the existing ESPHome API connection, not an HA service-bus event."""
    try:
        async with asyncio.timeout(10):
            result = runtime.client.execute_service(action, {"address": address, "pin": pin})
            if inspect.isawaitable(result):
                await result
    except asyncio.CancelledError:
        raise
    except Exception as error:
        raise DeviceUnavailable("Could not prepare passkey pairing on the ESPHome proxy") from error


async def async_pair_gateway(hass: HomeAssistant, client, device, pin: str) -> None:
    """Pair over the actual connected route before any GTW authentication."""
    if bluez_path(device):
        await async_pair(device, pin)
        return
    if (source := proxy_source(device)) is None:
        raise UnsupportedAdapter("Unsupported Bluetooth transport")
    runtime = proxy_runtime(hass, source)
    if not runtime.available:
        raise DeviceUnavailable("The selected ESPHome proxy is offline")
    action = pairing_action(runtime)
    try:
        await _send_code(runtime, action, device.address, pin)
        try:
            async with asyncio.timeout(60):
                await client.pair()
        except NotImplementedError as error:
            raise UnsupportedAdapter("Update the ESPHome proxy firmware to support pairing") from error
        except TimeoutError as error:
            raise DeviceUnavailable("Pairing through the ESPHome proxy timed out") from error
        except BleakError as error:
            raise PairingError("Pairing through the ESPHome proxy was rejected") from error
    finally:
        with suppress(DeviceUnavailable):
            await _send_code(runtime, action, device.address, "")
