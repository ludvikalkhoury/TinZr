#pragma once

//
// TinZr feature switches (compile-time)
//
// Change these for each firmware, then recompile.
// You can also override them in your .ino *before* including TinZrNode.h.
//

#ifndef TINZR_ENABLE_WIFI
#define TINZR_ENABLE_WIFI 0   // 1 = include Wi-Fi + TinZrConnect; 0 = no Wi-Fi code
#endif

#ifndef TINZR_ENABLE_BLE
#define TINZR_ENABLE_BLE 1   // 1 = include BLE + TinZrBleConnect; 0 = no BLE code
#endif

#ifndef TINZR_ENABLE_OTA
#define TINZR_ENABLE_OTA 0   // 1 = compile TinZrOTA (Wi-Fi OTA); 0 = no OTA support
                             // ⚠ Only set this to 1 when TINZR_ENABLE_WIFI == 1
#endif
