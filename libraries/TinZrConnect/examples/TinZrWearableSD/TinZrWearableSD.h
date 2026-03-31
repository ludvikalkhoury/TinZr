#pragma once

#include <Arduino.h>

#include "TinZrConfig.h"
#include "TinZrCore.h"
#include "TinZrBLE.h"          // TinZrBLEConnect (GATT)
#include "TinZrLED.h"          // TinZrStatusLED
#include "TinZrSD.h"           // TinZrSDLogger (SD card logging)

struct TinZrWearableSDConfig {
	const char* hostname           = "TinZrWearableSD";
	// Sampling interval in ms (4 ms ≈ 250 Hz target, resampled to 240 Hz on hub)
	uint16_t    sample_interval_ms = 4;
	bool        enable_pc_clock_drift_correction = true;

	// nullptr => use TINZR_SD_LOG_DIR (from TinZrConfig.h)
	const char* sd_log_dir         = nullptr;
};

// We keep enum for future flexibility, but effectively use BLE_ONLY.
enum class TinZrWearSDMode : uint8_t {
	BLE_ONLY = 0,
	SD_ONLY,
	BLE_AND_SD,
	NUM_MODES
};

class TinZrWearableSDClass {
public:
	TinZrWearableSDClass();
	void begin(const TinZrWearableSDConfig& cfg);
	void handle();
	void forceBatteryUpdate();

private:
	TinZrWearableSDConfig _cfg{};

#if TINZR_ENABLE_BLE
	TinZrBLEConnect _ble;
	bool            _bleStarted      = false;
	bool            _bleWasConnected = false;
#endif

	// State
	TinZrWearSDMode _mode           = TinZrWearSDMode::BLE_AND_SD;

	// IMU required; PPG optional
	bool _sensorsReady = false;  // kept for compatibility (== _imuReady)
	bool _imuReady     = false;  // IMU present/configured
	bool _ppgReady     = false;  // PPG present/configured (optional)

	bool     _streaming    = false;
	uint32_t _lastSampleUs = 0;
	bool     _wasSoftOn    = false;

	// ===== SD logging controlled by PC heartbeat =====
	bool     _sdReady             = false;
	bool     _recordArmed         = false;   // set by GUI (START/STOP)
	bool     _recording           = false;   // actual: armed + heartbeat OK
	String   _participant         = "";

	uint32_t _lastHeartbeatUs     = 0;       // local micros when last T: received
	uint32_t _pcAnchorLocalUs     = 0;       // local micros corresponding to _pcAnchorUs
	uint64_t _pcAnchorUs          = 0;       // PC epoch us received in T:
	bool     _hasPcAnchor         = false;
	uint32_t _sampleIdx 					= 0;
	double   _pcUsPerLocalUs      = 1.0;     // estimated clock ratio from heartbeat history
	bool     _hasClockScale       = false;
	uint64_t _prevPcAnchorUs      = 0;
	uint32_t _prevPcAnchorLocalUs = 0;
	uint64_t _recordStartPcUs     = 0;
	uint32_t _recordStartLocalUs  = 0;

	// --- deferred BLE actions (do NOT notify inside onWrite) ---
	volatile bool _pendingSdList = false;
	volatile bool _pendingBattReply = false;
	volatile bool _pendingStartGet = false;
	volatile bool _pendingHeartbeat = false;

	String _pendingGetName;
	uint64_t _pendingPcAnchorUs = 0;


	// Store the *string* timestamp from X: for filename + header
	String   _pcAnchorStr         = "";
	bool     _hasPcAnchorStr      = false;

	// NEW: SD hot-plug probing state
	unsigned long _lastSdProbeMs  = 0;

	// NEW: whether we already wrote the metadata header for the current file
	bool     _logHeaderWritten    = false;

	static constexpr uint32_t HEARTBEAT_TIMEOUT_MS = 6500UL; // stop logging if heartbeat missing
	static constexpr uint32_t HEARTBEAT_TIMEOUT_US = HEARTBEAT_TIMEOUT_MS * 1000UL;

	// Internal handlers
	static TinZrWearableSDClass* _self;
	static void _bleWriteStatic(const uint8_t* data, size_t len);
	void _handleBleCommand(const uint8_t* data, size_t len);

	void _handleBLE();
	void _handleDeferredBleActions();
	void _handleStreaming();
	void _applyStreamingChange(bool enable);

	// NEW: SD hot-plug probe helper
	void _probeSDHotplug(unsigned long now);

	// NEW: write metadata header + csv header (called once per log open)
	void _writeLogHeaders(const String& file_base);


// =========================
// SD retrieval over BLE (verified transfer)
// =========================
void _sendSdList();
void _startSdTransfer(const String& name);
void _pumpSdTransfer();
uint32_t _crc32_file(File& f);
uint32_t _crc32_bytes(const uint8_t* data, size_t len, uint32_t crc);

bool     _sdXferActive      = false;
bool     _sdXferWaitingAck  = false;
uint16_t _sdXferSeq         = 0;
uint16_t _sdXferLastSeqSent = 0;
String   _sdXferName;
File     _sdXferFile;
uint32_t _sdXferFileCrc32   = 0;
size_t   _sdXferFileSize    = 0;
uint8_t  _sdXferLastPayload[200];
uint16_t _sdXferLastLen     = 0;
volatile bool _sdListPending = false;


	// LED
	void _updateLED();
	void _setErrorLED();
};

extern TinZrWearableSDClass TinZrWearableSD;
