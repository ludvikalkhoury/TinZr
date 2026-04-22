#pragma once

#include <Arduino.h>
#include <Adafruit_NeoPixel.h>
#include <MAX30105.h>
#include <SD.h>

#ifndef PB_PIN
#define PB_PIN 9
#endif

#ifndef PIN_RGB_LED
#define PIN_RGB_LED 8
#endif

#ifndef SD_CS_PIN
#define SD_CS_PIN SS
#endif

struct TinZrWearableSDConfig {
	uint16_t    sample_interval_ms = 4;        // 250 Hz
	const char* sd_log_dir         = "/TinZrLogs";
};

class TinZrWearableSD_Class {
public:
	TinZrWearableSD_Class();

	void begin();
	void begin(const TinZrWearableSDConfig& cfg);
	void handle();

private:
	static constexpr uint8_t LSM6_ADDR     = 0x6A; // try 0x6B if needed
	static constexpr uint8_t REG_CTRL1_XL  = 0x10;
	static constexpr uint8_t REG_CTRL2_G   = 0x11;
	static constexpr uint8_t REG_CTRL3_C   = 0x12;
	static constexpr uint8_t REG_OUTX_L_G  = 0x22;
	static constexpr uint8_t WHO_AM_I_LSM6 = 0x0F;

	static constexpr uint8_t MAX30102_ADDR = 0x57;
	static constexpr uint8_t REG_FIFO_WR_PTR = 0x04;
	static constexpr uint8_t REG_OVF_COUNTER = 0x05;
	static constexpr uint8_t REG_FIFO_RD_PTR = 0x06;

	static constexpr uint32_t FLUSH_INTERVAL_MS  = 500;
	static constexpr float ACC_SCALE_G_PER_LSB   = 0.000244f;
	static constexpr float GYR_SCALE_DPS_PER_LSB = 0.035f;
	static constexpr float PPG_ADC_FULL_SCALE_NA = 16384.0f;
	static constexpr float PPG_ADC_MAX_COUNT     = 262143.0f;

	TinZrWearableSDConfig cfg;

	Adafruit_NeoPixel pixel;
	MAX30105 ppg;

	File logFile;

	bool recording = false;
	bool ppgAvailable = false;

	bool lastButton = false;
	bool stableButton = false;
	uint32_t lastDebounceMs = 0;

	uint32_t recordStartMs = 0;     // real millis() at recording start
	uint32_t scheduledMs   = 0;     // ideal sample schedule in millis()
	uint32_t lastFlushMs   = 0;

	void setLED(uint8_t r, uint8_t g, uint8_t b);
	void setStoppedLED();
	void setRecordingLED();

	bool readButtonPressed();
	bool buttonPressedEvent();

	bool ensureDir(const char* path);
	const char* logDir() const;
	String makeFilename();

	void lsm6Write8(uint8_t reg, uint8_t val);
	bool lsm6ReadMulti(uint8_t reg, uint8_t* buf, uint8_t len);
	bool initIMU();

	void maxWrite8(uint8_t reg, uint8_t val);
	bool initPPG();

	void startRecording();
	void stopRecording();
	void writeLogHeader(const String& filename);
	void writeOneSample(uint32_t sampleTimeMs);
};

extern TinZrWearableSD_Class TinZrWearableSD_;
