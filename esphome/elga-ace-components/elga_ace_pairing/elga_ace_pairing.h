#pragma once

#include <array>
#include <cstdint>
#include <cstring>
#include <string>

#include "esphome/core/component.h"
#include "esphome/core/hal.h"
#include "esphome/components/esp32_ble/ble.h"

namespace esphome::elga_ace_pairing {

class ElgaAcePairing : public Component {
 public:
  bool set_pairing_code(const std::string &address, const std::string &pin);
  void gap_event_handler(esp_gap_ble_cb_event_t event, esp_ble_gap_cb_param_t *param);
  void loop() override;

 protected:
  static constexpr uint32_t CODE_LIFETIME = 120000;

  struct PendingCode {
    std::array<uint8_t, 6> address{};
    uint32_t passkey{0};
    uint32_t created_at{0};
    bool active{false};
  };

  std::array<PendingCode, 4> codes_{};
  PendingCode *find_code_(const uint8_t *address);
  static bool parse_address_(const std::string &address, std::array<uint8_t, 6> &result);
  static void clear_(PendingCode &code);
};

}
