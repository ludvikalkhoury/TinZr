#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <Preferences.h>
#include <esp_wifi.h>

#include "TinZrConfig.h"
#include "TinZrConnections.h"

class TinZrCore;
class TinZrConnect;

// Optional default behavior
#ifndef TINZR_AUTOSAVE_WIFI_ON
#define TINZR_AUTOSAVE_WIFI_ON true
#endif

struct TinZrConsoleDefaults {
    const char* ssid       = "Ludvik";
    const char* pass       = "Lud12345";
    const char* hostname   = "esp32c3-ota";
    bool        use_static = false;
};

// ========== TinZrConsole ==========
class TinZrConsole {
public:
    TinZrConsole() : _autosave_wifi(TINZR_AUTOSAVE_WIFI_ON) {}

    void begin(const TinZrConsoleDefaults& def, uint32_t connect_timeout_ms = 15000);
    void handle();

    void setAutosaveWifi(bool on) { _autosave_wifi = on; }

    void attachNet(TinZrConnect* net) { _net = net; }
    void attachCore(TinZrCore* core)  { _core = core; }

    const String& getHostname() const { return _host; }

    bool connected() const {
#if TINZR_ENABLE_OTA
        return _ota.connected();
#else
        return (WiFi.status() == WL_CONNECTED);
#endif
    }

    bool ready() const {
#if TINZR_ENABLE_OTA
        return _ota.ready();
#else
        return (WiFi.status() == WL_CONNECTED);
#endif
    }

    IPAddress ip() const {
#if TINZR_ENABLE_OTA
        return _ota.ip();
#else
        return WiFi.localIP();
#endif
    }

private:
    TinZrWiFi     _wifi;
    TinZrOTA      _ota;
    Preferences   _prefs;
    TinZrCore*    _core = nullptr;
    TinZrConnect* _net  = nullptr;

    String  _ssid;
    String  _pass;
    String  _host;
    bool    _use_static    = false;
    bool    _autosave_wifi = false;

    void applyConfig(uint32_t connect_timeout_ms = 15000);
    void saveToNVS();
    bool loadFromNVS();
    void wipeNVS();
    void wipeWiFiDriverNVS();

    void showConfig();
    void handleSerial();
    void printHelp(bool with_header = true);
};
