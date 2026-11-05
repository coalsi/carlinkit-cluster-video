import io
import json
import os
import struct
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import carlinkit_cluster as cc


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FakeAdapter:
    """Plays back (time, type, payload) messages; records what the host sends."""

    def __init__(self, clock, script):
        self.clock = clock
        self.script = list(script)
        self.sent = []

    def send(self, msg_type, payload=b""):
        self.sent.append((msg_type, payload))

    def recv(self):
        self.clock.now += 0.25
        if self.script and self.script[0][0] <= self.clock.now:
            _, msg_type, payload = self.script.pop(0)
            return msg_type, payload
        return None

    def commands(self):
        return [struct.unpack("<I", p)[0] for t, p in self.sent if t == cc.MSG_COMMAND]


def video(width=1280, height=480, data=b"\x00\x00\x00\x01\x65"):
    return struct.pack("<5I", width, height, 1, 0, 0) + data


class WireFormat(unittest.TestCase):
    def test_frame_header(self):
        self.assertEqual(cc.frame(cc.MSG_HEARTBEAT).hex(), "aa55aa5500000000aa00000055ffffff")

    def test_header_round_trip(self):
        self.assertEqual(cc.parse_header(cc.frame(0x2C, b"abc")[:16]), (3, 0x2C))

    def test_bad_magic_is_refused(self):
        with self.assertRaises(ValueError):
            cc.parse_header(b"\0" * 16)

    def test_size_file_is_24_bytes(self):
        content = cc.size_file_content(1280, 480, 30)
        self.assertEqual(len(content), 24)
        self.assertEqual(content.hex(), "00050000" "e0010000" "00000000" "00000000" "1e000000" "00000000")

    def test_send_file_layout(self):
        payload = cc.send_file_payload("/a", b"xy")
        self.assertEqual(payload, b"\x03\0\0\0/a\0\x02\0\0\0xy")

    def test_navi_settings(self):
        settings = json.loads(cc.navi_settings(960, 720, 30))["naviScreenInfo"]
        self.assertEqual((settings["width"], settings["height"], settings["fps"]), (960, 720, 30))
        self.assertEqual(settings["safearea"], {"width": 960, "height": 720, "x": 0, "y": 0, "outside": 0})


class Session(unittest.TestCase):
    def run_stream(self, script, unlock=False, secs=10):
        clock = FakeClock()
        adapter = FakeAdapter(clock, script)
        out = io.BytesIO()
        stream = cc.ClusterStream(adapter, 1280, 480, 30, out, unlock=unlock, clock=clock)
        stream.run(secs)
        return adapter, out, stream

    def test_plain_stream_never_writes_to_the_adapter(self):
        adapter, _, _ = self.run_stream([])
        self.assertNotIn(cc.MSG_SEND_FILE, [t for t, _ in adapter.sent])

    def test_unlock_sends_the_file_between_open_and_settings(self):
        adapter, _, _ = self.run_stream([], unlock=True)
        self.assertEqual([t for t, _ in adapter.sent[:3]], [cc.MSG_OPEN, cc.MSG_SEND_FILE, cc.MSG_BOX_SETTINGS])

    def test_saves_video_without_its_header(self):
        script = [(1, cc.MSG_PLUGGED, b""), (2, cc.MSG_CLUSTER_VIDEO, video(data=b"NAL"))]
        _, out, stream = self.run_stream(script)
        self.assertEqual(out.getvalue(), b"NAL")
        self.assertEqual(stream.size, (1280, 480))
        self.assertEqual(stream.frames, 1)

    def test_requests_the_stream_after_the_phone_arrives(self):
        adapter, _, _ = self.run_stream([(1, cc.MSG_PLUGGED, b"")], secs=20)
        self.assertEqual(adapter.commands().count(cc.CMD_REQUEST_CLUSTER_VIDEO), 3)

    def test_echoes_the_adapters_request(self):
        script = [(1, cc.MSG_COMMAND, cc.command_payload(cc.CMD_REQUEST_CLUSTER_VIDEO))]
        adapter, _, _ = self.run_stream(script)
        self.assertEqual(adapter.commands(), [cc.CMD_REQUEST_CLUSTER_VIDEO])

    def test_releases_the_stream_at_the_end(self):
        adapter, _, _ = self.run_stream([(1, cc.MSG_CLUSTER_VIDEO, video())])
        self.assertEqual(adapter.commands()[-1], cc.CMD_RELEASE_CLUSTER_VIDEO)

    def test_short_video_payload_is_ignored(self):
        _, out, stream = self.run_stream([(1, cc.MSG_CLUSTER_VIDEO, b"short")])
        self.assertEqual((out.getvalue(), stream.frames), (b"", 0))


class Info(unittest.TestCase):
    def test_leaves_out_identifiers(self):
        settings = {"productType": "A15W", "uuid": "secret", "DevList": ["phone"]}
        clock = FakeClock()
        script = [
            (1, cc.MSG_FIRMWARE_VERSION, b"2025.10.15.1127CAY\0"),
            (2, cc.MSG_BOX_SETTINGS, json.dumps(settings).encode()),
        ]
        adapter = FakeAdapter(clock, script)
        printed = io.StringIO()
        original, sys.stdout = sys.stdout, printed
        try:
            self.assertTrue(cc.read_info(adapter, 10, clock=clock))
        finally:
            sys.stdout = original
        self.assertIn("A15W", printed.getvalue())
        self.assertNotIn("secret", printed.getvalue())
        self.assertEqual([t for t, _ in adapter.sent if t == cc.MSG_SEND_FILE], [])


if __name__ == "__main__":
    unittest.main()
