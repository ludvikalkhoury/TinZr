#pragma once
#include <Arduino.h>
#include <WiFi.h>

class TinZrCore;
class TinZrConnect;

class TinZrHubCommands {
public:
  TinZrHubCommands(TinZrCore* core, TinZrConnect* net);

  // Main handler – called by the static callback
  void handleNetMessage(IPAddress from, const uint8_t* data, size_t len);

  // Expose current LED state (for BTN messages, etc.)
  uint8_t ledR()  const { return _curR; }
  uint8_t ledG()  const { return _curG; }
  uint8_t ledB()  const { return _curB; }
  uint8_t ledBr() const { return _curBr; }

  // Static trampoline used as TinZrConnect onMessage callback
  static void netCallback(IPAddress from, const uint8_t* data, size_t len);

private:
  TinZrCore*    _core;
  TinZrConnect* _net;

  uint8_t _curR  = 0;
  uint8_t _curG  = 0;
  uint8_t _curB  = 0;
  uint8_t _curBr = 0;

  static TinZrHubCommands* _self;   // <--- singleton instance pointer

  // Command handlers...
  void _cmdOff();
  void _cmdLed(const String& s);
  void _cmdPing(IPAddress from);
  void _cmdBattery();
  void _cmdDigital(const String& s);
  void _cmdAnalog(const String& s);
};
