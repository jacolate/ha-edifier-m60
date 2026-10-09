# Edifier M60 – Home Assistant integration (handoff spec)

Goal: a Home Assistant custom integration that controls a pair of **Edifier M60** speakers over BLE,
mainly to **switch the input source** (Bluetooth / USB-C / AUX) from HA automations, plus read back
the current input. Use case: an Echo Dot (running EchoMuse, a local HA voice satellite / media player)
is connected to the M60 via AUX; HA should switch the M60 to AUX when the Echo plays music, and back
to Bluetooth when needed.

Everything below was obtained by decompiling the official app (EDIFIER ConneX 1.0.31,
`com.edifier.edifierconnex`) and then **verified live against the speaker** from a MacBook with
Python + bleak (see "Verified test log").

---

## 1. BLE identification

| Item | Value |
|---|---|
| Advertised name | `EDIFIER BLE` (sometimes no name in scan results) |
| Manufacturer ID | `0x07E0` (2016, Edifier) |
| Manufacturer data (my unit) | `c8 24 78 80 88 d2 02 00` |
| Search/advertised service UUID (from app config) | `0000f600-0000-1000-8000-00805f9b34fb` |
| GATT service | `4809f601-1a48-11e9-ab14-d663bd873d93` |
| Write characteristic (commands) | `48090002-1a48-11e9-ab14-d663bd873d93` (write with response worked) |
| Notify characteristic (responses) | `48090001-1a48-11e9-ab14-d663bd873d93` |

Manufacturer data layout (from app code `BLEManager`):
- bytes 0–5: classic Bluetooth / A2DP MAC of the speaker → here `C8:24:78:80:88:D2`
- second-to-last byte: protocol ("BLE") version → `0x02`
- last byte: payload encryption flag → `0x00` = none (`0x10` would mean XOR every payload byte with `0xA5`)

Discovery should match on manufacturer ID `0x07E0` and/or service UUID `0000f600-…`, **not** on the name.

## 2. Packet format (protocol version 2, as used by the M60)

```
Command (app → speaker):  AA EC <cmd> <len_hi> <len_lo> <payload...> <checksum>
Response (speaker → app): BB EC <cmd> <len_hi> <len_lo> <payload...> <checksum>
```
- `AA` = header for commands sent by the app, `BB` = header on frames from the speaker
- `EC` = app code (236)
- `len` = payload length, big-endian, 2 bytes
- `checksum` = sum of all preceding bytes, `& 0xFF`
- No payload encryption on this unit (flag `0x00`). If the flag is `0x10`, XOR payload bytes with `0xA5`
  (both directions), header/len/checksum unencrypted; checksum is computed over the encrypted bytes.

Python reference:
```python
def packet(cmd: int, payload: bytes = b"") -> bytes:
    d = bytes([0xAA, 0xEC, cmd, len(payload) >> 8, len(payload) & 0xFF]) + bytes(payload)
    return d + bytes([sum(d) & 0xFF])

def parse(frame: bytes):
    # returns (cmd, payload) or None; validates header, length, checksum
    if len(frame) < 6 or frame[0] not in (0xAA, 0xBB) or frame[1] != 0xEC:
        return None
    length = (frame[3] << 8) | frame[4]
    if len(frame) != length + 6 or (sum(frame[:-1]) & 0xFF) != frame[-1]:
        return None
    return frame[2], frame[5:5 + length]
```
Notes: the speaker also pushes unsolicited frames (see §4), and a single notification may in theory
contain one frame; handle buffering/splitting defensively.

## 3. Input source commands (verified)

| Cmd | Name in app | Payload | Response payload |
|---|---|---|---|
| `0x61` (97) | `input_source_query` | none | `[type, current_input]` |
| `0x62` (98) | `input_source_set` | `[type, input]` | `[0x01]` = OK; speaker then pushes a `0x61` frame with the new state |

For the M60 the speaker reports **type = `0x0F`**. Input values for type `0x0F`:

| Value | Input |
|---|---|
| `0x01` | Bluetooth |
| `0x02` | USB-C (USB audio) |
| `0x03` | AUX (3.5 mm line in) |

Ready-made packets:
| Action | Bytes |
|---|---|
| Query input | `AA EC 61 00 00 F7` |
| Set Bluetooth | `AA EC 62 00 02 0F 01 0A` |
| Set USB-C | `AA EC 62 00 02 0F 02 0B` |
| Set AUX | `AA EC 62 00 02 0F 03 0C` |

The integration should use the `type` byte from the query response rather than hard-coding `0x0F`.
(The app's mapping table: type 2 → BT=1/AUX=2/USB=3; types 15 & 19 → BT=1/USB=2/AUX=3.)

## 4. Other frames / commands seen or available (not yet verified on the M60)

Seen as unsolicited pushes after connecting:
- `0x84` (132, `smart_light_query`) – e.g. `BB EC 84 00 03 03 01 00 32` – touch-panel backlight state
- `0x50` (80, `play_info_set`) – now-playing info over Bluetooth (contains track title text); all-zero payload when not on Bluetooth

From the app's command table (`CommandIndex` / `CommandManager`), possibly useful, **untested**:
- `0x66` (102) `device_volume_query`, `0x67` (103) `device_volume_set` – payload `[volume]`
- `0xC8` (200) `mac_query`, `0xC9` (201) `name_query`
- `0xD7` (215) / `0xD6` (214) auto-shutdown-without-audio query/set
- `0xCE` (206) `shutdown` – do not expose casually
- `0x16` (22) `device_exception` – error frame

Only expose the input source (and maybe volume after testing) in v1.

## 5. Verified test log (MacBook, bleak, M60 on Bluetooth input, Mac connected for audio)

```
Found: EDIFIER BLE  Manufacturer: {'0x7e0': 'c8 24 78 80 88 d2 02 00'}
Query  → BB EC 61 00 02 0F 01 1A          (type 0F, input 01 = Bluetooth)
         BB EC 84 00 03 03 01 00 32
         BB EC 50 00 1B 00 06 12 ...       (now playing)
Set 0F 03 → BB EC 62 00 01 01 0B          (OK)
            BB EC 61 00 02 0F 03 1C       (now AUX)
            BB EC 50 00 05 00 00 00 00 00 FC
Query while on AUX → BB EC 61 00 02 0F 03 1C   (still reachable over BLE in AUX mode)
```

## 6. Open question that affects the architecture

The app code only uses BLE while the phone also has an **A2DP (classic Bluetooth audio) connection** to
the speaker (`ConnectManager.getCorrectDevice` ignores BLE adverts unless A2DP is connected). In my
tests the Mac was connected for audio the whole time. **Not yet tested:** whether the speaker still
advertises/accepts BLE when *no* device holds an A2DP connection.

- If BLE works without A2DP → a plain HA Bluetooth adapter or an **ESPHome Bluetooth proxy** (active
  connections) is enough. Preferred.
- If A2DP is required → the host doing the BLE control must also hold a classic BT audio connection
  (e.g. Raspberry Pi / VM with a USB BT dongle running BlueZ). Then a small service + MQTT may be easier
  than a pure HA integration.

Please design so the BLE transport goes through HA's Bluetooth stack (works with local adapters and
ESPHome proxies), and add clear logging so this question can be answered during testing.

## 7. Requested implementation

- HA **custom integration** (`custom_components/edifier_m60/`), installable via HACS / manual copy.
- Use `homeassistant.components.bluetooth` for discovery (match manufacturer ID `0x07E0` and/or
  service UUID `0000f600-0000-1000-8000-00805f9b34fb`) and `bleak-retry-connector` for connections.
- Config flow: pick discovered device (or enter address).
- Entities:
  - `select.<name>_input_source` with options `Bluetooth`, `USB-C`, `AUX` (state from `0x61` responses/pushes)
  - optionally `sensor` for connection state / raw input value
- Connection strategy: connect on demand (or keep alive with reconnect), subscribe to notify, send query
  after connect, update state from both responses and unsolicited `0x61` pushes; short disconnect timeout
  so the official app can still connect when needed (the speaker likely allows only one BLE client).
- Robust frame parsing (checksum check, ignore unknown commands, debug-log raw hex).
- Support the XOR encryption flag from manufacturer data even though my unit uses none.

## 8. Environment

- Home Assistant OS in a Proxmox VM; Zigbee stick passed through to it.
- Bluetooth hardware on the Proxmox host: being checked (USB dongle or ESP32 Bluetooth proxy are options).
- Echo Dot 2nd gen running EchoMuse (it can act as an ESPHome Bluetooth proxy) connected to the M60 via AUX.
