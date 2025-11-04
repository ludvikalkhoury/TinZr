#include "TinZrConsole.h"

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

  // defaults (RAM)
  _ssid = def.ssid ? def.ssid : "";
  _pass = def.pass ? def.pass : "";
  _host = def.hostname ? def.hostname : "tinzr";
  _use_static = def.use_static;

  // load persisted values, if present
  if (loadFromNVS()) {
    Serial.println("Auto-loaded saved settings.");
  }

  // show help
  printHelp(false);

  // bring up OTA with current RAM settings
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
  bool ok = _prefs.isKey("ssid") && _prefs.isKey("pass") && _prefs.isKey("host");
  if (ok) {
    _ssid       = _prefs.getString("ssid", _ssid);
    _pass       = _prefs.getString("pass", _pass);
    _use_static = _prefs.getUChar("use_static", 0) != 0;
    _host       = _prefs.getString("host", _host);
  }
  _prefs.end();
  if (ok) Serial.println("📥 Loaded settings from NVS.");
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
  Serial.println("  SAVE | LOAD | WIPE | WIPEWIFI | SHOW | REBOOT\n");
}

void TinZrConsole::handleSerial() {
  if (!Serial.available()) return;

  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  if (cmd.isEmpty()) return;

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

  Serial.println("❓ Unknown command.");
  printHelp(false);
}
