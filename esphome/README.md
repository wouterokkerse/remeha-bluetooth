# Simple ESPHome proxy setup

Use the existing ESPHome components: **no custom C++, external component or
PIN-forwarding API action**. The fixed PIN is configured on the ESP32 once;
Home Assistant uses the standard proxy pairing method.

1. Copy `elga-pairing.yaml` next to your existing ESPHome proxy YAML.
2. Merge this into that proxy's configuration, using your gateway's MAC:

   ```yaml
   substitutions:
     elga_address: "AA:BB:CC:DD:EE:FF"

   packages:
     elga_pairing: !include elga-pairing.yaml
   ```

3. Add `elga_pin` to the proxy's existing `secrets.yaml`, with your actual numeric
   Bluetooth PIN. Quote it if it starts with zeroes. Do not publish this file.
4. Compile and install the changed proxy configuration yourself, then add or
   reload the ESPHome proxy in Home Assistant.
5. Install the Elga integration, select the discovered gateway and give it a
   name. **Leave the Home Assistant local-adapter PIN field empty.** For proxies,
   that field is not forwarded or used; the PIN comes from `secrets.yaml`.

Keep your existing board, Wi-Fi/Ethernet, encrypted API and OTA settings. This
package is for an ESP32 proxy with built-in `ble_client` support. It sets the
controller's I/O capability to `keyboard_only`; do not override it to `none`.

`auto_connect: false` is intentional: this BLE client handles only matching-MAC
passkey events and must not take the GATT connection away from Home Assistant.
The standard Bluetooth proxy owns the actual connection. The package allows
four BLE clients: the usual three proxy slots plus this passkey handler. If you
have more proxy slots or other BLE clients, increase `max_connections` to match.

If you previously installed the custom `elga_ace_pairing` component/package,
remove that old package include and use this one instead. Do not keep both PIN
handlers enabled for the same gateway. No separate ESPHome native API action is
needed. Install the package on every active proxy that may connect to this
gateway; each ESP32 maintains its own Bluetooth bond.

Marketplace/HACS installs the Home Assistant integration only. Download this
YAML separately from the repository. Local Linux/BlueZ adapters continue to use
the PIN entered in the Home Assistant setup form.

Configuration validation and C++ generation pass with ESPHome 2026.10.2,
including the MAC filter, disabled auto-connect and leading-zero PIN. Actual
pairing and RF behavior still require an in-range test. No automatic flashing
or live Bluetooth tests are performed by this repository's tools.

References: [ESPHome passkey handling](https://esphome.io/components/ble_client/#on_passkey_request),
[Bluetooth proxy configuration](https://esphome.io/components/bluetooth_proxy/).
