#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <Preferences.h>
#include <esp_wifi.h>
#include "TinZrOTA.h"

class TinZrCore; 
class TinZrConnect;   


// Optional knobs for default behavior
#ifndef TINZR_AUTOSAVE_WIFI_ON
#define TINZR_AUTOSAVE_WIFI_ON true   // true = WIFI command will save+reboot
#endif

struct TinZrConsoleDefaults {
  const char* ssid       = "Ludvik";
  const char* pass       = "Lud12345";
  const char* hostname   = "esp32c3-ota";
  bool        use_static = false;
};



class TinZrConsole {
public:
  TinZrConsole() : _autosave_wifi(TINZR_AUTOSAVE_WIFI_ON) {}

  // Initialize console, load saved settings (if any), and bring up OTA
  void begin(const TinZrConsoleDefaults& def, uint32_t connect_timeout_ms = 15000);

  // Call in loop()
  void handle();

  // Change policy at runtime
  void setAutosaveWifi(bool on) { _autosave_wifi = on; }
    
  void attachNet(TinZrConnect* net) { _net = net; }

  const String& getHostname() const { return _host; }
      
  // Expose connection helpers
  bool connected() const { return _ota.connected(); }
  bool ready() const { return _ota.ready(); }
  
  IPAddress ip() const { return _ota.ip(); }
    
  
  void attachCore(TinZrCore* core) { _core = core; }    
  
  

    
private:
  // State
  TinZrOTA     _ota;
  Preferences  _prefs;
  TinZrCore*   _core = nullptr; 
  TinZrConnect* _net = nullptr;    

  String  _ssid;
  String  _pass;
  String  _host;
  bool    _use_static = false;
  bool    _autosave_wifi = false;

  // Core flows
  void applyConfig(uint32_t connect_timeout_ms = 15000);
  void saveToNVS();
  bool loadFromNVS();
  void wipeNVS();
  void wipeWiFiDriverNVS();

  // UI
  void showConfig();
  void handleSerial();

  // Pretty banner
  void printHelp(bool with_header = true);
};
