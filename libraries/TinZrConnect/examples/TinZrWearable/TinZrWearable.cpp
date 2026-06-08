// =============================================================
// TinZrWearable.cpp  (FULL FILE)
// =============================================================
#include "TinZrWearable.h"
#include <string.h>
#include "TinZrLED.h"

#include "spo2_algorithm.h"  // Maxim HR/SpO2 algorithm

// Static self pointer for BLE callback trampoline
TinZrWearableClass* TinZrWearableClass::_self = nullptr;

TinZrWearableClass TinZrWearable;

// ===== Packed binary frames for BLE =====
static const uint8_t FRAMES_PER_PACKET = 7;
static const uint16_t FRAME_SYNC_WORD = 0xA55A;
static const uint8_t BLE_PACKET_MAGIC0 = 0xA5;
static const uint8_t BLE_PACKET_MAGIC1 = 0x5A;
static const uint8_t BLE_PACKET_TYPE_WEARABLE = 0x01;
static const uint8_t BLE_PACKET_VERSION = 0x01;
static const uint8_t BLE_HEADER_BYTES = 13;
static const uint8_t BLE_CRC_BYTES = 2;
static constexpr float GRAVITY_MPS2 = 9.80665f;

// Scaling factors (match Python viewer)
static constexpr float ACC_SCALE = 1000.0f;  // accel: m/s^2 → milli-units (viewer expects *1000)
static constexpr float GYR_SCALE = 100.0f;   // gyro:  dps → centi-units  (viewer expects *100)
static constexpr float G_SENS_DPS_PER_LSB = 35e-3f; // 35 mdps/LSB for ±1000 dps
static constexpr float PPG_ADC_FULL_SCALE_NA = 16384.0f;
static constexpr float PPG_ADC_MAX_COUNT = 262143.0f;

// --- HR / SpO2 + battery in frame ---
struct __attribute__((packed)) WearFrame {
	uint16_t sync_word;      // fixed marker so the GUI can re-lock to frame boundaries
	uint32_t sample_idx;     // monotonically increasing sample counter since START
	int16_t  ax, ay, az;   // accel * ACC_SCALE
	int16_t  gx, gy, gz;   // gyro  * GYR_SCALE
	float    red_nA;       // approximate PPG red photodiode current
	float    ir_nA;        // approximate PPG IR photodiode current
	uint8_t  hr_bpm;       // last computed heart rate
	uint8_t  spo2_pct;     // last computed SpO2
	uint8_t  batt_pct;     // last battery % (0–100)
};

static WearFrame sFrameBuf[FRAMES_PER_PACKET];
static uint8_t   sFrameCount = 0;
static uint32_t  sSampleCounter = 0;
static uint32_t  sBlePacketCounter = 0;
static uint8_t   sBlePacketBuf[BLE_HEADER_BYTES + (FRAMES_PER_PACKET * sizeof(WearFrame)) + BLE_CRC_BYTES];

// Optional IR threshold if you want to gate on "finger present"
static const uint32_t IR_THRESHOLD = 30000;

static uint16_t crc16_ccitt(const uint8_t* data, size_t len) {
	uint16_t crc = 0xFFFF;
	for (size_t i = 0; i < len; ++i) {
		crc ^= (uint16_t)data[i] << 8;
		for (uint8_t bit = 0; bit < 8; ++bit) {
			if (crc & 0x8000) crc = (uint16_t)((crc << 1) ^ 0x1021);
			else crc <<= 1;
		}
	}
	return crc;
}

static size_t build_wearable_packet(uint8_t* dst, const WearFrame* frames, uint8_t frameCount, uint32_t timestampMs) {
	if (!dst || !frames || frameCount == 0 || frameCount > FRAMES_PER_PACKET) return 0;

	uint32_t packetCounter = sBlePacketCounter++;
	dst[0] = BLE_PACKET_MAGIC0;
	dst[1] = BLE_PACKET_MAGIC1;
	dst[2] = BLE_PACKET_TYPE_WEARABLE;
	dst[3] = BLE_PACKET_VERSION;
	dst[4] = (uint8_t)((packetCounter >> 24) & 0xFF);
	dst[5] = (uint8_t)((packetCounter >> 16) & 0xFF);
	dst[6] = (uint8_t)((packetCounter >>  8) & 0xFF);
	dst[7] = (uint8_t)(packetCounter & 0xFF);
	dst[8] = (uint8_t)((timestampMs >> 24) & 0xFF);
	dst[9] = (uint8_t)((timestampMs >> 16) & 0xFF);
	dst[10] = (uint8_t)((timestampMs >>  8) & 0xFF);
	dst[11] = (uint8_t)(timestampMs & 0xFF);
	dst[12] = frameCount;

	size_t offset = BLE_HEADER_BYTES;
	const size_t payloadSize = sizeof(WearFrame) * frameCount;
	memcpy(dst + offset, frames, payloadSize);
	offset += payloadSize;

	uint16_t crc = crc16_ccitt(dst, offset);
	dst[offset++] = (uint8_t)((crc >> 8) & 0xFF);
	dst[offset++] = (uint8_t)(crc & 0xFF);
	return offset;
}

static void send_wearable_packet(TinZrBLEConnect& ble, uint8_t frameCount, uint32_t timestampMs) {
	if (frameCount == 0) return;
	size_t packetSize = build_wearable_packet(sBlePacketBuf, sFrameBuf, frameCount, timestampMs);
	if (packetSize > 0) {
		ble.sendNotify(sBlePacketBuf, packetSize);
	}
}

// ====== HR / SpO2 state, updated every 5 s ======
static uint16_t      sLastHr             = 0;
static uint16_t      sLastSpo2           = 0;
static unsigned long sLastHrSpo2UpdateMs = 0;
static const unsigned long HR_SPO2_UPDATE_INTERVAL_MS = 5000UL; // 5 seconds

// ====== Battery state, updated every 5 min (or on demand) ======
static uint16_t      sLastBattPct      = 0;
static unsigned long sLastBattSampleMs = 0;
static const unsigned long BATT_PERIOD_MS = 5UL * 60UL * 1000UL; // 5 minutes

// Read battery % from TinZrCore and clamp 0..100
static uint16_t read_battery_pct() {
	int pct = TinZr.readBatteryPercent();
	if (pct < 0)   pct = 0;
	if (pct > 100) pct = 100;
	return static_cast<uint16_t>(pct);
}

// OPTIONAL: call this from any "battery query" command handler you already have
void TinZrWearableClass::forceBatteryUpdate() {
	// force a refresh on the next _handleStreaming() tick
	sLastBattSampleMs = 0;
}

// -------- HR / SpO2 computation using Maxim algorithm --------
// Uses a sliding buffer of 100 samples (BUFFER_SIZE in spo2_algorithm.h)
static void update_hr_spo2_from_ppg(uint32_t red, uint32_t ir) {
	const int N_SAMPLES = BUFFER_SIZE;
	static uint32_t ir_buf[N_SAMPLES];
	static uint32_t red_buf[N_SAMPLES];
	static int idx25         = 0;
	static int decim_counter = 0;

	// ---- 1) Decimate 250 Hz → 25 Hz ----
	decim_counter++;
	if (decim_counter < 10) return;
	decim_counter = 0;

	// ---- 2) If no finger → reset algorithm state ----
	if (ir < IR_THRESHOLD) {
		idx25     = 0;
		sLastHr   = 0;
		sLastSpo2 = 0;
		return;
	}

	// ---- 3) Store decimated sample ----
	ir_buf[idx25]  = ir;
	red_buf[idx25] = red;
	idx25++;

	if (idx25 < N_SAMPLES) return;
	idx25 = 0;

	int32_t spo2;
	int8_t  spo2_valid;
	int32_t hr;
	int8_t  hr_valid;

	maxim_heart_rate_and_oxygen_saturation(
		ir_buf, N_SAMPLES,
		red_buf,
		&spo2, &spo2_valid,
		&hr,   &hr_valid
	);

	if (hr_valid && hr > 20 && hr < 230) sLastHr = hr;
	if (spo2_valid && spo2 >= 60 && spo2 <= 100) sLastSpo2 = spo2;
}

// ---------------- TinZrWearable ------------------
TinZrWearableClass::TinZrWearableClass() {}

void TinZrWearableClass::begin(const TinZrWearableConfig& cfg) {
	_cfg = cfg;

	Serial.begin(115200);
	delay(200);

	Serial.println();
	Serial.println("===== TinZrWearable (BLE GATT, TinZrCore sensors) =====");

	// Core (soft power, battery, button, base LED, etc.)
	TinZr.begin();
	// NOTE: TinZr.begin() already initializes TinZrLED.
	_wasSoftOn = TinZr.isSoftOn();

	// ---------- BLE ----------
#if TINZR_ENABLE_BLE
	{
		const char* name =
			(_cfg.hostname && _cfg.hostname[0] != '\0')
				? _cfg.hostname
				: "TinZrWearable";

		Serial.print("BLE name: ");
		Serial.println(name);

		TinZrBLEConfig bcfg;
		bcfg.device_name = name;
		// Prefer larger MTU when possible; notify() will still chunk if needed.
		bcfg.preferred_mtu = 247;
		bcfg.max_notify_chunk = 244;

		_bleStarted = _ble.begin(bcfg);
		if (!_bleStarted) {
			Serial.println("❌ TinZrWearable: BLE start failed");
			TinZrLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
		} else {
			Serial.println("🔵 BLE started (advertising)");

			_self = this;
			_ble.onWrite(&TinZrWearableClass::_bleWriteStatic);

			_bleWasConnected = _ble.connected();
			TinZrLED.setMode(TinZrStatusLED::Mode::BLE_ADVERTISING);
		}
	}
#else
	Serial.println("🚫 BLE disabled at compile time");
#endif

	// ---------- Sensors (via TinZrCore) ----------
	Serial.println("🔧 Initializing sensors via TinZrCore...");
	bool ok = TinZr.sensorsBegin();
	_imuReady = TinZr.imuReady();
	_ppgReady = TinZr.ppgReady();
	_sensorsReady = _imuReady;

	if (!ok || !_imuReady) {
		Serial.println("❌ IMU NOT found → cannot stream");
	} else if (_ppgReady) {
		Serial.println("✅ Sensors ready (IMU + PPG)");
	} else {
		Serial.println("⚠️ PPG NOT found → will stream IMU, send PPG zeros");
	}

	// Default mode and streaming
	_mode         = TinZrWearMode::BLE_ONLY;
	_streaming    = false;
	_lastSampleMs = 0;

	sFrameCount          = 0;
	sLastHr              = 0;
	sLastSpo2            = 0;
	sLastHrSpo2UpdateMs  = 0;
	sLastBattPct         = 0;
	sLastBattSampleMs    = 0;

	_updateLED();

	Serial.println("Controls:");
	Serial.println("  - Wait for host START command before streaming.");
	Serial.println("  - Python HUB controls Start/Stop Data.");
}

void TinZrWearableClass::handle() {
	TinZr.handle();

	if (!TinZr.isSoftOn()) {
		TinZrLED.setMode(TinZrStatusLED::Mode::OFF);
		return;
	}

	_handleBLE();
	_handleStreaming();
}

// ===================== BLE ======================
void TinZrWearableClass::_handleBLE() {
#if TINZR_ENABLE_BLE
	if (!_bleStarted) return;

	_ble.handle();

	bool nowConn = _ble.connected();

	// Rising edge: just connected
	if (!_bleWasConnected && nowConn) {
		Serial.println("BLE connected");
	}

	// Falling edge: just disconnected → stop streaming
	if (_bleWasConnected && !nowConn) {
		Serial.println("📡 BLE disconnected → stop streaming");
		_applyStreamingChange(false);
	}

	_bleWasConnected = nowConn;
	_updateLED();
#endif
}

// ========== BLE write callback (S / E / BAT) ==========
void TinZrWearableClass::_bleWriteStatic(const uint8_t* data, size_t len) {
	if (!_self || !data || len == 0) return;
	_self->_handleBleCommand(data, len);
}

void TinZrWearableClass::_handleBleCommand(const uint8_t* data, size_t len) {
	String s;
	s.reserve(len + 1);
	for (size_t i = 0; i < len; ++i) {
		s += char(data[i]);
	}
	s.trim();
	if (!s.length()) return;

	Serial.print("BLE CMD: ");
	Serial.println(s);

	if (s.equalsIgnoreCase("S")) {
		Serial.println("→ START streaming");
		_applyStreamingChange(true);
	} else if (s.equalsIgnoreCase("E")) {
		Serial.println("→ STOP streaming");
		_applyStreamingChange(false);
	} else if (s.equalsIgnoreCase("BAT")) {
		Serial.println("→ BATTERY refresh requested");
		forceBatteryUpdate();
	} else if (s.equalsIgnoreCase("T")) {
		Serial.println("→ TEST: LED blue flash x5");
		TinZrLED.flashColor(0, 0, 255, 50, 5, 120, 120);
		TinZrLED.refresh();
	}
}

// ================= STREAM TOGGLE ================
void TinZrWearableClass::_applyStreamingChange(bool enable) {
	if (enable == _streaming) return;

	if (enable) {
		// IMU required for streaming; PPG optional
		if (!_imuReady) {
			Serial.println("❌ Cannot start streaming: IMU not ready");
			_setErrorLED();
			return;
		}

		_streaming           = true;
		sFrameCount          = 0;
		sSampleCounter       = 0;
		_lastSampleMs        = 0;
		sLastHr              = 0;
		sLastSpo2            = 0;
		sLastHrSpo2UpdateMs  = 0;

		Serial.println("▶ Streaming started (BLE GATT, TinZrCore sensors)");
	} else {
#if TINZR_ENABLE_BLE
		if (_bleStarted && _ble.connected() && sFrameCount > 0) {
			send_wearable_packet(_ble, sFrameCount, millis());
			sFrameCount = 0;
		}
#endif
		_streaming = false;
		Serial.println("⏹ Streaming stopped");
	}

	_updateLED();
}

// ================== STREAMING ===================
void TinZrWearableClass::_handleStreaming() {
	if (!_streaming) return;

	unsigned long now = millis();
	if (now - _lastSampleMs < _cfg.sample_interval_ms) return;
	_lastSampleMs = now;

	bool need_ble = false;
#if TINZR_ENABLE_BLE
	need_ble = _bleStarted && _ble.connected();
#endif
	if (!need_ble) return;

	// ---------- Read sensors via TinZrCore ----------
	TinZrImuSampleSI imu;
	TinZrPpgSample ppg;
	bool gotPpg = false;
	bool okImu = TinZr.readImuPpg(imu, ppg, gotPpg);
	if (!okImu) return;

	// ---------- PPG (or zeros if missing) ----------
	static uint32_t last_red = 0;
	static uint32_t last_ir  = 0;

	uint32_t red_raw = 0;
	uint32_t ir_raw  = 0;

	if (_ppgReady) {
		if (gotPpg) {
			red_raw = ppg.red;
			ir_raw  = ppg.ir;
			last_red = red_raw;
			last_ir  = ir_raw;
		} else {
			// No new sample this loop: reuse last one
			red_raw = last_red;
			ir_raw  = last_ir;
		}

		// Feed every sample into HR / SpO2 estimator
		update_hr_spo2_from_ppg(red_raw, ir_raw);

		if (sLastHrSpo2UpdateMs == 0 || (now - sLastHrSpo2UpdateMs) >= HR_SPO2_UPDATE_INTERVAL_MS) {
			sLastHrSpo2UpdateMs = now;
			Serial.print("💓 HR update: ");
			Serial.print(sLastHr);
			Serial.print(" bpm, SpO2: ");
			Serial.print(sLastSpo2);
			Serial.println(" %");
		}
	} else {
		red_raw   = 0;
		ir_raw    = 0;
		sLastHr   = 0;
		sLastSpo2 = 0;
	}

	float red_nA = red_raw * PPG_ADC_FULL_SCALE_NA / PPG_ADC_MAX_COUNT;
	float ir_nA  = ir_raw  * PPG_ADC_FULL_SCALE_NA / PPG_ADC_MAX_COUNT;

	// ---------- Battery refresh (every 5 min or forced) ----------
	if (sLastBattSampleMs == 0 || (now - sLastBattSampleMs) >= BATT_PERIOD_MS) {
		sLastBattSampleMs = now;
		sLastBattPct      = read_battery_pct();
	}

	// ---------- Pack into binary frame ----------
	WearFrame& f = sFrameBuf[sFrameCount];
	f.sync_word = FRAME_SYNC_WORD;
	f.sample_idx = ++sSampleCounter;

	// NOTE: We keep the SAME packing/scaling scheme your Python viewer expects.
	// TinZrCore now provides SI-ish values (g and dps), so convert explicitly.
	f.ax = (int16_t)(imu.ax_g * GRAVITY_MPS2 * ACC_SCALE);
	f.ay = (int16_t)(imu.ay_g * GRAVITY_MPS2 * ACC_SCALE);
	f.az = (int16_t)(imu.az_g * GRAVITY_MPS2 * ACC_SCALE);

	f.gx = (int16_t)(imu.gx_dps * GYR_SCALE);
	f.gy = (int16_t)(imu.gy_dps * GYR_SCALE);
	f.gz = (int16_t)(imu.gz_dps * GYR_SCALE);

	f.red_nA   = red_nA;
	f.ir_nA    = ir_nA;
	f.hr_bpm   = (uint8_t)sLastHr;
	f.spo2_pct = (uint8_t)sLastSpo2;
	f.batt_pct = (uint8_t)sLastBattPct;

	sFrameCount++;

#if TINZR_ENABLE_BLE
	if (sFrameCount >= FRAMES_PER_PACKET) {
		send_wearable_packet(_ble, FRAMES_PER_PACKET, now);
		sFrameCount = 0;
	}
#endif
}

// ===================== LED ======================
void TinZrWearableClass::_updateLED() {
#if TINZR_ENABLE_BLE
	if (!_bleStarted) {
		TinZrLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
		return;
	}

	if (_bleWasConnected) {
		if (_streaming)
			TinZrLED.setMode(TinZrStatusLED::Mode::BLE_CONNECTED);
		else
			TinZrLED.setMode(TinZrStatusLED::Mode::BLE_ADVERTISING);
	} else {
		TinZrLED.setMode(TinZrStatusLED::Mode::BLE_ADVERTISING);
	}
#else
	TinZrLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
#endif
}

void TinZrWearableClass::_setErrorLED() {
	TinZrLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
}
