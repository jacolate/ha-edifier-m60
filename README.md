# Edifier M60 for Home Assistant

[![CI](https://github.com/jacolate/ha-edifier-m60/actions/workflows/ci.yml/badge.svg)](https://github.com/jacolate/ha-edifier-m60/actions/workflows/ci.yml)
[![Validate](https://github.com/jacolate/ha-edifier-m60/actions/workflows/validate.yml/badge.svg)](https://github.com/jacolate/ha-edifier-m60/actions/workflows/validate.yml)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)

Custom integration that controls Edifier M60 speakers over BLE, mainly to switch the
input source (Bluetooth / USB-C / AUX). It uses Home Assistant's Bluetooth stack, so it
works with a local adapter or an ESPHome Bluetooth proxy (with active connections enabled).

Protocol notes are in [`proto.md`](proto.md). `m60.py` is a standalone bleak script for testing from a laptop.

## Entities

| Entity | Notes |
|---|---|
| `select.<name>_input_source` | Bluetooth, USB-C, AUX. Updates live when the input is changed on the speaker (persistent mode). |
| `binary_sensor.<name>_connected` | Whether HA currently holds the BLE link (diagnostic). |
| `number.<name>_volume` | Created only if the speaker answers the volume query (`0x66`). Range 0–30 is a guess, see `VOLUME_MAX` in `const.py`. |

## Install

1. Copy `custom_components/edifier_m60/` to `/config/custom_components/` (or in HACS: *⋮ → Custom repositories*, add `https://github.com/jacolate/ha-edifier-m60` as type *Integration*).
2. Restart Home Assistant. The speaker should show up as discovered. Otherwise go to
   *Settings → Devices & services → Add integration → Edifier M60*.

## Options

- **Connection mode**
  - *Persistent* (default): stays connected and reconnects automatically. While HA is connected,
    the EDIFIER ConneX app probably can't connect.
  - *On demand*: connects for each command or poll and disconnects after the idle timeout.
- **Poll interval / idle disconnect**: used in on-demand mode only.

## Debugging

```yaml
logger:
  logs:
    custom_components.edifier_m60: debug
```

At INFO level the log records when the speaker starts or stops advertising, connects or disconnects,
and why a connection failed. At DEBUG level it adds every TX/RX frame in hex. *Download diagnostics*
on the device page dumps the last advertisement, connection events and frames.

### Open question: does BLE work without an A2DP connection?

Disconnect every phone or computer from the speaker's Bluetooth audio, then watch the log:
- `No longer advertising`: the speaker only advertises BLE while A2DP is connected.
- `Connect failed: …` while it is still advertising: it advertises but refuses BLE connections.
- `Connected` and the input select keeps working: it works without A2DP, nothing else is needed.

## Example automation

```yaml
triggers:
  - trigger: state
    entity_id: media_player.echo_dot
    to: playing
actions:
  - action: select.select_option
    target:
      entity_id: select.edifier_m60_88_d2_input_source  # check the actual id in HA
    data:
      option: AUX
```

## Development

Requires Python 3.13.

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt -r requirements_lint.txt
pre-commit install            # optional: ruff on every commit (pip install pre-commit)

pytest --cov                  # tests, fails below 95 % coverage
ruff check . && ruff format --check .
mypy                          # strict mode
```

The tests run against a real Home Assistant core (via
`pytest-homeassistant-custom-component`) with a mocked Bluetooth stack. `tests/conftest.py`
has a `FakeSpeaker` that answers the M60 protocol, so the config flow, the entities,
reconnects, on-demand mode and encryption are all tested without hardware.

After editing `strings.json`, copy it to `translations/en.json` (a test checks this).

CI (`.github/workflows/`):
- **CI**: ruff, mypy and pytest with coverage on every push and PR.
- **Validate**: `hassfest` (HA's manifest/translation checks) and the HACS action, also weekly.
- **Dependabot**: bumps the Home Assistant test harness and its Bluetooth libraries together.
