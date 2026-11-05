# Cluster video protocol notes

Everything here was seen on a CPC200-CCPA (`A15W`) running stock firmware
`2025.10.15.1127CAY` with a wireless iPhone. All integers are little-endian.

## Message framing

Every message starts with a 16-byte header:

| Offset | Value |
|---|---|
| 0 | Magic `0x55AA55AA` |
| 4 | Payload length |
| 8 | Message type |
| 12 | Bitwise NOT of the type |

## Messages used

| Type | Name | Direction | Payload |
|---|---|---|---|
| `0x01` | Open | to adapter | 7 ints: width, height, fps, format, packet max, ibox version, phone mode |
| `0x02` | Plugged | from adapter | |
| `0x03` | Phase | from adapter | 1 int. 7 negotiating, 8 streaming, 13 negotiation failed |
| `0x04` | Unplugged | from adapter | |
| `0x08` | Command | both | 1 int |
| `0x19` | BoxSettings | both | JSON |
| `0x2C` | Cluster video | from adapter | See below |
| `0x99` | SendFile | to adapter | See below |
| `0xAA` | Heartbeat | to adapter | Empty, every 2 s |
| `0xCC` | Firmware version | from adapter | Text |
| `0xCD` | Reboot | to adapter | Empty |
| `0xCE` | USB reset | to adapter | Empty |

Commands: 508 requests the cluster stream, 509 releases it.

## Turning the stream on

1. Send **Open**.
2. Send **SendFile** to `/etc/RiddleBoxData/HU_NAVISCREEN_INFO`.
3. Send **BoxSettings**:

   ```json
   {"naviScreenInfo":{"width":1280,"height":480,"fps":30,
    "safearea":{"width":1280,"height":480,"x":0,"y":0,"outside":0}}}
   ```

4. Send a heartbeat every 2 seconds.
5. About 2 seconds after **Plugged**, send command 508. Retry at 4-second
   intervals, up to 3 times.
6. Whenever the adapter sends command 508, send 508 back.
7. Read `0x2C` messages.

The file only needs writing once per size. A host that ignores the adapter's 508
keeps its CarPlay session, but gets no cluster video.

### SendFile payload

| Field | Size |
|---|---|
| Path length, including the trailing NUL | 4 |
| Path, NUL terminated | varies |
| Content length | 4 |
| Content | varies |

### Size file

24 bytes, six ints:

```text
width, height, 0, 0, fps, 0
```

For 1280x480 at 30 fps: `00050000 e0010000 00000000 00000000 1e000000 00000000`.

A 12-byte file (width, height, fps) makes every CarPlay session go from phase 7 to
phase 13, even sessions that never ask for cluster video.

## Video payload

A 20-byte header, then Annex-B H.264:

| Offset | Field |
|---|---|
| 0 | Width |
| 4 | Height |
| 8 | Encoder state (seen as 1) |
| 12 | PTS |
| 16 | Flags |

## Session behaviour

- **The file sets the size.** It is read as a phone session starts.
  `naviScreenInfo` did not change the size by itself.
- **A host reconnect is not a new phone session.** The adapter keeps the phone,
  often with no Plugged message, and `0x2C` carries on mid-stream, so a new host
  can join between keyframes.
- **USB reset (`0xCE`) starts a new session.** The phone returns in 5 to 10 seconds.
  Changing size this way took about 10 seconds, with the picture back 7 to 11
  seconds later.
- **Rewriting the same size doesn't start a new session.**
- **Keyframes:** at about 0, 3 and 7 seconds of a stream, then none for minutes.
  Command 12 only reached the main stream, and 509 followed by 508 did not restart
  this one. A decoder that loses data should decode on rather than wait.
- **Idle adapter:** until a host sends Open, the adapter leaves the USB bus for
  about 3 seconds in every 12. Wait for it and send Open at once. In a long-lived
  libusb process a fresh start may be needed to see it again.
- **`supportFeatures` stays empty** even with the stream working, so don't use it
  to detect support.
