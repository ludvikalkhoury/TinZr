#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_wifi.h>
#include <vector>

#include "TinZrConfig.h"

// Forward-declare TinZrCore for HubCommands and OTA LED
class TinZrCore;
extern TinZrCore TinZr;

// =============================
// TinZrLink - generic transport
// =============================
class TinZrLink {
public:
    using MsgHandler = void (*)(IPAddress from, const uint8_t* data, size_t len);
    virtual ~TinZrLink() {}

    virtual bool start() = 0;
    virtual void handle() = 0;

    virtual void sendUDP(const uint8_t* data, size_t len) = 0;
    virtual int  sendTCP(const uint8_t* data, size_t len,
                         uint32_t timeoutMs = 200) = 0;

    virtual void onMessage(MsgHandler cb) = 0;
};

// =============================
// TinZrWiFi - shared Wi-Fi helper
// =============================
struct TinZrWiFiConfig {
    const char*  ssid       = "";
    const char*  pass       = "";
    bool         use_static = false;

    IPAddress   ip   = IPAddress(192, 168, 1, 40);
    IPAddress   gw   = IPAddress(192, 168, 1, 1);
    IPAddress   mask = IPAddress(255, 255, 255, 0);
    IPAddress   dns1 = IPAddress(8, 8, 8, 8);
    IPAddress   dns2 = IPAddress(8, 8, 4, 4);
  
    uint16_t     hubTcpPort   = 4211;
    uint16_t     hubUdpPort   = 4210;
    IPAddress    hubMcastGrp  = IPAddress(239, 1, 1, 1);

    wifi_power_t tx_power = WIFI_POWER_8_5dBm;
    const char*  hostname = nullptr;
    bool         force_dhcp_config = false;
};

class TinZrWiFi {
public:
    void begin(const TinZrWiFiConfig& cfg) { _cfg = cfg; }
    using TickCallback = void (*)();

    bool connect(uint32_t timeout_ms, TickCallback tick = nullptr);

    bool connected() const { return WiFi.status() == WL_CONNECTED; }
    IPAddress ip() const { return WiFi.localIP(); }

private:
    TinZrWiFiConfig _cfg;
};

// =============================
// TinZrConnect - Wi-Fi UDP/TCP link
// =============================
class TinZrConsole; // forward

class TinZrConnect : public TinZrLink {
public:
    TinZrConnect();

    bool start(uint16_t hubTcpPort, uint16_t hubUdpPort, IPAddress hubMcastGrp);
    bool start() override { return _started; }

    void handle() override;

    void sendUDP(const uint8_t* data, size_t len) override;
    int  sendTCP(const uint8_t* data, size_t len,
                 uint32_t timeoutMs = 200) override;

    void onMessage(MsgHandler cb) override { _cb = cb; }

    void attachConsole(TinZrConsole* c) { _console = c; }
    void sendDiscovery();
    void setHubIP(IPAddress ip) { _hubIP = ip; }
    void setName(const char* name);

private:
    bool        _started      = false;
    uint16_t    _hubTcpPort   = 0;
    uint16_t    _hubUdpPort   = 0;
    IPAddress   _hubMcastGrp;
    IPAddress   _hubIP;
    WiFiUDP     _udp;
    WiFiClient  _tcpClient;
    MsgHandler  _cb           = nullptr;
    TinZrConsole* _console    = nullptr;
    uint32_t    _lastHello    = 0;
    String      _name;

    void _handleUDP();
    void _handleTCP();
};

// =============================
// TinZrBleConnect - BLE link
// =============================
#if TINZR_ENABLE_BLE
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>

class TinZrBleConnect : public TinZrLink,
                        public BLEServerCallbacks,
                        public BLECharacteristicCallbacks {
public:
    TinZrBleConnect();
    void setName(const char* name);
    bool start() override;
    void handle() override;

    void sendUDP(const uint8_t* data, size_t len) override;
    int  sendTCP(const uint8_t* data, size_t len,
                 uint32_t timeoutMs = 200) override;

    void onMessage(MsgHandler cb) override { _cb = cb; }
    bool isConnected() const { return _connected; }
    

protected:
    void onConnect(BLEServer* server) override;
    void onDisconnect(BLEServer* server) override;
    void onWrite(BLECharacteristic* ch) override;

private:
    String             _name;
    BLEServer*         _server          = nullptr;
    BLECharacteristic* _rxCharacteristic = nullptr;
    BLECharacteristic* _txCharacteristic = nullptr;
    MsgHandler         _cb              = nullptr;
    bool               _connected       = false;
};
#endif // TINZR_ENABLE_BLE

// =============================
// OTA config + TinZrOTA
// =============================
struct TinZrCfg {
  uint16_t ota_port     = 3232;
  const char* ota_password = nullptr;
};


#if TINZR_ENABLE_OTA
#include <ArduinoOTA.h>
#endif

class TinZrOTA {
public:
#if TINZR_ENABLE_OTA
  // Wi-Fi is already managed elsewhere (Console / Node).
  // We just start OTA when Wi-Fi is up.
  void begin(const char* hostname, const TinZrCfg& cfg);
  void handle();

  bool connected() const { return WiFi.status() == WL_CONNECTED; }
  bool ready() const     { return WiFi.status() == WL_CONNECTED; }

  IPAddress    ip()   const { return WiFi.localIP(); }
  const String& host() const { return _hostname; }
#else
  void begin(const char*, const TinZrCfg&, uint32_t = 15000) {}
  void handle() {}
  bool connected() const { return false; }
  bool ready() const { return false; }
  IPAddress ip() const { return IPAddress(0,0,0,0); }
  const String& host() const { static String empty; return empty; }
#endif

private:
#if TINZR_ENABLE_OTA
  void   setupOTA();

  TinZrCfg _cfg{};
  String   _hostname{};
  bool     _started = false;

  static TinZrOTA* _self;
#endif
};

// =============================
// TinZrHubCommands - hub text protocol
// =============================
class TinZrHubCommands {
public:
  TinZrHubCommands(TinZrCore* core, TinZrLink* net);

  void handleNetMessage(IPAddress from, const uint8_t* data, size_t len);

  uint8_t ledR()  const { return _curR; }
  uint8_t ledG()  const { return _curG; }
  uint8_t ledB()  const { return _curB; }
  uint8_t ledBr() const { return _curBr; }

  static void netCallback(IPAddress from, const uint8_t* data, size_t len);

private:
  TinZrCore* _core;
  TinZrLink* _net;

  uint8_t _curR  = 0;
  uint8_t _curG  = 0;
  uint8_t _curB  = 0;
  uint8_t _curBr = 0;

  static TinZrHubCommands* _self;

  void _cmdOff();
  void _cmdLed(const String& s);
  void _cmdPing(IPAddress from);
  void _cmdBattery();
  void _cmdDigital(const String& s);
  void _cmdAnalog(const String& s);
};
