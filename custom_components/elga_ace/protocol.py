"""GTW framing, challenge responses and correlated read requests."""

from __future__ import annotations

import struct

from .const import CHAR_IDENTIFICATION_NOTIFY, CHAR_UNLOCK_NOTIFY

PREAMBLE = bytes.fromhex("0102000000")
UINT32_MASK = 0xFFFFFFFF


class ElgaError(Exception):
    """Base error from the Elga gateway."""


class DeviceUnavailable(ElgaError):
    """The selected gateway cannot be reached."""


class PairingError(ElgaError):
    """Bluetooth rejected the supplied pairing code."""


class UnsupportedAdapter(ElgaError):
    """A local BlueZ adapter is required for passkey pairing."""


class ProtocolError(ElgaError):
    """A malformed response or invalid authorization was received."""


class RegisterReadError(ElgaError):
    """One register is absent or could not be read."""


class GatewayError(ProtocolError):
    """An error notification from the gateway."""

    def __init__(self, code: bytes) -> None:
        self.code = code
        super().__init__(f"GTW error {code.hex()}")

    @property
    def is_register_error(self) -> bool:
        return self.code in (bytes.fromhex("01000103"), bytes.fromhex("02000103"))


def crc16_modbus(data: bytes) -> int:
    """Calculate the CRC used inside GTW messages."""
    checksum = 0xFFFF
    for value in data:
        checksum ^= value
        for _ in range(8):
            checksum = (checksum >> 1) ^ 0xA001 if checksum & 1 else checksum >> 1
    return checksum


def frame(message: bytes) -> bytes:
    """Encode one terminal BLE fragment."""
    return b"\xff" + message + struct.pack(">H", crc16_modbus(message))


def control_frame(message: bytes) -> bytes:
    """Encode a control-channel request."""
    return frame(b"\x01" + message)


def authorization_key(challenge: bytes, level: int, byteorder: str = "big") -> bytes:
    """Calculate the TEA-derived response verified against the app capture."""
    if len(challenge) != 8:
        raise ProtocolError("An authorization challenge must contain eight bytes")
    key0 = 0x15EFA43F
    key1 = int.from_bytes(challenge[:4], byteorder)
    key2 = int.from_bytes(challenge[4:], byteorder)
    first = second = level
    total = 0
    for _ in range(32):
        total = (total + 0x9E3779B9) & UINT32_MASK
        second = (
            second
            + (((first << 4) + key0) ^ (first + total) ^ ((first >> 5) + key1))
        ) & UINT32_MASK
        first = (
            first
            + (((second << 4) + key2) ^ (second + total) ^ ((second >> 5) + level))
        ) & UINT32_MASK
    return second.to_bytes(4, byteorder) + first.to_bytes(4, byteorder)


def ble_unlock_request(challenge: bytes) -> bytes:
    """Build the unframed BLE unlock value."""
    return b"\x04" + authorization_key(challenge, 4, "little")


def identification_request(protocol_id: int) -> bytes:
    """Request CAN or appliance identification."""
    version = 3 if protocol_id == 1 else 0
    return bytes([2, protocol_id, 0, 2, 0, version])


def unlock_request(challenge: bytes, protocol_id: int, level: int) -> bytes:
    """Build CAN or appliance authorization."""
    version = 3 if protocol_id == 1 else 0
    return bytes([2, protocol_id, 0, 2, 2, version, 0xFF, level]) + authorization_key(
        challenge, level
    )


def parse_challenge(message: bytes, protocol_id: int) -> bytes:
    """Validate and extract a control-channel challenge."""
    version = 3 if protocol_id == 1 else 0
    if len(message) != 14 or message[:6] != bytes([2, protocol_id, 0, 2, 1, version]):
        raise ProtocolError("Invalid identification response")
    return message[6:14]


def validate_unlock(message: bytes, protocol_id: int, level: int) -> None:
    """Require the authorization level and the separate success flag."""
    version = 3 if protocol_id == 1 else 0
    expected = bytes([2, protocol_id, 0, 2, 3, version, 0xFF, level, 0xFF, 1])
    if message != expected:
        raise ProtocolError("Gateway authorization was rejected")


def read_request(node: int, index: int, subindex: int) -> bytes:
    """Build a single-object READ request, never a register WRITE."""
    return frame(PREAMBLE + b"\x00\x00" + bytes([node]) + struct.pack(">HB", index, subindex))


def read_response_header(node: int, index: int, subindex: int) -> bytes:
    """Return the complete correlation header for one register."""
    return PREAMBLE + b"\x01\x00" + bytes([node]) + struct.pack(">HB", index, subindex)


def parse_gateway_error(data: bytes) -> GatewayError | None:
    """Error notifications are raw payload-ID plus four bytes, without CRC."""
    if len(data) < 5 or data[0] != 2:
        raise ProtocolError("Malformed gateway error notification")
    return GatewayError(data[1:5]) if any(data[1:5]) else None


class Reassembler:
    """Reassemble and CRC-check the main channel, not the raw BLE channels."""

    def __init__(self) -> None:
        self._buffer = bytearray()
        self._next_fragment = 0

    def feed(self, data: bytes) -> bytes | None:
        """Return a complete payload when a terminal fragment arrives."""
        if not data:
            return None
        if data[0] != 0xFF:
            if data[0] != self._next_fragment:
                self.reset()
                raise ProtocolError("Unexpected BLE fragment sequence")
            self._next_fragment += 1
        self._buffer.extend(data[1:])
        if len(self._buffer) > 65536:
            self.reset()
            raise ProtocolError("BLE response exceeded the size limit")
        if data[0] != 0xFF:
            return None
        message = bytes(self._buffer)
        self.reset()
        if len(message) < 3 or int.from_bytes(message[-2:], "big") != crc16_modbus(message[:-2]):
            raise ProtocolError("CRC mismatch in BLE response")
        return message[:-2]

    def reset(self) -> None:
        """Discard incomplete fragments on disconnect."""
        self._buffer.clear()
        self._next_fragment = 0


RAW_CHANNELS = {CHAR_IDENTIFICATION_NOTIFY, CHAR_UNLOCK_NOTIFY}
