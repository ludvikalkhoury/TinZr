// =============================================================
// TinZrWearable.cpp  (FULL FILE)
// =============================================================
#include "TinZrMultiWearable.h"
#include <string.h>

// ===== Sensors =====
#include <Wire.h>

// We keep these headers ONLY to use their begin()/setup() once.
// We do NOT use getEvent(), getRed(), getIR() anymore.
#include <Adafruit_LSM6DS3TRC.h>
#include <Adafruit_Sensor.h>
#include "MAX30105.h"
#include "spo2_algorithm.h"

// ---------- Global/local sensor instances ----------
static Adafruit_LSM6DS3TRC sImu;
static MAX30105            sPpg;

// Static self pointer for BLE callback trampoline
TinZrWearable* TinZrWearable::_self = nullptr;

// Non-blocking PPG reader using SparkFun's internal ring buffer.
// This mimics getRed()/getIR() but without safeCheck() blocking.
static bool ppg_read_fast(uint32_t &red, uint32_t &ir)
{
	// Update internal sense[] buffer from hardware FIFO (non-blocking)
	sPpg.check();   // reads any new samples into sense.red / sense.IR

	if (!sPpg.available()) {
		// No new sample ready this loop
		return false;
	}

	// Oldest unread sample (FIFO-style)
	red = sPpg.getFIFORed();
	ir  = sPpg.getFIFOIR();

	// Advance software tail pointer
	sPpg.nextSample();

	return true;
}

// ===== IMU (LSM6DS3TR-C) register-level helpers =====
// (Please verify these against your datasheet!)
static const uint8_t LSM6_ADDR           = 0x6A;  // typical for LSM6DS3TR-C
static const uint8_t REG_FUNC_CFG_ACCESS = 0x01;
static const uint8_t REG_FIFO_CTRL1      = 0x06;
static const uint8_t REG_FIFO_CTRL2      = 0x07;
static const uint8_t REG_FIFO_CTRL3      = 0x08;
static const uint8_t REG_FIFO_CTRL4      = 0x09;
static const uint8_t REG_FIFO_CTRL5      = 0x0A;

static const uint8_t REG_CTRL1_XL        = 0x10;
static const uint8_t REG_CTRL2_G         = 0x11;
static const uint8_t REG_CTRL3_C         = 0x12;
// OUTX_L_G is usually 0x22 in LSM6 family
static const uint8_t REG_OUTX_L_G        = 0x22;

static void lsm6_write8(uint8_t reg, uint8_t val)
{
	Wire.beginTransmission(LSM6_ADDR);
	Wire.write(reg);
	Wire.write(val);
	Wire.endTransmission();
}

static void lsm6_read_multi(uint8_t reg, uint8_t *buf, uint8_t len)
{
	Wire.beginTransmission(LSM6_ADDR);
	Wire.write(reg);
	Wire.endTransmission(false);   // repeated start
	Wire.requestFrom(LSM6_ADDR, len);
	for (uint8_t i = 0; i < len && Wire.available(); ++i) {
		buf[i] = Wire.read();
	}
}

// Configure LSM6 for ~416 Hz ODR accel/gyro, ±8g, ±1000 dps (as set below)
static void lsm6_config()
{
	// CTRL3_C:
	//   BDU = 1 (block data update)
	//   IF_INC = 1 (auto-increment)
	// Typical: 0b01000100 = 0x44
	lsm6_write8(REG_CTRL3_C, 0x44);

	// CTRL1_XL:
	//   ODR_XL[3:0] = 0110 → 416 Hz
	//   FS_XL[1:0]  = 11   → ±8 g
	// Bits: 0b0110 1100 = 0x6C
	lsm6_write8(REG_CTRL1_XL, 0x6C);

	// CTRL2_G:
	//   ODR_G[3:0] = 0110 → 416 Hz
	//   FS_G[1:0]  = 10   → ±1000 dps
	//  Bits: 0b0110 1000 = 0x68
	lsm6_write8(REG_CTRL2_G, 0x68);
}

// Burst-read raw gyro + accel in one shot.
// Order (12 bytes):
//   GX_L, GX_H, GY_L, GY_H, GZ_L, GZ_H,
//   AX_L, AX_H, AY_L, AY_H, AZ_L, AZ_H
static void lsm6_read_raw(int16_t &gx, int16_t &gy, int16_t &gz,
                          int16_t &ax, int16_t &ay, int16_t &az)
{
	uint8_t b[12];
	lsm6_read_multi(REG_OUTX_L_G, b, 12);

	gx = (int16_t)((uint16_t)b[1] << 8 | b[0]);
	gy = (int16_t)((uint16_t)b[3] << 8 | b[2]);
	gz = (int16_t)((uint16_t)b[5] << 8 | b[4]);
	ax = (int16_t)((uint16_t)b[7] << 8 | b[6]);
	ay = (int16_t)((uint16_t)b[9] << 8 | b[8]);
	az = (int16_t)((uint16_t)b[11] << 8 | b[10]);
}

// ===== PPG (MAX30102) register-level helpers =====
// (Verify addresses/constants with the MAX30102 datasheet.)
static const uint8_t MAX30102_ADDR       = 0x57;
static const uint8_t REG_FIFO_WR_PTR     = 0x04;
static const uint8_t REG_OVF_COUNTER     = 0x05;
static const uint8_t REG_FIFO_RD_PTR     = 0x06;
static const uint8_t REG_FIFO_DATA       = 0x07;
static const uint8_t REG_MODE_CONFIG     = 0x09;
static const uint8_t REG_SPO2_CONFIG     = 0x0A;
static const uint8_t REG_LED1_PA         = 0x0C;  // Red LED current
static const uint8_t REG_LED2_PA         = 0x0D;  // IR LED current
static const uint8_t REG_MULTI_LED_CTRL1 = 0x11;
static const uint8_t REG_MULTI_LED_CTRL2 = 0x12;

static void max_write8(uint8_t reg, uint8_t val)
{
	Wire.beginTransmission(MAX30102_ADDR);
	Wire.write(reg);
	Wire.write(val);
	Wire.endTransmission();
}

static void max_read_multi(uint8_t reg, uint8_t *buf, uint8_t len)
{
	Wire.beginTransmission(MAX30102_ADDR);
	Wire.write(reg);
	Wire.endTransmission(false);
	Wire.requestFrom(MAX30102_ADDR, len);
	for (uint8_t i = 0; i < len && Wire.available(); ++i) {
		buf[i] = Wire.read();
	}
}

// Configure MAX30102 for 400 sps, 2-LED mode (RED + IR), FIFO mode
static void max30102_config()
{
	// Use SparkFun driver for initial config (one-time; acceptable overhead)
	// It sets FIFO, SPO2, LED currents, etc.
	const byte powerLevel     = 0x3F;   // ~mid LED current
	const byte sampleAverage  = 4;      // 4-sample averaging
	const byte ledMode        = 2;      // Red + IR
	const int  sampleRate     = 400;    // 400 sps
	const int  pulseWidth     = 411;    // 18-bit
	const int  adcRange       = 16384;  // largest range

	sPpg.setup(powerLevel, sampleAverage, ledMode,
	           sampleRate, pulseWidth, adcRange);

	// Fine-tune LED currents
	sPpg.setPulseAmplitudeRed(powerLevel);
	sPpg.setPulseAmplitudeIR(powerLevel);

	// Reset FIFO pointers to start clean
	max_write8(REG_FIFO_WR_PTR, 0x00);
	max_write8(REG_FIFO_RD_PTR, 0x00);
	max_write8(REG_OVF_COUNTER, 0x00);
}

// ===== Packed binary frames for BLE =====
static const uint8_t FRAMES_PER_PACKET = 9;

// Scaling factors (match Python viewer)
static constexpr float ACC_SCALE = 1000.0f;  // accel: m/s^2 → milli-units
static constexpr float GYR_SCALE = 100.0f;   // gyro:  dps → centi-units
static constexpr float G_SENS_DPS_PER_LSB = 35e-3f; // 35 mdps/LSB for ±1000 dps

// --- HR / SpO2 + battery in frame ---
struct __attribute__((packed)) WearFrame
{
	int16_t  ax, ay, az;   // accel * ACC_SCALE
	int16_t  gx, gy, gz;   // gyro  * GYR_SCALE
	uint32_t red;          // raw PPG red
	uint32_t ir;           // raw PPG ir
	uint8_t  hr_bpm;       // last computed heart rate
	uint8_t  spo2_pct;     // last computed SpO2
	uint8_t  batt_pct;     // last battery % (0–100)
};

static WearFrame sFrameBuf[FRAMES_PER_PACKET];
static uint8_t   sFrameCount = 0;

// Optional IR threshold if you want to gate on "finger present"
static const uint32_t IR_THRESHOLD = 30000;

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
static uint16_t read_battery_pct()
{
	float pct = TinZr.batteryPercent();   // TinZrCore API
	if (pct < 0.0f)   pct = 0.0f;
	if (pct > 100.0f) pct = 100.0f;

	return static_cast<uint16_t>(pct + 0.5f); // round to nearest integer
}

// OPTIONAL: call this from any "battery query" command handler you already have
void TinZrWearable::forceBatteryUpdate()
{
	// force a refresh on the next _handleStreaming() tick
	sLastBattSampleMs = 0;
}

// -------- HR / SpO2 computation using Maxim algorithm --------
// Uses a sliding buffer of 100 samples (BUFFER_SIZE in spo2_algorithm.h)
static void update_hr_spo2_from_ppg(uint32_t red, uint32_t ir)
{
	const int N_SAMPLES = BUFFER_SIZE;
	static uint32_t ir_buf[N_SAMPLES];
	static uint32_t red_buf[N_SAMPLES];
	static int idx25         = 0;
	static int decim_counter = 0;

	// ---- 1) Decimate 250 Hz → 25 Hz ----
	decim_counter++;
	if (decim_counter < 10)
		return;
	decim_counter = 0;

	// ---- 2) If no finger → just reset algorithm state ----
	if (ir < IR_THRESHOLD) {
		idx25     = 0;  // restart streaming for algorithm
		sLastHr   = 0;
		sLastSpo2 = 0;
		return;
	}

	// ---- 3) Store decimated sample ----
	ir_buf[idx25]  = ir;
	red_buf[idx25] = red;
	idx25++;

	// Not enough samples for the algorithm
	if (idx25 < N_SAMPLES)
		return;

	// ---- 4) Full window → run Maxim algorithm ----
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

	// ---- 5) Validate ----
	if (hr_valid && hr > 20 && hr < 230)
		sLastHr = hr;

	if (spo2_valid && spo2 >= 60 && spo2 <= 100)
		sLastSpo2 = spo2;
}

// ---------------- TinZrWearable ------------------
TinZrWearable::TinZrWearable()
{
}

void TinZrWearable::begin(const TinZrWearableConfig& cfg)
{
	_cfg = cfg;

	Serial.begin(115200);
	delay(200);

	Serial.println();
	Serial.println("===== TinZrWearable (BLE only, NO SD, raw I2C) =====");

	// Core (soft power etc.)
	TinZr.begin();

	// Status LED on same pin as TinZrNode (25)
	_statusLED.begin(25);
	_statusLED.setMode(TinZrStatusLED::Mode::OFF);
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

		_ble.setName(name);
		_bleStarted = _ble.start();
		if (!_bleStarted) {
			Serial.println("❌ TinZrWearable: BLE start failed");
			_statusLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
		} else {
			Serial.println("🔵 BLE started (advertising)");

			// Register BLE message callback (S / E / BAT)
			_self = this;
			_ble.onMessage(&TinZrWearable::_bleCallbackStatic);

			_bleWasConnected = _ble.isConnected();
			_statusLED.setMode(TinZrStatusLED::Mode::BLE_ADVERTISING);
		}
	}
#else
	Serial.println("🚫 BLE disabled at compile time");
#endif

	// ---------- Sensors ----------
	Serial.println("🔧 Initializing IMU (LSM6DS3TR-C) + PPG (MAX30102)...");
	Wire.begin();
	Wire.setClock(400000);   // 400 kHz I2C

	// Use Adafruit begin ONLY to confirm presence; then do our own config.
	_imuReady = sImu.begin_I2C(0x6A, &Wire);
	if (_imuReady) {
		lsm6_config();
	}

	_ppgReady = sPpg.begin(Wire, I2C_SPEED_FAST);
	if (_ppgReady) {
		max30102_config();
	}

	// IMU required, PPG optional
	_sensorsReady = _imuReady;

	if (_imuReady) {
		if (_ppgReady) {
			Serial.println("✅ Sensors ready (IMU + PPG)");
		} else {
			Serial.println("⚠️ PPG NOT found → will stream IMU, send PPG zeros");
		}
	} else {
		Serial.println("❌ IMU NOT found → cannot stream");
	}

	// No SD in this build
	Serial.println("💾 SD logging: DISABLED (fully removed)");

	// Default mode and streaming
	_mode         = TinZrWearMode::BLE_ONLY;  // only BLE is effective
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
	Serial.println("  • Streaming auto-starts on BLE connect.");
	Serial.println("  • Python HUB gates viewing with Start/Stop Data.");
}

void TinZrWearable::handle()
{
	// Soft power
	TinZr.handle();
	_statusLED.handle();

	if (!TinZr.isSoftOn()) {
		_statusLED.setMode(TinZrStatusLED::Mode::OFF);
		return;
	}

	_handleBLE();
	_handleStreaming();
}

// ===================== BLE ======================
void TinZrWearable::_handleBLE()
{
#if TINZR_ENABLE_BLE
	if (!_bleStarted) return;

	_ble.handle();

	bool nowConn = _ble.isConnected();

	// Rising edge: just connected → auto-start streaming
	if (!_bleWasConnected && nowConn) {
		Serial.println("🔵 BLE connected → auto-start streaming");
		_applyStreamingChange(true);
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

// ========== BLE command callback (S / E / BAT) ==========
void TinZrWearable::_bleCallbackStatic(IPAddress from,
                                       const uint8_t* data,
                                       size_t len)
{
	(void)from;  // unused for now
	if (!_self || !data || len == 0) return;
	_self->_handleBleCommand(data, len);
}

void TinZrWearable::_handleBleCommand(const uint8_t* data, size_t len)
{
	// Commands are tiny ASCII strings: "S", "E", "BAT"
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
		forceBatteryUpdate();   // sets sLastBattSampleMs = 0 so next tick re-reads battery
	} else if (s.equalsIgnoreCase("T")) {
		Serial.println("→ TEST: LED blue flash x5");
		_statusLED.flashColor(0, 0, 255, 50, 5, 120, 120);
		_statusLED.refresh();
	}
}

// ================= STREAM TOGGLE ================
void TinZrWearable::_applyStreamingChange(bool enable)
{
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
		_lastSampleMs        = 0;
		sLastHr              = 0;
		sLastSpo2            = 0;
		sLastHrSpo2UpdateMs  = 0;

		Serial.println("▶ Streaming started (BLE only, raw I2C)");
	} else {
		_streaming = false;
		Serial.println("⏹ Streaming stopped");
	}

	_updateLED();
}

// ================== STREAMING ===================
void TinZrWearable::_handleStreaming()
{
	if (!_streaming) return;

	unsigned long now = millis();
	if (now - _lastSampleMs < _cfg.sample_interval_ms) return;
	_lastSampleMs = now;

	// Decide if BLE output is needed
	bool need_ble = false;
#if TINZR_ENABLE_BLE
	need_ble =
		_bleStarted &&
		_ble.isConnected() &&
		(_mode == TinZrWearMode::BLE_ONLY ||
		 _mode == TinZrWearMode::BLE_AND_SD ||
		 _mode == TinZrWearMode::SD_ONLY);
#endif

	if (!need_ble) {
		return;  // no outputs required; skip sensor reads
	}

	// ---------- Read IMU via raw registers ----------
	int16_t gx_raw, gy_raw, gz_raw;
	int16_t ax_raw, ay_raw, az_raw;
	lsm6_read_raw(gx_raw, gy_raw, gz_raw, ax_raw, ay_raw, az_raw);

	// ---------- Read PPG via FIFO (or send zeros if PPG missing) ----------
	static uint32_t last_red = 0;
	static uint32_t last_ir  = 0;

	uint32_t red_raw = 0;
	uint32_t ir_raw  = 0;

	if (_ppgReady) {
		if (!ppg_read_fast(red_raw, ir_raw)) {
			// No new sample this loop: reuse last one
			red_raw = last_red;
			ir_raw  = last_ir;
		} else {
			last_red = red_raw;
			last_ir  = ir_raw;
		}

		// Feed every sample into HR / SpO2 estimator
		update_hr_spo2_from_ppg(red_raw, ir_raw);

		// Only print every 5 seconds for debugging (only if PPG exists)
		if (sLastHrSpo2UpdateMs == 0 ||
			(now - sLastHrSpo2UpdateMs) >= HR_SPO2_UPDATE_INTERVAL_MS)
		{
			sLastHrSpo2UpdateMs = now;

			Serial.print("💓 HR update: ");
			Serial.print(sLastHr);
			Serial.print(" bpm, SpO2: ");
			Serial.print(sLastSpo2);
			Serial.println(" %");
		}
	} else {
		// Hard zeros if sensor not present
		red_raw   = 0;
		ir_raw    = 0;
		sLastHr   = 0;
		sLastSpo2 = 0;
	}

	// ---------- Battery refresh (every 5 min or forced) ----------
	if (sLastBattSampleMs == 0 ||
		(now - sLastBattSampleMs) >= BATT_PERIOD_MS)
	{
		sLastBattSampleMs = now;
		sLastBattPct      = read_battery_pct();
	}

	// ---------- Pack into binary frame ----------
	WearFrame &f = sFrameBuf[sFrameCount];

	// accel raw → scaled (LSB scaling here is your original assumption)
	f.ax = (int16_t)((float)ax_raw * (ACC_SCALE / 4096.0f));
	f.ay = (int16_t)((float)ay_raw * (ACC_SCALE / 4096.0f));
	f.az = (int16_t)((float)az_raw * (ACC_SCALE / 4096.0f));

	// gyro raw → scaled (raw * 0.875 per your original comment)
	f.gx = (int16_t)(gx_raw * (GYR_SCALE * G_SENS_DPS_PER_LSB));
	f.gy = (int16_t)(gy_raw * (GYR_SCALE * G_SENS_DPS_PER_LSB));
	f.gz = (int16_t)(gz_raw * (GYR_SCALE * G_SENS_DPS_PER_LSB));

	// PPG + derived values (zeros if _ppgReady is false)
	f.red      = red_raw;
	f.ir       = ir_raw;
	f.hr_bpm   = (uint8_t)sLastHr;
	f.spo2_pct = (uint8_t)sLastSpo2;
	f.batt_pct = (uint8_t)sLastBattPct;

	sFrameCount++;

#if TINZR_ENABLE_BLE
	// ---------- When we have FRAMES_PER_PACKET, send one binary packet ----------
	if (sFrameCount >= FRAMES_PER_PACKET) {
		const size_t payloadSize = sizeof(WearFrame) * FRAMES_PER_PACKET;
		_ble.sendTCP(reinterpret_cast<const uint8_t*>(sFrameBuf), payloadSize);
		sFrameCount = 0;
	}
#endif
}

// ===================== LED ======================
void TinZrWearable::_updateLED()
{
#if TINZR_ENABLE_BLE
	if (!_bleStarted) {
		_statusLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
		return;
	}

	if (_bleWasConnected) {
		if (_streaming)
			_statusLED.setMode(TinZrStatusLED::Mode::BLE_CONNECTED);
		else
			_statusLED.setMode(TinZrStatusLED::Mode::BLE_ADVERTISING);
	} else {
		_statusLED.setMode(TinZrStatusLED::Mode::BLE_ADVERTISING);
	}
#else
	_statusLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
#endif
}

void TinZrWearable::_setErrorLED()
{
	_statusLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
}
