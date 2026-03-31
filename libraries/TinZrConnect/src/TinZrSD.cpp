#include "TinZrSD.h"
#include "TinZrCore.h"
#include "TinZrLED.h"

TinZrSDLogger TinZrSD; 


void TinZrSDLogger::_applyLedState(bool force) {
	static TinZrStatusLED::Mode last = TinZrStatusLED::Mode::OFF;

	TinZrStatusLED::Mode m = TinZrStatusLED::Mode::OFF;

	if (_mounted) {
		// SD present
		m = _recording
			? TinZrStatusLED::Mode::SUCCESS_STEADY   // solid green
			: TinZrStatusLED::Mode::SUCCESS_STROBE;  // flashing green
	} else {
		// SD missing (optional)
		m = TinZrStatusLED::Mode::FAIL_BLINK;       // flashing red
	}

	if (!force && m == last) return;
	TinZrLED.setMode(m);
	last = m;
}

bool TinZrSDLogger::begin(const TinZrSDConfig& cfg) {
	_cfg = cfg;

#if TINZR_SD_USE_SD_MMC
	_mounted = SD_MMC.begin("/sd", true);
	if (!_mounted) {
		Serial.println("TinZrSD: SD_MMC.begin() failed");
		_mounted = false;          // IMPORTANT
		_applyLedState(true);      // will set FAIL_BLINK (background)
		return false;
	}
#else
	_cs = cfg.cs_pin;
	if (_cs < 0) {
		Serial.println("TinZrSD: SPI SD requires cs_pin");
		_mounted = false;          // IMPORTANT
		_applyLedState(true);      // background red blink
		return false;
	}

	_mounted = SD.begin(_cs);
	if (!_mounted) {
		Serial.println("TinZrSD: SD.begin() failed");
		_mounted = false;          // IMPORTANT
		_applyLedState(true);      // background red blink
		return false;
	}
#endif

	if (_cfg.auto_mkdir) {
		if (!ensureDir(_cfg.log_dir)) {
			Serial.print("TinZrSD: log_dir unavailable: ");
			Serial.println(_cfg.log_dir ? _cfg.log_dir : "(null)");
#if TINZR_SD_USE_SD_MMC
			SD_MMC.end();
#else
			SD.end();
#endif
			_mounted = false;
			_applyLedState(true);
			return false;
		}
	}

	Serial.print("TinZrSD: mounted, log_dir=");
	Serial.println(_cfg.log_dir);

	_applyLedState(true);          // background green blink (not recording)
	return true;
}



void TinZrSDLogger::end() {
	closeLog();
	if (!_mounted) return;

#if TINZR_SD_USE_SD_MMC
	SD_MMC.end();
#else
	SD.end();
#endif
	_mounted = false;
	Serial.println("TinZrSD: unmounted");
}

bool TinZrSDLogger::ensureDir(const char* path) {
	if (!_mounted) return false;
	if (!path || path[0] == '\0') return false;

	String p(path);
	if (!p.startsWith("/")) p = "/" + p;

	if (_fs->exists(p.c_str())) return true;

	String cur = "";
	int idx = 0;
	while (idx < (int)p.length()) {
		int next = p.indexOf('/', idx + 1);
		if (next < 0) next = p.length();
		cur = p.substring(0, next);
		if (cur.length() > 0 && !_fs->exists(cur.c_str())) {
			if (!_fs->mkdir(cur.c_str())) {
				Serial.print("TinZrSD: mkdir failed: ");
				Serial.println(cur);
				return false;
			}
		}
		idx = next;
	}
	return true;
}

void TinZrSDLogger::listDir(const char* dir_path, uint8_t levels, bool show_sizes) {
	if (!_mounted) {
		Serial.println("TinZrSD: listDir called but SD not mounted");
		return;
	}

	File root = _fs->open(dir_path);
	if (!root) {
		Serial.print("TinZrSD: open dir failed: ");
		Serial.println(dir_path);
		return;
	}
	if (!root.isDirectory()) {
		Serial.print("TinZrSD: not a directory: ");
		Serial.println(dir_path);
		root.close();
		return;
	}

	File f = root.openNextFile();
	while (f) {
		if (f.isDirectory()) {
			Serial.print("DIR : ");
			Serial.println(f.name());
			if (levels > 0) {
				listDir(f.name(), levels - 1, show_sizes);
			}
		} else {
			Serial.print("FILE: ");
			Serial.print(f.name());
			if (show_sizes) {
				Serial.print("\tSIZE: ");
				Serial.println((uint32_t)f.size());
			} else {
				Serial.println();
			}
		}
		f = root.openNextFile();
	}
	root.close();
}


void TinZrSDLogger::setRecording(bool on) {
	if (_recording == on) return;
	_recording = on;
	_applyLedState(true);
}


void TinZrSDLogger::handle() {
	TinZr.handle();
	static uint32_t lastTry = 0;
	uint32_t now = millis();

	if (!_recording && !_mounted && (now - lastTry) > 1000) {
		lastTry = now;

#if TINZR_SD_USE_SD_MMC
		_mounted = SD_MMC.begin("/sd", true);
#else
		if (_cs >= 0) _mounted = SD.begin(_cs);
#endif

		_applyLedState(true); // switches to green blink immediately if mount succeeds
	}

	// No need to call _applyLedState continuously unless your state changes
}

bool TinZrSDLogger::probePresence() {
	if (!_mounted) return false;

	File root = _fs->open("/");
	if (!root) return false;

	const bool ok = root.isDirectory();
	root.close();
	return ok;
}



String TinZrSDLogger::_sanitize(const char* s) {
	String out = s ? String(s) : String("");
	out.trim();
	out.replace(" ", "_");
	out.replace("/", "_");
	out.replace("\\", "_");
	out.replace(":", "_");
	out.replace("*", "_");
	out.replace("?", "_");
	out.replace("\"", "_");
	out.replace("<", "_");
	out.replace(">", "_");
	out.replace("|", "_");
	return out;
}

bool TinZrSDLogger::_timeIsValid() {
	// “Valid enough” heuristic: after ~2020-01-01.
	time_t now = time(nullptr);
	return (now > 1577836800);
}

String TinZrSDLogger::_timestampString() {
	char buf[40];

	if (_timeIsValid()) {
		struct tm t;
		time_t now = time(nullptr);
		localtime_r(&now, &t);

		// Milliseconds from millis() (not perfect, but good enough for filename uniqueness)
		uint16_t ms = (uint16_t)(millis() % 1000);

		snprintf(buf, sizeof(buf),
			"%04d-%02d-%02d_%02d-%02d-%02d_%03u",
			t.tm_year + 1900, t.tm_mon + 1, t.tm_mday,
			t.tm_hour, t.tm_min, t.tm_sec,
			(unsigned)ms
		);
	} else {
		// If you haven’t set RTC/NTP yet, you won't get real year/month/day.
		// This fallback still guarantees uniqueness.
		uint32_t ms = millis();
		snprintf(buf, sizeof(buf), "noRTC_%lu", (unsigned long)ms);
	}

	return String(buf);
}

String TinZrSDLogger::_joinPath(const char* a, const String& b) {
	String out(a ? a : "");
	if (!out.startsWith("/")) out = "/" + out;
	if (!out.endsWith("/")) out += "/";
	out += b;
	return out;
}

String TinZrSDLogger::makeTimestampedName(const char* dir, const char* base, const char* ext) {
	if (!_mounted) return "";

	String d = dir ? String(dir) : String(_cfg.log_dir);
	String b = _sanitize(base);
	if (b.isEmpty()) b = "log";

	String e = ext ? String(ext) : String("csv");
	e.trim();
	if (e.startsWith(".")) e = e.substring(1);

	String ts = _timestampString();

	// First try: no counter
	String stem = b + "_" + ts;
	String name0 = stem + "." + e;
	String path0 = _joinPath(d.c_str(), name0);

	if (!_fs->exists(path0.c_str())) {
		return path0;
	}

	// If exists, add _NN counter
	for (int i = 0; i < 1000; ++i) {
		char cbuf[8];
		snprintf(cbuf, sizeof(cbuf), "_%02d", i);
		String name = stem + String(cbuf) + "." + e;
		String path = _joinPath(d.c_str(), name);
		if (!_fs->exists(path.c_str())) {
			return path;
		}
	}

	// Extremely unlikely fallback
	return path0;
}



String TinZrSDLogger::makePlainName(
	const char* dir,
	const char* base,
	const char* ext
) {
	String path = dir;
	if (!path.endsWith("/")) path += "/";

	// Base filename without extension
	String baseName = base;

	// Extension (including dot if present)
	String dotExt;
	if (ext && ext[0] != '\0') {
		dotExt = ".";
		dotExt += ext;
	}

	// First try: base.ext
	String candidate = path + baseName + dotExt;
	if (!_fs->exists(candidate)) {
		return candidate;
	}

	// Otherwise, append _NN
	for (uint16_t i = 1; i < 10000; ++i) {
		char suffix[8];
		snprintf(suffix, sizeof(suffix), "_%03u", i);

		candidate = path + baseName + suffix + dotExt;
		if (!_fs->exists(candidate)) {
			return candidate;
		}
	}

	// Fallback (should never happen)
	return path + baseName + "_XXXX" + dotExt;
}



bool TinZrSDLogger::openLog(const char* base, const char* ext, const char* dir, bool append, bool timestamp) {
	if (!_mounted) return false;

	closeLog();

	const char* useDir = dir ? dir : _cfg.log_dir;
	if (_cfg.auto_mkdir) ensureDir(useDir);

	if (timestamp) {
		_logPath = makeTimestampedName(useDir, base, ext);
	} else {
		_logPath = makePlainName(useDir, base, ext);
	}

	if (_logPath.isEmpty()) return false;

	const char* mode = append ? FILE_APPEND : FILE_WRITE;
	_log = _fs->open(_logPath.c_str(), mode);
	if (!_log) {
		Serial.print("TinZrSD: openLog failed: ");
		Serial.println(_logPath);
		_logOpen = false;
		return false;
	}

	_logOpen = true;
	Serial.print("TinZrSD: log opened: ");
	Serial.println(_logPath);
	setRecording(true);   // solid green
	return true;
}

bool TinZrSDLogger::writeLine(const String& line) {
	if (!_logOpen) return false;

	if (line.endsWith("\n")) {
		_log.print(line);
	} else {
		_log.print(line);
		_log.print("\n");
	}
	return true;
}

bool TinZrSDLogger::writeBytes(const void* data, size_t len) {
	if (!_logOpen) return false;
	if (!data || len == 0) return true;

	size_t w = _log.write((const uint8_t*)data, len);
	return (w == len);
}

void TinZrSDLogger::flush() {
	if (!_logOpen) return;
	_log.flush();
}

void TinZrSDLogger::closeLog() {
	if (!_logOpen) return;

	_log.flush();
	_log.close();
	_logOpen = false;
	
	setRecording(false);

	Serial.print("TinZrSD: log closed: ");
	Serial.println(_logPath);
}
