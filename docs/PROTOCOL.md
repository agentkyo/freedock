# MiraBox N4 USB protocol

What FreeDock knows about the N4's wire protocol. Verified on a retail **MiraBox N4**
(`6602:1001`, serial `0300D0780A1E`, firmware `V3.293N4.01.006` as reported by the vendor app).

## USB

The N4 is a composite device behind its own VIA Labs USB hub (`2109:2817`), so the deck shows up
next to whatever you plug into the N4's ports.

| Interface | Usage page / usage | Role |
|---|---|---|
| `MI_00` | `0xFFA0` / `0x01` | Vendor HID: screen, keys, knobs, touch strip |
| `MI_01` | `0x0001` / `0x06` | Standard keyboard, idle (the vendor app does not use it) |

> **Power / cabling.** If the N4 keeps connecting and disconnecting every few seconds, its USB 3
> link is failing and it falls back to USB 2 without enough power for the screens and the hub.
> Use the original cable, straight into a rear port, or feed the N4's power input.

## Reports

* **Output:** report id `0x00` + **1024** bytes → every write is 1025 bytes.
* **Input:** 512 bytes. Byte 9 = control id, byte 10 = state.

Every command starts with `CRT\0\0` and is zero-padded to 1024 bytes:

```
00 | 43 52 54 00 00 | <command> ... | 00 00 00 ...
id |  C  R  T       |
```

| Command | Bytes after `CRT\0\0` | Meaning |
|---|---|---|
| Wake | `DIS` | Turn the panel on |
| Brightness | `LIG 00 00 <0-100>` | Backlight |
| Clear | `CLE 00 00 00 <id>` | Clear one image slot, `0xFF` = all |
| Flush | `STP` | Commit pending images to the panel |
| Heartbeat | `CONNECT` | Keep-alive. Without it the firmware returns to its idle screen. FreeDock sends it every 8 s |
| Sleep | `HAN` | Screen off |
| Hand back | `CLE 00 00 'D' 'C'` | Release the panel to the firmware logo |
| Key image | `BAT <len:u32be> <id>` then `len` bytes of JPEG | Upload one image (≤ 10240 bytes) |
| Background | `BGPIC <len:u32be> <x:u16> <y:u16> <w:u16> <h:u16> <sel:u8> 00` | Full-panel image (seen on N4 Pro firmware, experimental on N4) |
| Clear background | `BGCLE 03 00` | |

Image data follows the `BAT` header as plain 1024-byte chunks (report id `0x00`, no `CRT`
prefix), zero-padded. After all images of a frame, send `STP` once.

Images are **JPEG, rotated 180°** (the panel is mounted upside down).

## Image slots

The firmware numbers images strip-first:

| Image id | Surface | Size |
|---|---|---|
| 1, 2, 3, 4 | Touch-strip zones, left → right | 176 × 112 |
| 6 … 10 | Bottom row of keys, left → right | 112 × 112 |
| 11 … 15 | Top row of keys, left → right | 112 × 112 |

## Input ids

| Control | Ids | State byte |
|---|---|---|
| Keys (top-left → bottom-right) | `0x01` … `0x0A` | 1 = down, 0 = up |
| Strip zones | `0x40` … `0x43` | release only |
| Strip swipe | `0x38` left, `0x39` right | – |
| Knob 1 push / left / right | `0x37` / `0xA0` / `0xA1` | – |
| Knob 2 push / left / right | `0x35` / `0x50` / `0x51` | – |
| Knob 3 push / left / right | `0x33` / `0x90` / `0x91` | – |
| Knob 4 push / left / right | `0x36` / `0x70` / `0x71` | – |

Knobs send one report per detent. Pushes and strip taps only report the release.

## Throughput

On a USB 2 link FreeDock measured about **100 full frames per second**: all 10 keys at about
1.2 KB of JPEG each, plus `STP`. A single key reached about 800 updates per second. That leaves
plenty of headroom for 30 fps animations.

## Firmware version

The vendor app reads `V3.293N4.01.006` through a feature report. On Windows, `hid_get_feature_report`
returns nothing on this interface (the same thing happens in other projects). FreeDock shows the
version when the OS returns it.

## Credits

This map was put together from USB behaviour on real hardware, plus public work by others:
[Bitfocus Companion's Stream Dock surface](https://github.com/bitfocus/companion-surface-mirabox-stream-dock) (MIT),
[mirajazz](https://github.com/4ndv/mirajazz) (MPL-2.0),
[kriomant/mirabox](https://github.com/kriomant/mirabox) (N4 Pro `BGPIC` framing) and
MiraBox's own [StreamDock Device SDK](https://github.com/MiraboxSpace/StreamDock-Device-SDK).
FreeDock contains no code from these projects.
