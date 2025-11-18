#include "TinZrHubCommands.h"
#include "TinZrCore.h"
#include "TinZrConnect.h"

TinZrHubCommands* TinZrHubCommands::_self = nullptr;

TinZrHubCommands::TinZrHubCommands(TinZrCore* core, TinZrConnect* net)
: _core(core), _net(net)
{
  _self = this;
  if (_net) {
    // Register static callback with TinZrConnect
    _net->onMessage(&TinZrHubCommands::netCallback);
  }
}

void TinZrHubCommands::netCallback(IPAddress from, const uint8_t* data, size_t len)
{
  if (_self) {
    _self->handleNetMessage(from, data, len);
  }
}

// Main dispatcher: called from onNetMessage
void TinZrHubCommands::handleNetMessage(IPAddress from, const uint8_t* data, size_t len)
{
  if (!data || len == 0) return;

  // Convert payload to String
  String s;
  s.reserve(len + 1);
  for (size_t i = 0; i < len; ++i) s += (char)data[i];
  s.trim();

  if (!s.length()) return;

  // Ignore HUB-ACK completely (hub heartbeat)
  if (s.equalsIgnoreCase("HUB-ACK")) {
    return;
  }

  // Debug log
  Serial.print("📩 Msg from ");
  Serial.print(from);
  Serial.print("  ->  '");
  Serial.print(s);
  Serial.println("'");

  // ---- Dispatch commands ----

  if (s.equalsIgnoreCase("OFF")) {
    _cmdOff();
    return;
  }

  if (s.startsWith("LED ")) {
    _cmdLed(s);
    return;
  }

  if (s.equalsIgnoreCase("PING")) {
    _cmdPing(from);
    return;
  }

  // Battery query: "BAT", "BAT LEVEL", "BAT VOLT", etc.
  if (s.startsWith("BAT")) {
    _cmdBattery();
    return;
  }

  // Digital write: "DIG <pin> HIGH|LOW|1|0"
  if (s.startsWith("DIG ")) {
    _cmdDigital(s);
    return;
  }

  // Analog/PWM write: "ANA <pin> <value>"
  if (s.startsWith("ANA ")) {
    _cmdAnalog(s);
    return;
  }

  // Unknown commands are just logged (already done above)
}

// ------------------- Command implementations -------------------

void TinZrHubCommands::_cmdOff()
{
  Serial.println("↪️  CMD OFF → LED off");

  if (_core) {
    _core->ledOff();   // or TinZr.ledOff() if you prefer
  }

  _curR = _curG = _curB = 0;
  _curBr = 0;
}

void TinZrHubCommands::_cmdLed(const String& s)
{
  // Format: LED r g b [brightness]
  int r, g, b, br = 25;

  int n = sscanf(s.c_str() + 4, "%d %d %d %d", &r, &g, &b, &br);
  if (n < 3) {
    Serial.println("❌ LED cmd: expected LED <r> <g> <b> [brightness]");
    return;
  }

  r  = constrain(r,  0, 255);
  g  = constrain(g,  0, 255);
  b  = constrain(b,  0, 255);
  br = constrain(br, 0, 255);

  Serial.printf("↪️  CMD LED (%d,%d,%d) @ %d\n", r, g, b, br);

  if (_core) {
    _core->setLED((uint8_t)r, (uint8_t)g, (uint8_t)b, (uint8_t)br);
  }

  _curR  = (uint8_t)r;
  _curG  = (uint8_t)g;
  _curB  = (uint8_t)b;
  _curBr = (uint8_t)br;
}

void TinZrHubCommands::_cmdPing(IPAddress /*from*/)
{
  Serial.println("↪️  CMD PING → PONG");

  if (_net) {
    _net->sendUDP(String("PONG"));
  }
}

void TinZrHubCommands::_cmdBattery()
{
  if (!_core || !_net) {
    Serial.println("⚠️ BAT cmd: core or net not attached");
    return;
  }

  // Assuming TinZrCore has readBatteryVoltage() + batteryPercent()
  float vbat   = _core->readBatteryVoltage();
  int   pct    = _core->batteryPercent();

  char buf[64];
  snprintf(buf, sizeof(buf), "BAT %d%% %.2fV", pct, vbat);

  Serial.print("↪️  CMD BAT → ");
  Serial.println(buf);

  // Send battery info back to hub/peers
  _net->sendTCP(String(buf));
}

void TinZrHubCommands::_cmdDigital(const String& s)
{
  int pin;
  char levelStr[8] = {0};

  // Parse: DIG <pin> <level>
  int n = sscanf(s.c_str() + 4, "%d %7s", &pin, levelStr);
  if (n < 2) {
    Serial.println("❌ DIG cmd: expected DIG <pin> <HIGH|LOW|1|0>");
    return;
  }

  int val = -1;
  if (!strcasecmp(levelStr, "HIGH") || !strcmp(levelStr, "1")) {
    val = HIGH;
  } else if (!strcasecmp(levelStr, "LOW") || !strcmp(levelStr, "0")) {
    val = LOW;
  }

  if (val == -1) {
    Serial.println("❌ DIG cmd: level must be HIGH/LOW/1/0");
    return;
  }

  pinMode(pin, OUTPUT);
  digitalWrite(pin, val);

  Serial.printf("↪️  CMD DIG pin %d -> %s\n", pin, (val == HIGH ? "HIGH" : "LOW"));
}

void TinZrHubCommands::_cmdAnalog(const String& s)
{
  int pin;
  int value;

  // Parse: ANA <pin> <value>
  int n = sscanf(s.c_str() + 4, "%d %d", &pin, &value);
  if (n < 2) {
    Serial.println("❌ ANA cmd: expected ANA <pin> <value>");
    return;
  }

  value = constrain(value, 0, 255);   // adjust range if you want 0–4095 etc.

  // Simple analogWrite – you can later upgrade to LEDC if needed
  analogWrite(pin, value);

  Serial.printf("↪️  CMD ANA pin %d -> %d\n", pin, value);
}
