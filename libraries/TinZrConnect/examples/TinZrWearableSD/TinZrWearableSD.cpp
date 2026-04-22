// ====================
// TinZrWearableSD.cpp
// ====================
#include "TinZrWearableSD.h"
#include <SD.h>
#include <string.h>
#include "TinZrLED.h"

#include "spo2_algorithm.h"  // Maxim HR/SpO2 algorithm

// Static self pointer for BLE callback trampoline
TinZrWearableSDClass* TinZrWearableSDClass::_self = nullptr;

TinZrWearableSDClass TinZrWearableSD;

// ===== Packed binary frames for BLE =====
static const uint8_t FRAMES_PER_PACKET = 9;
static constexpr float PPG_ADC_FULL_SCALE_NA = 16384.0f;
static constexpr float PPG_ADC_MAX_COUNT = 262143.0f;

// Scaling factors (match Python viewer)
static constexpr float ACC_SCALE = 1000.0f;  // accel: m/s^2 -> milli-units (viewer expects *1000)
static constexpr float GYR_SCALE = 100.0f;   // gyro: dps -> centi-units (viewer expects *100)
static constexpr float G_SENS_DPS_PER_LSB = 35e-3f; // 35 mdps/LSB for +/-1000 dps

// --- HR / SpO2 + battery in frame ---
struct __attribute__((packed)) WearFrame {
	int16_t  ax, ay, az;   // accel * ACC_SCALE
	int16_t  gx, gy, gz;   // gyro  * GYR_SCALE
	float    red_nA;       // approximate PPG red photodiode current
	float    ir_nA;        // approximate PPG IR photodiode current
	uint8_t  hr_bpm;       // last computed heart rate
	uint8_t  spo2_pct;     // last computed SpO2
	uint8_t  batt_pct;     // last battery % (0-100)
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

// ====== SD hot-plug probing ======
static const unsigned long SD_PROBE_PERIOD_MS = 2000UL; // try every 2 seconds
static const unsigned long SD_IDLE_LIVENESS_CHECK_PERIOD_MS = 5000UL;

static uint64_t _estimate_pc_time_us(uint64_t anchor_pc_us, uint32_t anchor_local_us, uint32_t now_local_us) {
	return anchor_pc_us + (uint32_t)(now_local_us - anchor_local_us);
}

static uint64_t _estimate_pc_time_us_scaled(
	uint64_t anchor_pc_us,
	uint32_t anchor_local_us,
	uint32_t now_local_us,
	double pc_us_per_local_us
) {
	const uint32_t dt_local_us = (uint32_t)(now_local_us - anchor_local_us);
	const double dt_pc_us = (double)dt_local_us * pc_us_per_local_us;
	return anchor_pc_us + (uint64_t)(dt_pc_us + 0.5);
}

// =============================================================
// Battery helper
// =============================================================

// Read battery % from TinZrCore and clamp 0..100
static uint16_t read_battery_pct() {
	int pct = TinZr.readBatteryPercent();
	if (pct < 0)   pct = 0;
	if (pct > 100) pct = 100;
	return static_cast<uint16_t>(pct);
}

// OPTIONAL: call this from any "battery query" command handler you already have
void TinZrWearableSDClass::forceBatteryUpdate() {
	// force a refresh on the next _handleStreaming() tick
	sLastBattSampleMs = 0;
}

// =============================================================
// HR / SpO2 computation using Maxim algorithm
// =============================================================

// Uses a sliding buffer of 100 samples (BUFFER_SIZE in spo2_algorithm.h)
static void update_hr_spo2_from_ppg(uint32_t red, uint32_t ir) {
	const int N_SAMPLES = BUFFER_SIZE;
	static uint32_t ir_buf[N_SAMPLES];
	static uint32_t red_buf[N_SAMPLES];
	static int idx25         = 0;
	static int decim_counter = 0;

	// ---- 1) Decimate 250 Hz -> 25 Hz ----
	decim_counter++;
	if (decim_counter < 10) return;
	decim_counter = 0;

	// ---- 2) If no finger -> reset algorithm state ----
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

	if (hr_valid && hr > 20 && hr < 230) sLastHr = (uint16_t)hr;
	if (spo2_valid && spo2 >= 60 && spo2 <= 100) sLastSpo2 = (uint16_t)spo2;
}

// =============================================================
// PC time parsing + filename sanitization helpers
// =============================================================

// days-from-civil (Howard Hinnant), returns days since 1970-01-01
static int64_t _days_from_civil(int y, unsigned m, unsigned d) {
	y -= m <= 2;
	const int era = (y >= 0 ? y : y - 399) / 400;
	const unsigned yoe = (unsigned)(y - era * 400);
	const unsigned doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + d - 1;
	const unsigned doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
	return (int64_t)era * 146097 + (int64_t)doe - 719468;
}

static void _civil_from_days(int64_t z, int& y, unsigned& m, unsigned& d) {
	z += 719468;
	const int64_t era = (z >= 0 ? z : z - 146096) / 146097;
	const unsigned doe = (unsigned)(z - era * 146097);
	const unsigned yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
	y = (int)yoe + (int)(era * 400);
	const unsigned doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
	const unsigned mp = (5 * doy + 2) / 153;
	d = doy - (153 * mp + 2) / 5 + 1;
	m = mp + (mp < 10 ? 3 : -9);
	y += (m <= 2);
}

static void _format_epoch_us_iso(uint64_t epoch_us, char* out, size_t out_size) {
	const uint64_t total_seconds = epoch_us / 1000000ULL;
	const uint32_t micros = (uint32_t)(epoch_us % 1000000ULL);
	const int64_t days = (int64_t)(total_seconds / 86400ULL);
	const uint32_t sec_of_day = (uint32_t)(total_seconds % 86400ULL);

	int year = 1970;
	unsigned month = 1;
	unsigned day = 1;
	_civil_from_days(days, year, month, day);

	const uint32_t hour = sec_of_day / 3600UL;
	const uint32_t minute = (sec_of_day % 3600UL) / 60UL;
	const uint32_t second = sec_of_day % 60UL;

	snprintf(
		out, out_size,
		"%04d-%02u-%02uT%02lu:%02lu:%02lu.%06lu",
		year,
		month,
		day,
		(unsigned long)hour,
		(unsigned long)minute,
		(unsigned long)second,
		(unsigned long)micros
	);
}

static bool _is_digit(char c) { return (c >= '0' && c <= '9'); }

static bool _all_digits(const String& s) {
	for (size_t i = 0; i < s.length(); ++i) {
		if (!_is_digit(s.charAt(i))) return false;
	}
	return s.length() > 0;
}

// Parse formats like (examples):
//   "2026-01-09T18:38:41.598802"
//   "2026-01-09_18:38:41.598802"
//   "2026-01-09 18:38:41.598802"
// returns true if parsed; outputs epoch us (no timezone offset, local-ish)
static bool _parse_pc_timestr_to_epoch_us(const String& in, uint64_t& out_us) {
	String vals[8];
	int got = 0;
	String s = in;
	s.trim();
	if (!s.length()) return false;

	String cur;
	for (size_t i = 0; i < s.length(); ++i) {
		char c = s.charAt(i);
		if (_is_digit(c)) {
			cur += c;
		} else {
			if (cur.length()) {
				if (got < 8) vals[got++] = cur;
				cur = "";
			}
		}
	}
	if (cur.length()) {
		if (got < 8) vals[got++] = cur;
	}

	if (got < 6) return false;

	int Y  = vals[0].toInt();
	int Mo = vals[1].toInt();
	int D  = vals[2].toInt();
	int H  = vals[3].toInt();
	int Mi = vals[4].toInt();
	int Se = vals[5].toInt();

	if (Y < 1970 || Mo < 1 || Mo > 12 || D < 1 || D > 31) return false;
	if (H < 0 || H > 23 || Mi < 0 || Mi > 59 || Se < 0 || Se > 60) return false;

	uint64_t frac_us = 0;
	if (got >= 7) {
		String frac = vals[6];
		if (frac.length() > 6) frac.remove(6);
		while (frac.length() < 6) frac += '0';
		frac_us = frac.toInt();
	}

	int64_t days = _days_from_civil(Y, (unsigned)Mo, (unsigned)D);
	int64_t sec  = days * 86400LL + (int64_t)H * 3600LL + (int64_t)Mi * 60LL + (int64_t)Se;
	int64_t us   = sec * 1000000LL + (int64_t)frac_us;

	out_us = (us < 0) ? 0ULL : (uint64_t)us;
	return true;
}

// returns true if parsed; outputs epoch ms (no timezone offset, local-ish)
static bool _parse_pc_timestr_to_epoch_ms(const String& in, uint64_t& out_ms) {
	uint64_t out_us = 0;
	if (!_parse_pc_timestr_to_epoch_us(in, out_us)) return false;
	out_ms = out_us / 1000ULL;
	return true;
}

// Keep [A-Za-z0-9_-], everything else -> '_'
static String _sanitize_subject_for_filename(const String& subj) {
	String part = subj;
	part.trim();
	if (!part.length()) part = "anon";

	String safe;
	safe.reserve(part.length());
	for (size_t i = 0; i < part.length(); ++i) {
		char c = part.charAt(i);
		if ((c >= '0' && c <= '9') ||
			(c >= 'A' && c <= 'Z') ||
			(c >= 'a' && c <= 'z') ||
			c == '_' || c == '-') {
			safe += c;
		} else {
			safe += '_';
		}
	}
	while (safe.indexOf("__") >= 0) safe.replace("__", "_");
	if (safe.length() > 32) safe = safe.substring(0, 32);
	return safe;
}

// "2026-01-09T18:38:41:598.8028" -> "2026-01-09T18-38-41-598-8028"
static String _sanitize_pc_time_for_filename(const String& ts) {
	String s = ts;
	s.trim();
	if (!s.length()) return "no_pc_time";

	String out;
	out.reserve(s.length());

	for (size_t i = 0; i < s.length(); ++i) {
		char c = s.charAt(i);

		if ((c >= '0' && c <= '9') ||
			(c >= 'A' && c <= 'Z') ||
			(c >= 'a' && c <= 'z') ||
			c == '-' || c == '_' || c == 'T') {
			out += c;
		} else if (c == ':' || c == '.') {
			out += '-';
		} else {
			out += '_';
		}
	}

	while (out.indexOf("--") >= 0) out.replace("--", "-");
	while (out.indexOf("__") >= 0) out.replace("__", "_");
	while (out.endsWith("-") || out.endsWith("_")) out.remove(out.length() - 1);

	if (out.length() > 48) out = out.substring(0, 48);
	return out;
}

// =============================================================
// NEW: Metadata header writer (goes BEFORE CSV header)
// =============================================================
static void _write_log_metadata_header(
	const String& fileBase,
	const TinZrWearableSDConfig& cfg,
	const String& participant,
	const String& pcAnchorStr,
	bool hasPcAnchorStr,
	uint64_t pcAnchorUs,
	uint32_t startLocalUs,
	double pcUsPerLocalUs,
	bool hasClockScale
) {
	TinZrSD.writeLine("# ================================================");
	TinZrSD.writeLine("# TinZrWearableSD Log");
	TinZrSD.writeLine("# ================================================");
	TinZrSD.writeLine(String("# file_base: ") + fileBase);
	TinZrSD.writeLine(String("# participant: ") + (participant.length() ? participant : "anon"));

	if (hasPcAnchorStr && pcAnchorStr.length()) {
		TinZrSD.writeLine(String("# start_time_from_pc_datetime: ") + pcAnchorStr);
	} else {
		TinZrSD.writeLine(pcAnchorUs != 0
			? String("# start_time_from_pc_us: ") + String((unsigned long long)pcAnchorUs)
			: String("# start_time_from_pc_us: unavailable"));
	}

	TinZrSD.writeLine(String("# time_sample_interval_ms: ") + String(cfg.sample_interval_ms));
	TinZrSD.writeLine(String("# time_start_local_micros: ") + String((unsigned long)startLocalUs));
	TinZrSD.writeLine(String("# time_pc_clock_drift_correction_enabled: ") + String(cfg.enable_pc_clock_drift_correction ? "true" : "false"));
	TinZrSD.writeLine(String("# time_scale_pc_us_per_local_us: ") + String(pcUsPerLocalUs, 9));
	TinZrSD.writeLine(String("# time_scale_estimated_from_pc_heartbeats: ") + String(hasClockScale ? "true" : "false"));
	TinZrSD.writeLine("# heartbeat_freshness_timeout_us: 2500000");
	TinZrSD.writeLine("# sd_log_queue_depth: 128");
	TinZrSD.writeLine("# time_note: local_elapsed_us is the canonical device timeline; heartbeat-derived columns are optional calibration metadata");
	
	// ---- IMU metadata (matches TinZrCore.cpp config) ----
	TinZrSD.writeLine("# imu_accel_units: g");
	TinZrSD.writeLine("# imu_gyro_units: dps");
	TinZrSD.writeLine("# imu_accel_fullscale: +/-8g");
	TinZrSD.writeLine("# imu_gyro_fullscale: +/-1000dps");
	TinZrSD.writeLine("# ppg_units: approximate MAX30102 photodiode current in nanoamps");
	TinZrSD.writeLine("# ppg_adc_full_scale_nA: 16384");
	TinZrSD.writeLine("# ppg_adc_resolution_bits: 18");
	TinZrSD.writeLine("# ppg_adc_max_count: 262143");
	TinZrSD.writeLine("# ppg_current_conversion: current_nA = adc_count * 16384 / 262143");
	
	TinZrSD.writeLine("# -----------------------------------------------");
	TinZrSD.writeLine("#"); // blank/comment line separator
}

// =============================================================
// SD hot-plug probe helper
// =============================================================
void TinZrWearableSDClass::_probeSDHotplug(unsigned long now) {
	// If ready, nothing to do
	if (_sdReady) return;

	if (_lastSdProbeMs != 0 && (now - _lastSdProbeMs) < SD_PROBE_PERIOD_MS) return;
	_lastSdProbeMs = now;

	TinZrSDConfig scfg;
	scfg.cs_pin     = SS;
	scfg.log_dir    = (_cfg.sd_log_dir && _cfg.sd_log_dir[0] != '\0') ? _cfg.sd_log_dir : TINZR_SD_LOG_DIR;
	scfg.auto_mkdir = true;

	bool ok = TinZrSD.begin(scfg);
	if (ok) {
		_sdReady = true;
		TinZrSD.setRecording(false);

		Serial.println("SD detected (hot-plug) -> logging enabled");
		_updateLED();
	} else {
		_sdReady = false; // stay failing
	}
}

// ---------------- TinZrWearableSD ------------------
TinZrWearableSDClass::TinZrWearableSDClass() {}

void TinZrWearableSDClass::begin(const TinZrWearableSDConfig& cfg) {
	_cfg = cfg;

	Serial.begin(115200);
	delay(200);

	Serial.println();
	Serial.println("===== TinZrWearable (BLE GATT, TinZrCore sensors) =====");

	// Core
	TinZr.begin();
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
		bcfg.preferred_mtu = 247;

		_bleStarted = _ble.begin(bcfg);
		if (!_bleStarted) {
			Serial.println("TinZrWearable: BLE start failed");
			TinZrLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
		} else {
			Serial.println("BLE started (advertising)");

			_self = this;
			_ble.onWrite(&TinZrWearableSDClass::_bleWriteStatic);

			_bleWasConnected = _ble.connected();
			TinZrLED.setMode(TinZrStatusLED::Mode::BLE_ADVERTISING);
		}
	}
#else
	Serial.println("BLE disabled at compile time");
#endif

	// ---------- Sensors ----------
	Serial.println("Initializing sensors via TinZrCore...");
	bool ok = TinZr.sensorsBegin();
	_imuReady = TinZr.imuReady();
	_ppgReady = TinZr.ppgReady();
	_sensorsReady = _imuReady;

	if (!ok || !_imuReady) {
		Serial.println("IMU NOT found -> cannot stream");
	} else if (_ppgReady) {
		Serial.println("Sensors ready (IMU + PPG)");
	} else {
		Serial.println("PPG NOT found -> will stream IMU, send PPG zeros");
	}

	// ---------- SD ----------
	{
		TinZrSDConfig scfg;
		scfg.cs_pin     = SS;
		scfg.log_dir    = (_cfg.sd_log_dir && _cfg.sd_log_dir[0] != '\0') ? _cfg.sd_log_dir : TINZR_SD_LOG_DIR;
		scfg.auto_mkdir = true;

		_sdReady = TinZrSD.begin(scfg);
		if (_sdReady) {
			Serial.println("SD ready");
			TinZrSD.setRecording(false);
		} else {
			Serial.println("SD not ready (logging disabled until inserted)");
		}
	}

	// SD probe state
	_lastSdProbeMs = 0;

	// Default mode and streaming
	_mode         = TinZrWearSDMode::BLE_ONLY;
	_streaming    = false;
	_lastSampleUs = 0;

	sFrameCount         = 0;
	sLastHr             = 0;
	sLastSpo2           = 0;
	sLastHrSpo2UpdateMs = 0;
	sLastBattPct        = 0;
	sLastBattSampleMs   = 0;

	_pcAnchorStr = "";
	_hasPcAnchorStr = false;

	_hasPcAnchor = false;
	_pcAnchorUs = 0;
	_pcAnchorLocalUs = 0;
	_lastHeartbeatUs = 0;
	_pcUsPerLocalUs = 1.0;
	_hasClockScale = false;
	_prevPcAnchorUs = 0;
	_prevPcAnchorLocalUs = 0;
	_recordStartPcUs = 0;
	_recordStartLocalUs = 0;
	_goodHeartbeatPairs = 0;

	_recordArmed = false;
	_recording = false;
	_sdLogDroppedLinesTotal = 0;
	_sdLogDroppedLinesReported = 0;
	_sdFlushPending = false;
	_sdBackpressureFlag = false;
	_lastLagIntervalsThisSample = 0;

	_participant = "";

	_bleWasConnected = false;

	_updateLED();

	Serial.println("Controls:");
	Serial.println("  - Streaming auto-starts on BLE connect.");
	Serial.println("  - SD logging can continue autonomously after BLE disconnect.");
}

void TinZrWearableSDClass::handle() {
	TinZr.handle();
	// Make sure LED animations run
	TinZrLED.handle();
	static uint32_t lastSdIdleLivenessCheckMs = 0;

	if (!TinZr.isSoftOn()) {
		if (_recording) {
			_stopRecording("soft off");
		}
		TinZrLED.setMode(TinZrStatusLED::Mode::OFF);
		return;
	}
	

	// SD hot-plug recovery (keep FAIL_BLINK until card appears)
	_probeSDHotplug(millis());

	const uint32_t now_ms = millis();
	if (!_recording && _sdReady && (uint32_t)(now_ms - lastSdIdleLivenessCheckMs) >= SD_IDLE_LIVENESS_CHECK_PERIOD_MS) {
		lastSdIdleLivenessCheckMs = now_ms;
		if (!TinZrSD.probePresence()) {
			Serial.println("SD removed/unavailable while idle");
			_sdReady = false;
			_updateLED();
		}
	}

	_handleBLE();

	if (_pendingHeartbeat) {
		uint64_t pendingPcAnchorUs = 0;
		noInterrupts();
		pendingPcAnchorUs = _pendingPcAnchorUs;
		_pendingHeartbeat = false;
		interrupts();

		const uint32_t now_us = micros();
		noInterrupts();
		if (_hasPcAnchor) {
			_prevPcAnchorUs = _pcAnchorUs;
			_prevPcAnchorLocalUs = _pcAnchorLocalUs;
		}
		_pcAnchorUs = pendingPcAnchorUs;
		_pcAnchorLocalUs = now_us;
		_lastHeartbeatUs = now_us;
		_hasPcAnchor = true;
		interrupts();

		if (_cfg.enable_pc_clock_drift_correction && _prevPcAnchorUs != 0) {
			const uint64_t delta_pc_us = pendingPcAnchorUs - _prevPcAnchorUs;
			const uint32_t delta_local_us = (uint32_t)(now_us - _prevPcAnchorLocalUs);
			if (delta_pc_us >= HEARTBEAT_MIN_INTERVAL_US &&
				delta_pc_us <= HEARTBEAT_MAX_INTERVAL_US &&
				delta_local_us >= HEARTBEAT_MIN_INTERVAL_US &&
				delta_local_us <= HEARTBEAT_MAX_INTERVAL_US) {
				const double raw_scale = (double)delta_pc_us / (double)delta_local_us;
				if (raw_scale > HEARTBEAT_SCALE_MIN && raw_scale < HEARTBEAT_SCALE_MAX) {
					if (_goodHeartbeatPairs < 255) {
						_goodHeartbeatPairs++;
					}

					if (_goodHeartbeatPairs >= HEARTBEAT_SCALE_LOCK_MIN_PAIRS) {
						if (_hasClockScale) {
							_pcUsPerLocalUs =
								(1.0 - HEARTBEAT_SCALE_GAIN_LOCKED) * _pcUsPerLocalUs +
								HEARTBEAT_SCALE_GAIN_LOCKED * raw_scale;
						} else {
							_pcUsPerLocalUs = raw_scale;
							_hasClockScale = true;
						}
					} else {
						_pcUsPerLocalUs =
							(1.0 - HEARTBEAT_SCALE_GAIN_PRELOCK) * _pcUsPerLocalUs +
							HEARTBEAT_SCALE_GAIN_PRELOCK * raw_scale;
					}
				}
			}
		} else if (!_cfg.enable_pc_clock_drift_correction) {
			_pcUsPerLocalUs = 1.0;
			_hasClockScale = false;
			_goodHeartbeatPairs = 0;
		}

		if (_recording && _recordStartLocalUs != 0 && _recordStartPcUs == 0) {
			_recordStartPcUs = _estimate_pc_time_us_scaled(_pcAnchorUs, _pcAnchorLocalUs, _recordStartLocalUs, _pcUsPerLocalUs);
		}
	}

	_handleStreaming();
	_drainSdLogBuffer();
	_handleDeferredBleActions();
	_pumpSdTransfer();
}




void TinZrWearableSDClass::_handleDeferredBleActions() {
#if !TINZR_ENABLE_BLE
	return;
#else
	if (!_bleStarted || !_ble.connected()) return;

	// BAT reply
	if (_pendingBattReply) {
		_pendingBattReply = false;

		// ensure battery was sampled (forceBatteryUpdate sets timer to refresh)
		// if you want immediate sample here:
		sLastBattPct = read_battery_pct();

		char msg[32];
		snprintf(msg, sizeof(msg), "BAT:%u", (unsigned)sLastBattPct);
		_ble.sendNotify((const uint8_t*)msg, strlen(msg));
	}

	// LS
	if (_pendingSdList) {
		_pendingSdList = false;
		_sendSdList();
	}

	// GET
	if (_pendingStartGet) {
		_pendingStartGet = false;
		String name = _pendingGetName;
		_pendingGetName = "";
		_startSdTransfer(name); // sends GET:BEGIN safely now
	}
#endif
}



// ===================== BLE ======================
void TinZrWearableSDClass::_handleBLE() {
#if TINZR_ENABLE_BLE
	if (!_bleStarted) return;

	_ble.handle();

	bool nowConn = _ble.connected();

	if (!_bleWasConnected && nowConn) {
		Serial.println("BLE connected");
		_applyStreamingChange(_desiredStreamingEnabled());
	}

	if (_bleWasConnected && !nowConn) {
		Serial.println("BLE disconnected");
		_applyStreamingChange(_desiredStreamingEnabled());
	}

	_bleWasConnected = nowConn;
	_updateLED();
#endif
}

bool TinZrWearableSDClass::_desiredStreamingEnabled() const {
#if TINZR_ENABLE_BLE
	return _recordArmed || (_bleStarted && _ble.connected());
#else
	return _recordArmed;
#endif
}

bool TinZrWearableSDClass::_isAcquisitionActive() const {
	return _streaming || _recordArmed || _recording;
}

bool TinZrWearableSDClass::_heartbeatIsFresh(uint32_t now_us) const {
	if (!_hasPcAnchor || _lastHeartbeatUs == 0) return false;
	return (uint32_t)(now_us - _lastHeartbeatUs) <= HEARTBEAT_FRESHNESS_TIMEOUT_US;
}

// ========== BLE write callback ==========


uint32_t TinZrWearableSDClass::_crc32_bytes(const uint8_t* data, size_t len, uint32_t crc) {
	// Standard CRC32 (Ethernet/ZIP), reflected poly 0xEDB88320
	crc = ~crc;
	for (size_t i = 0; i < len; ++i) {
		crc ^= (uint32_t)data[i];
		for (int k = 0; k < 8; ++k) {
			uint32_t mask = -(crc & 1u);
			crc = (crc >> 1) ^ (0xEDB88320u & mask);
		}
	}
	return ~crc;
}

uint32_t TinZrWearableSDClass::_crc32_file(File& f) {
	uint32_t crc = 0;
	uint8_t buf[256];
	while (f && f.available()) {
		int n = f.read(buf, sizeof(buf));
		if (n <= 0) break;
		crc = _crc32_bytes(buf, (size_t)n, crc);
	}
	return crc;
}





void TinZrWearableSDClass::_sendSdList() {
#if !TINZR_ENABLE_BLE
	return;
#else
	if (!_bleStarted || !_ble.connected()) return;

	// Always notify BEGIN/END so PC can unblock even on errors
	auto notify_txt = [&](const char* s) {
		_ble.sendNotify((const uint8_t*)s, strlen(s));
	};

	if (_isAcquisitionActive()) {
		notify_txt("LS:BEGIN");
		notify_txt("LS:ERR|BUSY");
		notify_txt("LS:END");
		return;
	}

	if (!_sdReady) {
			Serial.println("SD not ready (logging disabled until inserted)");
		notify_txt("LS:BEGIN");
		notify_txt("LS:ERR|SD_NOT_READY");
		notify_txt("LS:END");
		return;
	}

	const char* dir = (_cfg.sd_log_dir && _cfg.sd_log_dir[0] != '\0') ? _cfg.sd_log_dir : TINZR_SD_LOG_DIR;

	Serial.print("LS dir = ");
	Serial.println(dir);

	notify_txt("LS:BEGIN");

	File d = SD.open(dir, FILE_READ);
	if (!d) {
		Serial.println("LS: open dir failed");
		notify_txt("LS:ERR|OPEN_FAILED");
		notify_txt("LS:END");
		return;
	}

	if (!d.isDirectory()) {
		Serial.println("LS: path is not a directory");
		notify_txt("LS:ERR|NOT_A_DIR");
		d.close();
		notify_txt("LS:END");
		return;
	}

	int count = 0;

	for (;;) {
		File f = d.openNextFile();
		if (!f) break;

		if (!f.isDirectory()) {
			String name = String(f.name());
			int slash = name.lastIndexOf('/');
			if (slash >= 0) name = name.substring(slash + 1);

			String line = String("LS:") + name + String("|") + String((unsigned long)f.size());

			// Debug print (so you can confirm begin/lines/end timing)
			Serial.println(line);

			_ble.sendNotify((const uint8_t*)line.c_str(), line.length());

			count++;
		}
		f.close();
	}

	d.close();

	Serial.print("LS: sent files = ");
	Serial.println(count);

	notify_txt("LS:END");
#endif
}






void TinZrWearableSDClass::_startSdTransfer(const String& name) {
	if (!_sdReady) return;
	if (_isAcquisitionActive()) {
#if TINZR_ENABLE_BLE
		if (_bleStarted && _ble.connected()) {
			const char* msg = "GET:ERR|BUSY";
			_ble.sendNotify((const uint8_t*)msg, strlen(msg));
		}
#endif
		return;
	}

	// Stop any previous transfer
	if (_sdXferActive) {
		_sdXferActive = false;
		_sdXferWaitingAck = false;
		if (_sdXferFile) _sdXferFile.close();
	}

	const char* dir = (_cfg.sd_log_dir && _cfg.sd_log_dir[0] != '\0') ? _cfg.sd_log_dir : TINZR_SD_LOG_DIR;

	String path = String(dir);
	if (!path.endsWith("/")) path += "/";
	String clean = name;
	while (clean.startsWith("/")) clean = clean.substring(1);
	path += clean;

	File f = SD.open(path.c_str(), FILE_READ);
	if (!f) {
		Serial.println("GET: open failed");
		return;
	}

	// Compute CRC32 upfront, then reopen so we can stream from the beginning.
	uint32_t crc = _crc32_file(f);
	size_t size = (size_t)f.size();
	f.close();

	f = SD.open(path.c_str(), FILE_READ);
	if (!f) return;

	_sdXferActive = true;
	_sdXferWaitingAck = false;
	_sdXferSeq = 0;
	_sdXferLastSeqSent = 0;
	_sdXferName = clean;
	_sdXferFile = f;
	_sdXferFileCrc32 = crc;
	_sdXferFileSize = size;
	_sdXferLastLen = 0;

#if TINZR_ENABLE_BLE
	if (_bleStarted && _ble.connected()) {
		char hdr[96];
		snprintf(hdr, sizeof(hdr), "GET:BEGIN|%s|%lu|%08lX", clean.c_str(), (unsigned long)size, (unsigned long)crc);
		_ble.sendNotify((const uint8_t*)hdr, strlen(hdr));
	}
#endif
}

void TinZrWearableSDClass::_pumpSdTransfer() {
#if !TINZR_ENABLE_BLE
	return;
#else
	if (!_sdXferActive) return;
	if (!_bleStarted || !_ble.connected()) return;
	if (_isAcquisitionActive()) return;

	// If waiting for ACK, do nothing.
	if (_sdXferWaitingAck) return;

	if (!_sdXferFile) {
		_sdXferActive = false;
		_sdXferWaitingAck = false;
#if TINZR_ENABLE_BLE
		if (_bleStarted && _ble.connected()) {
			char endmsg[48];
			snprintf(endmsg, sizeof(endmsg), "GET:END|%08lX", (unsigned long)_sdXferFileCrc32);
			_ble.sendNotify((const uint8_t*)endmsg, strlen(endmsg));
		}
#endif
		return;
	}

	uint8_t payload[180];
	int n = _sdXferFile.read(payload, sizeof(payload));
	if (n <= 0) {
		_sdXferFile.close();
		return; // next pump will send GET:END
	}

	uint32_t crc = _crc32_bytes(payload, (size_t)n, 0);

	// Build packet: 'D' + seq(u16) + len(u16) + crc32(u32) + payload
	uint8_t pkt[1 + 2 + 2 + 4 + 180];
	size_t off = 0;
	pkt[off++] = (uint8_t)'D';
	pkt[off++] = (uint8_t)(_sdXferSeq & 0xFF);
	pkt[off++] = (uint8_t)((_sdXferSeq >> 8) & 0xFF);
	pkt[off++] = (uint8_t)((uint16_t)n & 0xFF);
	pkt[off++] = (uint8_t)(((uint16_t)n >> 8) & 0xFF);
	pkt[off++] = (uint8_t)(crc & 0xFF);
	pkt[off++] = (uint8_t)((crc >> 8) & 0xFF);
	pkt[off++] = (uint8_t)((crc >> 16) & 0xFF);
	pkt[off++] = (uint8_t)((crc >> 24) & 0xFF);
	memcpy(pkt + off, payload, (size_t)n);
	off += (size_t)n;

	// cache last packet for resend
	_sdXferLastSeqSent = _sdXferSeq;
	_sdXferLastLen = (uint16_t)n;
	memcpy(_sdXferLastPayload, payload, (size_t)n);

	_ble.sendNotify(pkt, off);

	_sdXferWaitingAck = true;
	_sdXferSeq++;
#endif
}

void TinZrWearableSDClass::_bleWriteStatic(const uint8_t* data, size_t len) {
	if (!_self || !data || len == 0) return;
	_self->_handleBleCommand(data, len);
}

void TinZrWearableSDClass::_handleBleCommand(const uint8_t* data, size_t len) {
	String s;
	s.reserve(len + 1);
	for (size_t i = 0; i < len; ++i) {
		s += char(data[i]);
	}
	s.trim();
	if (!s.length()) return;

	Serial.print("BLE CMD: ");
	Serial.println(s);

	if (s.equalsIgnoreCase("BAT")) {
		forceBatteryUpdate();
		_pendingBattReply = true;   // defer actual notify
		return;
	}

	if (s.equalsIgnoreCase("LS")) {
		_pendingSdList = true;
		return;
	}

	if (s.startsWith("GET:") || s.startsWith("get:")) {
		String name = s.substring(4);
		name.trim();
		if (name.length()) {
			_pendingGetName = name;
			_pendingStartGet = true; // defer start (which notifies GET:BEGIN)
		}
		return;
	}

	if (s.startsWith("ACK:") || s.startsWith("ack:")) {
		String v = s.substring(4);
		v.trim();
		uint16_t seq = (uint16_t)v.toInt();
		if (_sdXferActive && _sdXferWaitingAck && seq == _sdXferLastSeqSent) {
			_sdXferWaitingAck = false;
		}
		return;
	}

	if (s.startsWith("NAK:") || s.startsWith("nak:")) {
		String v = s.substring(4);
		v.trim();
		uint16_t seq = (uint16_t)v.toInt();
		if (_sdXferActive && _sdXferWaitingAck && seq == _sdXferLastSeqSent) {
			// resend last payload with same seq
			uint32_t crc = _crc32_bytes(_sdXferLastPayload, (size_t)_sdXferLastLen, 0);
			uint8_t pkt[1 + 2 + 2 + 4 + 200];
			size_t off = 0;
			pkt[off++] = (uint8_t)'D';
			pkt[off++] = (uint8_t)(seq & 0xFF);
			pkt[off++] = (uint8_t)((seq >> 8) & 0xFF);
			pkt[off++] = (uint8_t)(_sdXferLastLen & 0xFF);
			pkt[off++] = (uint8_t)((_sdXferLastLen >> 8) & 0xFF);
			pkt[off++] = (uint8_t)(crc & 0xFF);
			pkt[off++] = (uint8_t)((crc >> 8) & 0xFF);
			pkt[off++] = (uint8_t)((crc >> 16) & 0xFF);
			pkt[off++] = (uint8_t)((crc >> 24) & 0xFF);
			memcpy(pkt + off, _sdXferLastPayload, (size_t)_sdXferLastLen);
			off += (size_t)_sdXferLastLen;
#if TINZR_ENABLE_BLE
			if (_bleStarted && _ble.connected()) {
				_ble.sendNotify(pkt, off);
			}
#endif
		}
		return;
	}

	if (s.startsWith("P:") || s.startsWith("p:")) {
		String name = s.substring(2);
		name.trim();
		_participant = name;

#if TINZR_ENABLE_BLE
		if (_bleStarted && _ble.connected()) {
			String ack = "P:OK";
			_ble.sendNotify((const uint8_t*)ack.c_str(), ack.length());
		}
#endif
		return;
	}

	// X:<subject>|<pc_time_str>
	if (s.startsWith("X:") || s.startsWith("x:")) {
		String payload = s.substring(2);
		payload.trim();
		int bar = payload.indexOf('|');
		if (bar > 0) {
			String subj = payload.substring(0, bar);
			String ts   = payload.substring(bar + 1);
			subj.trim();
			ts.trim();

			if (subj.length()) _participant = subj;

			_pcAnchorStr = ts;
			_hasPcAnchorStr = (_pcAnchorStr.length() > 0);

			Serial.print("X parsed subj=");
			Serial.print(_participant);
			Serial.print("  ts=");
			Serial.println(_pcAnchorStr);

#if TINZR_ENABLE_BLE
			if (_bleStarted && _ble.connected()) {
				const char* ack = "X:OK";
				_ble.sendNotify((const uint8_t*)ack, strlen(ack));
			}
#endif
		}
		return;
	}

	// T:<epoch_ms>  OR  T:<pc_time_str>
	if (s.startsWith("T:") || s.startsWith("t:")) {
		String t = s.substring(2);
		t.trim();

		uint64_t pc_time_us = 0;

		if (_all_digits(t)) {
			for (size_t i = 0; i < t.length(); ++i) {
				pc_time_us = pc_time_us * 10ULL + (uint64_t)(t.charAt(i) - '0');
			}
			if (pc_time_us < 100000000000000ULL) {
				pc_time_us *= 1000ULL; // backward compatibility for epoch-ms senders
			}
		} else {
			if (!_parse_pc_timestr_to_epoch_us(t, pc_time_us)) {
				Serial.println("T: parse failed (ignored)");
				return;
			}
		}

		noInterrupts();
		_pendingPcAnchorUs = pc_time_us;
		_pendingHeartbeat = true;
		interrupts();

		return;
	}

	if (s.equalsIgnoreCase("S")) {
		Serial.println("ARM recording");
		_recordArmed = true;
		_applyStreamingChange(_desiredStreamingEnabled());

#if TINZR_ENABLE_BLE
		if (_bleStarted && _ble.connected()) {
			const char* ack = "REC:ARMED";
			_ble.sendNotify((const uint8_t*)ack, strlen(ack));
		}
#endif
		return;
	}

	if (s.equalsIgnoreCase("E")) {
		Serial.println("DISARM recording");
		_recordArmed = false;
		_stopRecording("disarmed", true);
		_applyStreamingChange(_desiredStreamingEnabled());

#if TINZR_ENABLE_BLE
		if (_bleStarted && _ble.connected()) {
			const char* ack = "REC:DISARMED";
			_ble.sendNotify((const uint8_t*)ack, strlen(ack));
		}
#endif
		return;
	}
}

// ================= STREAM TOGGLE ================
void TinZrWearableSDClass::_applyStreamingChange(bool enable) {
	if (enable == _streaming) return;

	if (enable) {
		if (!_imuReady) {
			Serial.println("Cannot start acquisition: IMU not ready");
			_setErrorLED();
			return;
		}

		_streaming          = true;
		sFrameCount         = 0;
		_lastSampleUs       = 0;
		sLastHr             = 0;
		sLastSpo2           = 0;
		sLastHrSpo2UpdateMs = 0;

		Serial.println("Acquisition started");
	} else {
		_streaming = false;
		_lastSampleUs = 0;
		Serial.println("Acquisition stopped");
	}

	_updateLED();
}

// ================== STREAMING ===================
void TinZrWearableSDClass::_resetSdLogBuffer() {
	_sdLogHead = 0;
	_sdLogTail = 0;
	_sdLogCount = 0;
	_lastSdFlushMs = 0;
	_sdFlushPending = false;
	_sdBackpressureFlag = false;
}

bool TinZrWearableSDClass::_queueSdLogLine(const char* line) {
	if (!line) return false;
	if (_sdLogCount >= SD_LOG_QUEUE_DEPTH) {
		_sdLogDroppedLinesTotal++;
		_sdBackpressureFlag = true;
		return false;
	}

	snprintf(_sdLogQueue[_sdLogHead], SD_LOG_LINE_MAX, "%s", line);
	_sdLogHead = (_sdLogHead + 1) % SD_LOG_QUEUE_DEPTH;
	_sdLogCount++;
	if (_sdLogCount >= SD_FLUSH_QUEUE_THRESHOLD) {
		_sdFlushPending = true;
	}
	return true;
}

bool TinZrWearableSDClass::_beginRecording(uint32_t now_us) {
	if (_recording) return true;
	if (!_sdReady) {
			Serial.println("SD not ready (logging disabled until inserted)");
		_updateLED();
		return false;
	}

	String safe = _sanitize_subject_for_filename(_participant);
	String base;
	if (_hasPcAnchorStr && _pcAnchorStr.length()) {
		String safeTs = _sanitize_pc_time_for_filename(_pcAnchorStr);
		base = safe + "__" + safeTs;
	} else if (_pcAnchorUs != 0) {
		base = safe + "__" + String((unsigned long long)(_pcAnchorUs / 1000ULL));
	} else {
		base = safe + "__local_" + String((unsigned long)millis());
	}

	Serial.print("SD start: ");
	Serial.println(base);

	if (!TinZrSD.openLog(base.c_str(), "csv", nullptr, true, false)) {
		Serial.println("Failed to open SD log");
		_sdReady = false;
		_updateLED();
		return false;
	}

	_recording = true;
	_sampleIdx = 0;
	_recordStartLocalUs = now_us;
	_recordStartPcUs = 0;
	_sdLogDroppedLinesTotal = 0;
	_sdLogDroppedLinesReported = 0;
	_lastLagIntervalsThisSample = 0;
	if (_hasPcAnchor && _pcAnchorUs != 0) {
		_recordStartPcUs = _estimate_pc_time_us_scaled(_pcAnchorUs, _pcAnchorLocalUs, now_us, _pcUsPerLocalUs);
	}
	_resetSdLogBuffer();
	TinZrSD.setRecording(true);

	_write_log_metadata_header(
		base,
		_cfg,
		_participant,
		_pcAnchorStr,
		_hasPcAnchorStr,
		_pcAnchorUs,
		now_us,
		_pcUsPerLocalUs,
		_hasClockScale
	);

	uint8_t  batt_pct;     // last battery % (0-100)

#if TINZR_ENABLE_BLE
	if (_bleStarted && _ble.connected()) {
		const char* msg = "LOG:STARTED";
		_ble.sendNotify((const uint8_t*)msg, strlen(msg));
	}
#endif
	_updateLED();
	return true;
}

void TinZrWearableSDClass::_stopRecording(const char* reason, bool writeEndMarker) {
	if (!_recording) return;

	_drainSdLogBuffer(true);
	if (_sdLogCount != 0) {
		Serial.print("WARNING: closing log with pending queued lines: ");
		Serial.println((unsigned)_sdLogCount);
	}
	if (writeEndMarker && TinZrSD.logOpen()) {
		TinZrSD.writeLine("====================RecordingEndsHere====================");
	}
	if (reason && reason[0] != '\0') {
		Serial.print("SD stop: ");
		Serial.println(reason);
	}
	TinZrSD.flush();
	TinZrSD.closeLog();
	TinZrSD.setRecording(false);
	_recording = false;
	_sampleIdx = 0;
	_recordStartPcUs = 0;
	_recordStartLocalUs = 0;
	_resetSdLogBuffer();

#if TINZR_ENABLE_BLE
	if (_bleStarted && _ble.connected()) {
		const char* msg = "LOG:STOPPED";
		_ble.sendNotify((const uint8_t*)msg, strlen(msg));
	}
#endif
	_updateLED();
}

void TinZrWearableSDClass::_drainSdLogBuffer(bool forceFlush) {
	if (!_recording || !TinZrSD.logOpen()) {
		_resetSdLogBuffer();
		return;
	}

	const uint32_t start_us = micros();
	size_t lines_written = 0;
	while (_sdLogCount > 0) {
		if (!forceFlush) {
			const uint32_t elapsed_us = (uint32_t)(micros() - start_us);
			if (lines_written >= SD_LOG_DRAIN_MAX_LINES_PER_PASS || elapsed_us >= SD_LOG_DRAIN_MAX_US_PER_PASS) {
				break;
			}
		}

		if (!TinZrSD.writeLine(String(_sdLogQueue[_sdLogTail]))) {
			_sdFlushPending = true;
			_sdBackpressureFlag = true;
			break;
		}
		_sdLogTail = (_sdLogTail + 1) % SD_LOG_QUEUE_DEPTH;
		_sdLogCount--;
		lines_written++;
	}

	if (_sdLogDroppedLinesTotal != _sdLogDroppedLinesReported) {
		Serial.print("WARNING: SD log queue overflow, dropped lines: ");
		Serial.println(_sdLogDroppedLinesTotal);
		_sdLogDroppedLinesReported = _sdLogDroppedLinesTotal;
	}

	const unsigned long now_ms = millis();
	const bool flush_due =
		forceFlush ||
		(_sdLogCount > 0 && _lastSdFlushMs != 0 && (uint32_t)(now_ms - _lastSdFlushMs) >= SD_FLUSH_PERIOD_MS) ||
		(_sdLogCount >= SD_FLUSH_QUEUE_THRESHOLD) ||
		_sdFlushPending;

	if (flush_due) {
		TinZrSD.flush();
		_lastSdFlushMs = now_ms;
		_sdFlushPending = false;
		if (_sdLogCount == 0) {
			_sdBackpressureFlag = false;
		}
	} else if (_lastSdFlushMs == 0) {
		_lastSdFlushMs = now_ms;
	}
}

void TinZrWearableSDClass::_handleStreaming() {
	const uint32_t now_us = micros();
	const unsigned long now_ms = millis();
	const uint32_t sample_interval_us = (uint32_t)_cfg.sample_interval_ms * 1000UL;
	static constexpr uint32_t MAX_SAMPLE_LAG_INTERVALS = 4;

	if (!_isAcquisitionActive()) return;
	if (sample_interval_us == 0) return;
	if (_lastSampleUs == 0) {
		_lastSampleUs = now_us;
		_lastLagIntervalsThisSample = 0;
		return;
	}

	const uint32_t elapsed_us = (uint32_t)(now_us - _lastSampleUs);
	if (elapsed_us < sample_interval_us) return;

	_lastLagIntervalsThisSample = 0;
	const uint32_t elapsed_intervals = elapsed_us / sample_interval_us;
	if (elapsed_intervals > 1) {
		const uint32_t skipped_intervals = elapsed_intervals - 1;
		_lastLagIntervalsThisSample = skipped_intervals;
		if (_recording) {
			_sampleIdx += skipped_intervals;
		}

		if (elapsed_intervals > MAX_SAMPLE_LAG_INTERVALS) {
			_lastSampleUs = now_us - sample_interval_us;
		} else {
			_lastSampleUs += skipped_intervals * sample_interval_us;
		}
	}

	_lastSampleUs += sample_interval_us;

	if (!_imuReady) return;

	if (_recordArmed && !_recording) {
		if (!_beginRecording(now_us)) {
			return;
		}
	}

	if (!_recordArmed && _recording) {
		_stopRecording("disarmed");
		return;
	}

	// Read sensors via TinZrCore
	TinZrImuSampleSI imu;
	TinZrPpgSample ppg;
	bool gotPpg = false;
	bool okImu = TinZr.readImuPpg(imu, ppg, gotPpg);
	if (!okImu) return;

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
			red_raw = last_red;
			ir_raw  = last_ir;
		}
		update_hr_spo2_from_ppg(red_raw, ir_raw);
	} else {
		red_raw   = 0;
		ir_raw    = 0;
		sLastHr   = 0;
		sLastSpo2 = 0;
	}

	const float red_nA = red_raw * PPG_ADC_FULL_SCALE_NA / PPG_ADC_MAX_COUNT;
	const float ir_nA  = ir_raw  * PPG_ADC_FULL_SCALE_NA / PPG_ADC_MAX_COUNT;

	// Battery refresh (every 5 min or forced)
	if (sLastBattSampleMs == 0 || (now_ms - sLastBattSampleMs) >= BATT_PERIOD_MS) {
		sLastBattSampleMs = now_ms;
		sLastBattPct      = read_battery_pct();
	}

	// If recording, but SD disappears mid-run -> stop + FAIL_BLINK until hot-plug recovers
	if (_recording) {
		if (!_sdReady || !TinZrSD.logOpen()) {
			_stopRecording("sd unavailable");
			_sdReady = false; // force FAIL_BLINK until _probeSDHotplug() succeeds
			_updateLED();
			return;
		}
	}

	// Write to SD
	if (_recording && TinZrSD.logOpen()) {
		const uint32_t sample_idx = _sampleIdx;
		const uint32_t local_elapsed_us = (_recordStartLocalUs != 0)
			? (uint32_t)(now_us - _recordStartLocalUs)
			: 0U;
		const size_t queue_depth_at_sample = _sdLogCount;
		const uint32_t dropped_log_lines_total = _sdLogDroppedLinesTotal;
		const uint8_t sd_backpressure_flag = _sdBackpressureFlag ? 1U : 0U;
		bool hasPcAnchor = false;
		bool hasClockScale = false;
		uint64_t pcAnchorUs = 0;
		uint32_t pcAnchorLocalUs = 0;
		uint32_t lastHeartbeatUs = 0;
		double pcUsPerLocalUs = 1.0;
		noInterrupts();
		hasPcAnchor = _hasPcAnchor;
		hasClockScale = _hasClockScale;
		pcAnchorUs = _pcAnchorUs;
		pcAnchorLocalUs = _pcAnchorLocalUs;
		lastHeartbeatUs = _lastHeartbeatUs;
		pcUsPerLocalUs = _pcUsPerLocalUs;
		interrupts();

		const bool hasPcTime = (hasPcAnchor && pcAnchorUs != 0);
		const bool heartbeat_present = hasPcAnchor && lastHeartbeatUs != 0;
		const bool heartbeat_fresh = heartbeat_present && _heartbeatIsFresh(now_us);
		const uint64_t pc_time_us = hasPcTime
			? _estimate_pc_time_us_scaled(pcAnchorUs, pcAnchorLocalUs, now_us, pcUsPerLocalUs)
			: 0ULL;
		const uint64_t t_ms = (_recordStartPcUs != 0 && pc_time_us >= _recordStartPcUs)
			? ((pc_time_us - _recordStartPcUs) / 1000ULL)
			: 0ULL;
		const uint64_t t_nominal_ms = (uint64_t)sample_idx * (uint64_t)_cfg.sample_interval_ms;
		_sampleIdx++;


		char pc_time_iso[32];
		char sync_pc_time_iso[32];
		char heartbeat_age_buf[24];
		char pc_scale_buf[24];
		char line[SD_LOG_LINE_MAX];

		// Log the exact heartbeat anchor on the sample nearest to its arrival, else 0.
		uint64_t sync_pc_time_us = 0;
		if (heartbeat_present) {
			const uint32_t dt_us = (uint32_t)(now_us - lastHeartbeatUs);
			if (dt_us <= sample_interval_us) {
				sync_pc_time_us = pcAnchorUs;
			}
		}

		if (pc_time_us != 0) {
			_format_epoch_us_iso(pc_time_us, pc_time_iso, sizeof(pc_time_iso));
		} else {
			pc_time_iso[0] = '\0';
		}
		if (sync_pc_time_us != 0) {
			_format_epoch_us_iso(sync_pc_time_us, sync_pc_time_iso, sizeof(sync_pc_time_iso));
		} else {
			sync_pc_time_iso[0] = '\0';
		}

		if (heartbeat_present) {
			snprintf(heartbeat_age_buf, sizeof(heartbeat_age_buf), "%lu", (unsigned long)(now_us - lastHeartbeatUs));
		} else {
			heartbeat_age_buf[0] = '\0';
		}

		if (hasClockScale) {
			snprintf(pc_scale_buf, sizeof(pc_scale_buf), "%.9f", pcUsPerLocalUs);
		} else {
			pc_scale_buf[0] = '\0';
		}

		snprintf(
			line, sizeof(line),
			"%lu,%lu,%llu,%llu,%s,%s,%s,%u,%u,%s,%u,%lu,%lu,%u,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%.6f,%u,%u,%u",
			(unsigned long)local_elapsed_us,
			(unsigned long)sample_idx,
			(unsigned long long)t_nominal_ms,
			(unsigned long long)t_ms,
			pc_time_iso,
			sync_pc_time_iso,
			heartbeat_age_buf,
			(unsigned)heartbeat_present,
			(unsigned)heartbeat_fresh,
			pc_scale_buf,
			(unsigned)queue_depth_at_sample,
			(unsigned long)dropped_log_lines_total,
			(unsigned long)_lastLagIntervalsThisSample,
			(unsigned)sd_backpressure_flag,
			(double)red_nA,
			(double)ir_nA,
			(double)imu.ax_g, (double)imu.ay_g, (double)imu.az_g,
			(double)imu.gx_dps, (double)imu.gy_dps, (double)imu.gz_dps,
			(unsigned)sLastBattPct,
			(unsigned)sLastHr,
			(unsigned)sLastSpo2
		);

		_queueSdLogLine(line);
	}
}

// ===================== LED ======================
void TinZrWearableSDClass::_updateLED() {
#if TINZR_ENABLE_BLE
	if (!_bleStarted) {
		TinZrLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
		return;
	}

	if (!_sdReady) {
		TinZrLED.setMode(TinZrStatusLED::Mode::FAIL_BLINK);
		return;
	}

	if (_recording) {
		TinZrLED.setMode(TinZrStatusLED::Mode::OTA_ACTIVE);
	} else if (_streaming) {
		TinZrLED.setMode(TinZrStatusLED::Mode::BLE_CONNECTED);
	} else {
		TinZrLED.setMode(TinZrStatusLED::Mode::BLE_ADVERTISING);
	}
#else
	TinZrLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
#endif
}

void TinZrWearableSDClass::_setErrorLED() {
	TinZrLED.setMode(TinZrStatusLED::Mode::WIFI_FAIL);
}
