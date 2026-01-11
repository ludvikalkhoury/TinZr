#pragma once

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>

// ============================================================
// TinZrWiFiState
// ============================================================

enum class TinZrWiFiState : uint8_t {
	OFF = 0,
	CONNECTING,
	CONNECTED,
	FAILED
};

// ============================================================
// TinZrWiFiConfig
// - Preserves your existing WiFi connection config
// - Adds OPTIONAL multicast discovery + TCP DPU framing settings
// ============================================================

struct TinZrWiFiConfig {
	// ---- STA credentials ----
	const char* ssid              = nullptr;
	const char* pass              = nullptr;
	const char* hostname          = "tinzr";
	bool        use_static        = false;

	// Optional static configuration (only used if use_static=true)
	IPAddress   static_ip         = IPAddress(192,168,1,40);
	IPAddress   gateway           = IPAddress(192,168,1,1);
	IPAddress   subnet            = IPAddress(255,255,255,0);
	IPAddress   dns1              = IPAddress(8,8,8,8);
	IPAddress   dns2              = IPAddress(8,8,4,4);

	// Behavior
	bool        auto_reconnect    = true;
	uint32_t    reconnect_ms      = 3000;   // retry interval
	uint8_t     max_retries       = 0;      // 0 = infinite

	// RF tuning
	wifi_power_t tx_power         = WIFI_POWER_13dBm; // this is a weaker, yet, safer options in case the 13 dBm gives a hard time: WIFI_POWER_8_5dBm;

	// If true, force WiFi.config() even for DHCP path (some networks behave better)
	bool        force_dhcp_config = true;

	// ---- OPTIONAL: multicast discovery ----
	bool        mcast_enable      = false;
	IPAddress   mcast_group       = IPAddress(239,1,1,1);
	uint16_t    mcast_port        = 4210;
	uint32_t    mcast_hello_ms    = 2000;

	// ---- Unicast UDP hub (optional) ----
	uint16_t    udp_port          = 4210;
	
	// ---- TCP + DPU hub link ----
	bool        tcp_enable        = false;
	IPAddress   hub_ip            = IPAddress(0,0,0,0); // 0.0.0.0 = discover via mcast
	uint16_t    tcp_port          = 4211;
	uint32_t    tcp_connect_timeout_ms = 1500;
	uint32_t    tcp_io_timeout_ms      = 20;
};

// ============================================================
// TinZrWiFiDPU
// ------------------------------------------------------------
// Robust framing for sending/receiving messages over a TCP
// byte-stream.
//
// Frame format (little-endian):
//   magic[2]   = 'T','Z'
//   ver        = 1
//   flags      = bit0: CRC32 present
//   type       = uint16_t (app-defined)
//   length     = uint16_t payload length in bytes
//   payload    = [length]
//   crc32      = uint32_t (optional)
// ============================================================

class TinZrWiFiDPU {
public:
	static constexpr uint8_t  kVer = 1;
	static constexpr uint16_t kMagic = 0x5A54; // 'T''Z' in LE

	enum : uint8_t {
		FLAG_CRC32 = 0x01,
	};

	struct Header {
		uint16_t magic;   // 0x5A54
		uint8_t  ver;     // 1
		uint8_t  flags;   // FLAG_*
		uint16_t type;    // app-defined
		uint16_t length;  // payload length
	};

	// Writes a full frame into dst. Returns bytes written, or 0 on failure.
	static size_t encode(
		uint8_t* dst,
		size_t dst_cap,
		uint16_t type,
		const uint8_t* payload,
		size_t payload_len,
		bool add_crc32 = false
	);

	// ---- incremental decode ----
	TinZrWiFiDPU(size_t rx_capacity = 2048);
	~TinZrWiFiDPU();

	// Feed raw bytes from TCP into the internal RX buffer.
	// Returns how many bytes were accepted.
	size_t feed(const uint8_t* data, size_t len);

	// Try to extract one complete frame.
	// If available, returns true and provides type/payload/len.
	bool next(uint16_t& out_type, const uint8_t*& out_payload, size_t& out_len);

	// After next(), call consume() to drop it from the buffer.
	void consume();

	// Reset parser and discard buffered bytes.
	void reset();

	size_t buffered() const { return _len; }

private:
	uint8_t* _buf = nullptr;
	size_t   _cap = 0;
	size_t   _len = 0;

	bool     _has_frame = false;
	Header   _hdr{};
	size_t   _frame_total = 0;
	const uint8_t* _payload_ptr = nullptr;

	static uint32_t crc32(const uint8_t* data, size_t len);
	bool _tryParse();
	bool _resync();
};

// ============================================================
// TinZrWiFiTCP
// ------------------------------------------------------------
// Thin TCP client wrapper with optional DPU framing.
// - Maintains a connection to hub IP:port
// - Incrementally parses incoming DPU frames
// ============================================================

class TinZrWiFiTCP {
public:
	using FrameHandler = void (*)(IPAddress from, uint16_t type, const uint8_t* payload, size_t len);

	TinZrWiFiTCP();

	void setHub(IPAddress hub_ip, uint16_t hub_port);
	void setTimeout(uint32_t connect_timeout_ms, uint32_t io_timeout_ms);

	bool connect();
	void disconnect();
	bool connected() { return _client.connected(); }

	// Call this frequently from loop()
	void handle();

	// Raw send (no framing)
	int sendRaw(const uint8_t* data, size_t len);

	// DPU send (framed)
	int sendDPU(uint16_t type, const uint8_t* payload, size_t len, bool crc32 = false);

	void onFrame(FrameHandler cb) { _cb = cb; }

	IPAddress hubIP() const { return _hubIP; }
	uint16_t  hubPort() const { return _hubPort; }

private:
	IPAddress  _hubIP{0,0,0,0};
	uint16_t   _hubPort = 0;

	uint32_t   _connectTimeoutMs = 500;
	uint32_t   _ioTimeoutMs = 20;

	WiFiClient _client;
	TinZrWiFiDPU _dpu;
	FrameHandler _cb = nullptr;

	uint8_t _tmp[512];
};

// ============================================================
// TinZrWiFiMcast
// ------------------------------------------------------------
// UDP multicast discovery:
// - listen on mcast group:port
// - periodically announce "HELLO" (optionally with a name)
// - capture hub IP from first meaningful response
// ============================================================

class TinZrWiFiMcast {
public:
	using UdpHandler = void (*)(IPAddress from, const uint8_t* data, size_t len);

	TinZrWiFiMcast();

	bool begin(IPAddress mcast_group, uint16_t udp_port);
	void end();
	bool started() const { return _started; }

	void setName(const char* name);
	void setHelloInterval(uint32_t ms) { _helloIntervalMs = ms; }

	void handle();
	void sendHello();
	void sendUDP(const uint8_t* data, size_t len);
	
	bool helloAcked() const { return _helloAcked; }
	void clearHelloAck() { _helloAcked = false; }


	bool hasHub() const { return _hubIP != IPAddress(0,0,0,0); }
	IPAddress hubIP() const { return _hubIP; }
	void setHubIP(IPAddress ip) { _hubIP = ip; }

	void onUDP(UdpHandler cb) { _cb = cb; }

	IPAddress group() const { return _group; }
	uint16_t  port() const { return _port; }

private:
	bool      _started = false;
	IPAddress _group{239,1,1,1};
	uint16_t  _port = 0;
	WiFiUDP   _udp;

	IPAddress _hubIP{0,0,0,0};
	String    _name;

	uint32_t  _lastHelloMs = 0;
	uint32_t  _helloIntervalMs = 2000;
	
	bool _helloAcked = false;
	
	UdpHandler _cb = nullptr;

	void _handleUDP();
	void _maybeCaptureHub(IPAddress from, const uint8_t* data, size_t len);
};

// ============================================================
// TinZrWiFiConnect (your existing WiFi manager)
// ------------------------------------------------------------
// Now also *optionally* owns:
// - multicast discovery
// - TCP DPU link
// ============================================================

class TinZrWiFiConnect {
public:
	TinZrWiFiConnect() = default;

	void begin(const TinZrWiFiConfig& cfg);
	bool connect(uint32_t timeout_ms = 15000);
	void disconnect(bool wipe_driver_nvs = false);

	void handle();

	bool ready() const { return _state == TinZrWiFiState::CONNECTED; }
	bool connected() const { return WiFi.status() == WL_CONNECTED; }

	TinZrWiFiState state() const { return _state; }
	IPAddress ip() const { return WiFi.localIP(); }
	int8_t rssi() const { return WiFi.RSSI(); }

	const char* hostname() const { return _cfg.hostname; }

	// ---- new: access transport helpers ----
	TinZrWiFiMcast& mcast() { return _mcast; }
	TinZrWiFiTCP&   tcp()   { return _tcp; }
	const TinZrWiFiMcast& mcast() const { return _mcast; }
	const TinZrWiFiTCP&   tcp()   const { return _tcp; }

private:
	TinZrWiFiConfig _cfg{};
	TinZrWiFiState  _state = TinZrWiFiState::OFF;

	uint32_t _last_attempt_ms = 0;
	uint8_t  _retries = 0;

	TinZrWiFiMcast _mcast;
	TinZrWiFiTCP   _tcp;

	void _startConnect();
	void _applyConfig();
	void _applyLed(bool force = false);
};

extern TinZrWiFiConnect TinZrWiFi;
