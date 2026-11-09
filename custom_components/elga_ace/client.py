"""Read-only, serialized and reconnectable Elga Ace BLE client."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from .const import (
    CHAR_ERROR, CHAR_IDENTIFICATION_NOTIFY, CHAR_IDENTIFICATION_WRITE,
    CHAR_NOTIFY, CHAR_UNLOCK_NOTIFY, CHAR_UNLOCK_WRITE, CHAR_WRITE,
)
from .pairing import async_pair
from .protocol import (
    DeviceUnavailable, GatewayError, ProtocolError, Reassembler, RegisterReadError,
    ble_unlock_request, control_frame, identification_request, parse_challenge,
    parse_gateway_error, read_request, read_response_header, unlock_request,
    validate_unlock,
)


class ElgaClient:
    """Expose authentication and reads only; there is no register-write API."""

    def __init__(self, device_callback: Callable[[], BLEDevice | None], pin: str) -> None:
        self._device_callback = device_callback
        self._pin = pin
        self._client: BleakClientWithServiceCache | None = None
        self._events: asyncio.Queue[tuple[str, bytes] | Exception] = asyncio.Queue()
        self._reassembler = Reassembler()
        self._lock = asyncio.Lock()
        self._authorized = False
        self._notifications: list[str] = []

    @property
    def connected(self) -> bool:
        return self._client is not None and self._client.is_connected and self._authorized

    def _disconnected(self, client: Any) -> None:
        if client is self._client:
            self._authorized = False
            self._events.put_nowait(DeviceUnavailable("Gateway disconnected"))

    def _notification(self, uuid: str, data: bytes) -> None:
        try:
            if uuid == CHAR_NOTIFY:
                if (message := self._reassembler.feed(data)) is not None:
                    self._events.put_nowait((uuid, message))
            elif uuid == CHAR_ERROR:
                if (error := parse_gateway_error(data)) is not None:
                    self._events.put_nowait(error)
            else:
                self._events.put_nowait((uuid, data))
        except ProtocolError as error:
            self._events.put_nowait(error)

    def _drain(self) -> None:
        while not self._events.empty():
            self._events.get_nowait()

    async def _response(self, uuid: str, predicate: Callable[[bytes], bool], timeout: float) -> bytes:
        async with asyncio.timeout(timeout):
            while True:
                event = await self._events.get()
                if isinstance(event, Exception):
                    raise event
                source, data = event
                if source == uuid and predicate(data):
                    return data

    async def _write(self, uuid: str, data: bytes) -> None:
        if self._client is None or not self._client.is_connected:
            raise DeviceUnavailable("Gateway is not connected")
        async with asyncio.timeout(10):
            await self._client.write_gatt_char(uuid, data, response=True)

    async def async_connect(self) -> None:
        """Pair and perform BLE, CAN and appliance authorization in order."""
        async with self._lock:
            if self.connected:
                return
            await self._close()
            device = self._device_callback()
            if device is None:
                raise DeviceUnavailable("Selected gateway is not in Bluetooth range")
            await async_pair(device, self._pin)
            try:
                async with asyncio.timeout(60):
                    self._client = await establish_connection(
                        BleakClientWithServiceCache, device, device.name or device.address,
                        disconnected_callback=self._disconnected, max_attempts=2,
                        timeout=20, pair=False,
                    )
                    required = (
                        CHAR_WRITE, CHAR_NOTIFY, CHAR_ERROR,
                        CHAR_IDENTIFICATION_WRITE, CHAR_IDENTIFICATION_NOTIFY,
                        CHAR_UNLOCK_WRITE, CHAR_UNLOCK_NOTIFY,
                    )
                    if any(self._client.services.get_characteristic(uuid) is None for uuid in required):
                        raise ProtocolError("The selected device has no supported GTW Bluetooth services")
                    for uuid in (CHAR_NOTIFY, CHAR_ERROR, CHAR_IDENTIFICATION_NOTIFY, CHAR_UNLOCK_NOTIFY):
                        await self._client.start_notify(
                            uuid, lambda _, data, channel=uuid: self._notification(channel, bytes(data))
                        )
                        self._notifications.append(uuid)
                    await self._authenticate()
                    self._authorized = True
            except BaseException:
                await self._close()
                raise

    async def _authenticate(self) -> None:
        self._drain()
        await self._write(CHAR_IDENTIFICATION_WRITE, b"")
        challenge = await self._response(CHAR_IDENTIFICATION_NOTIFY, lambda data: True, 5)
        await self._write(CHAR_UNLOCK_WRITE, ble_unlock_request(challenge))
        response = await self._response(CHAR_UNLOCK_NOTIFY, lambda data: True, 5)
        if response != b"\x01":
            raise ProtocolError("BLE unlock was rejected")
        for protocol_id, level in ((1, 4), (0, 5)):
            self._drain()
            await self._write(CHAR_WRITE, control_frame(identification_request(protocol_id)))
            prefix = bytes([1, 2, protocol_id, 0, 2, 1])
            response = await self._response(CHAR_NOTIFY, lambda data: data.startswith(prefix), 5)
            challenge = parse_challenge(response[1:], protocol_id)
            await self._write(CHAR_WRITE, control_frame(unlock_request(challenge, protocol_id, level)))
            prefix = bytes([1, 2, protocol_id, 0, 2, 3])
            response = await self._response(CHAR_NOTIFY, lambda data: data.startswith(prefix), 5)
            validate_unlock(response[1:], protocol_id, level)

    async def async_read(self, node: int, index: int, subindex: int, timeout: float = 2.0) -> bytes:
        """Read and correlate by node, index and subindex, skipping late replies."""
        async with self._lock:
            if not self.connected:
                raise DeviceUnavailable("Gateway is not authorized")
            self._drain()
            header = read_response_header(node, index, subindex)
            try:
                async with asyncio.timeout(timeout):
                    await self._write(CHAR_WRITE, read_request(node, index, subindex))
                    response = await self._response(CHAR_NOTIFY, lambda data: data.startswith(header), timeout)
            except GatewayError as error:
                if error.is_register_error:
                    raise RegisterReadError(str(error)) from error
                self._authorized = False
                raise
            except TimeoutError as error:
                if self._client is None or not self._client.is_connected:
                    raise DeviceUnavailable("Gateway disconnected during a read") from error
                raise RegisterReadError("Register did not respond") from error
            if len(response) <= len(header):
                raise RegisterReadError("Register returned no value")
            return response[len(header):]

    async def _close(self) -> None:
        client, self._client = self._client, None
        self._authorized = False
        if client is not None:
            for uuid in reversed(self._notifications):
                if client.is_connected:
                    with suppress(BleakError, OSError, TimeoutError):
                        async with asyncio.timeout(2):
                            await client.stop_notify(uuid)
            with suppress(BleakError, OSError, TimeoutError):
                async with asyncio.timeout(10):
                    await client.disconnect()
        self._notifications.clear()
        self._reassembler.reset()
        self._drain()

    async def async_close(self) -> None:
        """Release the connection and notifications on unload or cancellation."""
        async with self._lock:
            await self._close()
