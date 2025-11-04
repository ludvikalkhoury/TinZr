#include "TinZrConnect.h"

// Helper macro: some cores only support beginMulticast(group, port) and no beginPacketMulticast.
// We detect by signature availability; if unsure, fall back to begin() + beginPacket().
static bool beginMulticastCompat(WiFiUDP& udp, IPAddress group, uint16_t port) {
  // Your core has: uint8_t beginMulticast(IPAddress a, uint16_t p);
  return udp.beginMulticast(group, port);
}

bool TinZrConnect::start(uint16_t tcpPort, uint16_t udpPort, IPAddress mcast) {
  if (WiFi.status() != WL_CONNECTED) return false;

  _mcast = mcast;
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
  return true;
}

void TinZrConnect::handle() {
  // Periodic HELLO (1/s) so peers can discover each other
  if (millis() - _lastHello > 1000) {
    _lastHello = millis();
    _sayHello();
  }

  _recvUDP();
  _acceptTCP();
}

void TinZrConnect::broadcast(const uint8_t* data, size_t len) {
  // No beginPacketMulticast in your core → use beginPacket(group, port)
  _udp.beginPacket(_mcast, _udpPort);
  _udp.write(data, len);
  _udp.endPacket();
}

int TinZrConnect::sendToAll(const uint8_t* data, size_t len, uint32_t timeoutMs) {
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

// ❌ Remove stray standalone declaration — it caused the '-fpermissive' error
// void TinZrConnect::onMessage(MsgHandler cb);

void TinZrConnect::_sayHello() {
  const char msg[] = "HELLO";
  _udp.beginPacket(_mcast, _udpPort);
  _udp.write((const uint8_t*)msg, sizeof(msg)-1);
  _udp.endPacket();
}

void TinZrConnect::_recvUDP() {
  int pktLen = _udp.parsePacket();
  if (pktLen <= 0) return;

  IPAddress from = _udp.remoteIP();
  uint8_t buf[512];
  if (pktLen > (int)sizeof(buf)) pktLen = sizeof(buf);
  int n = _udp.read(buf, pktLen);

  _learnPeer(from);

  if (_onMsg && n > 0) _onMsg(from, buf, (size_t)n);
}

void TinZrConnect::_acceptTCP() {
  WiFiClient client = _srv.available();
  if (!client) return;

  IPAddress from = client.remoteIP();
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
    if (_peers[i].ip == ip) { _peers[i].lastSeen = now; return; }
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
