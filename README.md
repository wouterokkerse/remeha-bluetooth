# Remeha Elga Ace Bluetooth

A local, **read-only** Home Assistant custom integration for an Elga Ace with a
GTW Bluetooth gateway. Select the discovered gateway, enter the appliance's
Bluetooth pairing code, give the device a name, and let Home Assistant connect.

**Experimental: the standalone reader and captured authentication exchange have
been verified; the integration itself still needs a real Home Assistant/device
test. No live Bluetooth tests are performed by the development tools.**

## Requirements

- Home Assistant **2026.11 or later** on Linux, including Home Assistant OS,
  with the built-in Bluetooth integration configured. The integration follows
  the Home Assistant 2026.11 Bluetooth APIs. The schema importer includes a
  Voluptuous fallback, but older Home Assistant releases are not a tested target.
- Either a local Linux/BlueZ Bluetooth adapter or an **active ESP32 ESPHome
  Bluetooth proxy** within range of the gateway. Proxy passkey support requires
  the supplied companion firmware package and native API encryption; stock
  proxy firmware does not forward a setup PIN. See [proxy setup](esphome/README.md).
- For local adapters only: access from Home Assistant to system D-Bus/BlueZ.
  Container users must expose that socket and adapter correctly. Proxy-only
  installations do not need a local Bluetooth adapter or local pairing agent.
- Bluetooth/service mode active on the appliance. Close the Remeha app and the
  standalone reader before connecting; they must not compete for the gateway.
- The device catalog was extracted for **EHC-07, OBD 1.51**. Other firmware or
  appliance variants can have missing or different registers.

## Installation

### Marketplace / HACS

1. Add `https://github.com/wouterokkerse/remeha-bluetooth` as a custom repository
   in Marketplace/HACS, choosing **Integration** if asked for a repository type.
2. Download **Remeha Elga Ace Bluetooth** and restart Home Assistant.
3. Open **Settings > Devices & services > Add integration** and select
   **Remeha Elga Ace Bluetooth**.
4. Select your gateway, enter its pairing code, and choose a device name.

Marketplace/HACS installs `custom_components/elga_ace` directly from the selected
GitHub version. No release ZIP asset is required. It does **not** install or
flash the separate ESPHome firmware package: proxy users must also follow
[`esphome/README.md`](https://github.com/wouterokkerse/remeha-bluetooth/blob/main/esphome/README.md).

If you see **"no hacs.json in its root"**, the version being installed must
include the root `hacs.json`. Push the updated files to GitHub and select the
updated default branch, or publish a new release from that commit. Updating
`main` does not fix an already-published older release tag. Version **0.2.1**
includes the HACS metadata fix.

### Manual installation

1. Copy `custom_components/elga_ace` into your Home Assistant configuration
   directory. The resulting path must be
   `/config/custom_components/elga_ace/manifest.json`.
2. Restart Home Assistant.
3. Open **Settings > Devices & services > Add integration** and select
   **Remeha Elga Ace Bluetooth**. A discovery card may also appear automatically.
4. Select your gateway from the device list.
5. Enter a device name and the Bluetooth pairing code from your appliance.
   Leading zeroes are preserved; this is not a fabricated default PIN.
6. Wait for pairing, BLE unlock, CAN authorization, appliance authorization and
   the verification read to finish.

The code is used for real Bluetooth authentication, not just stored in a form.
Local connections use a device-scoped BlueZ agent without replacing the system
default agent. Proxy connections deliver the code to the selected proxy's
companion handler over its encrypted native API. Existing bonds are reused.
A wrong PIN can temporarily lock the gateway, so wait about a minute before
retrying if needed.

### Bluetooth proxies

Version **0.2.0** supports active ESP32 ESPHome proxies without a local Bluetooth
adapter. First install the small firmware package described in
[`esphome/README.md`](esphome/README.md), then use the same device-selection,
name and pairing-code wizard. All 487 entities remain read-only.

The standard proxy pairing API has no passkey argument. The companion package
is therefore required for this PIN-protected gateway; the integration does not
pretend that `pair(pin=...)` would work on unchanged stock firmware. Missing
helper firmware, disabled API encryption or unsupported pairing produces a
specific setup error. Install the helper on every proxy that HA may select for
the gateway. The release ZIP includes the ESPHome sources and example YAML, but
does not flash hardware automatically.

To build an installable archive, run `python tools/build_release.py`. Extract
`dist/remeha-elga-ace-bluetooth.zip` into `/config` so that `custom_components`
lands directly inside the configuration directory. Do not add an extra nested
repository folder.

## Entities

The integration creates **one enabled, read-only entity per unique register**:
**487 entities** from the app metadata, deduplicating addresses repeated in
different app screens. Parameters that are writable in the service app are
still exposed only as sensors here.

- Numeric measurements, counters and settings are sensors.
- Dictionary-defined Yes/No and Off/On values are binary sensors, not switches.
- Other enumerations use the service app's labels as sensor states.
- Names use the English descriptions found in the app, with parameter codes
  and zone/subindex suffixes where applicable. Full descriptions, addresses,
  original app symbols, scale factors and raw bytes are available as attributes.
- Datatypes and scaling come from the app dictionary. Signed temperatures use
  little-endian decoding; room temperature uses tenths, not hundredths.
- Known physical units get the appropriate Home Assistant device class. Unit
  IDs without a confirmed mapping remain unitless, with `unit_id` preserved.
- Structured histories, schedules and other undecoded arrays remain readable
  as hexadecimal sensor states, with complete raw bytes in `raw_hex`. Long
  states are abbreviated to satisfy Home Assistant's state-length limit.
- A register absent on your appliance remains `unavailable`. Its entity is not
  silently removed, and the whole device does not fail for one missing register.
- Service settings/history entities are categorized as diagnostic. The chosen
  device name is used as the entity-name prefix; individual entities can be
  renamed or disabled in Home Assistant normally.

All 487 entries are **known catalog addresses**, not a claim that this particular
appliance responds to all of them. Unsupported registers are retried with
backoff. The integration does not scan unknown addresses or issue any register
write, reset, calibration, schedule-change or heating-control command.

### Frequently refreshed measurements

| Register | Name | App code | Scale |
| --- | --- | --- | --- |
| `0x5031:0` | Control temperature | AM040 | 0.01 °C |
| `0x4359:0` | Heat pump return temperature | — | 0.01 °C |
| `0x501E:0` | Outside temperature | AM027 | 0.01 °C |
| `0x4350:0` | Compressor discharge temperature | — | 0.01 °C |
| `0x50A9:0` | Internal appliance setpoint | AM101 | 0.01 °C |
| `0x434D:0` | Indoor heat exchanger temperature | — | 0.01 °C |
| `0x434E:0` | Outdoor evaporator temperature | — | 0.01 °C |
| `0x5404:1` | Room temperature, zone 1 | CM030 | 0.1 °C |
| `0x5013:0` | Flow temperature | AM016 | 0.01 °C |
| `0x5302:0` | Central heating setpoint | PM002 | 0.01 °C |

These are proprietary object-dictionary indices, **not Modbus register
addresses**. The AM/HM/CM/PM codes identify corresponding concepts but do not
make the two protocols' register numbers interchangeable.

## Polling and reconnection

The connection remains open between polls. If it drops, the next coordinator
update pairs/reconnects and repeats all three application-level handshakes.
Entities become unavailable on a connection failure rather than pretending old
values were just measured.

By default, the ten core values refresh every **30 seconds**. Each cycle also
reads up to **20 background registers**, with a **15-second total read budget**
and a small inter-request delay. Background entries refresh at most once every
ten minutes. Reading the initial full catalog therefore takes several minutes,
typically around 12 minutes if responses are fast, and longer when registers
time out. Entity attributes include the actual `last_read` timestamp.

Use the integration's **Configure** button to change the device name, poll
interval (20-300 seconds) or background batch size (1-60). Disabling an entity
does not currently remove its address from the catalog polling rotation. The
batch scheduler, not the entity count, bounds Bluetooth traffic.

The pairing code is stored in Home Assistant's private config-entry storage,
as other integration credentials are. It is masked in the setup form, never
exposed on entities, and redacted from downloadable integration diagnostics.
Protect Home Assistant configuration files and backups accordingly.

## Troubleshooting

- **No gateways listed:** enable service/Bluetooth mode, close the app, check
  that the gateway is visible to a local adapter and retry the setup dialog.
- **Proxy pairing helper required:** install `esphome/elga-pairing-package.yaml`
  and its component on the actual selected proxy, enable native API encryption,
  flash it and reload its ESPHome integration. Merely seeing advertisements
  through a proxy is not sufficient: it must support active GATT connections.
- **Pairing rejected:** check every PIN digit, including leading zeroes, and
  wait out the gateway's temporary lockout. An existing bond is not deleted
  automatically. Remove stale bonds using your host's Bluetooth tools only if
  necessary; the integration deliberately does not unpair devices automatically.
- **Handshake failed:** keep service mode active and close every other client.
  The integration uses the captured BLE little-endian challenge response,
  followed by CAN level 4 and appliance level 5 authorization.
- **Some entities unavailable:** that register may not exist on this firmware,
  may be a disconnected sensor, or may not have been visited yet. Inspect its
  `register`, `subindex` and `read_error` attributes and download diagnostics.
- **Time programs/history show hex:** those structures are preserved, not
  guessed or misrepresented as temperatures.

## Development and offline checks

The standalone `elga_read.py` is retained as the original Dutch-language
reference reader. The Home Assistant integration is self-contained and does not
import it or require the service app, its binaries or its decryption key.

Run the offline checks with:

```sh
python -m pip install -r requirements-test.txt
python -m unittest discover -s tests -v
python -m compileall -q custom_components tools
```

Tests use simulated Home Assistant, BLE and BlueZ objects. They exercise the
capture's authentication bytes, response correlation, reconnect/cleanup paths,
register coverage and scaling, read-only entity behavior, pairing-code handling,
setup validation, scheduler behavior and diagnostics redaction. They do not
substitute for a real Home Assistant installation or an in-range hardware test.

`elga_registers.csv` contains the original metadata export. The bundled JSON
catalog additionally includes selection labels and transport lengths from the
decoded device cache. To regenerate it when that XML is locally available:

```sh
python tools/build_catalog.py --metadata-xml /path/to/decoded-device-cache.xml
```

No packet capture, encrypted account state, decryption key or personal PIN is
included in the integration or its release archive.

## References

- [Home Assistant Bluetooth integration APIs](https://contributors.home-assistant.io/core/integrations/bluetooth/)
- [BlueZ device pairing API](https://github.com/bluez/bluez/blob/master/doc/org.bluez.Device.rst)
- [BlueZ pairing agent API](https://github.com/bluez/bluez/blob/master/doc/org.bluez.Agent.rst)
- [Bleak retry connector](https://bleak-retry-connector.readthedocs.io/en/latest/usage.html)

This is an unofficial integration, not affiliated with or supported by Remeha.

## Publishing updates

Commit and push the repository files, including `hacs.json` and the integration
manifest, to `wouterokkerse/remeha-bluetooth`. The integration stays in
`custom_components/elga_ace`; keep `content_in_root` and `zip_release` disabled.
The manually built ZIP is a `/config` bundle, not a HACS ZIP-release asset.

For a versioned update, publish a GitHub release from the updated commit with a
tag matching the integration manifest version, for example `v0.2.1`. The tagged
tree must also include `hacs.json`. Users can install the default branch without
a release. Adding a custom repository does not automatically list the integration
in the public Marketplace/HACS catalog.
