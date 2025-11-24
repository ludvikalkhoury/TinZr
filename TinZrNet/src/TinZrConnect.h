#pragma once
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include "TinZrLink.h"

#ifndef TINZR_MAX_PEERS
#define TINZR_MAX_PEERS 64
#endif

class TinZrConsole;

class TinZrConnect : public TinZrLink {
public:
	// Reuse the generic link callback type
	using MsgHandler = TinZrLink::MsgHandler;

	// ------------------------------------------------------------------
	// Link API
	// ------------------------------------------------------------------

	// Wi-Fi specific start with explicit ports (what you already use)
	bool start(uint16_t tcpPort = 4211,
	           uint16_t udpPort = 4210,
	           IPAddress mcast = IPAddress(239, 1, 1, 1));

	// Generic link start() (used via TinZrLink pointer)
	// Falls back to defaults if ports/group not set yet.
	bool start() override {
		uint16_t tcp = _tcpPort ? _tcpPort : 4211;
		uint16_t udp = _udpPort ? _udpPort : 4210;
		IPAddress mc = (_mcast != IPAddress(0, 0, 0, 0))
		             ? _mcast
		             : IPAddress(239, 1, 1, 1);
		return start(tcp, udp, mc);
	}

	// Call regularly in loop()
	void handle() override;

	// True once we've seen a HUB-ACK from the PC hub
	bool hubReady() const { return _hubFound; }

	// Best-effort UDP multicast / broadcast
	void sendUDP(const uint8_t* data, size_t len) override;
	void sendUDP(const String& s) { sendUDP((const uint8_t*)s.c_str(), s.length()); }

	// Reliable TCP send to all known peers; returns count delivered
	int  sendTCP(const uint8_t* data, size_t len, uint32_t timeoutMs = 200) override;
	int  sendTCP(const String& s, uint32_t timeoutMs = 200) {
		return sendTCP((const uint8_t*)s.c_str(), s.length(), timeoutMs);
	}

	// Register inbound message callback (fires for UDP + TCP)
	void onMessage(MsgHandler cb) override { _onMsg = cb; }

	// ------------------------------------------------------------------
	// Wi-Fi / hub helpers
	// ------------------------------------------------------------------

	// User-set PC IP (hub)
	void setHubIP(IPAddress ip) {
		_hubIP = ip;
	}

	// Peer info
	size_t peerCount() const { return _peerCount; }

	// Manual discovery trigger (for debugging)
	void sendDiscovery() { _sendDiscovery(); }
  
	void attachConsole(TinZrConsole* c) { _console = c; }
  

private:
	// --- timing constants ---
	static constexpr uint32_t HELLO_INTERVAL_SEARCH_MS = 2000;   // before hub is found
	static constexpr uint32_t HELLO_INTERVAL_IDLE_MS   = 15000;  // after hub is found

	struct Peer {
		IPAddress ip;
		uint32_t  lastSeen;
	};

	Peer   _peers[TINZR_MAX_PEERS];
	size_t _peerCount = 0;

	WiFiUDP   _udp;
	IPAddress _mcast = IPAddress(0, 0, 0, 0);
	uint16_t  _udpPort = 0;

	WiFiServer _srv = WiFiServer(0);
	uint16_t   _tcpPort = 0;
    
	TinZrConsole* _console = nullptr;
  
	MsgHandler _onMsg = nullptr;

	IPAddress _hubIP  = IPAddress(172, 20, 10, 4);  // default PC IP
	bool      _hubFound = false;                    // have we seen HUB-ACK yet?

	uint32_t _lastHello = 0;    // last time we sent HELLO

	void _sendDiscovery();   // multicast + hub + broadcast HELLO
	void _recvUDP();         // handle HUB-ACK + user UDP
	void _acceptTCP();       // accept and read TCP clients
	void _learnPeer(IPAddress ip);
};
