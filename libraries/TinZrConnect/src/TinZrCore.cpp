#include "TinZrCore.h"
#include "TinZrLED.h"

#include <WiFi.h>
#include <Wire.h>
#include <SPI.h>

// Expect PB_PIN to be defined in variant/config; fallback if not defined:
#ifndef PB_PIN
#define PB_PIN 9
#endif

TinZrCore TinZr;

// --------- internal helpers ---------
bool TinZrCore::readButtonState() const {
	int level = digitalRead(PB_PIN);
	if (BUTTON_ACTIVE_LOW) {
		return (level == LOW);
	} else {
		return (level == HIGH);
	}
}

// --------- public API ---------
void TinZrCore::begin(uint8_t ledBrightness) {
	// Button
	if (BUTTON_ACTIVE_LOW) {
		pinMode(PB_PIN, INPUT_PULLUP);
	} else {
		// ESP32 supports INPUT_PULLDOWN
		pinMode(PB_PIN, INPUT_PULLDOWN);
	}

	_buttonLast       = readButtonState();
	_pressStart       = 0;
	_longPressLatched = false;
	_softOn           = true;

	// Battery
	pinMode(PIN_BAT, INPUT);

	// LED module init (NeoPixel is owned by TinZrLED.cpp)
	TinZrLED.begin(ledBrightness);
	TinZrLED.setMode(TinZrStatusLED::Mode::OFF);

	float vbat = readBatteryVoltage();
	int   pct  = readBatteryPercent();

	Serial.println("==== TinZrCore boot ====");
	Serial.print("Battery: ");
	Serial.print(vbat, 3);
	Serial.print(" V (");
	Serial.print(pct);
	Serial.println(" %)");

	// Boot flash + steady (optional)
	TinZrLED.flashColor(255, 255, 255, 25, 3, 120, 120);
	//TinZrLED.setColor(255, 255, 255, 25);
}

float TinZrCore::readBatteryVoltage() const {
	const int N = 16;
	uint32_t acc = 0;

	for (int i = 0; i < N; ++i) {
		acc += analogRead(PIN_BAT);
		delay(2);
	}

	float raw   = acc / float(N);
	float v_div = raw * (VREF / ADC_MAX);
	float v_bat = v_div / DIVIDER_RATIO;
	return v_bat;
}

int TinZrCore::readBatteryPercent() const {
	float v = readBatteryVoltage();
	if (v <= VBAT_MIN) return 0;
	if (v >= VBAT_MAX) return 100;

	float frac = (v - VBAT_MIN) / (VBAT_MAX - VBAT_MIN);
	int pct = int(frac * 100.0f + 0.5f);

	if (pct < 0)   pct = 0;
	if (pct > 100) pct = 100;
	return pct;
}

void TinZrCore::softOff() {
	if (!_softOn) return;

	Serial.println("TinZrCore: SOFT OFF");

	// LED indication
	TinZrLED.flashColor(255, 255, 255, 25, 3, 120, 120);
	TinZrLED.setMode(TinZrStatusLED::Mode::OFF);

	WiFi.disconnect(true);
	WiFi.mode(WIFI_OFF);

	Serial.end();

	// Put common pins into safe state
	pinMode(TX, INPUT);
	pinMode(RX, INPUT);

	Wire.end();
	pinMode(SDA, INPUT);
	pinMode(SCL, INPUT);

	SPI.end();
	pinMode(MOSI, INPUT);
	pinMode(MISO, INPUT);
	pinMode(SCK,  INPUT);
	pinMode(SS,   INPUT);

	_softOn = false;
}

void TinZrCore::softOn() {
	if (_softOn) return;

	Serial.println("TinZrCore: SOFT ON");

	// LED indication (will be brief before restart)
	TinZrLED.flashColor(255, 255, 255, 25, 3, 120, 120);
	TinZrLED.setColor(255, 255, 255, 25);

	_softOn = true;
	delay(150);
	ESP.restart();
}

void TinZrCore::handle() {
	// Keep LED animations alive
	TinZrLED.handle();

	bool pressed = readButtonState();
	uint32_t now = millis();

	if (pressed && !_buttonLast) {
		_pressStart       = now;
		_longPressLatched = false;
	} else if (!pressed && _buttonLast) {
		_pressStart       = 0;
		_longPressLatched = false;
	}

	if (pressed && !_longPressLatched && _pressStart != 0) {
		uint32_t held = now - _pressStart;
		if (held >= LONG_PRESS_MS) {
			_longPressLatched = true;
			if (_softOn) softOff();
			else         softOn();
		}
	}

	_buttonLast = pressed;
}




// --------------------------
// IMU (LSM6) helpers
// --------------------------
void TinZrCore::_lsm6Write8(uint8_t reg, uint8_t val) {
	Wire.beginTransmission(_scfg.imu_addr);
	Wire.write(reg);
	Wire.write(val);
	Wire.endTransmission();
}

void TinZrCore::_lsm6ReadMulti(uint8_t reg, uint8_t* buf, uint8_t len) {
	Wire.beginTransmission(_scfg.imu_addr);
	Wire.write(reg);
	Wire.endTransmission(false);
	Wire.requestFrom((int)_scfg.imu_addr, (int)len);

	for (uint8_t i = 0; i < len && Wire.available(); ++i) {
		buf[i] = Wire.read();
	}
}

void TinZrCore::_lsm6Config() {
	// CTRL3_C:
	//   BDU = 1 (block data update)
	//   IF_INC = 1 (auto-increment)
	// Typical: 0b01000100 = 0x44
	_lsm6Write8(REG_CTRL3_C, 0x44);

	// CTRL1_XL:
	//   ODR_XL[3:0] = 0110 → 416 Hz
	//   FS_XL[1:0]  = 11   → ±8 g
	// Bits: 0b0110 1100 = 0x6C
	_lsm6Write8(REG_CTRL1_XL, 0x6C);

	// CTRL2_G:
	//   ODR_G[3:0] = 0110 → 416 Hz
	//   FS_G[1:0]  = 10   → ±1000 dps
	//  Bits: 0b0110 1000 = 0x68
	_lsm6Write8(REG_CTRL2_G, 0x68);
	
}

bool TinZrCore::imuReadRaw(TinZrImuSample& out) {
	if (!_imuReady) return false;

	uint8_t b[12];
	_lsm6ReadMulti(REG_OUTX_L_G, b, 12);

	out.t_ms = millis();

	out.gx = (int16_t)((uint16_t)b[1]  << 8 | b[0]);
	out.gy = (int16_t)((uint16_t)b[3]  << 8 | b[2]);
	out.gz = (int16_t)((uint16_t)b[5]  << 8 | b[4]);

	out.ax = (int16_t)((uint16_t)b[7]  << 8 | b[6]);
	out.ay = (int16_t)((uint16_t)b[9]  << 8 | b[8]);
	out.az = (int16_t)((uint16_t)b[11] << 8 | b[10]);

	return true;
}


bool TinZrCore::imuReadSI(TinZrImuSampleSI& out) {
	TinZrImuSample raw;
	if (!imuReadRaw(raw)) return false;

	out.t_ms = raw.t_ms;

	// Accel: ±8g → 0.000244 g/LSB
	const float ACC_SCALE = 0.000244f;

	out.ax_g = raw.ax * ACC_SCALE;
	out.ay_g = raw.ay * ACC_SCALE;
	out.az_g = raw.az * ACC_SCALE;

	// Gyro: ±1000 dps → 0.035 dps/LSB
	const float GYR_SCALE = 0.035f;

	out.gx_dps = raw.gx * GYR_SCALE;
	out.gy_dps = raw.gy * GYR_SCALE;
	out.gz_dps = raw.gz * GYR_SCALE;

	return true;
}


// --------------------------
// PPG (MAX3010x) helpers
// --------------------------

// Same constants you used in TinZrWearable.cpp
static constexpr uint8_t MAX30102_ADDR   = 0x57;
static constexpr uint8_t REG_FIFO_WR_PTR = 0x04;
static constexpr uint8_t REG_OVF_COUNTER = 0x05;
static constexpr uint8_t REG_FIFO_RD_PTR = 0x06;

static void _max_write8(uint8_t reg, uint8_t val) {
	Wire.beginTransmission(MAX30102_ADDR);
	Wire.write(reg);
	Wire.write(val);
	Wire.endTransmission();
}

void TinZrCore::_ppgConfig() {
	// Same settings as your wearable code
	const byte powerLevel    = 0x3F;   // LED current
	const byte sampleAverage = 4;
	const byte ledMode       = 2;      // Red + IR
	const int  sampleRate    = 400;    // 400 sps
	const int  pulseWidth    = 411;    // 18-bit
	const int  adcRange      = 16384;

	_ppg.setup(powerLevel, sampleAverage, ledMode,
	           sampleRate, pulseWidth, adcRange);

	_ppg.setPulseAmplitudeRed(powerLevel);
	_ppg.setPulseAmplitudeIR(powerLevel);

	// Reset FIFO pointers to start clean (your wearable did this)
	_max_write8(REG_FIFO_WR_PTR, 0x00);
	_max_write8(REG_FIFO_RD_PTR, 0x00);
	_max_write8(REG_OVF_COUNTER, 0x00);
}

bool TinZrCore::_ppgReadFast(uint32_t& red, uint32_t& ir) {
	// Non-blocking FIFO update (your wearable pattern)
	_ppg.check();

	if (!_ppg.available()) return false;

	red = _ppg.getFIFORed();
	ir  = _ppg.getFIFOIR();
	_ppg.nextSample();
	return true;
}

bool TinZrCore::ppgRead(TinZrPpgSample& out) {
	if (!_ppgReady) return false;

	uint32_t red, ir;
	if (!_ppgReadFast(red, ir)) return false;

	out.t_ms = millis();
	out.red  = red;
	out.ir   = ir;
	return true;
}

bool TinZrCore::readImuPpg(TinZrImuSampleSI& imu, TinZrPpgSample& ppg, bool& gotPpg) {
	bool okImu = imuReadSI(imu);
	gotPpg = ppgRead(ppg);
	return okImu;
}

// --------------------------
// sensorsBegin()
// --------------------------
bool TinZrCore::sensorsBegin(const TinZrSensorsConfig& cfg) {
	_scfg = cfg;

	Wire.begin();
	if (_scfg.i2c_fast) {
		Wire.setClock(400000);
	}

	// IMU: use Adafruit begin() only as a presence check
	_imuReady = _imu.begin_I2C(_scfg.imu_addr, &Wire);
	if (_imuReady) {
		_lsm6Config();
	}

	// PPG: optional
	_ppgReady = false;
	if (_scfg.init_ppg) {
		_ppgReady = _ppg.begin(Wire, I2C_SPEED_FAST);
		if (_ppgReady) {
			_ppgConfig();
		}
	}

	return _imuReady;
}
