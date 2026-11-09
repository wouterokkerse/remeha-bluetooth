"""Short-lived, MAC-scoped passkeys for an ESPHome Bluetooth proxy."""

import esphome.codegen as cg
import esphome.config_validation as cv
from esphome.components import esp32_ble
from esphome.const import CONF_ID

DEPENDENCIES = ["esp32", "esp32_ble", "bluetooth_proxy"]
CONF_BLE_ID = "ble_id"

namespace = cg.esphome_ns.namespace("elga_ace_pairing")
ElgaAcePairing = namespace.class_("ElgaAcePairing", cg.Component)

CONFIG_SCHEMA = cv.Schema({
    cv.GenerateID(): cv.declare_id(ElgaAcePairing),
    cv.GenerateID(CONF_BLE_ID): cv.use_id(esp32_ble.ESP32BLE),
}).extend(cv.COMPONENT_SCHEMA)


async def to_code(config):
    component = cg.new_Pvariable(config[CONF_ID])
    await cg.register_component(component, config)
    parent = await cg.get_variable(config[CONF_BLE_ID])
    esp32_ble.register_gap_event_handler(parent, component)
