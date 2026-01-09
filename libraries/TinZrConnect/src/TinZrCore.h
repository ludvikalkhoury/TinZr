#pragma once
#include <Arduino.h>
#include <Wire.h>

#if defined(ARDUINO)

// Arduino build: use normal includes so the Arduino dependency scanner works.
#include <Adafruit_LSM6DS3TRC.h>
#include <Adafruit_Sensor.h>
#include <MAX30105.h>

#else

// Non-Arduino build: optional friendly diagnostics.
#if !__has_include(<Adafruit_LSM6DS3TRC.h>)
  #error "Missing dependency: Adafruit LSM6DS3TR-C. Install `Adafruit LSM6D by Adafruit`."
#endif

#if !__has_include(<MAX30105.h>)
  #error "Missing dependency: MAX30105. Install `SparkFun MAX3010x Pulse and Proximity Sensor Library`."
#endif

#include <Adafruit_LSM6DS3TRC.h>
#include <Adafruit_Sensor.h>
#include <MAX30105.h>

#endif



struct TinZrImuSample {
	int16_t ax = 0, ay = 0, az = 0;  // raw LSB
	int16_t gx = 0, gy = 0, gz = 0;  // raw LSB
	int16_t temp = 0;                // optional raw if you implement it later
	uint32_t t_ms = 0;
};

struct TinZrImuSampleSI {
	float ax_g, ay_g, az_g;        // g
	float gx_dps, gy_dps, gz_dps;  // deg/s
	uint32_t t_ms;
};


struct TinZrPpgSample {
	uint32_t red = 0;
	uint32_t ir  = 0;
	uint32_t t_ms = 0;
};

struct TinZrSensorsConfig {
	uint8_t imu_addr = 0x6A;
	bool    i2c_fast = true;     // 400 kHz
	bool    init_ppg = true;     // PPG optional
};

// =============================
// TinZrCore  (battery, button, soft power, base LED)
// =============================
class TinZrCore {
public:
    void begin(uint8_t ledBrightness = 25);   // call from setup()
    void handle();  // call from loop()

    // Battery helpers
    float readBatteryVoltage() const;  // Volts
    int   readBatteryPercent() const;      // 0–100

    // Soft power
    bool isSoftOn() const { return _softOn; }
    void softOff();
    void softOn();
    
    // Read button
    bool readButtonState() const;
    
    
    // ---------- Sensors ----------
    bool sensorsBegin() {
        return sensorsBegin(TinZrSensorsConfig{});
    }
    bool sensorsBegin(const TinZrSensorsConfig& cfg);


	bool imuReady() const { return _imuReady; }
	bool ppgReady() const { return _ppgReady; }

	// Fast reads (non-blocking)
	bool imuReadRaw(TinZrImuSample& out);
	bool imuReadSI(TinZrImuSampleSI& out);

	
	bool ppgRead(TinZrPpgSample& out);
    
    // Convenience: read both, return what you got this loop
	// (IMU is usually always available when ready; PPG may not have a new sample)
	bool readImuPpg(TinZrImuSample& imu, TinZrPpgSample& ppg, bool& gotPpg);

    
    
    

    
private:
    // Hardware constants
    static constexpr int   PIN_BAT = A1;       // VBAT divider
    static constexpr bool  BUTTON_ACTIVE_LOW = true;
    static constexpr uint32_t LONG_PRESS_MS = 5000;  // 5s

    static constexpr float VREF    = 3.3f;
    static constexpr float ADC_MAX = 4095.0f;

    // Divider 220k (top) / 150k (bottom)
    static constexpr float DIVIDER_RATIO =
        150000.0f / (220000.0f + 150000.0f);

    static constexpr float VBAT_MIN = 3.3f;
    static constexpr float VBAT_MAX = 4.2f;

    bool     _buttonLast       = false;
    uint32_t _pressStart       = 0;
    bool     _longPressLatched = false;
    bool     _softOn           = true;


    // ---- IMU + PPG instances (owned by core now) ----
	Adafruit_LSM6DS3TRC _imu;
	MAX30105            _ppg;

	TinZrSensorsConfig  _scfg{};
	bool _imuReady = false;
	bool _ppgReady = false;

	// ---- LSM6 (register-level) ----
	static constexpr uint8_t REG_CTRL1_XL = 0x10;
	static constexpr uint8_t REG_CTRL2_G  = 0x11;
	static constexpr uint8_t REG_CTRL3_C  = 0x12;
	static constexpr uint8_t REG_OUTX_L_G = 0x22;

	void _lsm6Write8(uint8_t reg, uint8_t val);
	void _lsm6ReadMulti(uint8_t reg, uint8_t* buf, uint8_t len);
	void _lsm6Config();
    
    // ---- MAX30102/30105 (via SparkFun driver) ----
	void _ppgConfig();
	bool _ppgReadFast(uint32_t& red, uint32_t& ir);
    

    
};

extern TinZrCore TinZr;
