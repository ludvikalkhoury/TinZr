#pragma once
#include <Arduino.h>
#include <FS.h>
#include <time.h>

#ifndef TINZR_SD_USE_SD_MMC
#define TINZR_SD_USE_SD_MMC 0
#endif

#if TINZR_SD_USE_SD_MMC
	#include <SD_MMC.h>
#else
	#include <SD.h>
	#include <SPI.h>
#endif

#ifndef TINZR_SD_LOG_DIR
#define TINZR_SD_LOG_DIR "/tinzr/logs"
#endif

struct TinZrSDConfig {
	// SPI SD: CS pin required. SD_MMC: ignored.
	int cs_pin = SS;

	// Directory where logs will go
	const char* log_dir = TINZR_SD_LOG_DIR;

	// If true, create directories automatically
	bool auto_mkdir = true;
};

class TinZrSDLogger {
public:
	TinZrSDLogger() = default;

	// begin can now be called either TinZrSD.begin() or 
	//                   TinZrSD.begin(TinZrSDConfig cfg)
	bool begin(const TinZrSDConfig& cfg);
	bool begin() {
		TinZrSDConfig cfg;
		return begin(cfg);
	}	
	
	void setRecording(bool on);
	void end();
	
	void handle();
	bool mounted() const { return _mounted; }

	// ---------- Directory utilities ----------
	bool ensureDir(const char* path);
	void listDir(const char* dir_path, uint8_t levels = 3, bool show_sizes = true);

	// ---------- Filename generation ----------
	// Creates: <dir>/<base>_YYYY-MM-DD_HH-MM-SS_mmm[_NN].<ext>
	// If exists, increments NN.
	String makeTimestampedName(const char* dir, const char* base, const char* ext);
	
	String makePlainName(const char* dir, const char* base, const char* ext );
	
	// ---------- File utilities ----------
	// Opens a new session file with timestamp+counter and keeps it open for appends
	bool openLog(const char* base, const char* ext = "csv", const char* dir = nullptr, bool append = true,  bool timestamp = false);

	// Append a line (adds '\n' if missing)
	bool writeLine(const String& line);

	// Write raw bytes (binary)
	bool writeBytes(const void* data, size_t len);

	// Flush and close
	void flush();
	void closeLog();

	// Info
	bool logOpen() const { return _logOpen; }
	const String& logPath() const { return _logPath; }

private:
	TinZrSDConfig _cfg{};
	bool _mounted = false;
	bool _recording = false;
	void _applyLedState(bool force = false);
	
#if TINZR_SD_USE_SD_MMC
	fs::FS* _fs = &SD_MMC;
#else
	fs::FS* _fs = &SD;
	int _cs = -1;
#endif

	File _log;
	bool _logOpen = false;
	String _logPath;

	// Helpers
	static String _sanitize(const char* s);
	static bool _timeIsValid();
	static String _timestampString(); // YYYY-MM-DD_HH-MM-SS_mmm
	static String _joinPath(const char* a, const String& b);
};

extern TinZrSDLogger TinZrSD;   // declaration