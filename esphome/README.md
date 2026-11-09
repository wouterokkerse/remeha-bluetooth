# Elga Ace passkey support for an ESPHome Bluetooth proxy

This companion package lets the Home Assistant integration send the code from
its setup form to an **ESP32** Bluetooth proxy over its existing **encrypted
native API connection**. No local Home Assistant Bluetooth adapter or BlueZ
pairing agent is needed when using the proxy.

## Why a firmware helper is needed

ESPHome's standard Bluetooth proxy API supports a pair request, but does not
include a passkey in that request. Passing `pin=` to the standard Bleak proxy
backend would not deliver your code to the ESP32. This package supplies a scoped
GAP passkey handler and a native API action instead; it does not replace the
proxy, implement another GATT client or write appliance registers.

## Install on an existing proxy

Use **ESPHome 2026.10.2 or newer** and an ESP32 board using ESP-IDF. Other proxy
platforms are not supported by this companion component. Keep your existing
board, Ethernet/Wi-Fi, encryption and OTA settings.

1. Copy `elga-pairing-package.yaml` and the entire `elga-ace-components` folder
   from this directory into the directory containing your proxy's ESPHome YAML.
   This is commonly `/config/esphome`. No GitHub publication is required.
2. Add this to the proxy YAML, merging it with any existing `packages` mapping:

   ```yaml
   packages:
     elga_pairing: !include elga-pairing-package.yaml
   ```

3. Ensure the proxy has `bluetooth_proxy: {active: true}` and native API
   encryption enabled. Keep at least one BLE connection slot free. The package
   sets `esp32_ble.io_capability` to `keyboard_only`; do not override it back to
   `none`. This is a controller-wide capability, but passkey replies are scoped
   to the selected gateway MAC, not a global PIN for every device.
4. Compile and install the changed proxy firmware yourself. Reconnect/reload
   that proxy's ESPHome integration in Home Assistant so its native actions are
   refreshed. This repository's tools never flash a device automatically.
5. Replace the Home Assistant integration with version 0.2.0 or newer and
   restart Home Assistant. Add **Remeha Elga Ace Bluetooth**, select the gateway,
   and enter its real pairing code and your chosen device name as usual.

The result should contain these files next to your existing proxy YAML:

```text
your-proxy.yaml
elga-pairing-package.yaml
elga-ace-components/
  elga_ace_pairing/
    __init__.py
    elga_ace_pairing.h
    elga_ace_pairing.cpp
```

If you change the component folder location, adjust the local
`external_components.source.path` in the package. It is relative to the main
proxy configuration, not to the package file. Rebuild the firmware after moving
or modifying the component.

## Optional complete example

`elga-proxy.yaml` is a starting configuration for a **generic ESP32 dev board**,
not a firmware image for every proxy product. Select the right board and
network configuration for your hardware. Copy `secrets.yaml.example` to
`secrets.yaml` and supply your own Wi-Fi credentials, a newly generated 32-byte
base64 API encryption key, and an OTA password. Do not put the Elga pairing code
in this file; Home Assistant supplies it at pairing time.

## How pairing works

- Home Assistant chooses an active local/proxy connection path using its normal
  Bluetooth routing and slot management. Passive advertisement-only proxies
  cannot carry GATT traffic.
- The integration checks the **actual connected path**, not just the scanner
  that produced a preferred advertisement. It matches an ESPHome proxy to its
  loaded config entry by the proxy's Bluetooth MAC.
- It invokes the exact `elga_ace_set_pairing_code(address, pin)` native action
  through that proxy's existing encrypted API client. It does not create a new
  API connection or broadcast credentials as a Home Assistant service event.
- It then invokes the standard proxy pairing method. The ESP32 answers a passkey
  request only for that gateway's address. Numeric comparison, if requested,
  is accepted only when it matches the supplied code.
- Codes are stored only in RAM, never preferences or entities. They expire after
  two minutes, are cleared on authentication completion, and are explicitly
  cleared by Home Assistant after success, failure or cancellation. Existing
  Bluetooth bonds remain in the proxy's normal bond store.
- After pairing, the normal BLE unlock, CAN authorization, appliance
  authorization and read-only register polling run through the proxy unchanged.

The helper can hold up to four independent pending codes without overwriting an
unrelated active code. It never logs a passkey or replies to a different MAC.
Enabling SMP/Bluetooth protocol-level debug logging can expose security details;
avoid that when sharing logs.

If Home Assistant switches to a different proxy on reconnect, that proxy also
needs the helper and API encryption. Install the package on all active proxies
that may be selected for this gateway. Local BlueZ connections continue to use
the local pairing agent. Stock proxies without the helper produce an explicit
setup error rather than silently ignoring the PIN.

## Validation and limitations

Python tests simulate proxy-only discovery, actual route switching, encrypted
API code delivery and clearing, rejected/unsupported pairing, reconnection and
the captured GTW handshake. Configuration validation and C++ generation pass
with ESPHome 2026.10.2. The companion C++ also passes an ESP32 cross-compiler
syntax check with stubbed runtime headers. A complete firmware build was
attempted on Windows but stopped during ESP-IDF setup because the toolchain
reported missing `riscv32-esp-elf` and `dfu-util`; that is not a completed firmware
build. Compile the final configuration in your ESPHome environment. RF range,
bonding with the actual Elga and firmware installation still need an in-range
hardware test by you.

Primary references:

- [ESPHome proxy pair implementation](https://github.com/Bluetooth-Devices/bleak-esphome/blob/main/src/bleak_esphome/backend/client.py)
- [ESPHome BLE component](https://esphome.io/components/esp32_ble/)
- [ESPHome Bluetooth proxy configuration](https://esphome.io/components/bluetooth_proxy/)
