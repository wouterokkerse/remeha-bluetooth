"""Typed app metadata and conservative register-value decoding."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

UNITS = {
    "31001": ("°C", "temperature"),
    "31008": ("bar", "pressure"),
    "31009": ("rpm", None),
    "31014": ("%", None),
    "31019": ("kW", "power"),
    "31020": ("V", "voltage"),
    "31021": ("Wh", "energy"),
    "31024": ("kWh", "energy"),
    "31028": ("Hz", "frequency"),
    "31033": ("A", "current"),
}
INTEGER_LENGTHS = {"uint8": 1, "int8": 1, "uint16": 2, "int16": 2, "uint32": 4}


@dataclass(frozen=True, slots=True)
class Register:
    """One uniquely addressed read-only datapoint from the app dictionary."""

    node: int
    index: int
    subindex: int
    name: str
    symbol: str
    description: str
    code: str
    datatype: str
    factor: float
    decimals: int
    unit_id: str
    selection: str
    choices: dict[str, str]
    binary: bool
    sections: list[str]
    read_level: int
    length: int

    @property
    def key(self) -> str:
        return f"{self.node}_{self.index:04x}_{self.subindex}"

    @property
    def unit(self) -> str | None:
        if self.choices or self.datatype not in INTEGER_LENGTHS:
            return None
        return UNITS.get(self.unit_id, (None, None))[0]

    @property
    def device_class(self) -> str | None:
        if self.choices or self.datatype not in INTEGER_LENGTHS:
            return None
        return UNITS.get(self.unit_id, (None, None))[1]

    @property
    def diagnostic(self) -> bool:
        return "signal" not in self.sections and "counter" not in self.sections

    def decode(self, raw: bytes) -> int | float | str | None:
        """Use the declared datatype and scale; retain unknown structures as hex."""
        if not raw:
            return None
        if self.datatype in INTEGER_LENGTHS:
            if len(raw) != INTEGER_LENGTHS[self.datatype]:
                raise ValueError(f"Expected {INTEGER_LENGTHS[self.datatype]} bytes for {self.key}")
            value = int.from_bytes(raw, "little", signed=self.datatype.startswith("int"))
            if self.datatype == "int16" and self.unit_id == "31001" and value == -32768:
                return None
            if self.choices:
                return value
            result = round(value * self.factor, self.decimals)
            return int(result) if self.decimals == 0 else result
        if self.datatype == "string":
            return raw.rstrip(b"\x00\xff").decode("utf-8", errors="replace")[:255]
        encoded = raw.hex()
        return encoded if len(encoded) <= 255 else encoded[:252] + "..."

    def state_label(self, value: int | float | str | None) -> str | None:
        """Return the app's enumeration label while bounding HA state length."""
        if value is None:
            return None
        return self.choices.get(str(value), f"Unknown ({value})")[:255]

    def attributes(self) -> dict[str, Any]:
        """Expose original metadata without inventing an unverified unit."""
        return {
            "node": self.node,
            "register": f"0x{self.index:04X}",
            "subindex": self.subindex,
            "parameter_code": self.code or None,
            "app_symbol": self.symbol or None,
            "description": self.description,
            "datatype": self.datatype,
            "factor": self.factor,
            "unit_id": self.unit_id or None,
            "sections": self.sections,
            "read_only": True,
        }


@cache
def load_registers() -> tuple[Register, ...]:
    """Load in HA's executor, not on the event loop."""
    document = json.loads(Path(__file__).with_name("register_catalog.json").read_text(encoding="utf-8"))
    if document["schema_version"] != 1:
        raise ValueError("Unsupported register catalog format")
    registers = tuple(Register(**item) for item in document["registers"])
    if len({register.key for register in registers}) != len(registers):
        raise ValueError("Duplicate addresses in register catalog")
    return registers
