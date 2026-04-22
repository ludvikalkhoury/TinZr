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
	static constexpr size_t SD_LOG_LINE_MAX = 448;
	static constexpr size_t SD_LOG_QUEUE_DEPTH = 128;
	char     _sdLogQueue[SD_LOG_QUEUE_DEPTH][SD_LOG_LINE_MAX]{};
	size_t   _sdLogHead = 0;
	size_t   _sdLogTail = 0;
	size_t   _sdLogCount = 0;
	uint32_t _sdLogDroppedLinesTotal = 0;
	uint32_t _sdLogDroppedLinesReported = 0;
	unsigned long _lastSdFlushMs = 0;
	bool     _sdFlushPending = false;
	bool     _sdBackpressureFlag = false;
	uint32_t _lastLagIntervalsThisSample = 0;
	static constexpr size_t SD_LOG_DRAIN_MAX_LINES_PER_PASS = 2;
	static constexpr uint32_t SD_LOG_DRAIN_MAX_US_PER_PASS = 1500UL;
	static constexpr uint32_t SD_FLUSH_PERIOD_MS = 3000UL;
	static constexpr size_t SD_FLUSH_QUEUE_THRESHOLD = SD_LOG_QUEUE_DEPTH / 2;

	// ===== SD logging =====
	bool     _sdReady             = false;
	bool     _recordArmed         = false;   // set by GUI (START/STOP)
	bool     _recording           = false;   // actual: armed + SD log open
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
	uint8_t  _goodHeartbeatPairs  = 0;
	static constexpr uint8_t HEARTBEAT_SCALE_LOCK_MIN_PAIRS = 3;
	static constexpr uint32_t HEARTBEAT_MIN_INTERVAL_US = 250000UL;
	static constexpr uint32_t HEARTBEAT_MAX_INTERVAL_US = 4000000UL;
	static constexpr uint32_t HEARTBEAT_FRESHNESS_TIMEOUT_US = 2500000UL;
	static constexpr double HEARTBEAT_SCALE_MIN = 0.98;
	static constexpr double HEARTBEAT_SCALE_MAX = 1.02;
	static constexpr double HEARTBEAT_SCALE_GAIN_PRELOCK = 0.15;
	static constexpr double HEARTBEAT_SCALE_GAIN_LOCKED = 0.05;

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

	// Internal handlers
	static TinZrWearableSDClass* _self;
	static void _bleWriteStatic(const uint8_t* data, size_t len);
	void _handleBleCommand(const uint8_t* data, size_t len);

	void _handleBLE();
	void _handleDeferredBleActions();
	void _handleStreaming();
	void _drainSdLogBuffer(bool forceFlush = false);
	bool _queueSdLogLine(const char* line);
	void _resetSdLogBuffer();
	bool _beginRecording(uint32_t now_us);
	void _stopRecording(const char* reason = nullptr, bool writeEndMarker = false);
	void _applyStreamingChange(bool enable);
	bool _desiredStreamingEnabled() const;
	bool _isAcquisitionActive() const;
	bool _heartbeatIsFresh(uint32_t now_us) const;

	// NEW: SD hot-plug probe helper
	void _probeSDHotplug(unsigned long now);

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


	// LED
	void _updateLED();
	void _setErrorLED();
};

extern TinZrWearableSDClass TinZrWearableSD;
