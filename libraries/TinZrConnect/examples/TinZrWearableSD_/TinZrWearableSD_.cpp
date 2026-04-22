#include "TinZrWearableSD_.h"

#include <SPI.h>
#include <Wire.h>

TinZrWearableSD_Class TinZrWearableSD_;

TinZrWearableSD_Class::TinZrWearableSD_Class()
	: pixel(1, PIN_RGB_LED, NEO_GRB + NEO_KHZ800) {}

// =========================
// LED
// =========================
void TinZrWearableSD_Class::setLED(uint8_t r, uint8_t g, uint8_t b) {
	pixel.setPixelColor(0, pixel.Color(r, g, b));
	pixel.show();
}

void TinZrWearableSD_Class::setStoppedLED() {
	setLED(255, 0, 0);
}

void TinZrWearableSD_Class::setRecordingLED() {
	setLED(0, 255, 0);
}

// =========================
// Button
// =========================
bool TinZrWearableSD_Class::readButtonPressed() {
	return digitalRead(PB_PIN) == LOW;
}

bool TinZrWearableSD_Class::buttonPressedEvent() {
	bool now = readButtonPressed();
	bool event = false;

	if (now != lastButton) {
		lastDebounceMs = millis();
	}

	if ((millis() - lastDebounceMs) > 40) {
		if (now != stableButton) {
			stableButton = now;
			if (stableButton) event = true;
		}
	}

	lastButton = now;
	return event;
}

// =========================
// SD helpers
// =========================
bool TinZrWearableSD_Class::ensureDir(const char* path) {
	if (SD.exists(path)) return true;
	return SD.mkdir(path);
}

const char* TinZrWearableSD_Class::logDir() const {
	return (cfg.sd_log_dir && cfg.sd_log_dir[0] != '\0') ? cfg.sd_log_dir : "/TinZrLogs";
}

String TinZrWearableSD_Class::makeFilename() {
	for (int i = 1; i < 10000; i++) {
		String path = String(logDir()) + "/" + String(i) + ".csv";
		if (!SD.exists(path.c_str())) return path;
	}
	return String(logDir()) + "/9999.csv";
}

// =========================
// IMU
// =========================
void TinZrWearableSD_Class::lsm6Write8(uint8_t reg, uint8_t val) {
	Wire.beginTransmission(LSM6_ADDR);
	Wire.write(reg);
	Wire.write(val);
	Wire.endTransmission();
}

bool TinZrWearableSD_Class::lsm6ReadMulti(uint8_t reg, uint8_t* buf, uint8_t len) {
	Wire.beginTransmission(LSM6_ADDR);
	Wire.write(reg);
	if (Wire.endTransmission(false) != 0) return false;

	if (Wire.requestFrom((int)LSM6_ADDR, (int)len) != len) return false;

	for (uint8_t i = 0; i < len; i++) buf[i] = Wire.read();
	return true;
}

bool TinZrWearableSD_Class::initIMU() {
	uint8_t who = 0;

	Wire.beginTransmission(LSM6_ADDR);
	Wire.write(WHO_AM_I_LSM6);
	if (Wire.endTransmission(false) != 0) return false;

	if (Wire.requestFrom((int)LSM6_ADDR, 1) != 1) return false;
	who = Wire.read();

	Serial.print("IMU WHOAMI: ");
	Serial.println(who, HEX);

	// Configure like TinZr
	Wire.beginTransmission(LSM6_ADDR);
	Wire.write(REG_CTRL3_C);
	Wire.write(0x44);
	Wire.endTransmission();

	Wire.beginTransmission(LSM6_ADDR);
	Wire.write(REG_CTRL1_XL);
	Wire.write(0x6C);
	Wire.endTransmission();

	Wire.beginTransmission(LSM6_ADDR);
	Wire.write(REG_CTRL2_G);
	Wire.write(0x68);
	Wire.endTransmission();

	return true;
}

// =========================
// PPG
// =========================
void TinZrWearableSD_Class::maxWrite8(uint8_t reg, uint8_t val) {
	Wire.beginTransmission(MAX30102_ADDR);
	Wire.write(reg);
	Wire.write(val);
	Wire.endTransmission();
}

bool TinZrWearableSD_Class::initPPG() {
	if (!ppg.begin(Wire, I2C_SPEED_FAST, MAX30102_ADDR)) return false;

	// Slightly more explicit setup, still simple
	const byte powerLevel    = 0x3F;
	const byte sampleAverage = 4;
	const byte ledMode       = 2;
	const int  sampleRate    = 400;
	const int  pulseWidth    = 411;
	const int  adcRange      = 16384;

	ppg.setup(powerLevel, sampleAverage, ledMode, sampleRate, pulseWidth, adcRange);
	ppg.setPulseAmplitudeRed(powerLevel);
	ppg.setPulseAmplitudeIR(powerLevel);

	maxWrite8(REG_FIFO_WR_PTR, 0x00);
	maxWrite8(REG_FIFO_RD_PTR, 0x00);
	maxWrite8(REG_OVF_COUNTER, 0x00);

	return true;
}

// =========================
// Logging
// =========================
void TinZrWearableSD_Class::startRecording() {
	if (!ensureDir(logDir())) {
		Serial.println("DIR FAIL");
		return;
	}

	String fname = makeFilename();
	logFile = SD.open(fname.c_str(), FILE_WRITE);

	if (!logFile) {
		Serial.println("FILE OPEN FAIL");
		return;
	}

	writeLogHeader(fname);
	logFile.flush();

	recording = true;
	setRecordingLED();

	recordStartMs = millis();
	scheduledMs   = recordStartMs;
	lastFlushMs   = recordStartMs;

	Serial.print("STARTED: ");
	Serial.println(fname);
}

void TinZrWearableSD_Class::stopRecording() {
	if (!recording) return;

	logFile.flush();
	logFile.close();

	recording = false;
	setStoppedLED();

	Serial.println("STOPPED");
}

void TinZrWearableSD_Class::writeLogHeader(const String& filename) {
	logFile.println("# ================================================");
	logFile.println("# TinZrWearableSD_ Log");
	logFile.println("# ================================================");
	logFile.print("# file_path: ");
	logFile.println(filename);
	logFile.print("# sd_log_dir: ");
	logFile.println(logDir());
	logFile.print("# sample_interval_ms: ");
	logFile.println(cfg.sample_interval_ms);
	logFile.print("# target_sample_rate_hz: ");
	logFile.println(cfg.sample_interval_ms > 0 ? (1000.0f / cfg.sample_interval_ms) : 0.0f, 3);
	logFile.print("# imu_i2c_address: 0x");
	logFile.println(LSM6_ADDR, HEX);
	logFile.print("# ppg_i2c_address: 0x");
	logFile.println(MAX30102_ADDR, HEX);
	logFile.print("# ppg_detected: ");
	logFile.println(ppgAvailable ? "true" : "false");
	logFile.println("# column_units:");
	logFile.println("#   t_ms: milliseconds");
	logFile.println("#   red_nA, ir_nA: approximate MAX30102 photodiode current in nanoamps");
	logFile.println("#   ax_g, ay_g, az_g: g");
	logFile.println("#   gx_dps, gy_dps, gz_dps: degrees per second");
	logFile.println("# max30102_adc_full_scale_nA: 16384");
	logFile.println("# max30102_adc_resolution_bits: 18");
	logFile.println("# max30102_adc_max_count: 262143");
	logFile.println("# max30102_current_conversion: current_nA = adc_count * 16384 / 262143");
	logFile.println("# imu_accel_fullscale: +/-8g");
	logFile.println("# imu_gyro_fullscale: +/-1000dps");
	logFile.println("# imu_accel_scale_g_per_lsb: 0.000244");
	logFile.println("# imu_gyro_scale_dps_per_lsb: 0.035");
	logFile.println("# ppg_note: red_nA and ir_nA are logged as zero if PPG is unavailable");
	logFile.println("# time_note: t_ms is scheduled elapsed time from recording start");
	logFile.println("# -----------------------------------------------");
	logFile.println("t_ms,red_nA,ir_nA,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps");
}

// =========================
// Write one sample
// =========================
void TinZrWearableSD_Class::writeOneSample(uint32_t sampleTimeMs) {
	uint8_t b[12];
	if (!lsm6ReadMulti(REG_OUTX_L_G, b, 12)) return;

	int16_t gx = (int16_t)((b[1] << 8) | b[0]);
	int16_t gy = (int16_t)((b[3] << 8) | b[2]);
	int16_t gz = (int16_t)((b[5] << 8) | b[4]);

	int16_t ax = (int16_t)((b[7] << 8) | b[6]);
	int16_t ay = (int16_t)((b[9] << 8) | b[8]);
	int16_t az = (int16_t)((b[11] << 8) | b[10]);

	float ax_g = ax * ACC_SCALE_G_PER_LSB;
	float ay_g = ay * ACC_SCALE_G_PER_LSB;
	float az_g = az * ACC_SCALE_G_PER_LSB;

	float gx_dps = gx * GYR_SCALE_DPS_PER_LSB;
	float gy_dps = gy * GYR_SCALE_DPS_PER_LSB;
	float gz_dps = gz * GYR_SCALE_DPS_PER_LSB;

	uint32_t red = 0;
	uint32_t ir  = 0;

	if (ppgAvailable) {
		ppg.check();
		if (ppg.available()) {
			red = ppg.getFIFORed();
			ir  = ppg.getFIFOIR();
			ppg.nextSample();
		}
	}

	float red_nA = red * PPG_ADC_FULL_SCALE_NA / PPG_ADC_MAX_COUNT;
	float ir_nA  = ir  * PPG_ADC_FULL_SCALE_NA / PPG_ADC_MAX_COUNT;

	// Scheduled elapsed time from recording start
	uint32_t t_ms = sampleTimeMs - recordStartMs;

	logFile.print(t_ms);  logFile.print(",");
	logFile.print(red_nA, 6);   logFile.print(",");
	logFile.print(ir_nA, 6);    logFile.print(",");
	logFile.print(ax_g, 6);    logFile.print(",");
	logFile.print(ay_g, 6);    logFile.print(",");
	logFile.print(az_g, 6);    logFile.print(",");
	logFile.print(gx_dps, 6);  logFile.print(",");
	logFile.print(gy_dps, 6);  logFile.print(",");
	logFile.println(gz_dps, 6);
}

// =========================
// SETUP
// =========================
void TinZrWearableSD_Class::begin() {
	TinZrWearableSDConfig defaultCfg;
	begin(defaultCfg);
}

void TinZrWearableSD_Class::begin(const TinZrWearableSDConfig& config) {
	cfg = config;

	Serial.begin(115200);
	delay(300);

	pinMode(PB_PIN, INPUT_PULLUP);

	pixel.begin();
	pixel.setBrightness(25);
	pixel.clear();
	pixel.show();
	setStoppedLED();

	Wire.begin();
	SPI.begin();

	Serial.println("Init IMU...");
	if (!initIMU()) {
		Serial.println("IMU FAIL");
		while (1) {
			setStoppedLED();
			delay(150);
			setLED(0, 0, 0);
			delay(150);
		}
	}
	Serial.println("IMU OK");

	ppgAvailable = initPPG();
	Serial.println(ppgAvailable ? "PPG OK" : "PPG OFF");

	if (!SD.begin(SD_CS_PIN)) {
		Serial.println("SD FAIL");
		while (1) {
			setStoppedLED();
			delay(80);
			setLED(0, 0, 0);
			delay(80);
		}
	}

	if (!ensureDir(logDir())) {
		Serial.println("DIR FAIL");
		while (1) {
			setStoppedLED();
			delay(250);
			setLED(0, 0, 0);
			delay(250);
		}
	}

	Serial.println("READY");
}

// =========================
// LOOP
// =========================
void TinZrWearableSD_Class::handle() {
	if (buttonPressedEvent()) {
		if (!recording) startRecording();
		else stopRecording();
	}

	if (!recording) return;

	uint32_t now = millis();

	// Catch up all missed scheduled sample slots
	while ((uint32_t)(now - scheduledMs) >= cfg.sample_interval_ms) {
		scheduledMs += cfg.sample_interval_ms;
		writeOneSample(scheduledMs);
	}

	if ((uint32_t)(now - lastFlushMs) >= FLUSH_INTERVAL_MS) {
		lastFlushMs = now;
		logFile.flush();
	}
}
