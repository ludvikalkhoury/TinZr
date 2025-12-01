#pragma once

// =============================
// TinZr global configuration
// =============================

// Feature switches (can be overridden in the .ino before including TinZrNode.h)
#ifndef TINZR_ENABLE_WIFI
#define TINZR_ENABLE_WIFI  1
#endif

#ifndef TINZR_ENABLE_BLE
#define TINZR_ENABLE_BLE   0
#endif

#ifndef TINZR_ENABLE_OTA
#define TINZR_ENABLE_OTA   1   // requires TINZR_ENABLE_WIFI == 1
#endif

// You can also define PIN_RGB_LED / PB_PIN in the board variant or here if needed.
// The code assumes:
//   - PIN_RGB_LED is the NeoPixel data pin
//   - PB_PIN is the pushbutton pin (active low)
