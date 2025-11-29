#include "TinZrWearable.h"
#include <string.h>

// ===== Sensors =====
#include <Wire.h>

// We keep these headers ONLY to use their begin()/setup() once.
// We do NOT use getEvent(), getRed(), getIR() anymore.
#include <Adafruit_LSM6DS3TRC.h>
#include <Adafruit_Sensor.h>
#include "MAX30105.h"

// ---------- Global/local sensor instances ----------
static Adafruit_LSM6DS3TRC sImu;
static MAX30105            sPpg;



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
static const uint8_t LSM6_ADDR       = 0x6A;  // typical for LSM6DS3TR-C
static const uint8_t REG_FUNC_CFG_ACCESS = 0x01;
static const uint8_t REG_FIFO_CTRL1      = 0x06;
static const uint8_t REG_FIFO_CTRL2      = 0x07;
static const uint8_t REG_FIFO_CTRL3      = 0x08;
static const uint8_t REG_FIFO_CTRL4      = 0x09;
static const uint8_t REG_FIFO_CTRL5      = 0x0A;

static const uint8_t REG_CTRL1_XL   = 0x10;
static const uint8_t REG_CTRL2_G    = 0x11;
static const uint8_t REG_CTRL3_C    = 0x12;
// OUTX_L_G is usually 0x22 in LSM6 family
static const uint8_t REG_OUTX_L_G   = 0x22;

static void lsm6_write8(uint8_t reg, uint8_t val) {
	Wire.beginTransmission(LSM6_ADDR);
	Wire.write(reg);
	Wire.write(val);
	Wire.endTransmission();
}

static void lsm6_read_multi(uint8_t reg, uint8_t *buf, uint8_t len) {
	Wire.beginTransmission(LSM6_ADDR);
	Wire.write(reg);
	Wire.endTransmission(false);   // repeated start
	Wire.requestFrom(LSM6_ADDR, len);
	for (uint8_t i = 0; i < len && Wire.available(); ++i) {
		buf[i] = Wire.read();
	}
}

// Configure LSM6 for ~416 Hz ODR accel/gyro, ±2g, ±125 dps
static void lsm6_config() {
	// CTRL3_C:
	//   BDU = 1 (block data update)
	//   IF_INC = 1 (auto-increment)
	//   SW_RESET = 0
	// Typical: 0b01000100 = 0x44
	lsm6_write8(REG_CTRL3_C, 0x44);

	// CTRL1_XL:
	//   ODR_XL[3:0] = 0110 → 416 Hz
	//   FS_XL[1:0]  = 00   → ±2g
	// So 0b0110 0000 = 0x60
	lsm6_write8(REG_CTRL1_XL, 0x60);

	// CTRL2_G:
	//   ODR_G[3:0] = 0110 → 416 Hz
	//   FS_G[1:0]  = 00   → ±125 dps (or ±250dps depending on variant)
	// 0b0110 0000 = 0x60
	lsm6_write8(REG_CTRL2_G, 0x60);
}

// Burst-read raw gyro + accel in one shot.
// Order (12 bytes):
//   GX_L, GX_H, GY_L, GY_H, GZ_L, GZ_H, AX_L, AX_H, AY_L, AY_H, AZ_L, AZ_H
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

static void max_write8(uint8_t reg, uint8_t val) {
	Wire.beginTransmission(MAX30102_ADDR);
	Wire.write(reg);
	Wire.write(val);
	Wire.endTransmission();
}

static void max_read_multi(uint8_t reg, uint8_t *buf, uint8_t len) {
	Wire.beginTransmission(MAX30102_ADDR);
	Wire.write(reg);
	Wire.endTransmission(false);
	Wire.requestFrom(MAX30102_ADDR, len);
	for (uint8_t i = 0; i < len && Wire.available(); ++i) {
		buf[i] = Wire.read();
	}
}

// Configure MAX30102 for 400 sps, 2-LED mode (RED + IR), FIFO mode
static void max30102_config() {
	// Use SparkFun driver for initial config (one-time; acceptable overhead)
	// It sets FIFO, SPO2, LED currents, etc.
	// If you want fully manual, you can replace this with raw writes.
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

// Read a single sample (RED, IR) from FIFO (6 bytes).
// MAX30102 in 2-LED mode: data layout is RED(3 bytes) + IR(3 bytes).
static void max30102_read_sample(uint32_t &red, uint32_t &ir) {
	uint8_t buf[6];
	max_read_multi(REG_FIFO_DATA, buf, 6);

	red = ((uint32_t)buf[0] << 16) | ((uint32_t)buf[1] << 8) | buf[2];
	ir  = ((uint32_t)buf[3] << 16) | ((uint32_t)buf[4] << 8) | buf[5];

	// 18-bit values (top bits unused). If you want, mask:
	// red &= 0x3FFFF;
	// ir  &= 0x3FFFF;
}

// ===== Packed binary frames for BLE =====
static const uint8_t FRAMES_PER_PACKET = 10;

// Scaling factors (match Python viewer)
static constexpr float ACC_SCALE = 1000.0f;  // accel: m/s^2 → milli-units
static constexpr float GYR_SCALE = 100.0f;   // gyro:  rad/s or dps → centi-units

struct __attribute__((packed)) WearFrame {
	int16_t ax, ay, az;   // accel * ACC_SCALE
	int16_t gx, gy, gz;   // gyro  * GYR_SCALE
	uint32_t red;         // raw PPG red
	uint32_t ir;          // raw PPG ir
};

static WearFrame sFrameBuf[FRAMES_PER_PACKET];
static uint8_t   sFrameCount = 0;

// Button timing
static const unsigned long DEBOUNCE_MS    = 50;
static const unsigned long MULTI_CLICK_MS = 800;

// Optional IR threshold if you want to gate on "finger present"
static const uint32_t IR_THRESHOLD = 30000;

// ---------------- TinZrWearable ------------------

TinZrWearable::TinZrWearable()
{
}

void TinZrWearable::begin(const TinZrWearableConfig& cfg) {
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

	// Button (from TinZrConfig, active-LOW with pull-up)
	pinMode(PB_PIN, INPUT_PULLUP);
	_btnRaw        = digitalRead(PB_PIN);
	_btnStable     = _btnRaw;
	_btnLastStable = _btnStable;

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
			Serial.println("🔵 BLE started");
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
	bool imu_ok = sImu.begin_I2C(0x6A, &Wire);
	if (imu_ok) {
		lsm6_config();
	}

	bool ppg_ok = sPpg.begin(Wire, I2C_SPEED_FAST);
	if (ppg_ok) {
		max30102_config();
	}

	// Optional: print part ID using SparkFun driver (one-time)
	byte partID = sPpg.readPartID();
	Serial.print("PPG Part ID: 0x");
	Serial.println(partID, HEX);

	_sensorsReady = imu_ok && ppg_ok;
	if (_sensorsReady) {
		Serial.println("✅ Sensors ready");
	} else {
		Serial.println("⚠️ Sensors NOT ready");
	}

	// No SD in this build
	Serial.println("💾 SD logging: DISABLED");

	// Default mode and streaming
	_mode      = TinZrWearMode::BLE_ONLY;  // keep enum but only BLE is effective
	_streaming = false;
	_lastSampleMs = 0;

	sFrameCount = 0;

	_updateLED();

	Serial.println("Controls:");
	Serial.println("  • Six-click PB → cycle mode (BLE / SD / BLE+SD) [SD ignored]");
	Serial.println("  • Triple-click PB → start/stop streaming");
}

void TinZrWearable::handle() {
	// Soft power
	TinZr.handle();
	_statusLED.handle();

	if (!TinZr.isSoftOn()) {
		return;
	}

	_handleBLE();
	_handleButton();
	_handleStreaming();
}

// ===================== BLE ======================
void TinZrWearable::_handleBLE() {
#if TINZR_ENABLE_BLE
	if (!_bleStarted) return;

	_ble.handle();

	bool nowConn = _ble.isConnected();
	if (!_bleWasConnected && nowConn) {
		Serial.println("🔵 BLE connected");
	}
	if (_bleWasConnected && !nowConn) {
		Serial.println("📡 BLE disconnected");
		// If streaming in a BLE mode, we keep _streaming=true, but no BLE data
		// will be sent until reconnection.
	}
	_bleWasConnected = nowConn;
#endif
}

// ===================== BUTTON ===================
void TinZrWearable::_handleButton() {
	unsigned long now = millis();

	// Debounce raw PB
	bool raw = digitalRead(PB_PIN);  // active-LOW
	if (raw != _btnRaw) {
		_btnRaw        = raw;
		_btnLastChange = now;
	}
	if (now - _btnLastChange > DEBOUNCE_MS) {
		_btnStable = _btnRaw;
	}

	// Detect release edge: LOW -> HIGH = one "click"
	if (_btnLastStable == LOW && _btnStable == HIGH) {
		// Group clicks within MULTI_CLICK_MS
		if (now - _lastShortClickTime > MULTI_CLICK_MS) {
			_btnClickCount = 0;
		}
		_btnClickCount++;
		_lastShortClickTime = now;
	}

	_btnLastStable = _btnStable;

	// If we have pending clicks and the window expired, interpret them
	if (_btnClickCount > 0 && (now - _lastShortClickTime) > MULTI_CLICK_MS) {
		uint8_t clicks = _btnClickCount;
		_btnClickCount = 0;

		if (clicks == 3) {
			// triple-click → toggle streaming
			_applyStreamingChange(!_streaming);
		} else if (clicks >= 6) {
			// six-click (or more) → change mode
			_cycleMode();
		}
		// Single-click (1) → ignored
	}
}

// ===================== MODE =====================
void TinZrWearable::_cycleMode() {
	uint8_t idx = static_cast<uint8_t>(_mode);
	idx = (idx + 1) % static_cast<uint8_t>(TinZrWearMode::NUM_MODES);
	_mode = static_cast<TinZrWearMode>(idx);

	Serial.print("🔄 Mode: ");
	switch (_mode) {
	case TinZrWearMode::BLE_ONLY:
		Serial.println("BLE_ONLY");
		_statusLED.flashColor(0, 0, 255, 32, 5, 120, 120);
		break;

	case TinZrWearMode::SD_ONLY:
		Serial.println("SD_ONLY (ignored – no SD logging)");
		_statusLED.flashColor(255, 255, 0, 32, 5, 120, 120);
		break;

	case TinZrWearMode::BLE_AND_SD:
		Serial.println("BLE_AND_SD (SD ignored)");
		_statusLED.flashColor(0, 255, 255, 32, 5, 120, 120);
		break;

	default:
		Serial.println("UNKNOWN");
		break;
	}

	// After the flash sequence, restore the normal "steady" status LED
	_updateLED();
}

// Use existing TinZrStatusLED flashing behaviour
void TinZrWearable::_flashModeLED(TinZrWearMode wearMode) {
	TinZrStatusLED::Mode flashMode;

	switch (wearMode) {
	case TinZrWearMode::BLE_ONLY:
		flashMode = TinZrStatusLED::Mode::OTA_ACTIVE;  // repurposed
		break;

	case TinZrWearMode::SD_ONLY:
		flashMode = TinZrStatusLED::Mode::WIFI_FAIL;
		break;

	case TinZrWearMode::BLE_AND_SD:
		flashMode = TinZrStatusLED::Mode::OTA_ACTIVE;
		break;

	default:
		flashMode = TinZrStatusLED::Mode::WIFI_FAIL;
		break;
	}

	_statusLED.setMode(flashMode);

	const uint32_t HALF_MS      = 300;
	const uint8_t  N_FLASHES    = 5;
	const uint32_t totalWindow  = 2 * HALF_MS * N_FLASHES;

	uint32_t start = millis();
	while (millis() - start < totalWindow) {
		TinZr.handle();
		_statusLED.handle();
		delay(20);
	}
}

// ================= STREAM TOGGLE ================
void TinZrWearable::_applyStreamingChange(bool enable) {
	if (enable == _streaming) return;

	if (enable) {
		if (!_sensorsReady) {
			Serial.println("❌ Cannot start streaming: sensors not ready");
			_setErrorLED();
			return;
		}

		_streaming = true;
		sFrameCount = 0;
		Serial.println("▶ Streaming started (BLE only, raw I2C)");
	} else {
		_streaming = false;
		Serial.println("⏹ Streaming stopped");
	}

	_updateLED();
}

// ====================== SD (NO-OP STUBS) ======================
void TinZrWearable::_startSDLogging() {
	// SD logging disabled in this build
}

void TinZrWearable::_stopSDLogging() {
	// SD logging disabled in this build
}

bool TinZrWearable::_openNewLogFile() {
	return false;
}

void TinZrWearable::_writeHeader() {
}

// ================== STREAMING ===================
void TinZrWearable::_handleStreaming() {
	if (!_streaming) return;

	unsigned long now = millis();
	if (now - _lastSampleMs < _cfg.sample_interval_ms) return;
	_lastSampleMs = now;

	// Decide if BLE output is needed
	bool need_ble = false;
#if TINZR_ENABLE_BLE
	need_ble = _bleStarted && _ble.isConnected() &&
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

	// ---------- Read PPG via FIFO ----------
	// ---------- Read PPG via SparkFun's FIFO logic (non-blocking) ----------
	static uint32_t last_red = 0;
	static uint32_t last_ir  = 0;

	uint32_t red_raw, ir_raw;
	if (!ppg_read_fast(red_raw, ir_raw)) {
		// No new sample this loop: reuse last one
		red_raw = last_red;
		ir_raw  = last_ir;
	} else {
		last_red = red_raw;
		last_ir  = ir_raw;
	}

	// Optional: gate on finger presence if you want
	// if (ir_raw < IR_THRESHOLD) {
	//     // e.g., set red_raw = ir_raw = 0 or skip filling the frame
	// }


	// Optional: gate on finger
	// if (ir_raw < IR_THRESHOLD) { return; }

	// ---------- Pack into binary frame ----------
	WearFrame &f = sFrameBuf[sFrameCount];

	// accel raw → scaled
	// You can map counts → m/s^2 by scale factors, but we keep it simple
	f.ax = (int16_t)((float)ax_raw * (ACC_SCALE / 16384.0f));  // example scale
	f.ay = (int16_t)((float)ay_raw * (ACC_SCALE / 16384.0f));
	f.az = (int16_t)((float)az_raw * (ACC_SCALE / 16384.0f));

	// gyro raw → scaled
	// Similarly, adjust 131 or 262.4 etc depending on FS; here just example
	f.gx = (int16_t)((float)gx_raw * (GYR_SCALE / 131.0f));
	f.gy = (int16_t)((float)gy_raw * (GYR_SCALE / 131.0f));
	f.gz = (int16_t)((float)gz_raw * (GYR_SCALE / 131.0f));

	f.red = red_raw;
	f.ir  = ir_raw;

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
void TinZrWearable::_updateLED() {
	switch (_mode) {
	case TinZrWearMode::BLE_ONLY:
	case TinZrWearMode::BLE_AND_SD:
	case TinZrWearMode::SD_ONLY:    // treated same; SD removed
		if (_streaming)
			_statusLED.setMode(TinZrStatusLED::Mode::BLE_CONNECTED);
		else
			_statusLED.setMode(TinZrStatusLED::Mode::BLE_ADVERTISING);
		break;

	default:
		_statusLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
		break;
	}
}

void TinZrWearable::_setErrorLED() {
	_statusLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
}
