#include "elga_ace_pairing.h"

namespace esphome::elga_ace_pairing {

bool ElgaAcePairing::parse_address_(const std::string &address, std::array<uint8_t, 6> &result) {
  if (address.size() != 17)
    return false;
  for (size_t byte_index = 0; byte_index < result.size(); ++byte_index) {
    if (byte_index != 0 && address[byte_index * 3 - 1] != ':')
      return false;
    uint8_t value = 0;
    for (size_t digit_index = 0; digit_index < 2; ++digit_index) {
      const char digit = address[byte_index * 3 + digit_index];
      uint8_t nibble;
      if (digit >= '0' && digit <= '9')
        nibble = digit - '0';
      else if (digit >= 'a' && digit <= 'f')
        nibble = digit - 'a' + 10;
      else if (digit >= 'A' && digit <= 'F')
        nibble = digit - 'A' + 10;
      else
        return false;
      value = (value << 4) | nibble;
    }
    result[byte_index] = value;
  }
  return true;
}

void ElgaAcePairing::clear_(PendingCode &code) {
  code.passkey = 0;
  code.address.fill(0);
  code.created_at = 0;
  code.active = false;
}

void ElgaAcePairing::loop() {
  const uint32_t now = millis();
  for (auto &code : this->codes_) {
    if (code.active && static_cast<uint32_t>(now - code.created_at) >= CODE_LIFETIME)
      clear_(code);
  }
}

ElgaAcePairing::PendingCode *ElgaAcePairing::find_code_(const uint8_t *address) {
  this->loop();
  for (auto &code : this->codes_) {
    if (code.active && std::memcmp(code.address.data(), address, code.address.size()) == 0)
      return &code;
  }
  return nullptr;
}

bool ElgaAcePairing::set_pairing_code(const std::string &address, const std::string &pin) {
  std::array<uint8_t, 6> parsed_address{};
  if (!parse_address_(address, parsed_address))
    return false;
  PendingCode *slot = this->find_code_(parsed_address.data());
  if (pin.empty()) {
    if (slot != nullptr)
      clear_(*slot);
    return true;
  }
  if (pin.size() > 6)
    return false;
  uint32_t passkey = 0;
  for (const char digit : pin) {
    if (digit < '0' || digit > '9')
      return false;
    passkey = passkey * 10 + digit - '0';
  }
  if (slot == nullptr) {
    for (auto &candidate : this->codes_) {
      if (!candidate.active) {
        slot = &candidate;
        break;
      }
    }
  }
  if (slot == nullptr)
    return false;
  slot->address = parsed_address;
  slot->passkey = passkey;
  slot->created_at = millis();
  slot->active = true;
  return true;
}

void ElgaAcePairing::gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param) {
  switch (event) {
    case ESP_GAP_BLE_PASSKEY_REQ_EVT: {
      auto *code = this->find_code_(param->ble_security.ble_req.bd_addr);
      if (code != nullptr)
        esp_ble_passkey_reply(param->ble_security.ble_req.bd_addr, true, code->passkey);
      break;
    }
    case ESP_GAP_BLE_NC_REQ_EVT: {
      auto *code = this->find_code_(param->ble_security.key_notif.bd_addr);
      if (code != nullptr)
        esp_ble_confirm_reply(param->ble_security.key_notif.bd_addr,
                              code->passkey == param->ble_security.key_notif.passkey);
      break;
    }
    case ESP_GAP_BLE_AUTH_CMPL_EVT: {
      auto *code = this->find_code_(param->ble_security.auth_cmpl.bd_addr);
      if (code != nullptr)
        clear_(*code);
      break;
    }
    default:
      break;
  }
}

}
