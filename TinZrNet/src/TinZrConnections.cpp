#include "TinZrConnections.h"
#include "TinZrCore.h"
#include "TinZrLED.h"           // ← add this

extern TinZrStatusLED* gStatusLED;  // ← we defined this in TinZrNode.cpp

// ========== TinZrWiFi ==========
bool TinZrWiFi::connect(uint32_t timeout_ms, TickCallback tick) {
    WiFi.persistent(false);
    esp_wifi_set_storage(WIFI_STORAGE_RAM);
    WiFi.setAutoReconnect(true);
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.setTxPower(_cfg.tx_power);
    WiFi.disconnect(true, true);
    delay(300);

    if (_cfg.hostname && _cfg.hostname[0]) {
        WiFi.setHostname(_cfg.hostname);
    }

    if (_cfg.use_static) {
        Serial.println("🧭 Static IP requested…");
        if (!WiFi.config(_cfg.ip, _cfg.gw, _cfg.mask, _cfg.dns1, _cfg.dns2)) {
            Serial.println("⚠️  WiFi.config() failed → DHCP fallback");
        }
    } else if (_cfg.force_dhcp_config) {
        Serial.println("📱 DHCP mode");
        WiFi.config(INADDR_NONE, INADDR_NONE, INADDR_NONE);
    } else {
        Serial.println("📱 DHCP mode");
    }

    Serial.printf("📶 Connecting to SSID \"%s\"…\n", _cfg.ssid);

    // 🔹 While we are trying to connect → blink green
    if (gStatusLED) {
        gStatusLED->setMode(TinZrStatusLED::Mode::WIFI_SEARCH);
    }

    WiFi.begin(_cfg.ssid, _cfg.pass);

    uint32_t start = millis();
    while (WiFi.status() != WL_CONNECTED &&
           (millis() - start) < timeout_ms) {
        delay(50);

        // Step the status LED during the blocking wait
        if (gStatusLED) {
            gStatusLED->handle();
        }

        if (tick) tick();
    }

    if (WiFi.status() == WL_CONNECTED) {
        Serial.println("✅ Wi-Fi connected!");
        Serial.print("📡 IP: ");
        Serial.println(WiFi.localIP());

        // 🔹 Connected → solid green
        if (gStatusLED) {
            gStatusLED->setMode(TinZrStatusLED::Mode::WIFI_OK);
        }

        return true;
    } else {
        Serial.println("❌ Wi-Fi connect timeout");

        // 🔹 Timeout / failure → blink red
        if (gStatusLED) {
            gStatusLED->setMode(TinZrStatusLED::Mode::WIFI_FAIL);
        }

        return false;
    }
}



// ========== TinZrConnect ==========
static const uint32_t HELLO_INTERVAL_MS      = 2000;
static const uint32_t TCP_CONNECT_TIMEOUT_MS = 500;

TinZrConnect::TinZrConnect()
: _hubMcastGrp(239,1,1,1),
  _hubIP(0,0,0,0) {}


void TinZrConnect::setName(const char* name) {
    if (name && name[0]) {
        _name = name;
    } else {
        _name = "";
    }
}


bool TinZrConnect::start(uint16_t hubTcpPort, uint16_t hubUdpPort, IPAddress hubMcastGrp) {
    _hubTcpPort  = hubTcpPort;
    _hubUdpPort  = hubUdpPort;
    _hubMcastGrp = hubMcastGrp;

    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("TinZrConnect: Wi-Fi not connected; cannot start");
        _started = false;
        return false;
    }

    if (!_udp.beginMulticast(_hubMcastGrp, _hubUdpPort)) {
        Serial.println("TinZrConnect: UDP multicast begin failed");
        _started = false;
        return false;
    }

    Serial.print("TinZrConnect: listening UDP mcast ");
    Serial.print(_hubMcastGrp);
    Serial.print(":");
    Serial.println(_hubUdpPort);

    _started   = true;
    _lastHello = millis();
    return true;
}

void TinZrConnect::handle() {
    if (!_started) return;
    _handleUDP();
    _handleTCP();

    uint32_t now = millis();
    if (now - _lastHello >= HELLO_INTERVAL_MS) {
        _lastHello = now;
        sendDiscovery();
    }
}

void TinZrConnect::_handleUDP() {
    int pktLen = _udp.parsePacket();
    if (pktLen <= 0) return;

    IPAddress from = _udp.remoteIP();
    std::vector<uint8_t> buf(pktLen);
    int n = _udp.read(buf.data(), pktLen);
    if (n <= 0) return;

    if (_cb) {
        _cb(from, buf.data(), (size_t)n);
    }
}

void TinZrConnect::_handleTCP() {
    if (_tcpClient && _tcpClient.available()) {
        uint8_t buf[256];
        int n = _tcpClient.read(buf, sizeof(buf));
        if (n > 0 && _cb) {
            _cb(_tcpClient.remoteIP(), buf, (size_t)n);
        }
    }
}

void TinZrConnect::sendUDP(const uint8_t* data, size_t len) {
    if (!_started) return;

    _udp.beginPacket(_hubMcastGrp, _hubUdpPort);
    _udp.write(data, len);
    _udp.endPacket();


    if (_hubIP != IPAddress(0,0,0,0)) {
        _udp.beginPacket(_hubIP, _hubUdpPort);
        _udp.write(data, len);
        _udp.endPacket();
    }
}

int TinZrConnect::sendTCP(const uint8_t* data, size_t len, uint32_t timeoutMs) {
    if (!_started) return 0;
    if (_hubIP == IPAddress(0,0,0,0)) {
        sendUDP(data, len);
        return 0;
    }

    if (!_tcpClient.connected()) {
        if (!_tcpClient.connect(_hubIP, _hubTcpPort, timeoutMs)) {
            Serial.println("TinZrConnect: TCP connect failed");
            return 0;
        }
    }

    size_t written = _tcpClient.write(data, len);
    _tcpClient.flush();
    return (written > 0) ? 1 : 0;
}

void TinZrConnect::sendDiscovery() {
    char buf[64];

    if (_name.length() > 0) {
        // HELLO TinZrNodeBLE1
        snprintf(buf, sizeof(buf), "HELLO %s", _name.c_str());
    } else {
        snprintf(buf, sizeof(buf), "HELLO");
    }

    sendUDP((const uint8_t*)buf, strlen(buf));
}


// ========== TinZrBleConnect ==========
#if TINZR_ENABLE_BLE

static const char* BLE_SERVICE_UUID = "4fafc201-1fb5-459e-8fcc-c5c9c331914b";
static const char* BLE_RX_UUID      = "beb5483e-36e1-4688-b7f5-ea07361b26a8";
static const char* BLE_TX_UUID      = "beb5483e-36e1-4688-b7f5-ea07361b26a9";


TinZrBleConnect::TinZrBleConnect() {
    _name = "TinZrBLE";
}

void TinZrBleConnect::setName(const char* name) {
    if (name && name[0]) _name = name;
}

bool TinZrBleConnect::start() {
    BLEDevice::init(_name.c_str());
    _server = BLEDevice::createServer();
    _server->setCallbacks(this);

    BLEService* service = _server->createService(BLE_SERVICE_UUID);
    _rxCharacteristic = service->createCharacteristic(
        BLE_RX_UUID,
        BLECharacteristic::PROPERTY_WRITE
    );
    _txCharacteristic = service->createCharacteristic(
        BLE_TX_UUID,
        BLECharacteristic::PROPERTY_NOTIFY
    );

    _rxCharacteristic->setCallbacks(this);
    service->start();

    BLEAdvertising* adv = BLEDevice::getAdvertising();
    adv->addServiceUUID(BLE_SERVICE_UUID);
    adv->setScanResponse(true);
    adv->start();
    return true;
}

void TinZrBleConnect::handle() {
    // nothing; BLE is callback-driven
}

void TinZrBleConnect::sendUDP(const uint8_t* data, size_t len) {
    if (!_connected || !_txCharacteristic) return;
    _txCharacteristic->setValue((uint8_t*)data, (int)len);
    _txCharacteristic->notify();
}

int TinZrBleConnect::sendTCP(const uint8_t* data, size_t len, uint32_t) {
    sendUDP(data, len);
    return _connected ? 1 : 0;
}

void TinZrBleConnect::onConnect(BLEServer* server) {
    _connected = true;
}

void TinZrBleConnect::onDisconnect(BLEServer* server) {
    _connected = false;
    BLEDevice::startAdvertising();
}

void TinZrBleConnect::onWrite(BLECharacteristic* ch) {
    if (!_cb) return;

    String v = ch->getValue();
    if (v.length() == 0) return;   // or v.isEmpty()

    IPAddress from(0,0,0,0);
    _cb(from, (const uint8_t*)v.c_str(), v.length());
}

#endif // TINZR_ENABLE_BLE

// ========== TinZrOTA ==========
// ========== TinZrOTA ==========
#if TINZR_ENABLE_OTA

TinZrOTA* TinZrOTA::_self = nullptr;

void TinZrOTA::begin(const char* hostname, const TinZrCfg& cfg) {
  _cfg      = cfg;
  _hostname = (hostname && hostname[0]) ? hostname : String("tinzr");
  _self     = this;

  Serial.println("\n🚀 TinZrOTA boot");

  // We DO NOT touch Wi-Fi here. It must already be up or be brought up by TinZrNode/Console.
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("⚠️ OTA: Wi-Fi not connected yet; OTA will start when Wi-Fi is ready");
    _started = false;
    return;
  }

  setupOTA();
  _started = true;

  Serial.print("🖥️ OTA hostname: ");
  Serial.println(_hostname);
}

void TinZrOTA::handle() {
  // If not started yet, check if Wi-Fi became ready
  if (!_started) {
    if (WiFi.status() == WL_CONNECTED) {
      Serial.println("🌐 OTA: Wi-Fi is now connected → starting OTA");
      setupOTA();
      _started = true;
    }
    return;
  }

  // Normal OTA handling
  ArduinoOTA.handle();
}

void TinZrOTA::setupOTA() {
  ArduinoOTA.setHostname(_hostname.c_str());
  ArduinoOTA.setPort(_cfg.ota_port);

  if (_cfg.ota_password && _cfg.ota_password[0]) {
    ArduinoOTA.setPassword(_cfg.ota_password);
  }

  ArduinoOTA.onStart([]() {
    Serial.println("\nOTA start");
  });

  ArduinoOTA.onEnd([]() {
    Serial.println("\nOTA end");
  });

  ArduinoOTA.onError([](ota_error_t e) {
    Serial.printf("OTA error %u\n", e);
  });

  ArduinoOTA.begin();
  Serial.println("🌐 OTA ready");
}

#endif // TINZR_ENABLE_OTA


// ========== TinZrHubCommands ==========
TinZrHubCommands* TinZrHubCommands::_self = nullptr;

TinZrHubCommands::TinZrHubCommands(TinZrCore* core, TinZrLink* net)
: _core(core), _net(net) {
    _self = this;
    if (_net) {
        _net->onMessage(&TinZrHubCommands::netCallback);
    }
}

void TinZrHubCommands::netCallback(IPAddress from, const uint8_t* data, size_t len) {
    if (!_self) return;
    _self->handleNetMessage(from, data, len);
}

void TinZrHubCommands::handleNetMessage(IPAddress from, const uint8_t* data, size_t len) {
    if (!data || len == 0) return;

    String s;
    s.reserve(len+1);
    for (size_t i = 0; i < len; ++i) s += char(data[i]);
    s.trim();
    if (!s.length()) return;

    if (s.equalsIgnoreCase("OFF")) {
        _cmdOff();
    } else if (s.startsWith("LED ")) {
        _cmdLed(s);
    } else if (s.equalsIgnoreCase("PING")) {
        _cmdPing(from);
    } else if (s.equalsIgnoreCase("BAT")) {
        _cmdBattery();
    } else if (s.startsWith("DIG ")) {
        _cmdDigital(s);
    } else if (s.startsWith("ANA ")) {
        _cmdAnalog(s);
    }
}

void TinZrHubCommands::_cmdOff() {
    if (!_core) return;
    _core->ledOff();
    _curR = _curG = _curB = _curBr = 0;
}

void TinZrHubCommands::_cmdLed(const String& s) {
    if (!_core) return;
    int r=0,g=0,b=0,br=255;
    int n = sscanf(s.c_str()+4, "%d %d %d %d", &r,&g,&b,&br);
    if (n < 3) return;
    r  = constrain(r,0,255);
    g  = constrain(g,0,255);
    b  = constrain(b,0,255);
    br = constrain(br,0,255);
    _core->setLED((uint8_t)r,(uint8_t)g,(uint8_t)b,(uint8_t)br);
    _curR=(uint8_t)r; _curG=(uint8_t)g; _curB=(uint8_t)b; _curBr=(uint8_t)br;
}

void TinZrHubCommands::_cmdPing(IPAddress from) {
    (void)from;
    if (!_net) return;
    const char* msg = "PONG";
    _net->sendTCP((const uint8_t*)msg, strlen(msg));
}

void TinZrHubCommands::_cmdBattery() {
    if (!_core || !_net) return;
    float v = _core->readBatteryVoltage();
    int   p = _core->batteryPercent();
    char buf[64];
    snprintf(buf,sizeof(buf),"BAT %.3f %d",v,p);
    _net->sendTCP((const uint8_t*)buf, strlen(buf));
}

void TinZrHubCommands::_cmdDigital(const String& s) {
    int pin=-1;
    char levelStr[8]={0};
    int n = sscanf(s.c_str()+4, "%d %7s", &pin, levelStr);
    if (n < 2) return;
    int val=-1;
    if (!strcasecmp(levelStr,"HIGH") || !strcmp(levelStr,"1")) val=HIGH;
    else if (!strcasecmp(levelStr,"LOW") || !strcmp(levelStr,"0")) val=LOW;
    if (val == -1) return;
    pinMode(pin, OUTPUT);
    digitalWrite(pin, val);
}

void TinZrHubCommands::_cmdAnalog(const String& s) {
    int pin=-1, value=0;
    int n = sscanf(s.c_str()+4, "%d %d", &pin, &value);
    if (n < 2) return;
    value = constrain(value,0,255);
    analogWrite(pin, value);
}
