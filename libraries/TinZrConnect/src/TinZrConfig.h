#pragma once

// 1) Pull in user overrides if present
#if defined(__has_include)
#  if __has_include("TinZrUserConfig.h")
#    include "TinZrUserConfig.h"
#  endif
#endif

// 2) Defaults if not overridden
#ifndef TINZR_ENABLE_WIFI
#define TINZR_ENABLE_WIFI  0
#endif

#ifndef TINZR_ENABLE_BLE
#define TINZR_ENABLE_BLE   0
#endif

#ifndef TINZR_ENABLE_OTA
#define TINZR_ENABLE_OTA   0  // requires TINZR_ENABLE_WIFI == 1
#endif



// You can also define PIN_RGB_LED / PB_PIN in the board variant or here if needed.
// The code assumes:
//   - PIN_RGB_LED is the NeoPixel data pin
//   - PB_PIN is the pushbutton pin (active low)
