#!/usr/bin/env python3
"""Lees live-waarden uit een Remeha Elga Ace via de GTW-Bluetooth-module.

Afhankelijkheid: pip install bleak

Gebruik:
    python elga_read.py
    python elga_read.py --address AA:BB:CC:DD:EE:FF
    python elga_read.py --scan
    python elga_read.py --watch
    python elga_read.py --debug

De reader ontgrendelt eerst de BLE-services via de aparte GATT-kenmerken.
Daarna autoriseert hij het CAN-kanaal en het toestel, zoals in de app-opname.
BLE gebruikt little-endian woorden; CAN en het toestel gebruiken big-endian.
De eerste BLE-koppeling kan om de toestelcode vragen.
"""
import argparse
import asyncio
import struct
import sys
from contextlib import suppress

from bleak import BleakClient, BleakScanner

# --- GATT ---
CHAR_WRITE  = "496b1b03-cebc-4d59-9c32-14ea88c266f9"  # handle 0x0E, schrijf hierheen
CHAR_NOTIFY = "ab9af948-fb86-492d-820d-bdea2dcb7ecf"  # handle 0x10, antwoord hier
CHAR_ERROR  = "cff32957-5279-4075-bf3d-8e583d418c0d"
CHAR_IDENTIFICATION_WRITE = "94e54dc4-f69a-4c0c-8a6f-cc8f3135c665"
CHAR_IDENTIFICATION_NOTIFY = "69092300-a182-4fea-803b-24ada406da8a"
CHAR_UNLOCK_WRITE = "f1ca5aeb-02cd-44a8-972b-44b6e7250d0b"
CHAR_UNLOCK_NOTIFY = "e2c47825-6dff-4d79-b74a-5bdc4f4661cf"
NAME_HINTS  = ("GTW", "EHC-")

# --- Protocol ---
PREAMBLE = bytes.fromhex("0102000000")   # 5 vaste bytes voor elk verzoek
CONTROL_PAYLOAD_ID = 0x01
IDENTIFICATION_REQUEST = bytes.fromhex("020100020003")
AUTHORIZATION_LEVEL = 0x04
UNLOCK_SUCCESS = 0x01
UINT32_MASK = 0xFFFFFFFF
GATEWAY_ERRORS = {
    bytes.fromhex("00000302"): "CRC van het verzonden BLE-frame is ongeldig",
    bytes.fromhex("03000302"): "Onverwachte BLE-fragmentvolgorde",
    bytes.fromhex("00010302"): "BLE-ontvangstbuffer is vol",
    bytes.fromhex("01010302"): "Time-out bij ontvangst van BLE-fragmenten",
    bytes.fromhex("00020302"): "Ongeldig BLE payload-ID",
    bytes.fromhex("01000103"): "Geen antwoord van het toestel op de registeraanvraag",
    bytes.fromhex("02000103"): "Het toestel gaf een RUB-protocolfout",
    bytes.fromhex("00000203"): "Onverwacht RUB-fragmentnummer",
    bytes.fromhex("01000203"): "Time-out bij ontvangst van RUB-fragmenten",
    bytes.fromhex("01000303"): "Time-out bij bevestiging van een RUB-fragment",
    bytes.fromhex("02000303"): "Onverwachte RUB-fragmentbevestiging",
    bytes.fromhex("03000004"): "BLE-services vergrendeld",
}

def crc16_modbus(d: bytes) -> int:
    c = 0xFFFF
    for b in d:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return c

def frame(message: bytes) -> bytes:
    """Zet CRC (big-endian) achter het bericht en prefix het 0xFF-fragmentbyte."""
    msg = message + struct.pack(">H", crc16_modbus(message))
    return b"\xff" + msg            # enkel fragment -> 0xFF = laatste


def control_frame(message: bytes) -> bytes:
    """Voeg het control-payload-ID toe voordat de CRC wordt berekend."""
    return frame(bytes([CONTROL_PAYLOAD_ID]) + message)


def calculate_authorization_key(
    challenge: bytes, authorization_level: int = 4, byteorder: str = "big"
) -> bytes:
    """Bereken het 8-byte GTW-antwoord op serverSerial + timeChallenge."""
    if len(challenge) != 8:
        raise ValueError("De unlock-challenge moet exact 8 bytes zijn")

    key0 = 0x15EFA43F
    key1 = int.from_bytes(challenge[:4], byteorder)
    key2 = int.from_bytes(challenge[4:], byteorder)
    key3 = authorization_level & UINT32_MASK
    first = key3
    second = key3
    total = 0

    for _ in range(32):
        total = (total + 0x9E3779B9) & UINT32_MASK
        mix = (
            ((first << 4) + key0)
            ^ (first + total)
            ^ ((first >> 5) + key1)
        ) & UINT32_MASK
        second = (second + mix) & UINT32_MASK
        mix = (
            ((second << 4) + key2)
            ^ (second + total)
            ^ ((second >> 5) + key3)
        ) & UINT32_MASK
        first = (first + mix) & UINT32_MASK

    return second.to_bytes(4, byteorder) + first.to_bytes(4, byteorder)


def make_ble_unlock_request(challenge: bytes) -> bytes:
    """De GATT-unlock is niveau + twee little-endian TEA-woorden, zonder framing."""
    return bytes([AUTHORIZATION_LEVEL]) + calculate_authorization_key(
        challenge, AUTHORIZATION_LEVEL, byteorder="little"
    )


def parse_identification_response(message: bytes, protocol_id: int = 1) -> bytes:
    version = 3 if protocol_id == 1 else 0
    header = bytes([2, protocol_id, 0, 2, 1, version])
    if len(message) != 14 or message[:6] != header:
        raise ValueError(f"Onverwacht identificatie-antwoord: {message.hex()}")
    return message[6:14]


def make_unlock_request(challenge: bytes, protocol_id: int = 1, level: int = 4) -> bytes:
    version = 3 if protocol_id == 1 else 0
    prefix = bytes([2, protocol_id, 0, 2, 2, version, 0xFF, level])
    return prefix + calculate_authorization_key(challenge, level)


def parse_unlock_response(message: bytes, protocol_id: int = 1, level: int = 4) -> int:
    version = 3 if protocol_id == 1 else 0
    header = bytes([2, protocol_id, 0, 2, 3, version, 0xFF])
    if len(message) != 10 or message[:7] != header:
        raise ValueError(f"Onverwacht unlock-antwoord: {message.hex()}")
    if message[7] != level or message[8] != 0xFF:
        raise ValueError(f"Onverwacht autorisatieniveau in unlock-antwoord: {message.hex()}")
    return message[9]

def read_single(node: int, index: int, sub: int = 0) -> bytes:
    # header: PREAMBLE + tt(0x00) + 0x00, dan node, index(BE), sub
    body = PREAMBLE + bytes([0x00, 0x00, node, index >> 8, index & 0xFF, sub])
    return frame(body)

def read_bulk(node: int, items) -> bytes:
    # tt = 0x08 ; body = count + per item: 00 node idxhi idxlo sub
    entries = b"".join(bytes([0x00, node, i >> 8, i & 0xFF, s]) for i, s in items)
    body = PREAMBLE + bytes([0x08, 0x00]) + bytes([len(items)]) + entries
    return frame(body)

# Registers die we willen tonen: (node, index, subindex, label, schaal)
REGS = [
    (1, 0x5031, 0, "Regeltemperatuur (AM040)",        100),
    (1, 0x4359, 0, "Retourtemperatuur warmtepomp",    100),
    (1, 0x501E, 0, "Buitentemperatuur (AM027)",       100),
    (1, 0x4350, 0, "Persgastemperatuur compressor",   100),
    (1, 0x50A9, 0, "Intern toestelsetpoint (AM101)",  100),
    (1, 0x434D, 0, "Warmtewisselaar binnenunit",      100),
    (1, 0x434E, 0, "Verdampertemperatuur buitenunit", 100),
    (1, 0x5404, 1, "Kamertemperatuur zone 1 (CM030)",   10),
    (1, 0x5013, 0, "Aanvoertemperatuur (AM016)",       100),
    (1, 0x5302, 0, "CV-setpoint (PM002)",              100),
]

class Reasm:
    """Plakt notify-fragmenten tot een compleet (CRC-gecheckt) bericht."""
    def __init__(self, name="notify", debug=False):
        self.buf = bytearray()
        self.q = asyncio.Queue()
        self.name = name
        self.debug = debug

    def feed(self, data: bytes):
        if not data:
            return
        if self.debug:
            print(f"< {self.name}: {data.hex()}", file=sys.stderr)
        self.buf += data[1:]
        if data[0] == 0xFF:           # laatste fragment
            msg = bytes(self.buf); self.buf = bytearray()
            if len(msg) < 3 or struct.pack(">H", crc16_modbus(msg[:-2])) != msg[-2:]:
                self.q.put_nowait(None)   # CRC-fout
                return
            self.q.put_nowait(msg[:-2])


def parse_gateway_error(data: bytes):
    """Het error-kanaal bevat een payload-ID en vier foutbytes, zonder CRC."""
    if len(data) < 5 or data[0] != 0x02:
        return f"Onbekend GTW-foutbericht: {data.hex()}"
    code = data[1:5]
    if code == b"\x00\x00\x00\x00":
        return None
    description = GATEWAY_ERRORS.get(code, "Onbekende GTW-fout")
    return f"{description} (code {code.hex()}; bericht {data.hex()})"


class ErrorNotifications:
    def __init__(self, debug=False):
        self.q = asyncio.Queue()
        self.debug = debug

    def feed(self, data: bytes):
        if self.debug:
            print(f"< error: {data.hex()}", file=sys.stderr)
        error = parse_gateway_error(data)
        if error is not None:
            self.q.put_nowait(error)


class RawNotifications:
    """De BLE-identificatie en BLE-unlock hebben geen fragmentbyte of CRC."""
    def __init__(self, name, debug=False):
        self.q = asyncio.Queue()
        self.name = name
        self.debug = debug

    def feed(self, data: bytes):
        if self.debug:
            print(f"< {self.name}: {data.hex()}", file=sys.stderr)
        self.q.put_nowait(data)

def parse_single_response(msg: bytes):
    """Haal de ruwe waarde uit een enkel-object-antwoord.

    Antwoordopbouw (CRC er al af):
        [5 preamble][1 tt][1 pad][1 node][2 index BE][1 sub][waarde...]
    De waarde is dus alles vanaf byte 11.
    """
    HEADER = 11
    if len(msg) <= HEADER:
        return b""
    return msg[HEADER:]

def to_val(raw: bytes, scale: int):
    if not raw:
        return None
    iv = int.from_bytes(raw, "little", signed=True)
    if iv == -32768:
        return None                 # 0x8000 = geen sensor
    return iv / scale

async def find_device(address, want_scan):
    print("Scannen naar BLE-apparaten...", file=sys.stderr)
    devs = await BleakScanner.discover(timeout=6.0)
    if want_scan:
        for d in devs:
            print(f"  {d.address}  {d.name or '(geen naam)'}")
        return None
    if address:
        for d in devs:
            if d.address.lower() == address.lower():
                return d
        return address
    for d in devs:
        if d.name and any(hint.lower() in d.name.lower() for hint in NAME_HINTS):
            print(f"Gevonden: {d.address}  {d.name}", file=sys.stderr)
            return d
    print("Geen GTW/EHC-module gevonden. Gebruik --scan om te kijken, "
          "of geef --address.", file=sys.stderr)
    return None


def drain(queue):
    with suppress(asyncio.QueueEmpty):
        while True:
            queue.get_nowait()


async def wait_for_message(reasm, error_notifications, timeout):
    response_task = asyncio.create_task(reasm.q.get())
    error_task = asyncio.create_task(error_notifications.q.get())
    tasks = (response_task, error_task)
    try:
        done, _ = await asyncio.wait(
            tasks, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        if not done:
            raise asyncio.TimeoutError
        if error_task in done:
            raise RuntimeError(error_task.result())
        message = response_task.result()
        if message is None:
            raise RuntimeError(f"CRC-fout in BLE-antwoord op {reasm.name}")
        if isinstance(message, Exception):
            raise message
        return message
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def wait_for_response(reasm, error_notifications, subtype, timeout, protocol_id=1):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    header = bytes([CONTROL_PAYLOAD_ID, 2, protocol_id, 0, 2, subtype])
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        message = await wait_for_message(reasm, error_notifications, remaining)
        if message.startswith(header):
            return message[1:]
        if reasm.debug:
            print(f"  overgeslagen protocolbericht: {message.hex()}", file=sys.stderr)


async def write_value(client, uuid, packet, debug=False):
    if debug:
        print(f"> write {uuid}: {packet.hex() or '(leeg)'}", file=sys.stderr)
    await client.write_gatt_char(uuid, packet, response=True)


async def write_message(client, message, debug=False):
    await write_value(client, CHAR_WRITE, control_frame(message), debug)


async def unlock_services(
    client, identification_notifications, unlock_notifications,
    error_notifications, timeout=5.0, debug=False,
):
    drain(identification_notifications.q)
    drain(unlock_notifications.q)
    drain(error_notifications.q)
    print("BLE-services ontgrendelen...", file=sys.stderr)

    await write_value(client, CHAR_IDENTIFICATION_WRITE, b"", debug)
    try:
        challenge = await wait_for_message(
            identification_notifications, error_notifications, timeout
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError("Geen BLE-challenge ontvangen op het identificatiekanaal") from exc
    if len(challenge) != 8:
        raise RuntimeError(f"BLE-challenge heeft niet 8 bytes: {challenge.hex()}")
    if debug:
        print(f"  BLE-challenge: {challenge.hex()}", file=sys.stderr)

    await write_value(client, CHAR_UNLOCK_WRITE, make_ble_unlock_request(challenge), debug)
    try:
        response = await wait_for_message(unlock_notifications, error_notifications, timeout)
    except asyncio.TimeoutError as exc:
        raise RuntimeError("Geen BLE-unlockbevestiging ontvangen") from exc
    if response != bytes([UNLOCK_SUCCESS]):
        raise RuntimeError(f"GTW wees BLE-unlock af: {response.hex()}")
    print("BLE-services ontgrendeld.", file=sys.stderr)


async def authorize_channel(
    client, response_reasm, error_notifications, *,
    protocol_id, level, name, timeout=5.0, debug=False,
):
    drain(response_reasm.q)
    drain(error_notifications.q)
    print(f"{name} autoriseren...", file=sys.stderr)

    version = 3 if protocol_id == 1 else 0
    request = bytes([2, protocol_id, 0, 2, 0, version])
    await write_message(client, request, debug)
    try:
        identification = await wait_for_response(
            response_reasm, error_notifications, 0x01, timeout, protocol_id
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"Geen {name}-challenge ontvangen op het datakanaal") from exc

    challenge = parse_identification_response(identification, protocol_id)
    if debug:
        print(f"  {name}-challenge: {challenge.hex()}", file=sys.stderr)

    await write_message(client, make_unlock_request(challenge, protocol_id, level), debug)
    try:
        unlock_response = await wait_for_response(
            response_reasm, error_notifications, 0x03, timeout, protocol_id
        )
    except asyncio.TimeoutError as exc:
        raise RuntimeError(f"Geen {name}-autorisatiebevestiging ontvangen op het datakanaal") from exc

    status = parse_unlock_response(unlock_response, protocol_id, level)
    if status != UNLOCK_SUCCESS:
        raise RuntimeError(f"GTW wees {name}-autorisatie af met status 0x{status:02x}")
    print(f"{name} geautoriseerd.", file=sys.stderr)


async def poll_once(client, reasm, error_notifications, debug=False):
    print(f"{'Waarde':34} {'ruw':>8}   gedecodeerd")
    print("-" * 60)
    for node, idx, sub, label, scale in REGS:
        packet = read_single(node, idx, sub)
        if debug:
            print(f"> read {node}:{idx:04x}:{sub}: {packet.hex()}", file=sys.stderr)
        await client.write_gatt_char(CHAR_WRITE, packet, response=True)
        try:
            msg = await wait_for_message(reasm, error_notifications, timeout=3.0)
        except asyncio.TimeoutError:
            print(f"{label:34} (geen antwoord)"); continue
        raw = parse_single_response(msg)
        if not raw:
            print(f"{label:34} (leeg antwoord: {msg.hex()})")
            continue
        val = to_val(raw, scale)
        shown = "n.v.t." if val is None else f"{val:.2f} C"
        print(f"{label:34} {raw.hex():>8}   {shown}")


def self_test():
    """Controleer de bytes uit Wireshark-frames 1152 t/m 1192 van de opname."""
    can_challenge = bytes.fromhex("2e4818e1a1d3a8df")
    appliance_challenge = bytes.fromhex("2e4818e1a1d3946f")
    checks = {
        "CRC-16/MODBUS": crc16_modbus(b"123456789") == 0x4B37,
        "BLE-unlock frame 1153": make_ble_unlock_request(
            bytes.fromhex("9c5e26e130290d4d")
        ).hex() == "048ddea52790dca8a4",
        "CAN-identificatie frame 1157": control_frame(IDENTIFICATION_REQUEST).hex()
        == "ff0102010002000323d6",
        "CAN-autorisatie frame 1161": control_frame(make_unlock_request(can_challenge)).hex()
        == "ff01020100020203ff04a72cbba94f6cf51f2da6",
        "Toestel-autorisatie frame 1189": control_frame(
            make_unlock_request(appliance_challenge, protocol_id=0, level=5)
        ).hex() == "ff01020000020200ff0597e5cbb1383b49b2ca0f",
    }
    receiver = Reasm("response")
    receiver.feed(bytes.fromhex("ff010201000201032e4818e1a1d3a8df72d6"))
    checks["CAN-challenge frame 1160"] = (
        parse_identification_response(receiver.q.get_nowait()[1:]) == can_challenge
    )
    receiver.feed(bytes.fromhex("ff01020100020303ff04ff015438"))
    checks["CAN-bevestiging frame 1164"] = (
        parse_unlock_response(receiver.q.get_nowait()[1:]) == UNLOCK_SUCCESS
    )
    receiver.feed(bytes.fromhex("ff010200000201002e4818e1a1d3946fb7d0"))
    checks["Toestel-challenge frame 1188"] = (
        parse_identification_response(receiver.q.get_nowait()[1:], protocol_id=0)
        == appliance_challenge
    )
    receiver.feed(bytes.fromhex("ff01020000020300ff05ff010420"))
    checks["Toestel-bevestiging frame 1192"] = (
        parse_unlock_response(receiver.q.get_nowait()[1:], protocol_id=0, level=5)
        == UNLOCK_SUCCESS
    )
    packet = bytes.fromhex("ff010201000201032e4818e1a1d3a8df72d6")
    receiver.feed(b"\x00" + packet[1:9])
    receiver.feed(b"\xff" + packet[9:])
    checks["Fragmentatie"] = receiver.q.get_nowait() == packet[1:-2]
    captured_error = bytes.fromhex("020300000402000323d65900aa6900f401001100")
    checks["Vergrendelde BLE-services"] = "BLE-services vergrendeld" in parse_gateway_error(captured_error)
    checks["Geen GTW-fout"] = parse_gateway_error(bytes.fromhex("0200000000")) is None
    failures = [name for name, passed in checks.items() if not passed]
    if failures:
        raise RuntimeError("Protocol-self-test mislukt: " + ", ".join(failures))
    print("Protocol-self-test geslaagd: BLE-, CAN- en toestelautorisatie komen overeen met de opname.")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--address")
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--skip-handshake", action="store_true")
    ap.add_argument("--handshake-timeout", type=float, default=5.0)
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test()
        return
    if args.handshake_timeout <= 0:
        ap.error("--handshake-timeout moet groter dan nul zijn")

    device = await find_device(args.address, args.scan)
    if args.scan or not device:
        return

    response_reasm = Reasm("response", args.debug)
    identification_notifications = RawNotifications("BLE-identification", args.debug)
    unlock_notifications = RawNotifications("BLE-unlock", args.debug)
    error_notifications = ErrorNotifications(args.debug)
    address = getattr(device, "address", device)
    print(f"Verbinden met {address} ...", file=sys.stderr)
    async with BleakClient(device, pair=True, timeout=60.0) as client:
        channels = [(CHAR_NOTIFY, response_reasm), (CHAR_ERROR, error_notifications)]
        if not args.skip_handshake:
            channels.extend([
                (CHAR_IDENTIFICATION_NOTIFY, identification_notifications),
                (CHAR_UNLOCK_NOTIFY, unlock_notifications),
            ])
        required = [CHAR_WRITE] + [uuid for uuid, _ in channels]
        if not args.skip_handshake:
            required.extend([CHAR_IDENTIFICATION_WRITE, CHAR_UNLOCK_WRITE])
        for uuid in required:
            if client.services.get_characteristic(uuid) is None:
                raise RuntimeError(f"Vereist BLE-kanaal ontbreekt: {uuid}")

        started_channels = []
        try:
            for uuid, receiver in channels:
                await client.start_notify(
                    uuid, lambda _h, data, target=receiver: target.feed(bytes(data))
                )
                started_channels.append(uuid)
                if args.debug:
                    print(f"  notify actief: {uuid}", file=sys.stderr)

            if not args.skip_handshake:
                await unlock_services(
                    client,
                    identification_notifications,
                    unlock_notifications,
                    error_notifications,
                    timeout=args.handshake_timeout,
                    debug=args.debug,
                )
                for protocol_id, level, name in ((1, 4, "CAN-bus"), (0, 5, "Toestel")):
                    await authorize_channel(
                        client,
                        response_reasm,
                        error_notifications,
                        protocol_id=protocol_id,
                        level=level,
                        name=name,
                        timeout=args.handshake_timeout,
                        debug=args.debug,
                    )
            drain(response_reasm.q)
            while True:
                await poll_once(client, response_reasm, error_notifications, args.debug)
                if not args.watch:
                    break
                print()
                await asyncio.sleep(2.0)
        finally:
            for uuid in reversed(started_channels):
                with suppress(Exception):
                    await client.stop_notify(uuid)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        if getattr(exc, "winerror", None) == -2147467259:
            print(
                "Fout: Windows ziet de module, maar kan nu geen BLE-verbinding "
                "openen. Zet Bluetooth/service-modus op het toestel actief en "
                "sluit de Remeha-app op andere apparaten.",
                file=sys.stderr,
            )
        else:
            print(f"Fout: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except Exception as exc:
        print(f"Fout: {exc}", file=sys.stderr)
        raise SystemExit(1)
