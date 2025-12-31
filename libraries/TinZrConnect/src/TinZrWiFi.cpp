#include "TinZrWiFi.h"
#include "TinZrCore.h"
#include "TinZrLED.h"

// Global instance
TinZrWiFiConnect TinZrWiFi;

// ============================================================
// LED mapping (same logic you already had)
// ============================================================

static TinZrStatusLED::Mode _wifiLedModeFromState(TinZrWiFiState s) {
	switch (s) {
		case TinZrWiFiState::OFF:
			return TinZrStatusLED::Mode::OFF;

		case TinZrWiFiState::CONNECTING:
			return TinZrStatusLED::Mode::SUCCESS_STROBE;  // flashing green

		case TinZrWiFiState::CONNECTED:
			return TinZrStatusLED::Mode::SUCCESS_STEADY;  // solid green

		case TinZrWiFiState::FAILED:
		default:
			return TinZrStatusLED::Mode::FAIL_BLINK;      // flashing red
	}
}

static const char* _wifiStateName(TinZrWiFiState s) {
	switch (s) {
		case TinZrWiFiState::OFF:        return "OFF";
		case TinZrWiFiState::CONNECTING: return "CONNECTING";
		case TinZrWiFiState::CONNECTED:  return "CONNECTED";
		case TinZrWiFiState::FAILED:     return "FAILED";
		default:                         return "UNKNOWN";
	}
}

void TinZrWiFiConnect::_applyLed(bool force) {
	static TinZrStatusLED::Mode last = TinZrStatusLED::Mode::OFF;
	TinZrStatusLED::Mode m = _wifiLedModeFromState(_state);
	if (!force && m == last) return;
	TinZrLED.setMode(m);
	last = m;
}

// ============================================================
// TinZrWiFiConnect (your existing WiFi manager) + optional transport
// ============================================================

void TinZrWiFiConnect::begin(const TinZrWiFiConfig& cfg) {
	_cfg = cfg;
	
	Serial.println();
	Serial.println("=== TinZr WiFi ===");
	Serial.print("SSID: ");
	Serial.println(_cfg.ssid ? _cfg.ssid : "(null)");
	Serial.print("Hostname: ");
	Serial.println(_cfg.hostname ? _cfg.hostname : "(null)");
	Serial.print("Static IP: ");
	Serial.println(_cfg.use_static ? "yes" : "no");

	Serial.print("Mcast: ");
	Serial.println(_cfg.mcast_enable ? "enabled" : "disabled");
	if (_cfg.mcast_enable) {
		Serial.print("  group: ");
		Serial.print(_cfg.mcast_group);
		Serial.print(":");
		Serial.println(_cfg.mcast_port);
	}

	Serial.print("TCP: ");
	Serial.println(_cfg.tcp_enable ? "enabled" : "disabled");
	if (_cfg.tcp_enable) {
		Serial.print("  port: ");
		Serial.println(_cfg.tcp_port);
	}

	WiFi.mode(WIFI_STA);
	WiFi.setSleep(false);

	if (_cfg.hostname && _cfg.hostname[0] != '\0') {
		WiFi.setHostname(_cfg.hostname);
	}

	WiFi.setTxPower(_cfg.tx_power);
	_applyConfig();

	// Prepare helper classes (they won't start until WiFi is connected)
	_mcast.end();
	_mcast.setName(_cfg.hostname ? _cfg.hostname : "tinzr");
	_mcast.setHelloInterval(_cfg.mcast_hello_ms);
	
	_tcp.disconnect();
	_tcp.setTimeout(_cfg.tcp_connect_timeout_ms, _cfg.tcp_io_timeout_ms);

	_state = TinZrWiFiState::OFF;
	_last_attempt_ms = 0;
	_retries = 0;

	_applyLed(true);
}

void TinZrWiFiConnect::_applyConfig() {
	if (_cfg.use_static) {
		WiFi.config(_cfg.static_ip, _cfg.gateway, _cfg.subnet, _cfg.dns1, _cfg.dns2);
	} else {
		if (_cfg.force_dhcp_config) {
			WiFi.config(INADDR_NONE, INADDR_NONE, INADDR_NONE);
		}
	}
}

void TinZrWiFiConnect::_startConnect() {
	if (!_cfg.ssid || _cfg.ssid[0] == '\0') {
		_state = TinZrWiFiState::FAILED;
		Serial.println("WiFi: ❌ SSID not set");
		_applyLed(true);
		return;
	}

	Serial.print("WiFi: connecting to ");
	Serial.println(_cfg.ssid);

	WiFi.begin(_cfg.ssid, _cfg.pass ? _cfg.pass : "");
	_state = TinZrWiFiState::CONNECTING;
	_last_attempt_ms = millis();

	_applyLed(true);
}

bool TinZrWiFiConnect::connect(uint32_t timeout_ms) {
	_startConnect();

	uint32_t t0 = millis();
	while (millis() - t0 < timeout_ms) {

		// keep LED animations alive during blocking wait
		TinZr.handle();
		TinZrLED.handle();

		if (WiFi.status() == WL_CONNECTED) {
			_state = TinZrWiFiState::CONNECTED;
			_applyLed(true);

			Serial.print("WiFi: ✅ connected, IP=");
			Serial.println(WiFi.localIP());

			// Start multicast if enabled
			if (_cfg.mcast_enable && !_mcast.started()) {
				_mcast.begin(_cfg.mcast_group, _cfg.mcast_port);
			}

			return true;
		}
		delay(10);
	}

	_state = TinZrWiFiState::FAILED;
	_applyLed(true);
	Serial.println("WiFi: ❌ connect timeout");
	return false;
}

void TinZrWiFiConnect::disconnect(bool wipe_driver_nvs) {
	Serial.println("WiFi: disconnect");

	_tcp.disconnect();
	_mcast.end();

	WiFi.disconnect(true, wipe_driver_nvs);

	_state = TinZrWiFiState::OFF;
	_retries = 0;
	_last_attempt_ms = 0;

	_applyLed(true);
}

void TinZrWiFiConnect::handle() {
	// Keep LED animations alive even if the app forgets to call TinZr.handle()
	TinZr.handle();
	TinZrLED.handle();

	// Update state from WiFi driver
	if (WiFi.status() == WL_CONNECTED) {
		if (_state != TinZrWiFiState::CONNECTED) {
			_state = TinZrWiFiState::CONNECTED;
			Serial.print("WiFi: state -> ");
			Serial.println(_wifiStateName(_state));
			_applyLed(true);

			// Start multicast if enabled
			if (_cfg.mcast_enable && !_mcast.started()) {
				_mcast.begin(_cfg.mcast_group, _cfg.mcast_port);
			}
		}

		_retries = 0;

		// ---- NEW: transport services when connected ----
		if (_cfg.mcast_enable && _mcast.started()) {
			_mcast.handle();
		}

		if (_cfg.tcp_enable) {
			// If multicast discovered hub, bind TCP to it.
			if (_tcp.hubIP() == IPAddress(0,0,0,0) && _mcast.hasHub()) {
				_tcp.setHub(_mcast.hubIP(), _cfg.tcp_port);
				// Optional: attempt immediate connect
				_tcp.connect();
			}

			// Drive TCP receive/parse loop
			_tcp.handle();
		}

		return;
	}

	// Not connected
	if (_state == TinZrWiFiState::CONNECTED) {
		_state = TinZrWiFiState::CONNECTING;
		Serial.print("WiFi: state -> ");
		Serial.println(_wifiStateName(_state));
		_applyLed(true);

		// Ensure transport is stopped while disconnected
		_tcp.disconnect();
		_mcast.end();
	}

	if (!_cfg.auto_reconnect) {
		if (_state != TinZrWiFiState::OFF && _state != TinZrWiFiState::FAILED) {
			_state = TinZrWiFiState::FAILED;
			_applyLed(true);
		}
		return;
	}

	uint32_t now = millis();
	if (now - _last_attempt_ms < _cfg.reconnect_ms) return;

	if (_cfg.max_retries != 0 && _retries >= _cfg.max_retries) {
		if (_state != TinZrWiFiState::FAILED) {
			_state = TinZrWiFiState::FAILED;
			_applyLed(true);
		}
		return;
	}

	_retries++;
	_startConnect();
}

// ============================================================
// TinZrWiFiDPU implementation (merged)
// ============================================================

// Polynomial 0xEDB88320 (standard Ethernet/ZIP)
uint32_t TinZrWiFiDPU::crc32(const uint8_t* data, size_t len) {
	uint32_t crc = 0xFFFFFFFFu;
	for (size_t i = 0; i < len; ++i) {
		crc ^= data[i];
		for (uint8_t b = 0; b < 8; ++b) {
			uint32_t mask = -(crc & 1u);
			crc = (crc >> 1) ^ (0xEDB88320u & mask);
		}
	}
	return ~crc;
}

static inline void _wr16(uint8_t* p, uint16_t v) {
	p[0] = (uint8_t)(v & 0xFF);
	p[1] = (uint8_t)((v >> 8) & 0xFF);
}

static inline void _wr32(uint8_t* p, uint32_t v) {
	p[0] = (uint8_t)(v & 0xFF);
	p[1] = (uint8_t)((v >> 8) & 0xFF);
	p[2] = (uint8_t)((v >> 16) & 0xFF);
	p[3] = (uint8_t)((v >> 24) & 0xFF);
}

static inline uint16_t _rd16(const uint8_t* p) {
	return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static inline uint32_t _rd32(const uint8_t* p) {
	return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

size_t TinZrWiFiDPU::encode(
	uint8_t* dst,
	size_t dst_cap,
	uint16_t type,
	const uint8_t* payload,
	size_t payload_len,
	bool add_crc32
) {
	if (!dst) return 0;
	if (payload_len > 0xFFFF) return 0;

	const uint8_t flags = add_crc32 ? FLAG_CRC32 : 0;
	const size_t header_len = sizeof(Header);
	const size_t crc_len = add_crc32 ? 4 : 0;
	const size_t total = header_len + payload_len + crc_len;
	if (total > dst_cap) return 0;

	// Header
	_wr16(dst + 0, kMagic);
	dst[2] = kVer;
	dst[3] = flags;
	_wr16(dst + 4, type);
	_wr16(dst + 6, (uint16_t)payload_len);

	// Payload
	if (payload_len && payload) {
		memcpy(dst + header_len, payload, payload_len);
	}

	// CRC
	if (add_crc32) {
		const uint32_t c = crc32(dst, header_len + payload_len);
		_wr32(dst + header_len + payload_len, c);
	}

	return total;
}

TinZrWiFiDPU::TinZrWiFiDPU(size_t rx_capacity) {
	_cap = rx_capacity;
	_buf = (uint8_t*)malloc(_cap);
	_len = 0;
	reset();
}

TinZrWiFiDPU::~TinZrWiFiDPU() {
	if (_buf) free(_buf);
	_buf = nullptr;
	_cap = 0;
	_len = 0;
}

void TinZrWiFiDPU::reset() {
	_len = 0;
	_has_frame = false;
	_frame_total = 0;
	_payload_ptr = nullptr;
	memset(&_hdr, 0, sizeof(_hdr));
}

size_t TinZrWiFiDPU::feed(const uint8_t* data, size_t len) {
	if (!data || len == 0) return 0;
	if (!_buf || _cap == 0) return 0;

	if (_len + len > _cap) {
		_resync();
		if (_len >= _cap) return 0;
		len = min(len, _cap - _len);
	}

	memcpy(_buf + _len, data, len);
	_len += len;
	_tryParse();
	return len;
}

bool TinZrWiFiDPU::next(uint16_t& out_type, const uint8_t*& out_payload, size_t& out_len) {
	if (!_has_frame) {
		_tryParse();
		if (!_has_frame) return false;
	}

	out_type = _hdr.type;
	out_payload = _payload_ptr;
	out_len = _hdr.length;
	return true;
}

void TinZrWiFiDPU::consume() {
	if (!_has_frame) return;
	if (_frame_total == 0 || _frame_total > _len) {
		reset();
		return;
	}

	const size_t remain = _len - _frame_total;
	if (remain) memmove(_buf, _buf + _frame_total, remain);
	_len = remain;

	_has_frame = false;
	_frame_total = 0;
	_payload_ptr = nullptr;
	memset(&_hdr, 0, sizeof(_hdr));

	_tryParse();
}

bool TinZrWiFiDPU::_resync() {
	if (_len < 2) return false;

	size_t idx = 0;
	for (; idx + 1 < _len; ++idx) {
		if (_buf[idx] == 0x54 && _buf[idx + 1] == 0x5A) break;
	}
	if (idx == 0) return true;
	if (idx >= _len) {
		reset();
		return false;
	}

	const size_t remain = _len - idx;
	memmove(_buf, _buf + idx, remain);
	_len = remain;
	_has_frame = false;
	_frame_total = 0;
	_payload_ptr = nullptr;
	return true;
}

bool TinZrWiFiDPU::_tryParse() {
	_has_frame = false;
	_frame_total = 0;
	_payload_ptr = nullptr;

	if (_len < sizeof(Header)) return false;

	if (!(_buf[0] == 0x54 && _buf[1] == 0x5A)) {
		_resync();
		if (_len < sizeof(Header)) return false;
		if (!(_buf[0] == 0x54 && _buf[1] == 0x5A)) return false;
	}

	Header h;
	h.magic = _rd16(_buf + 0);
	h.ver   = _buf[2];
	h.flags = _buf[3];
	h.type  = _rd16(_buf + 4);
	h.length= _rd16(_buf + 6);

	if (h.magic != kMagic) {
		_resync();
		return false;
	}
	if (h.ver != kVer) {
		memmove(_buf, _buf + 1, _len - 1);
		_len -= 1;
		return false;
	}

	const bool has_crc = (h.flags & FLAG_CRC32) != 0;
	const size_t crc_len = has_crc ? 4 : 0;
	const size_t total = sizeof(Header) + (size_t)h.length + crc_len;
	if (total > _cap) {
		memmove(_buf, _buf + 2, _len > 2 ? _len - 2 : 0);
		_len = (_len > 2) ? (_len - 2) : 0;
		return false;
	}
	if (_len < total) return false;

	if (has_crc) {
		const uint32_t want = _rd32(_buf + sizeof(Header) + (size_t)h.length);
		const uint32_t got  = crc32(_buf, sizeof(Header) + (size_t)h.length);
		if (want != got) {
			memmove(_buf, _buf + 1, _len - 1);
			_len -= 1;
			return false;
		}
	}

	_hdr = h;
	_has_frame = true;
	_frame_total = total;
	_payload_ptr = _buf + sizeof(Header);
	return true;
}

// ============================================================
// TinZrWiFiTCP implementation (merged)
// ============================================================

TinZrWiFiTCP::TinZrWiFiTCP()
: _dpu(2048) {
	// no-op
}

void TinZrWiFiTCP::setHub(IPAddress hub_ip, uint16_t hub_port) {
	_hubIP = hub_ip;
	_hubPort = hub_port;
}

void TinZrWiFiTCP::setTimeout(uint32_t connect_timeout_ms, uint32_t io_timeout_ms) {
	_connectTimeoutMs = connect_timeout_ms;
	_ioTimeoutMs = io_timeout_ms;
}

bool TinZrWiFiTCP::connect() {
	if (WiFi.status() != WL_CONNECTED) return false;
	if (_hubIP == IPAddress(0,0,0,0) || _hubPort == 0) return false;
	if (_client.connected()) return true;

	_client.setTimeout(_connectTimeoutMs / 1000.0f);
	bool ok = _client.connect(_hubIP, _hubPort, _connectTimeoutMs);
	if (!ok) {
		_client.stop();
		return false;
	}
	_dpu.reset();
	return true;
}

void TinZrWiFiTCP::disconnect() {
	_client.stop();
	_dpu.reset();
}

int TinZrWiFiTCP::sendRaw(const uint8_t* data, size_t len) {
	if (!data || len == 0) return 0;
	if (!connected()) {
		if (!connect()) return 0;
	}
	size_t w = _client.write(data, len);
	_client.flush();
	return (w == len) ? 1 : 0;
}

int TinZrWiFiTCP::sendDPU(uint16_t type, const uint8_t* payload, size_t len, bool crc32) {
	if (!connected()) {
		if (!connect()) return 0;
	}

	uint8_t frame[sizeof(TinZrWiFiDPU::Header) + 1024 + 4];
	if (len > 1024) {
		return 0;
	}

	size_t n = TinZrWiFiDPU::encode(frame, sizeof(frame), type, payload, len, crc32);
	if (n == 0) return 0;

	size_t w = _client.write(frame, n);
	_client.flush();
	return (w == n) ? 1 : 0;
}

void TinZrWiFiTCP::handle() {
	if (!connected()) return;

	while (_client.available()) {
		int n = _client.read(_tmp, sizeof(_tmp));
		if (n <= 0) break;
		_dpu.feed(_tmp, (size_t)n);

		while (true) {
			uint16_t type = 0;
			const uint8_t* p = nullptr;
			size_t len = 0;
			if (!_dpu.next(type, p, len)) break;

			if (_cb) {
				_cb(_client.remoteIP(), type, p, len);
			}
			_dpu.consume();
		}
	}
}

// ============================================================
// TinZrWiFiMcast implementation (merged)
// ============================================================

TinZrWiFiMcast::TinZrWiFiMcast() {
	_name = "";
}

bool TinZrWiFiMcast::begin(IPAddress mcast_group, uint16_t udp_port) {
	_group = mcast_group;
	_port = udp_port;

	if (WiFi.status() != WL_CONNECTED) {
		Serial.println("TinZrWiFiMcast: WiFi not connected");
		_started = false;
		return false;
	}

	if (!_udp.beginMulticast(_group, _port)) {
		Serial.println("TinZrWiFiMcast: beginMulticast failed");
		_started = false;
		return false;
	}
	
	_started = true;
	_helloAcked = false;
	_lastHelloMs = 0; // force immediate HELLO on first handle()

	

	Serial.print("TinZrWiFiMcast: listening ");
	Serial.print(_group);
	Serial.print(":");
	Serial.println(_port);

	return true;
}

void TinZrWiFiMcast::end() {
	_udp.stop();
	_started = false;
	_hubIP = IPAddress(0,0,0,0);
}

void TinZrWiFiMcast::setName(const char* name) {
	if (name && *name) _name = name;
	else _name = "";
}

void TinZrWiFiMcast::handle() {
	if (!_started) return;
	_handleUDP();

	uint32_t now = millis();
	if (!_helloAcked && (now - _lastHelloMs >= _helloIntervalMs)) {
		_lastHelloMs = now;
		sendHello();
	}

}

void TinZrWiFiMcast::sendHello() {
	char buf[96];
	if (_name.length() > 0) {
		snprintf(buf, sizeof(buf), "HELLO %s", _name.c_str());
	} else {
		snprintf(buf, sizeof(buf), "HELLO");
	}
	sendUDP((const uint8_t*)buf, strlen(buf));
}

void TinZrWiFiMcast::sendUDP(const uint8_t* data, size_t len) {
	if (!_started || !data || !len) return;

	_udp.beginPacket(_group, _port);
	_udp.write(data, len);
	_udp.endPacket();

	if (_hubIP != IPAddress(0,0,0,0)) {
		_udp.beginPacket(_hubIP, _port);
		_udp.write(data, len);
		_udp.endPacket();
	}
}

void TinZrWiFiMcast::_handleUDP() {
	int pktLen = _udp.parsePacket();
	if (pktLen <= 0) return;

	IPAddress from = _udp.remoteIP();
	static uint8_t buf[512];
	int n = _udp.read(buf, min(pktLen, (int)sizeof(buf)));
	if (n <= 0) return;

	_maybeCaptureHub(from, buf, (size_t)n);

	if (_cb) {
		_cb(from, buf, (size_t)n);
	}
}

void TinZrWiFiMcast::_maybeCaptureHub(IPAddress from, const uint8_t* data, size_t len) {
	if (_hubIP != IPAddress(0,0,0,0)) return;
	if (!data || len < 2) return;

	const char* s = (const char*)data;
	if (len >= 3 && (memcmp(s, "HUB", 3) == 0)) {
		_hubIP = from;
		Serial.print("TinZrWiFiMcast: hub discovered @ ");
		Serial.println(_hubIP);
		return;
	}
	if (len >= 2 && (memcmp(s, "OK", 2) == 0)) {
		_hubIP = from;
		Serial.print("TinZrWiFiMcast: hub discovered @ ");
		Serial.println(_hubIP);
		return;
	}
	if (len >= 3 && (memcmp(s, "ACK", 3) == 0)) {
		_hubIP = from;
		_helloAcked = true;
		Serial.print("TinZrWiFiMcast: ✅ ACK from hub @ ");
		Serial.println(_hubIP);
		return;
	}

}
