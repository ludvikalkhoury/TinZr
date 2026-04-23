#include "TinZrWearableSD_.h"

#include <SPI.h>
#include <string.h>
#include <Wire.h>

TinZrWearableSD_Class TinZrWearableSD_;
TinZrWearableSD_Class* TinZrWearableSD_Class::self = nullptr;

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
// BLE start listener
// =========================
void TinZrWearableSD_Class::bleWriteStatic(const uint8_t* data, size_t len) {
	if (!self || !data || len == 0) return;
	self->handleBleCommand(data, len);
}

void TinZrWearableSD_Class::handleBleCommand(const uint8_t* data, size_t len) {
	if (recording) return;

	String cmd;
	cmd.reserve(len + 1);
	for (size_t i = 0; i < len; i++) {
		cmd += char(data[i]);
	}
	cmd.trim();

	if (cmd.equalsIgnoreCase("S") || cmd.equalsIgnoreCase("START")) {
		if (bleStartArmed && subjectName[0] != '\0' && pcStartTimestamp[0] != '\0') {
			pendingBleStart = true;
			bleStartArmed = false;
		} else {
			Serial.println("BLE start ignored: missing GUI metadata");
		}
	} else if (cmd.equalsIgnoreCase("BAT")) {
		sendBatteryLevel();
	} else if (cmd.startsWith("T:")) {
		String stamp = cmd.substring(2);
		stamp.trim();
		stamp.toCharArray(pcStartTimestamp, sizeof(pcStartTimestamp));
		if (subjectName[0] != '\0') bleStartArmed = true;
	} else if (cmd.startsWith("D:")) {
		String name = cmd.substring(2);
		name.trim();
		name.toCharArray(deviceName, sizeof(deviceName));
	} else if (cmd.startsWith("P:")) {
		String subject = cmd.substring(2);
		subject.trim();
		subject.toCharArray(subjectName, sizeof(subjectName));
		if (pcStartTimestamp[0] != '\0') bleStartArmed = true;
	}
}

void TinZrWearableSD_Class::initBLEStartListener() {
#if TINZR_ENABLE_BLE
	if (bleStarted) return;

	TinZrBLEConfig bleCfg;
	bleCfg.device_name = (cfg.hostname && cfg.hostname[0] != '\0') ? cfg.hostname : "TinZr";
	bleCfg.preferred_mtu = 247;

	self = this;
	bleStarted = ble.begin(bleCfg);
	if (bleStarted) {
		ble.onWrite(&TinZrWearableSD_Class::bleWriteStatic);
		Serial.println("BLE start listener ready");
	} else {
		Serial.println("BLE start listener off");
	}
#endif
}

void TinZrWearableSD_Class::handleBLEStartListener() {
#if TINZR_ENABLE_BLE
	if (bleStarted && !recording) {
		ble.handle();

		bool nowConnected = ble.connected();
		if (nowConnected) {
			uint32_t now = millis();
			if (!lastBleConnected) {
				bleBlinkMs = now;
				bleBlinkOn = false;
			}
			if ((uint32_t)(now - bleBlinkMs) >= 250) {
				bleBlinkMs = now;
				bleBlinkOn = !bleBlinkOn;
				if (bleBlinkOn) setLED(0, 255, 0);
				else setStoppedLED();
			}
		} else if (lastBleConnected) {
			bleBlinkOn = false;
			bleBlinkMs = 0;
			setStoppedLED();
		}
		lastBleConnected = nowConnected;
	}
#endif
}

void TinZrWearableSD_Class::stopBLEStartListener() {
#if TINZR_ENABLE_BLE
	if (bleStarted) {
		ble.end();
		bleStarted = false;
		lastBleConnected = false;
		bleBlinkMs = 0;
		bleBlinkOn = false;
		self = nullptr;
		Serial.println("BLE start listener stopped");
	}
#endif
}

float TinZrWearableSD_Class::readBatteryVoltage() const {
	const int N = 16;
	uint32_t acc = 0;

	for (int i = 0; i < N; i++) {
		acc += analogRead(PIN_BAT);
		delay(2);
	}

	float raw = acc / float(N);
	float vDiv = raw * (BAT_VREF / BAT_ADC_MAX);
	return vDiv / BAT_DIVIDER_RATIO;
}

int TinZrWearableSD_Class::readBatteryPercent() const {
	float vbat = readBatteryVoltage();
	if (vbat <= BAT_VBAT_MIN) return 0;
	if (vbat >= BAT_VBAT_MAX) return 100;

	float frac = (vbat - BAT_VBAT_MIN) / (BAT_VBAT_MAX - BAT_VBAT_MIN);
	int pct = int(frac * 100.0f + 0.5f);

	if (pct < 0) pct = 0;
	if (pct > 100) pct = 100;
	return pct;
}

void TinZrWearableSD_Class::sendBatteryLevel() {
#if TINZR_ENABLE_BLE
	if (!bleStarted || !ble.connected()) return;

	int pct = readBatteryPercent();
	char msg[16];
	snprintf(msg, sizeof(msg), "BAT:%d", pct);
	ble.sendNotify((const uint8_t*)msg, strlen(msg));
	delay(30);
#endif
}

// =========================
// Button
// =========================
bool TinZrWearableSD_Class::readButtonPressed() {
	return digitalRead(PB_PIN) == LOW;
}

bool TinZrWearableSD_Class::buttonHoldEvent() {
	bool now = readButtonPressed();
	bool event = false;

	if (now != lastButton) {
		lastDebounceMs = millis();
	}

	if ((millis() - lastDebounceMs) > 40) {
		if (now != stableButton) {
			stableButton = now;

			if (stableButton) {
				buttonHoldActive = true;
				buttonHoldEventSent = false;
				buttonHoldStartMs = millis();
			} else {
				buttonHoldActive = false;
				buttonHoldEventSent = false;
				buttonHoldStartMs = 0;
			}
		}
	}

	if (buttonHoldActive && now) {
		uint32_t heldMs = millis() - buttonHoldStartMs;

		if (heldMs >= 3000 && !buttonHoldEventSent) {
			buttonHoldEventSent = true;
			event = true;
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
	String stem = "";
	if (subjectName[0] != '\0') {
		String subject = String(subjectName);
		if (subject.startsWith("sub-")) {
			stem += subject;
		} else {
			stem += "sub-";
			stem += subject;
		}
	} else {
		stem += "sub-unknown";
	}

	if (deviceName[0] != '\0') {
		stem += "_device-";
		stem += String(deviceName);
	}

	if (pcStartTimestamp[0] != '\0') {
		stem += "_";
		stem += String(pcStartTimestamp);
	}

	for (size_t j = 0; j < stem.length(); j++) {
		char c = stem[j];
		bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '_';
		if (!ok) stem.setCharAt(j, '_');
	}

	String path = String(logDir()) + "/" + stem + ".csv";
	if (!SD.exists(path.c_str())) return path;

	for (int i = 2; i < 10000; i++) {
		String suffix = "_" + String(i);
		String candidate = String(logDir()) + "/" + stem + suffix;
		for (size_t j = 0; j < candidate.length(); j++) {
			char c = candidate[j];
			bool ok = (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '_';
			if (!ok && c != '/') candidate.setCharAt(j, '_');
		}
		candidate += ".csv";
		if (!SD.exists(candidate.c_str())) return candidate;
	}
	return String(logDir()) + "/" + stem + "_9999.csv";
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
	stopBLEStartListener();

	if (!ensureDir(logDir())) {
		Serial.println("DIR FAIL");
		setStoppedLED();
		initBLEStartListener();
		return;
	}

	String fname = makeFilename();
	logFile = SD.open(fname.c_str(), FILE_WRITE);

	if (!logFile) {
		Serial.println("FILE OPEN FAIL");
		setStoppedLED();
		initBLEStartListener();
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
	bleStartArmed = false;
	pendingBleStart = false;
	pcStartTimestamp[0] = '\0';
	subjectName[0] = '\0';
	setStoppedLED();
	initBLEStartListener();

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
	logFile.print("# device_name: ");
	logFile.println(deviceName[0] != '\0' ? deviceName : "unavailable");
	logFile.print("# subject_id: ");
	logFile.println(subjectName[0] != '\0' ? subjectName : "unknown");
	logFile.print("# pc_start_timestamp: ");
	logFile.println(pcStartTimestamp[0] != '\0' ? pcStartTimestamp : "unavailable");
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
	logFile.println("# pc_start_timestamp_note: PC timestamp sent by the GUI at the start command, right before SD logging starts");
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
	if (cfg.hostname && cfg.hostname[0] != '\0') {
		String name = String(cfg.hostname);
		name.trim();
		name.toCharArray(deviceName, sizeof(deviceName));
	}

	Serial.begin(115200);
	delay(300);

	pinMode(PB_PIN, INPUT_PULLUP);
	pinMode(PIN_BAT, INPUT);

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
		initBLEStartListener();
		while (1) {
			handleBLEStartListener();
			setStoppedLED();
			delay(150);
			handleBLEStartListener();
			setLED(0, 0, 0);
			delay(150);
		}
	}
	Serial.println("IMU OK");

	ppgAvailable = initPPG();
	Serial.println(ppgAvailable ? "PPG OK" : "PPG OFF");

	if (!SD.begin(SD_CS_PIN)) {
		Serial.println("SD FAIL");
		initBLEStartListener();
		while (1) {
			handleBLEStartListener();
			setStoppedLED();
			delay(80);
			handleBLEStartListener();
			setLED(0, 0, 0);
			delay(80);
		}
	}

	if (!ensureDir(logDir())) {
		Serial.println("DIR FAIL");
		initBLEStartListener();
		while (1) {
			handleBLEStartListener();
			setStoppedLED();
			delay(250);
			handleBLEStartListener();
			setLED(0, 0, 0);
			delay(250);
		}
	}

	initBLEStartListener();
	Serial.println("READY");
}

// =========================
// LOOP
// =========================
void TinZrWearableSD_Class::handle() {
	handleBLEStartListener();

	if (pendingBleStart && !recording) {
		pendingBleStart = false;
		bleStartArmed = false;
		sendBatteryLevel();
		startRecording();
	}

	if (buttonHoldEvent()) {
		if (!recording) {
			bleStartArmed = false;
			pendingBleStart = false;
			pcStartTimestamp[0] = '\0';
			subjectName[0] = '\0';
			startRecording();
		}
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
