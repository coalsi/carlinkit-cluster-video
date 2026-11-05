# Carlinkit cluster video

Turns on the second video stream on a **stock** Carlinkit CarPlay adapter, so a
head unit or computer gets CarPlay's cluster map as its own H.264 feed, next to
the main CarPlay screen. It needs no firmware flash, no SSH and no custom firmware.

CarPlay can drive a second display (the map in an instrument cluster). The
adapter supports this, but on a stock box the stream stays off until a size file
exists on the adapter. That file can be written over the normal USB protocol.
This repo does that, and documents the rest of what a host has to do.

```text
Before  phone -> adapter -> host: main CarPlay screen
After   phone -> adapter -> host: main CarPlay screen
                               + cluster map at the size you ask for
```

## Disclaimer

For **an adapter you own**. This writes a file to the adapter's persistent
storage, and **there is no known way to delete it**: it can only be overwritten.
It may void your warranty and you use it at your own risk. Not affiliated with
or endorsed by Carlinkit or Apple.

## What it was tested on

| | |
|---|---|
| Adapter | Carlinkit CPC200-CCPA, product type `A15W`, USB `1314:1520` |
| Firmware | stock `2025.10.15.1127CAY` |
| Phone | iPhone, wireless CarPlay |
| Host | macOS, Python 3 |

The results below came from the probe this script was cleaned up from. Its messages
are pinned by offline tests, but this exact script has not yet run on an adapter.

That is one adapter on one firmware. Other models and versions are untried, and
so are wired CarPlay and Linux. Run `info` first and compare.

**Do not take the vendor's 2026.x update if you depend on this.** Firmware
2026.07.31.1733 has [an open report](https://github.com/lvalen91/CPC200-CCPA_resources/issues/3)
of navigation data going missing.

## Use it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Check the adapter. This changes nothing:

```bash
.venv/bin/python carlinkit_cluster.py info
```

Turn the stream on and capture 60 seconds, with the phone connected and a
route running. `--unlock` writes the file, so you only need it once per size:

```bash
.venv/bin/pip install av
.venv/bin/python carlinkit_cluster.py stream --unlock --width 1280 --height 480 --fps 30 --png
```

Later runs leave it out:

```bash
.venv/bin/python carlinkit_cluster.py stream --secs 120 --out map --png
```

The result is `map.h264`, plus `map.png` with `--png`. Both show your live
location, so take care where you post them.

| Command | What it does |
|---|---|
| `info` | Prints model and firmware. Writes nothing. |
| `stream` | Saves the cluster stream. Add `--unlock` to write the size file first. |
| `reset` | USB reset, which starts a new phone session. A new size needs this. |
| `reboot` | Reboots the adapter. |

Only one program can hold the adapter, so close your head-unit app first.

## Results

Clean runs with nothing else on the adapter:

| Session | Length | Result |
|---|---|---|
| CarPlay only | 150 s | No drops |
| Cluster 1280x480 | 70 s | 1630 frames, 25.5 fps, no drops |
| Cluster 1280x480 | 45 s | 986 frames, 24.8 fps, no drops |

These are short runs, not a long soak test. In a head-unit app, sizes from
468x328 up to 960x720 all worked. The unlock survived a software reboot. **A real
power pull is untested.**

## Limits

- **Keyframes stop early.** They arrive at about 0, 3 and 7 seconds, then none
  for minutes. No forced-keyframe method is known for this stream. Keep your
  decoder running, and use `reset` only as a last resort.
- **A new size needs a new phone session.** A plain reconnect keeps the old one.
- **Quiet screens send few frames**, about one a second or fewer. That isn't a stall.
- **The map carries CarPlay's own card and ETA bar.** To hide them, ask for a
  taller picture and show only the middle: 960x720 cropped to the middle 360 rows
  works well.
- **Fast reconnects can come back with no phone.** Quitting without a clean stop
  and reopening 3 seconds later gave no session, while 60 seconds idle or a reboot
  cleared it.
- **No caller details.** Despite older notes, this adapter sent no call-status
  messages, with or without the unlock, on FaceTime Audio or cellular calls.
  Calls show up only as ring, start and stop audio commands.

## Warnings

- **The size file must be 24 bytes.** The 12-byte layout in some notes breaks
  *all* CarPlay on the adapter, with every session failing at negotiation. Writing
  the correct file fixes it.
- **Never write over `AIEIPIEREngines.datastore`.** It is signed, and breaking the
  signature is reported to silence media and call data.
- **A truly bricked CPC200-CCPA needs an SPI programmer.** It has no recovery mode
  over USB, so don't try writes beyond this one file without a backup.

## For your own head-unit app

[docs/PROTOCOL.md](docs/PROTOCOL.md) has the message sequence, formats and session
behaviour you need to add this to an app.

## Credits

Built on the protocol and firmware research in
[lvalen91/CPC200-CCPA_resources](https://github.com/lvalen91/CPC200-CCPA_resources).
[lvalen91/ocbm](https://github.com/lvalen91/ocbm) and
[f-io/LIVI](https://github.com/f-io/LIVI) are the routes to take if you want to
replace the vendor software instead.

## License

MIT
