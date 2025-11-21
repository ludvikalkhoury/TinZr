#include "TinZrConnect.h"

// Helper macro: some cores only support beginMulticast(group, port) and no beginPacketMulticast.
static bool beginMulticastCompat(WiFiUDP& udp, IPAddress group, uint16_t port) {
  // Your core has: uint8_t beginMulticast(IPAddress a, uint16_t p);
  return udp.beginMulticast(group, port);
}

bool TinZrConnect::start(uint16_t tcpPort, uint16_t udpPort, IPAddress mcast) {
  if (WiFi.status() != WL_CONNECTED) return false;

  _mcast   = mcast;
  _udpPort = udpPort;
  _tcpPort = tcpPort;

  // Bind UDP for multicast receive/send. If multicast bind fails, fall back to unicast UDP.
  if (!beginMulticastCompat(_udp, _mcast, _udpPort)) {
    if (!_udp.begin(_udpPort)) return false;
  }

  // Start TCP server for reliable messages
  _srv = WiFiServer(_tcpPort);
  _srv.begin();

  _lastHello = 0;
  _peerCount = 0;
  _hubFound  = false;

  return true;
}

void TinZrConnect::handle() {
  if (WiFi.status() != WL_CONNECTED) {
    // Wi-Fi lost → forget hub and timers so we re-discover later
    _hubFound  = false;
    _lastHello = 0;
    return;
  }

  uint32_t now = millis();

  // Handle incoming UDP + TCP
  _recvUDP();
  _acceptTCP();

  // Choose interval based on whether we already have a hub
  uint32_t interval = _hubFound ? HELLO_INTERVAL_IDLE_MS : HELLO_INTERVAL_SEARCH_MS;

  if (now - _lastHello >= interval) {
    _sendDiscovery();      // sends HELLO (multicast + hub IP + broadcast)
    _lastHello = now;
  }
}

void TinZrConnect::sendUDP(const uint8_t* data, size_t len) {
  if (WiFi.status() != WL_CONNECTED) {
    return;
  }

  // Send to multicast group
  _udp.beginPacket(_mcast, _udpPort);
  _udp.write(data, len);
  _udp.endPacket();

  // Also send directly to all known peers via UDP
  for (size_t i = 0; i < _peerCount; ++i) {
    _udp.beginPacket(_peers[i].ip, _udpPort);
    _udp.write(data, len);
    _udp.endPacket();
  }
}

int TinZrConnect::sendTCP(const uint8_t* data, size_t len, uint32_t timeoutMs) {
  if (WiFi.status() != WL_CONNECTED) return 0;

  int sent = 0;
  for (size_t i = 0; i < _peerCount; ++i) {
    WiFiClient c;
    if (c.connect(_peers[i].ip, _tcpPort, timeoutMs)) {
      c.write(data, len);
      c.flush();
      c.stop();
      ++sent;
    }
  }
  return sent;
}

// ---------------------------------------------------------------------------
// Discovery: send HELLO via multicast + hub IP + broadcast
// ---------------------------------------------------------------------------
void TinZrConnect::_sendDiscovery() {
  // Build "HELLO <name>"
  char msg[64];

  const char* host = WiFi.getHostname();
  if (!host || !host[0]) {
      host = "TinZr";
  }

  snprintf(msg, sizeof(msg), "HELLO %s", host);

  // 1) Multicast HELLO
  _udp.beginPacket(_mcast, _udpPort);
  _udp.write((const uint8_t*)msg, strlen(msg));
  _udp.endPacket();

  // 2) Unicast HELLO directly to your PC (hub IP)
  IPAddress hub = _hubIP;  // either user-set or default
  Serial.print("PC IP:");
  Serial.println(hub);
  _udp.beginPacket(hub, _udpPort);
  _udp.write((const uint8_t*)msg, strlen(msg));
  _udp.endPacket();

  // 3) Optional global broadcast
  IPAddress bcast(255, 255, 255, 255);
  _udp.beginPacket(bcast, _udpPort);
  _udp.write((const uint8_t*)msg, strlen(msg));
  _udp.endPacket();

  Serial.print("TinZrConnect: sent ");
  Serial.println(msg);
}



void TinZrConnect::_recvUDP() {
  int pktLen = _udp.parsePacket();
  if (pktLen <= 0) return;

  IPAddress from = _udp.remoteIP();

  // Ignore our own packets
  if (from == WiFi.localIP()) return;

  uint8_t buf[512];
  if (pktLen > (int)sizeof(buf)) pktLen = sizeof(buf);
  int n = _udp.read(buf, pktLen);
  if (n <= 0) return;

  _learnPeer(from);

  // Make safe as C-string
  if (n < (int)sizeof(buf)) {
    buf[n] = 0;
  } else {
    buf[sizeof(buf) - 1] = 0;
  }

  // -----------------------------
  // Control-plane messages
  // -----------------------------
  const char hubAck[]   = "HUB-ACK";
  const char hubQuery[] = "HUB-QUERY";

  // 🔹 HUB-ACK from hub
  if ((size_t)n == sizeof(hubAck) - 1 &&
      memcmp(buf, hubAck, sizeof(hubAck) - 1) == 0) {

    if (!_hubFound) {
      _hubIP    = from;
      _hubFound = true;
      Serial.print("✅ Hub discovered at: ");
      Serial.println(_hubIP);
    } else if (from != _hubIP) {
      Serial.print("⚠️ Hub IP changed from ");
      Serial.print(_hubIP);
      Serial.print(" to ");
      Serial.println(from);
      _hubIP = from;
    }

    // (optional) track last time we heard from hub
    // _hubLastSeen = millis();

    return;  // don't forward HUB-ACK to app callback
  }

  // 🔹 Ignore HELLO / HELLO <name> from other TinZrs (discovery beacons)
  if (n >= 5 && memcmp(buf, "HELLO", 5) == 0) {
    // Another TinZr’s discovery, not an app-level message
    return;
  }

  // 🔹 Ignore HUB-QUERY (hub trying to wake up silent nodes)
  // If you *want* to respond immediately, you could call _sendDiscovery() here.
  if ((size_t)n == sizeof(hubQuery) - 1 &&
      memcmp(buf, hubQuery, sizeof(hubQuery) - 1) == 0) {
    // Optional: actively reply when hub queries
    // _sendDiscovery();
    return;
  }

  // -----------------------------
  // Application-level payload
  // -----------------------------
  if (_onMsg && n > 0) {
    _onMsg(from, buf, (size_t)n);
  }
}





void TinZrConnect::_acceptTCP() {
  WiFiClient client = _srv.available();
  if (!client) return;

  IPAddress from = client.remoteIP();
  
  if (from == WiFi.localIP()) {
      client.stop();
      return;
  }
  
    
  _learnPeer(from);

  uint8_t buf[512];
  while (client.connected()) {
    int n = client.read(buf, sizeof(buf));
    if (n > 0) {
      if (_onMsg) _onMsg(from, buf, (size_t)n);
    } else if (n < 0) {
      break;
    } else {
      delay(1);
    }
  }
  client.stop();
}








void TinZrConnect::_learnPeer(IPAddress ip) {
  uint32_t now = millis();
  for (size_t i = 0; i < _peerCount; ++i) {
    if (_peers[i].ip == ip) {
      _peers[i].lastSeen = now;
      return;
    }
  }
  if (_peerCount < TINZR_MAX_PEERS) {
    _peers[_peerCount++] = { ip, now };
  } else {
    // Replace oldest
    size_t oldest = 0;
    for (size_t i = 1; i < _peerCount; ++i)
      if (_peers[i].lastSeen < _peers[oldest].lastSeen) oldest = i;
    _peers[oldest] = { ip, now };
  }
}
