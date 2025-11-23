#pragma once

#include <Arduino.h>
#include <WiFi.h>

#include "TinZrCore.h"
#include "TinZrConsole.h"
#include "TinZrConnect.h"
#include "TinZrHubCommands.h"

// High level config for the node
struct TinZrNodeConfig {
  TinZrConsoleDefaults console;              // WiFi / hostname / static
  uint16_t  hubTcpPort   = 4211;
  uint16_t  hubUdpPort   = 4210;
  IPAddress hubMcastGrp  = IPAddress(239, 1, 1, 1);
  IPAddress hubIP        = IPAddress(172, 20, 10, 4);  // PC IP
	
};

// High-level wrapper around Core + Console + Net + HubCommands
class TinZrNode {
public:
  TinZrNode();

  // Call this from Arduino setup()
  void begin(const TinZrNodeConfig& cfg);

  // Call this from Arduino loop()
  void handle();

  // Optional: access to internals if needed
  TinZrConsole&    console()   { return _console; }
  TinZrConnect&    net()       { return _net; }
  TinZrHubCommands& hubCmd()   { return _hubCmd; }

private:
  TinZrNodeConfig _cfg;
  bool _netStarted = false;

  TinZrConsole    _console;
  TinZrConnect    _net;
  TinZrHubCommands _hubCmd;   // constructed with (&TinZr, &_net)

  // button edge detection
  bool _lastButtonPressed = false;

  void _handleButtonToHub();
};
