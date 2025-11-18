/*
===============================================================================
TinZr — Serial Control for SSID / PASS / Static Mode / Hostname (+ Flash Save)
===============================================================================

WHAT THIS DOES
--------------
- Change Wi-Fi SSID/PASS, toggle STATIC (use_static), and change HOSTNAME
- Apply changes immediately (no reflashing)
- Wi-Fi credentials SAVE automatically when changed
- Device REBOOTS after WIFI command so LED/OTA state restarts clean
- Settings persist across reboot (NVS)
- OTA always active (rainbow → connect → green)

SERIAL MONITOR SETTINGS
-----------------------
- Baud:       115200
- Line ending: Newline

COMMANDS
--------
WIFI <ssid> <password>
    Set Wi-Fi credentials, auto-save to flash, and REBOOT immediately.
    ex: WIFI Ludvik Lud12345

STATIC ON
    Enable static IP mode (uses the TinZrCfg static block values already compiled in).

STATIC OFF
    Disable static IP (DHCP).

HOST <name>
    Change OTA/mDNS hostname and reconnect.
    ex: HOST esp32c3-ota

SAVE
    Save current RAM settings to flash (NVS). (Not needed for WIFI — auto)

LOAD
    Load settings from flash (NVS) into RAM and reconnect.

WIPE
    Delete saved TinZr settings from flash.

WIPEWIFI
    Erase Wi-Fi radio driver stored credentials (rarely needed)

SHOW
    Print current RAM settings and connection status.

REBOOT
    Restart the device.

NOTES
-----
- WIFI command auto-saves and reboots for clean OTA/LED cycle
- STATIC setting is persisted
- HOST name is persisted
===============================================================================
*/

#include <TinZrOTA.h>
#include <TinZrConsole.h>

// Set your preferred defaults here
TinZrConsoleDefaults DEF = {
  .ssid       = "Ludvik",
  .pass       = "Lud12345",
  .hostname   = "TinZr-ota",
  .use_static = true
};

TinZrConsole Console;

void setup() {
  Serial.begin(115200);
  delay(200);

  // Start console + OTA + load saved settings
  Console.begin(DEF);
}

void loop() {
  // Process serial commands + keep OTA alive + LED fsm
  Console.handle();
}
