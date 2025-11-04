#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>

#ifndef TINZR_MAX_PEERS
#define TINZR_MAX_PEERS 64
#endif

class TinZrConnect {
public:
  using MsgHandler = void (*)(IPAddress from, const uint8_t* data, size_t len);

  // Call start() after Wi-Fi is connected
  bool start(uint16_t tcpPort = 4211,
             uint16_t udpPort = 4210,
             IPAddress mcast = IPAddress(239,0,0,222));

  void handle();                           // call in loop()

  // Best-effort UDP multicast
  void broadcast(const uint8_t* data, size_t len);
  void broadcast(const String& s) { broadcast((const uint8_t*)s.c_str(), s.length()); }

  // Reliable TCP send to all known peers; returns count delivered
  int  sendToAll(const uint8_t* data, size_t len, uint32_t timeoutMs = 200);
  int  sendToAll(const String& s, uint32_t timeoutMs = 200) { return sendToAll((const uint8_t*)s.c_str(), s.length(), timeoutMs); }

  // Register inbound message callback (fires for UDP + TCP)
  void onMessage(MsgHandler cb) { _onMsg = cb; }

  // Peer info
  size_t peerCount() const { return _peerCount; }

private:
  struct Peer { IPAddress ip; uint32_t lastSeen; };
  Peer _peers[TINZR_MAX_PEERS];
  size_t _peerCount = 0;

  WiFiUDP _udp;
  IPAddress _mcast;
  uint16_t _udpPort = 0;

  WiFiServer _srv = WiFiServer(0);
  uint16_t _tcpPort = 0;

  MsgHandler _onMsg = nullptr;
  uint32_t _lastHello = 0;

  void _sayHello();                        // multicast presence
  void _recvUDP();                         // handle discovery + datagrams
  void _acceptTCP();                       // accept and read clients
  void _learnPeer(IPAddress ip);
};
