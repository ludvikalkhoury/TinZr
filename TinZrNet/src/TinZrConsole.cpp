#include "TinZrConsole.h"
#include "TinZrCore.h"   
#include "TinZrConnect.h"   // 🔹 add this

static inline void _wifi_ram_only() {
  WiFi.persistent(false);
  esp_wifi_set_storage(WIFI_STORAGE_RAM); // keep creds only in RAM
  WiFi.setAutoReconnect(true);
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
}



void TinZrConsole::begin(const TinZrConsoleDefaults& def, uint32_t connect_timeout_ms) {
  Serial.println("\n=== TinZr Serial Config (library) ===");

  _wifi_ram_only();

  // 1) Start with hardcoded defaults
  _ssid       = "";
  _pass       = "";
  _host       = "tinzr";
  _use_static = false;

  // 2) Try to load whatever we have from NVS
  _prefs.begin("tinzr_cfg", true);
  if (_prefs.isKey("ssid")) {
    _ssid = _prefs.getString("ssid", _ssid);
  }
  if (_prefs.isKey("pass")) {
    _pass = _prefs.getString("pass", _pass);
  }
  if (_prefs.isKey("host")) {
    String savedHost = _prefs.getString("host", _host);
    if (savedHost.length() > 0) {
      _host = savedHost;
    }
  }
  if (_prefs.isKey("use_static")) {
    _use_static = _prefs.getUChar("use_static", 0) != 0;
  }
  _prefs.end();

  // 3) Override with DEF *if provided* (DEF wins over NVS)
  if (def.ssid && def.ssid[0]) {
    _ssid = def.ssid;
  }
  if (def.pass && def.pass[0]) {
    _pass = def.pass;
  }
  if (def.hostname && def.hostname[0]) {
    _host = def.hostname;
  }

  // For use_static, you said: "I want ssid and pass and static to be the same I provided"
  // → Always take from DEF.
  _use_static = def.use_static;

  // 4) Save the resulting config back to NVS so it becomes the new default
  saveToNVS();

  // 5) Show help + bring up WiFi/OTA with the final settings
  printHelp(false);
  applyConfig(connect_timeout_ms);
}




void TinZrConsole::handle() {
  handleSerial();
  _ota.handle();
}

void TinZrConsole::applyConfig(uint32_t connect_timeout_ms) {
  // Full clean reconnect; keep DHCP if static not requested
  _wifi_ram_only();
  WiFi.disconnect(true, true);
  if (!_use_static) {
    WiFi.config(INADDR_NONE, INADDR_NONE, INADDR_NONE);
  }
  
  WiFi.setHostname(_host.c_str());
  
  delay(50);

  // Re-init TinZrOTA so LED state machine restarts (rainbow → success)
  _ota.~TinZrOTA();
  new (&_ota) TinZrOTA();

  TinZrCfg cfg;
  cfg.ssid       = _ssid.c_str();
  cfg.pass       = _pass.c_str();
  cfg.use_static = _use_static;

  _ota.begin(_host.c_str(), cfg, connect_timeout_ms);
}

void TinZrConsole::saveToNVS() {
  _prefs.begin("tinzr_cfg", false);
  _prefs.putString("ssid", _ssid);
  _prefs.putString("pass", _pass);
  _prefs.putUChar("use_static", _use_static ? 1 : 0);
  _prefs.putString("host", _host);
  _prefs.end();
  Serial.println("💾 Saved to NVS.");
}



bool TinZrConsole::loadFromNVS() {
  _prefs.begin("tinzr_cfg", true);

  bool ok = _prefs.isKey("ssid") && _prefs.isKey("pass");
  if (ok) {
    _ssid       = _prefs.getString("ssid", _ssid);
    _pass       = _prefs.getString("pass", _pass);
    _use_static = _prefs.getUChar("use_static", 0) != 0;

    if (_prefs.isKey("host")) {
      String savedHost = _prefs.getString("host", "");
      if (savedHost.length() > 0) {
        _host = savedHost;
      }
    }
  }

  _prefs.end();
  if (ok) Serial.println("📥 Loaded settings from NVS.");
  else    Serial.println("⚠️ No saved settings found in NVS.");

  return ok;
}





void TinZrConsole::wipeNVS() {
  _prefs.begin("tinzr_cfg", false);
  _prefs.clear();
  _prefs.end();
  Serial.println("🗑️  Wiped saved app settings from NVS.");
}

void TinZrConsole::wipeWiFiDriverNVS() {
  Serial.println("🧹 Clearing Wi-Fi driver stored credentials (radio NVS)...");
  WiFi.persistent(true);            // allow erase to stick
  WiFi.disconnect(true, true);      // erase STA cfg + creds
  esp_wifi_restore();               // clear Wi-Fi NVS namespaces
  delay(200);
  WiFi.persistent(false);
  Serial.println("✅ Cleared. Reboot or set WIFI <ssid> <pass> again.");
}

void TinZrConsole::showConfig() {
  Serial.println("\n=== CURRENT (RAM) SETTINGS ===");
  Serial.printf("SSID      : %s\n", _ssid.c_str());
  Serial.printf("PASS      : %s\n", _pass.c_str());
  Serial.printf("HOSTNAME  : %s\n", _host.c_str());
  Serial.printf("STATIC    : %s\n", _use_static ? "ON" : "OFF");
  Serial.printf("Connected : %s\n", _ota.connected() ? "YES" : "NO");
  if (_ota.connected()) {
    Serial.printf("IP        : %s\n", _ota.ip().toString().c_str());
    Serial.printf("mDNS/Host : %s.local\n", _host.c_str());
  }
  Serial.println("==============================\n");
}

void TinZrConsole::printHelp(bool with_header) {
  if (with_header) Serial.println("\n=== TinZr Serial Config (library) ===");
  Serial.println("Commands:");
  Serial.printf ("  WIFI <ssid> <pass>  (%s)\n",
                 _autosave_wifi ? "auto-saves + reboots" : "RAM only; use SAVE to persist");
  Serial.println("  STATIC ON | STATIC OFF");
  Serial.println("  HOST <name>");
  Serial.println("  SAVE | LOAD | WIPE | WIPEWIFI | SHOW | REBOOT");
  Serial.println();
  Serial.println("TinZrCore control:");
  Serial.println("  LED <r> <g> <b> [brightness 0-255]");
  Serial.println("  LED OFF");
  Serial.println("  VBAT          (show battery voltage & %)");
  Serial.println("  BAT           (alias of VBAT)");
  Serial.println("  BAT LEVEL     (alias of VBAT)");
  Serial.println("  SOFTOFF       (TinZrCore::softOff)");
  Serial.println("  SOFTON        (TinZrCore::softOn)");
  Serial.println();
  Serial.println("Pin I/O control:");
  Serial.println("  DIG <pin> <HIGH|LOW|1|0>");
  Serial.println("  ANA <pin> <value 0-255>   (PWM / analogWrite)");
  Serial.println();
  Serial.println("Network send (via TinZrConnect):");
  Serial.println("  TCP <message>   (send message via TCP to all peers)");
  Serial.println("  SEND <message>  (alias of TCP)");
  Serial.println("  UDP <message>   (UDP broadcast/multicast to peers/hub)");
  Serial.println();
}



void TinZrConsole::handleSerial() {
  if (!Serial.available()) return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd.isEmpty()) return;

  // ----- TinZrCore control: LED / battery / soft power -----

  // LED OFF
  if (cmd.equalsIgnoreCase("LED OFF")) {
    if (_core) {
      _core->ledOff();
      Serial.println("💡 LED OFF");
    } else {
      Serial.println("⚠️ No TinZrCore attached (call attachCore() in setup).");
    }
    return;
  }

  // LED R G B [BRIGHTNESS]
  if (cmd.startsWith("LED ")) {
    if (!_core) {
      Serial.println("⚠️ No TinZrCore attached (call attachCore() in setup).");
      return;
    }

    int r = 0, g = 0, b = 0, br = 255;
    // parse: LED  R  G  B  [BR]
    int n = sscanf(cmd.c_str() + 4, "%d %d %d %d", &r, &g, &b, &br);
    if (n < 3) {
      Serial.println("❌ Usage: LED <r> <g> <b> [brightness 0-255]");
      return;
    }
    r  = constrain(r,  0, 255);
    g  = constrain(g,  0, 255);
    b  = constrain(b,  0, 255);
    br = constrain(br, 0, 255);

    _core->setLED((uint8_t)r, (uint8_t)g, (uint8_t)b, (uint8_t)br);
    Serial.printf("💡 LED set to (%d,%d,%d) @ %d\n", r, g, b, br);
    return;
  }

  // VBAT / BAT / BAT LEVEL: show battery voltage / percentage
  if (cmd.equalsIgnoreCase("VBAT") ||
      cmd.equalsIgnoreCase("BAT") ||
      cmd.equalsIgnoreCase("BAT LEVEL")) {
    if (!_core) {
      Serial.println("⚠️ No TinZrCore attached (call attachCore() in setup).");
      return;
    }
    float v   = _core->readBatteryVoltage();
    int   pct = _core->batteryPercent();
    Serial.printf("🔋 Battery: %.3f V (%d %%)\n", v, pct);
    return;
  }

  // SOFTOFF / SOFTON
  if (cmd.equalsIgnoreCase("SOFTOFF")) {
    if (!_core) {
      Serial.println("⚠️ No TinZrCore attached (call attachCore() in setup).");
      return;
    }
    Serial.println("🛌 TinZrCore softOff()");
    _core->softOff();
    return;
  }

  if (cmd.equalsIgnoreCase("SOFTON")) {
    if (!_core) {
      Serial.println("⚠️ No TinZrCore attached (call attachCore() in setup).");
      return;
    }
    Serial.println("⚡ TinZrCore softOn()");
    _core->softOn();
    return;
  }

  // ------------------------------------------------------------
  // Pin I/O control: DIG / ANA
  // ------------------------------------------------------------

  // Digital write: DIG <pin> <HIGH|LOW|1|0>
  if (cmd.startsWith("DIG ")) {
    int pin;
    char levelStr[8] = {0};

    int n = sscanf(cmd.c_str() + 4, "%d %7s", &pin, levelStr);
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

    Serial.printf("↪️  DIG pin %d -> %s\n", pin, (val == HIGH ? "HIGH" : "LOW"));
    return;
  }

  // Analog / PWM write: ANA <pin> <value>
  if (cmd.startsWith("ANA ")) {
    int pin;
    int value;

    int n = sscanf(cmd.c_str() + 4, "%d %d", &pin, &value);
    if (n < 2) {
      Serial.println("❌ ANA cmd: expected ANA <pin> <value>");
      return;
    }

    value = constrain(value, 0, 255);  // tweak if you want 0–4095

    analogWrite(pin, value);

    Serial.printf("↪️  ANA pin %d -> %d\n", pin, value);
    return;
  }

  // ------------------------------------------------------------
  // Wi-Fi / config commands
  // ------------------------------------------------------------

  if (cmd.startsWith("WIFI ")) {
    int sp = cmd.indexOf(' ', 5);
    if (sp <= 0 || sp >= (int)cmd.length() - 1) {
      Serial.println("❌ Usage: WIFI <ssid> <pass>");
      return;
    }
    String ss = cmd.substring(5, sp); ss.trim();
    String pw = cmd.substring(sp + 1); pw.trim();
    if (ss.isEmpty() || pw.isEmpty()) {
      Serial.println("❌ WIFI <ssid> <pass>");
      return;
    }
    _ssid = ss;
    _pass = pw;
    Serial.printf("✅ WiFi set: SSID='%s'\n", _ssid.c_str());

    if (_autosave_wifi) {
      saveToNVS();
      Serial.println("🔄 Rebooting to apply and restart LEDs/OTA…");
      delay(200);
      ESP.restart();
      return; // not reached
    } else {
      Serial.println("ℹ️  Not saved. Type SAVE to persist.");
      applyConfig();
    }
    return;
  }

  if (cmd.equalsIgnoreCase("STATIC ON")) {
    _use_static = true;
    Serial.println("✅ STATIC enabled");
    applyConfig();
    return;
  }

  if (cmd.equalsIgnoreCase("STATIC OFF")) {
    _use_static = false;
    Serial.println("✅ STATIC disabled (DHCP)");
    applyConfig();
    return;
  }

  if (cmd.startsWith("HOST ")) {
    String h = cmd.substring(5); h.trim();
    if (h.isEmpty()) { Serial.println("❌ HOST <name>"); return; }
    _host = h;
    Serial.printf("✅ Hostname set to '%s'\n", _host.c_str());
    applyConfig();
    return;
  }

  if (cmd.equalsIgnoreCase("SAVE")) { saveToNVS(); return; }

  if (cmd.equalsIgnoreCase("LOAD")) {
    if (loadFromNVS()) {
      Serial.println("Reconnecting with loaded settings…");
      applyConfig();
    } else {
      Serial.println("⚠️  No saved settings found.");
    }
    return;
  }

  if (cmd.equalsIgnoreCase("WIPE")) { wipeNVS(); return; }

  if (cmd.equalsIgnoreCase("WIPEWIFI")) { wipeWiFiDriverNVS(); return; }

  if (cmd.equalsIgnoreCase("SHOW")) { showConfig(); return; }

  if (cmd.equalsIgnoreCase("REBOOT")) {
    Serial.println("🔄 Rebooting…");
    delay(200);
    ESP.restart();
    return;
  }

  // ------------------------------------------------------------
  // TCP / SEND  —  send message to PC Hub or peers
  // ------------------------------------------------------------
  if (cmd.startsWith("TCP ") || cmd.startsWith("SEND ")) {

    if (!_net) {
      Serial.println("⚠️ No TinZrConnect attached (call attachNet()).");
      return;
    }

    String msg;
    if (cmd.startsWith("TCP "))
      msg = cmd.substring(4);
    else
      msg = cmd.substring(5);

    msg.trim();
    if (msg.isEmpty()) {
      Serial.println("❌ Usage: TCP <message>");
      return;
    }

    int sent = _net->sendTCP(msg);
    Serial.printf("📤 Sent to %d peer(s): %s\n", sent, msg.c_str());
    return;
  }

  // ------------------------------------------------------------
  // UDP  —  broadcast message to hub / peers
  // ------------------------------------------------------------
  if (cmd.startsWith("UDP ")) {

    if (!_net) {
      Serial.println("⚠️ No TinZrConnect attached (call attachNet()).");
      return;
    }

    String msg = cmd.substring(4);
    msg.trim();
    if (msg.isEmpty()) {
      Serial.println("❌ Usage: UDP <message>");
      return;
    }

    _net->sendUDP(msg);
    Serial.printf("📡 UDP broadcast: %s\n", msg.c_str());
    return;
  }

  // ------------------------------------------------------------
  // Unknown command → show mini help
  // ------------------------------------------------------------
  Serial.println("❓ Unknown command.");
  printHelp(false);
}
