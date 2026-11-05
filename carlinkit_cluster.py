#!/usr/bin/env python3
"""Turn on the cluster (navigation) video stream on a stock Carlinkit adapter.

Talks to a Carlinkit "Auto Box" (USB 1314:1520) directly from a computer, with
no firmware flash and no shell on the adapter. See README.md before using
`stream --unlock`: it leaves a file on the adapter that cannot be deleted.
"""
import argparse
import json
import os
import struct
import subprocess
import sys
import time

VENDOR_ID = 0x1314
PRODUCT_ID = 0x1520
MAGIC = 0x55AA55AA
HEADER_LEN = 16

MSG_OPEN = 0x01
MSG_PLUGGED = 0x02
MSG_PHASE = 0x03
MSG_UNPLUGGED = 0x04
MSG_COMMAND = 0x08
MSG_BOX_SETTINGS = 0x19
MSG_CLUSTER_VIDEO = 0x2C
MSG_SEND_FILE = 0x99
MSG_HEARTBEAT = 0xAA
MSG_FIRMWARE_VERSION = 0xCC
MSG_REBOOT = 0xCD
MSG_RESET_USB = 0xCE

CMD_REQUEST_CLUSTER_VIDEO = 508
CMD_RELEASE_CLUSTER_VIDEO = 509

SIZE_FILE = "/etc/RiddleBoxData/HU_NAVISCREEN_INFO"
CLUSTER_HEADER_LEN = 20

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NO_ADAPTER = 3

# Fields of the adapter's first BoxSettings message worth showing. The message
# also carries identifiers (uuid, device list) that have no place in a pasted log.
INFO_FIELDS = ("productType", "boxType", "hwVersion", "MFD", "supportFeatures", "supportLinkType")


def frame(msg_type, payload=b""):
    return struct.pack("<IIII", MAGIC, len(payload), msg_type, ~msg_type & 0xFFFFFFFF) + payload


def parse_header(header):
    """Return (payload length, message type) from a 16-byte header."""
    magic, length, msg_type, _ = struct.unpack("<IIII", header)
    if magic != MAGIC:
        raise ValueError("bad magic 0x%08x" % magic)
    return length, msg_type


def open_payload(width=1414, height=1159, fps=30):
    # format 5, packet max 49152, ibox version 2, phone mode 2.
    return struct.pack("<7I", width, height, fps, 5, 49152, 2, 2)


def command_payload(number):
    return struct.pack("<I", number)


def send_file_payload(path, content):
    name = path.encode() + b"\0"
    return struct.pack("<I", len(name)) + name + struct.pack("<I", len(content)) + content


def size_file_content(width, height, fps):
    # Six little-endian ints. A 12-byte width/height/fps file makes every CarPlay
    # session fail at negotiation, so this layout must not be shortened.
    return struct.pack("<6I", width, height, 0, 0, fps, 0)


def navi_settings(width, height, fps):
    safe_area = {"width": width, "height": height, "x": 0, "y": 0, "outside": 0}
    settings = {"naviScreenInfo": {"width": width, "height": height, "fps": fps, "safearea": safe_area}}
    return json.dumps(settings, separators=(",", ":")).encode()


def parse_cluster_header(payload):
    """Return (width, height, encoder state, pts, flags) of a 0x2C payload."""
    return struct.unpack("<5I", payload[:CLUSTER_HEADER_LEN])


class AdapterGone(Exception):
    pass


class Adapter:
    """The adapter's bulk endpoints, through pyusb."""

    def __init__(self):
        import libusb_package
        import usb.backend.libusb1
        import usb.core
        import usb.util

        self._usb = usb
        backend = usb.backend.libusb1.get_backend(find_library=lambda _: libusb_package.get_library_path())
        self._device = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID, backend=backend)
        if self._device is None:
            raise AdapterGone("not on the bus")
        # An idle adapter re-enumerates, so give it a moment after it appears.
        time.sleep(0.3)
        try:
            self._device.set_configuration()
            usb.util.claim_interface(self._device, 0)
        except usb.core.USBError as error:
            raise AdapterGone(str(error))
        interface = self._device.get_active_configuration()[(0, 0)]
        endpoints = [endpoint.bEndpointAddress for endpoint in interface]
        is_in = lambda address: usb.util.endpoint_direction(address) == usb.util.ENDPOINT_IN
        self._in = next(address for address in endpoints if is_in(address))
        self._out = next(address for address in endpoints if not is_in(address))

    def send(self, msg_type, payload=b""):
        self._device.write(self._out, frame(msg_type, payload), timeout=2000)

    def recv(self):
        """Return (type, payload), or None if nothing arrived in 300 ms."""
        usb = self._usb
        try:
            header = bytes(self._device.read(self._in, HEADER_LEN, timeout=300))
        except usb.core.USBTimeoutError:
            return None
        except usb.core.USBError as error:
            raise AdapterGone(str(error))
        if len(header) != HEADER_LEN:
            return None
        try:
            length, msg_type = parse_header(header)
        except ValueError as error:
            print(error)
            return None
        payload = bytearray()
        while len(payload) < length:
            try:
                payload += bytes(self._device.read(self._in, min(length - len(payload), 65536), timeout=1000))
            except usb.core.USBTimeoutError:
                break
            except usb.core.USBError as error:
                raise AdapterGone(str(error))
        return msg_type, bytes(payload)

    def close(self):
        self._usb.util.release_interface(self._device, 0)
        self._usb.util.dispose_resources(self._device)


class ClusterStream:
    """One host session: sets up the cluster stream and saves its H.264."""

    KEEPALIVE_SECS = 2
    REQUEST_DELAY_SECS = 2
    REQUEST_RETRY_SECS = 4
    REQUEST_ATTEMPTS = 3

    def __init__(self, adapter, width, height, fps, out, unlock=False, clock=time.time):
        self.adapter = adapter
        self.width = width
        self.height = height
        self.fps = fps
        self.out = out
        self.unlock = unlock
        self.clock = clock
        self.frames = 0
        self.bytes = 0
        self.first_frame_at = None
        self.size = None
        self.plugged_at = None
        self.requests = 0

    def start(self):
        self.adapter.send(MSG_OPEN, open_payload())
        if self.unlock:
            content = size_file_content(self.width, self.height, self.fps)
            self.adapter.send(MSG_SEND_FILE, send_file_payload(SIZE_FILE, content))
            print("Wrote %s (%dx%d at %d fps)" % (SIZE_FILE, self.width, self.height, self.fps))
        self.adapter.send(MSG_BOX_SETTINGS, navi_settings(self.width, self.height, self.fps))

    def request_stream_if_due(self, now):
        if self.plugged_at is None or self.requests >= self.REQUEST_ATTEMPTS:
            return
        if now - self.plugged_at > self.REQUEST_DELAY_SECS + self.requests * self.REQUEST_RETRY_SECS:
            self.adapter.send(MSG_COMMAND, command_payload(CMD_REQUEST_CLUSTER_VIDEO))
            self.requests += 1

    def handle(self, msg_type, payload, now):
        if msg_type == MSG_CLUSTER_VIDEO and len(payload) >= CLUSTER_HEADER_LEN:
            if self.first_frame_at is None:
                self.first_frame_at = now
                self.size = parse_cluster_header(payload)[:2]
                print("%.1f s: first cluster frame, %dx%d" % (now, self.size[0], self.size[1]))
            self.out.write(payload[CLUSTER_HEADER_LEN:])
            self.frames += 1
            self.bytes += len(payload) - CLUSTER_HEADER_LEN
        elif msg_type == MSG_PLUGGED:
            self.plugged_at = self.plugged_at or now
            print("%.1f s: phone connected" % now)
        elif msg_type == MSG_UNPLUGGED:
            print("%.1f s: phone disconnected" % now)
            self.plugged_at = None
            self.requests = 0
        elif msg_type == MSG_PHASE and len(payload) >= 4:
            print("%.1f s: phase %d" % (now, struct.unpack("<I", payload[:4])[0]))
        elif msg_type == MSG_COMMAND and len(payload) >= 4:
            if struct.unpack("<I", payload[:4])[0] == CMD_REQUEST_CLUSTER_VIDEO:
                self.adapter.send(MSG_COMMAND, command_payload(CMD_REQUEST_CLUSTER_VIDEO))

    def run(self, secs):
        started = self.clock()
        self.start()
        last_keepalive = 0
        try:
            while self.clock() - started < secs:
                now = self.clock() - started
                if now - last_keepalive > self.KEEPALIVE_SECS:
                    self.adapter.send(MSG_HEARTBEAT)
                    last_keepalive = now
                self.request_stream_if_due(now)
                message = self.adapter.recv()
                if message is not None:
                    self.handle(message[0], message[1], self.clock() - started)
        except AdapterGone as error:
            print("Adapter dropped: %s" % error)
            return False
        if self.frames:
            self.adapter.send(MSG_COMMAND, command_payload(CMD_RELEASE_CLUSTER_VIDEO))
        return True

    def report(self, elapsed):
        if not self.frames:
            print("No cluster video. Is a phone connected, and has this adapter been unlocked?")
            return
        rate = self.frames / max(1, elapsed - self.first_frame_at)
        print("%d frames, %d bytes, %.1f fps" % (self.frames, self.bytes, rate))


def read_info(adapter, secs, clock=time.time):
    """Print the adapter's identity. Sends only Open and keepalives."""
    started = clock()
    adapter.send(MSG_OPEN, open_payload())
    last_keepalive = started
    found = {}
    while clock() - started < secs:
        if clock() - last_keepalive > ClusterStream.KEEPALIVE_SECS:
            adapter.send(MSG_HEARTBEAT)
            last_keepalive = clock()
        message = adapter.recv()
        if message is None:
            continue
        msg_type, payload = message
        if msg_type == MSG_FIRMWARE_VERSION:
            found["firmware"] = payload.rstrip(b"\0").decode("utf8", "replace")
        elif msg_type == MSG_BOX_SETTINGS:
            try:
                settings = json.loads(payload.rstrip(b"\0").decode("utf8", "replace"), strict=False)
            except ValueError:
                continue
            found.update({key: settings[key] for key in INFO_FIELDS if key in settings})
        if "firmware" in found and "productType" in found:
            break
    for key, value in found.items():
        print("%s: %s" % (key, value))
    return bool(found)


def save_last_frame(h264_path, png_path):
    try:
        import av
    except ImportError:
        print("Install PyAV to save a picture: pip install av")
        return
    container = av.open(h264_path, format="h264")
    last = None
    count = 0
    for picture in container.decode(container.streams.video[0]):
        last = picture
        count += 1
    if last is None:
        print("Nothing decoded yet. A capture needs its first keyframe.")
        return
    last.to_image().save(png_path)
    print("Decoded %d frames, saved %s" % (count, png_path))


def run_command(args):
    try:
        adapter = Adapter()
    except AdapterGone:
        return EXIT_NO_ADAPTER
    try:
        if args.command == "info":
            return EXIT_OK if read_info(adapter, args.secs) else EXIT_FAILED
        if args.command == "reset":
            adapter.send(MSG_RESET_USB)
            print("Reset sent. The phone returns in 5 to 10 s.")
            return EXIT_OK
        if args.command == "reboot":
            adapter.send(MSG_REBOOT)
            print("Reboot sent.")
            return EXIT_OK
        h264_path = args.out + ".h264"
        with open(h264_path, "wb") as out:
            stream = ClusterStream(adapter, args.width, args.height, args.fps, out, unlock=args.unlock)
            finished = stream.run(args.secs)
            stream.report(args.secs)
        if args.png and stream.frames:
            save_last_frame(h264_path, args.out + ".png")
        return EXIT_OK if finished and stream.frames else EXIT_FAILED
    finally:
        try:
            adapter.close()
        except Exception:
            pass


def wait_for_adapter(argv):
    """Rerun this script until it claims the adapter.

    An idle adapter leaves the bus for about 3 s in every 12, and a long-lived
    libusb process keeps a stale view of it, so each try is a fresh process.
    """
    environment = dict(os.environ, CARLINKIT_CLUSTER_CHILD="1")
    for _ in range(100):
        code = subprocess.call([sys.executable, "-u", os.path.abspath(__file__)] + argv, env=environment)
        if code != EXIT_NO_ADAPTER:
            return code
        time.sleep(0.2)
    print("No adapter found (USB %04x:%04x)." % (VENDOR_ID, PRODUCT_ID))
    return EXIT_NO_ADAPTER


def build_parser():
    parser = argparse.ArgumentParser(description="Cluster video on a stock Carlinkit adapter.")
    commands = parser.add_subparsers(dest="command", required=True)

    info = commands.add_parser("info", help="print model and firmware, changes nothing")
    info.add_argument("--secs", type=int, default=15)

    stream = commands.add_parser("stream", help="capture the cluster stream to a .h264 file")
    stream.add_argument("--unlock", action="store_true",
                        help="write the size file to the adapter first; it stays there, see README")
    stream.add_argument("--width", type=int, default=1280)
    stream.add_argument("--height", type=int, default=480)
    stream.add_argument("--fps", type=int, default=30)
    stream.add_argument("--secs", type=int, default=60)
    stream.add_argument("--out", default="cluster", help="output name, without extension")
    stream.add_argument("--png", action="store_true", help="also save the last frame (needs PyAV)")

    commands.add_parser("reset", help="USB reset: starts a new phone session")
    commands.add_parser("reboot", help="reboot the adapter")
    return parser


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)
    if os.environ.get("CARLINKIT_CLUSTER_CHILD"):
        return run_command(args)
    return wait_for_adapter(argv)


if __name__ == "__main__":
    sys.exit(main())
