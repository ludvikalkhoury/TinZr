#include "TinZrConsole.h"
#include "TinZrCore.h"

// ========== TinZrConsole NVS keys ==========
static const char* NVS_NS   = "tinzr_console";
static const char* KEY_SSID = "ssid";
static const char* KEY_PASS = "pass";
static const char* KEY_HOST = "host";
static const char* KEY_STAT = "static";

void TinZrConsole::begin(const TinZrConsoleDefaults& def, uint32_t connect_timeout_ms) {
    Serial.println();
    Serial.println("===== TinZr Console =====");

    if (!_prefs.begin(NVS_NS, false)) {
        Serial.println("⚠️ Preferences.begin() failed; using defaults only");
    }

    // 1) Load whatever is in NVS (if anything)
    bool hadNvs = loadFromNVS();

    if (!hadNvs) {
        // If nothing in NVS, start from some safe base
        _ssid       = "";
        _pass       = "";
        _host       = "tinzr";
        _use_static = false;
    }

    // 2) Apply overrides from firmware (TinZrNodeConfig -> TinZrConsoleDefaults)
    //    Whatever you specify in cfg will WIN over NVS.

    // SSID: override if provided and non-empty
    if (def.ssid && def.ssid[0] != '\0') {
        _ssid = def.ssid;
    }

    // PASS: allow empty string as a valid override (open network)
    if (def.pass) {
        _pass = def.pass;
    }

    // HOSTNAME: override if provided and non-empty
    if (def.hostname && def.hostname[0] != '\0') {
        _host = def.hostname;
    }

    // STATIC flag: always overridden by firmware
    _use_static = def.use_static;

    // 3) Save the resulting values back to NVS so they become the new defaults
    saveToNVS();

    showConfig();
    printHelp(true);
    applyConfig(connect_timeout_ms);
}


void TinZrConsole::handle() {
    handleSerial();
#if TINZR_ENABLE_OTA
    _ota.handle();
#endif
}

bool TinZrConsole::loadFromNVS() {
    if (!_prefs.isKey(KEY_SSID)) return false;
    _ssid       = _prefs.getString(KEY_SSID, "");
    _pass       = _prefs.getString(KEY_PASS, "");
    _host       = _prefs.getString(KEY_HOST, "tinzr");
    _use_static = _prefs.getBool(KEY_STAT, false);
    return true;
}

void TinZrConsole::saveToNVS() {
    _prefs.putString(KEY_SSID, _ssid);
    _prefs.putString(KEY_PASS, _pass);
    _prefs.putString(KEY_HOST, _host);
    _prefs.putBool(KEY_STAT, _use_static);
}

void TinZrConsole::wipeNVS() {
    _prefs.clear();
}

void TinZrConsole::wipeWiFiDriverNVS() {
    Serial.println("🧹 Wiping Wi-Fi driver NVS");
    WiFi.disconnect(true, true);
}

void TinZrConsole::applyConfig(uint32_t connect_timeout_ms)
{
    TinZrWiFiConfig wcfg;
    wcfg.ssid       = _ssid.c_str();
    wcfg.pass       = _pass.c_str();
    wcfg.use_static = _use_static;

    wcfg.tx_power          = WIFI_POWER_8_5dBm;
    wcfg.hostname          = _host.c_str();
    wcfg.force_dhcp_config = !_use_static;

    // START WIFI
    _wifi.begin(wcfg);
    _wifi.connect(connect_timeout_ms);

#if TINZR_ENABLE_OTA
    TinZrCfg ocfg;
    ocfg.ota_port     = 3232;
    ocfg.ota_password = nullptr;

    // OTA start AFTER Wi-Fi becomes ready
    _ota.begin(_host.c_str(), ocfg);
#endif
}


void TinZrConsole::showConfig() {
    Serial.println();
    Serial.println("Current Wi-Fi config:");
    Serial.print("  SSID: ");
    Serial.println(_ssid);
    Serial.print("  PASS: ");
    Serial.println(_pass.isEmpty() ? "(empty)" : "********");
    Serial.print("  HOST: ");
    Serial.println(_host);
    Serial.print("  Static IP: ");
    Serial.println(_use_static ? "yes" : "no");
}

void TinZrConsole::printHelp(bool with_header) {
    if (with_header) {
        Serial.println();
        Serial.println("Commands:");
    }
    Serial.println("  WIFI ssid pass       -> set Wi-Fi and reconnect (autosave+reboot if enabled)");
    Serial.println("  HOST name            -> set hostname");
    Serial.println("  STATIC 0|1           -> disable/enable static IP");
    Serial.println("  SHOW                 -> show current config");
    Serial.println("  NVS WIPE             -> wipe console NVS");
    Serial.println("  WIFI WIPE            -> wipe Wi-Fi driver NVS");
    Serial.println("  HELP                 -> show this help");
    Serial.println();
}

static String _serLine;

void TinZrConsole::handleSerial() {
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\r') continue;
        if (c == '\n') {
            String cmd = _serLine;
            _serLine.clear();
            cmd.trim();
            if (!cmd.isEmpty()) {
                Serial.print("> ");
                Serial.println(cmd);
                if (cmd.equalsIgnoreCase("SHOW")) {
                    showConfig();
                } else if (cmd.equalsIgnoreCase("HELP")) {
                    printHelp(false);
                } else if (cmd.startsWith("HOST ")) {
                    _host = cmd.substring(5);
                    _host.trim();
                    saveToNVS();
                    Serial.print("Hostname set to: ");
                    Serial.println(_host);
                } else if (cmd.startsWith("STATIC ")) {
                    int v = cmd.substring(7).toInt();
                    _use_static = (v != 0);
                    saveToNVS();
                    Serial.print("Static IP flag set to: ");
                    Serial.println(_use_static ? "1" : "0");
                } else if (cmd.startsWith("WIFI ")) {
                    int sp = cmd.indexOf(' ', 5);
                    if (sp > 5) {
                        _ssid = cmd.substring(5, sp);
                        _pass = cmd.substring(sp + 1);
                        _ssid.trim();
                        _pass.trim();
                        saveToNVS();
                        Serial.println("Wi-Fi updated, applying config…");
                        applyConfig(15000);
                        if (_autosave_wifi) {
                            Serial.println("Autosave ON → rebooting in 1s…");
                            delay(1000);
                            ESP.restart();
                        }
                    }
                } else if (cmd.equalsIgnoreCase("NVS WIPE")) {
                    wipeNVS();
                    Serial.println("Console NVS wiped.");
                } else if (cmd.equalsIgnoreCase("WIFI WIPE")) {
                    wipeWiFiDriverNVS();
                }
            }
        } else {
            _serLine += c;
        }
    }
}
