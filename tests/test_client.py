import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import support

from bleak.backends.device import BLEDevice
from custom_components.elga_ace import client, const, pairing, protocol

CAPTURE = (
    (const.CHAR_IDENTIFICATION_WRITE, "", const.CHAR_IDENTIFICATION_NOTIFY, "9c5e26e130290d4d"),
    (const.CHAR_UNLOCK_WRITE, "048ddea52790dca8a4", const.CHAR_UNLOCK_NOTIFY, "01"),
    (const.CHAR_WRITE, "ff0102010002000323d6", const.CHAR_NOTIFY, "ff010201000201032e4818e1a1d3a8df72d6"),
    (const.CHAR_WRITE, "ff01020100020203ff04a72cbba94f6cf51f2da6", const.CHAR_NOTIFY, "ff01020100020303ff04ff015438"),
    (const.CHAR_WRITE, "ff01020000020000e2ab", const.CHAR_NOTIFY, "ff010200000201002e4818e1a1d3946fb7d0"),
    (const.CHAR_WRITE, "ff01020000020200ff0597e5cbb1383b49b2ca0f", const.CHAR_NOTIFY, "ff01020000020300ff05ff010420"),
)


def device():
    return BLEDevice("00:80:E1:26:5E:9C", "GTW-test", {"path": "/org/bluez/hci0/dev_00_80_E1_26_5E_9C"})


class FakeGatt:
    def __init__(self):
        self.is_connected = True
        self.services = SimpleNamespace(get_characteristic=lambda uuid: object())
        self.callbacks = {}
        self.writes = []
        self.closed = False
        self.error = None
        self.wrong_response = False
        self.bad_crc = False
        self.empty_response = False
        self.hang = False
        self.on_disconnect = None

    async def start_notify(self, uuid, callback):
        self.callbacks[uuid] = callback

    async def stop_notify(self, uuid):
        self.callbacks.pop(uuid, None)

    async def disconnect(self):
        self.closed = True
        self.is_connected = False
        if self.on_disconnect:
            self.on_disconnect(self)

    async def write_gatt_char(self, uuid, packet, response):
        assert response is True
        stage = len(self.writes)
        self.writes.append((uuid, packet))
        if self.hang:
            return
        if stage < len(CAPTURE):
            expected_uuid, expected_bytes, notify_uuid, reply = CAPTURE[stage]
            assert uuid == expected_uuid, stage
            assert packet.hex() == expected_bytes, (stage, packet.hex())
            self.callbacks[notify_uuid](0, bytes.fromhex(reply))
            return
        assert uuid == const.CHAR_WRITE
        body = packet[1:-2]
        assert body[:7] == bytes.fromhex("01020000000000"), "Not a READ request"
        if self.error:
            self.callbacks[const.CHAR_ERROR](0, self.error)
            return
        node = body[7]
        index = int.from_bytes(body[8:10], "big")
        subindex = body[10]
        if self.wrong_response:
            late = protocol.read_response_header(node, index ^ 1, subindex) + b"\xff\x7f"
            self.callbacks[const.CHAR_NOTIFY](0, protocol.frame(late))
        raw = b"" if self.empty_response else bytes.fromhex("d700" if index == 0x5404 else "620c")
        reply = protocol.frame(protocol.read_response_header(node, index, subindex) + raw)
        if self.bad_crc:
            reply = reply[:-1] + bytes([reply[-1] ^ 1])
        await asyncio.sleep(0)
        self.callbacks[const.CHAR_NOTIFY](0, reply)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.gatt = FakeGatt()
        self.reader = client.ElgaClient(device, "000123")
        self.pair = patch.object(client, "async_pair", new=AsyncMock())
        self.connect = patch.object(client, "establish_connection", new=AsyncMock(side_effect=self.establish))
        self.pair_mock = self.pair.start()
        self.connect_mock = self.connect.start()
        self.addCleanup(self.pair.stop)
        self.addCleanup(self.connect.stop)
        self.addAsyncCleanup(self.reader.async_close)

    async def establish(self, *args, **kwargs):
        self.gatt.on_disconnect = kwargs["disconnected_callback"]
        return self.gatt

    async def test_capture_replayed_byte_for_byte(self):
        await self.reader.async_connect()
        self.assertTrue(self.reader.connected)
        self.assertEqual([(uuid, packet.hex()) for uuid, packet in self.gatt.writes], [(row[0], row[1]) for row in CAPTURE])
        self.pair_mock.assert_awaited_once()
        self.assertEqual(self.pair_mock.await_args.args[1], "000123")

    async def test_reuses_authorized_connection(self):
        await self.reader.async_connect()
        await self.reader.async_connect()
        self.assertEqual(self.connect_mock.await_count, 1)
        self.assertEqual(len(self.gatt.writes), 6)

    async def test_wrong_index_response_is_skipped(self):
        await self.reader.async_connect()
        self.gatt.wrong_response = True
        self.assertEqual(await self.reader.async_read(1, 0x5404, 1), bytes.fromhex("d700"))
        body = self.gatt.writes[-1][1][1:-2]
        self.assertEqual(body[7:].hex(), "01540401")

    async def test_concurrent_reads_are_serialized(self):
        await self.reader.async_connect()
        values = await asyncio.gather(self.reader.async_read(1, 0x5404, 1), self.reader.async_read(1, 0x5013, 0))
        self.assertEqual(values, [bytes.fromhex("d700"), bytes.fromhex("620c")])

    async def test_register_error_is_nonfatal(self):
        await self.reader.async_connect()
        self.gatt.error = bytes.fromhex("0201000103")
        with self.assertRaises(protocol.RegisterReadError):
            await self.reader.async_read(1, 0x5404, 1)
        self.assertTrue(self.reader.connected)
        self.gatt.error = None
        self.assertEqual(await self.reader.async_read(1, 0x5013, 0), bytes.fromhex("620c"))

    async def test_locked_services_invalidate_authorization(self):
        await self.reader.async_connect()
        self.gatt.error = bytes.fromhex("0203000004")
        with self.assertRaises(protocol.GatewayError):
            await self.reader.async_read(1, 0x5404, 1)
        self.assertFalse(self.reader.connected)

    async def test_empty_and_bad_crc_are_rejected(self):
        await self.reader.async_connect()
        self.gatt.empty_response = True
        with self.assertRaises(protocol.RegisterReadError):
            await self.reader.async_read(1, 0x5404, 1)
        self.gatt.empty_response = False
        self.gatt.bad_crc = True
        with self.assertRaises(protocol.ProtocolError):
            await self.reader.async_read(1, 0x5404, 1)

    async def test_timeout_does_not_poison_next_register(self):
        await self.reader.async_connect()
        self.gatt.hang = True
        with self.assertRaises(protocol.RegisterReadError):
            await self.reader.async_read(1, 0x5404, 1, timeout=0.001)
        self.gatt.hang = False
        self.assertEqual(await self.reader.async_read(1, 0x5013, 0), bytes.fromhex("620c"))

    async def test_reconnect_repeats_all_handshakes(self):
        await self.reader.async_connect()
        old_gatt = self.gatt
        await old_gatt.disconnect()
        self.assertFalse(self.reader.connected)
        self.gatt = FakeGatt()
        await self.reader.async_connect()
        self.assertTrue(self.reader.connected)
        self.assertEqual(len(self.gatt.writes), 6)
        self.assertEqual(self.pair_mock.await_count, 2)

    async def test_missing_gatt_service_cleans_up(self):
        self.gatt.services.get_characteristic = lambda uuid: None
        with self.assertRaises(protocol.ProtocolError):
            await self.reader.async_connect()
        self.assertTrue(self.gatt.closed)

    async def test_connect_cancellation_releases_notifications(self):
        self.gatt.hang = True
        task = asyncio.create_task(self.reader.async_connect())
        while not self.gatt.writes:
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.gatt.closed)
        self.assertFalse(self.gatt.callbacks)

    async def test_close_removes_all_notify_callbacks(self):
        await self.reader.async_connect()
        await self.reader.async_close()
        self.assertTrue(self.gatt.closed)
        self.assertFalse(self.gatt.callbacks)
        self.assertFalse(self.reader.connected)

    async def test_no_device_has_no_pairing_attempt(self):
        reader = client.ElgaClient(lambda: None, "000123")
        with self.assertRaises(protocol.DeviceUnavailable):
            await reader.async_connect()
        self.pair_mock.assert_not_awaited()

    async def test_proxy_pairing_and_capture_handshake_need_no_local_pin(self):
        remote = device()
        remote.details = {"source": "esphome-proxy"}
        self.gatt._connected_device = remote
        self.gatt.pair = AsyncMock()
        self.pair_mock.side_effect = pairing.async_pair
        reader = client.ElgaClient(device, "")
        self.addAsyncCleanup(reader.async_close)
        await reader.async_connect()
        self.assertTrue(reader.connected)
        self.gatt.pair.assert_awaited_once_with()
        self.assertIs(self.pair_mock.await_args.args[0], remote)
        self.assertEqual([packet.hex() for _, packet in self.gatt.writes], [row[1] for row in CAPTURE])

    async def test_pairing_failure_closes_connection(self):
        self.pair_mock.side_effect = protocol.PairingError("Rejected")
        with self.assertRaises(protocol.PairingError):
            await self.reader.async_connect()
        self.assertTrue(self.gatt.closed)
        self.assertFalse(self.gatt.callbacks)
