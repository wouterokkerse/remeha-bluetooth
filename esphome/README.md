# Elga proxy firmware

A completely stock ESPHome Bluetooth proxy cannot enter a Bluetooth PIN: the
ESPHome API that Home Assistant uses to pair has no passkey field. The Elga
proxy firmware is a normal ESPHome Bluetooth proxy plus a small component that
accepts the PIN from Home Assistant for a short pairing window. You flash it
once and never edit YAML or secrets.

## Install (once per ESP32)

1. Open the browser installer: <https://wouterokkerse.github.io/remeha-bluetooth/>
   (Chrome or Edge on a computer, ESP32 connected by USB).
2. Click **Connect**, install, and enter your Wi-Fi network when asked.
3. Home Assistant discovers **Elga Bluetooth Proxy**. Add it under
   **Settings > Devices & services**. Home Assistant provisions the API
   encryption key.
4. Add the Elga Ace integration, select the gateway and enter the appliance PIN.

Updates arrive like any ESPHome device. If you prefer ESPHome Device Builder,
the proxy shows up there for **Adopt** (via `dashboard_import`).

## How the PIN is handled

During setup and reconnects Home Assistant calls the proxy's
`elga_ace_set_pairing_code` action over the **encrypted** native API with the
gateway MAC and PIN, asks the proxy to pair, and then clears the code. The ESP32
keeps it in RAM for at most two minutes and only answers passkey requests from
that exact MAC. A proxy without API encryption never receives the PIN.

## Files

- `elga-proxy.yaml`: the full firmware (generic ESP32, ESP-IDF). Build it
  yourself for other boards by changing the `esp32:` block.
- `elga-pairing-package.yaml`: the PIN handler as a package, for adding to an
  existing proxy configuration instead.
- `elga-ace-components/elga_ace_pairing`: the C++ component.

The installer page is built by `.github/workflows/proxy-firmware.yml` and
published with GitHub Pages (enable Pages with source **GitHub Actions**).

References: [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy/),
[ESP Web Tools](https://esphome.github.io/esp-web-tools/).
