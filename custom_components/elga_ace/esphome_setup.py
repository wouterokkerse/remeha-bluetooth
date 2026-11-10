"""Write the Elga PIN and pairing package into an ESPHome proxy configuration.

Only small, line-based edits are made so comments, ``!secret`` and ``!include``
tags in the user's files stay untouched. Every changed file gets a one-time
``.elga-backup`` copy first. Flashing the proxy remains a manual step.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

PACKAGE_FILE = "elga-pairing.yaml"
PACKAGE_KEY = "elga_pairing"
SECRET_KEY = "elga_pin"
ADDRESS_KEY = "elga_address"
BACKUP_SUFFIX = ".elga-backup"
BUNDLED_PACKAGE = Path(__file__).parent / "esphome" / PACKAGE_FILE


class ProxyConfigError(Exception):
    """The proxy YAML cannot be updated safely and needs manual editing."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class ProxySetupResult:
    """What was written, so the flow can tell the user to flash the proxy."""

    proxy_file: str
    changed_files: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changed_files)


def _key_pattern(key: str) -> re.Pattern[str]:
    return re.compile(rf"^(?P<indent>[ \t]*){re.escape(key)}[ \t]*:(?P<rest>.*)$")


def _top_level(lines: list[str], key: str) -> int | None:
    pattern = re.compile(rf"^{re.escape(key)}[ \t]*:[ \t]*(#.*)?$")
    return next((index for index, line in enumerate(lines) if pattern.match(line)), None)


def _block_end(lines: list[str], start: int) -> int:
    """Index after the last indented (or blank/comment) line of a top-level block."""
    end = start + 1
    last = start + 1
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line[0].isspace() and not line.lstrip().startswith("#"):
            break
        if line.strip() and line[0].isspace():
            last = end + 1
        end += 1
    return last


def _child_indent(lines: list[str], start: int, end: int) -> str:
    for line in lines[start + 1:end]:
        if line.strip() and not line.lstrip().startswith("#"):
            return line[: len(line) - len(line.lstrip())]
    return "  "


def _write(path: Path, text: str, result: ProxySetupResult) -> None:
    if path.exists() and path.read_text(encoding="utf-8") == text:
        return
    if path.exists():
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if not backup.exists():
            shutil.copy2(path, backup)
    temporary = path.with_name(path.name + ".elga-tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)
    result.changed_files.append(path.name)


def update_secrets(text: str, pin: str) -> str:
    """Set ``elga_pin`` as a quoted string so leading zeroes survive."""
    line = f'{SECRET_KEY}: "{pin}"'
    lines = text.splitlines()
    pattern = re.compile(rf"^{SECRET_KEY}[ \t]*:")
    for index, existing in enumerate(lines):
        if pattern.match(existing):
            lines[index] = line
            return "\n".join(lines) + "\n"
    if lines and lines[-1].strip():
        lines.append("")
    lines.append(line)
    return "\n".join(lines) + "\n"


def update_proxy_yaml(text: str, address: str) -> str:
    """Add the gateway MAC substitution and the pairing package include."""
    lines = text.splitlines()
    if any("elga-pairing-package.yaml" in line for line in lines):
        raise ProxyConfigError("old_package")

    # substitutions: elga_address
    start = _top_level(lines, "substitutions")
    if start is None:
        lines = ["substitutions:", f'  {ADDRESS_KEY}: "{address}"', ""] + lines
    else:
        end = _block_end(lines, start)
        indent = _child_indent(lines, start, end)
        pattern = _key_pattern(ADDRESS_KEY)
        for index in range(start + 1, end):
            match = pattern.match(lines[index])
            if match and match["indent"] == indent:
                lines[index] = f'{indent}{ADDRESS_KEY}: "{address}"'
                break
        else:
            lines.insert(start + 1, f'{indent}{ADDRESS_KEY}: "{address}"')

    # packages: elga_pairing: !include elga-pairing.yaml
    if not any(PACKAGE_FILE in line and "!include" in line for line in lines):
        start = _top_level(lines, "packages")
        if start is None:
            if lines and lines[-1].strip():
                lines.append("")
            lines += ["packages:", f"  {PACKAGE_KEY}: !include {PACKAGE_FILE}"]
        else:
            end = _block_end(lines, start)
            indent = _child_indent(lines, start, end)
            children = [line.strip() for line in lines[start + 1:end] if line.strip() and not line.lstrip().startswith("#")]
            if children and children[0].startswith("- "):
                lines.insert(start + 1, f"{indent}- !include {PACKAGE_FILE}")
            else:
                lines.insert(start + 1, f"{indent}{PACKAGE_KEY}: !include {PACKAGE_FILE}")
    return "\n".join(lines) + "\n"


def find_proxy_file(esphome_dir: Path, proxy_name: str) -> Path:
    """Find the YAML whose ``esphome: name:`` matches the proxy's device name."""
    candidates = [esphome_dir / f"{proxy_name}.yaml", esphome_dir / f"{proxy_name.replace('-', '_')}.yaml"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    name = re.compile(rf"^[ \t]+name[ \t]*:[ \t]*[\"']?{re.escape(proxy_name)}[\"']?[ \t]*(#.*)?$", re.MULTILINE)
    substitution = re.compile(rf"^[ \t]+(?:name|device_name)[ \t]*:[ \t]*[\"']?{re.escape(proxy_name)}[\"']?[ \t]*(#.*)?$", re.MULTILINE)
    matches = []
    for path in sorted(esphome_dir.glob("*.yaml")):
        if path.name in {"secrets.yaml", PACKAGE_FILE}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if name.search(text) or substitution.search(text):
            matches.append(path)
    if len(matches) == 1:
        return matches[0]
    raise ProxyConfigError("proxy_yaml_not_found" if not matches else "proxy_yaml_ambiguous")


def configure_proxy(esphome_dir: Path, proxy_name: str, address: str, pin: str) -> ProxySetupResult:
    """Write secret, shared package and proxy include; return what changed."""
    if not esphome_dir.is_dir():
        raise ProxyConfigError("esphome_dir_not_found")
    proxy_file = find_proxy_file(esphome_dir, proxy_name)
    result = ProxySetupResult(proxy_file=proxy_file.name)

    proxy_text = update_proxy_yaml(proxy_file.read_text(encoding="utf-8"), address.upper())
    package = esphome_dir / PACKAGE_FILE
    if not package.exists():
        _write(package, BUNDLED_PACKAGE.read_text(encoding="utf-8"), result)
    secrets = esphome_dir / "secrets.yaml"
    current = secrets.read_text(encoding="utf-8") if secrets.exists() else ""
    _write(secrets, update_secrets(current, pin), result)
    _write(proxy_file, proxy_text, result)
    return result
