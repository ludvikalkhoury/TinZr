// AccToSD_Button_Rec.ino
// ESP32-C3 + LSM6DS3TR-C (I2C) + SD (SPI)
// Start/stop recording by pushbutton on GPIO9.
// On-board/addressable RGB (NeoPixel) on GPIO8: RED=idle, GREEN=recording.
//
// Requires: Adafruit_LSM6DS3TRC, Adafruit_Sensor, SD, SPI, Adafruit_NeoPixel

#include <Wire.h>
#include <Adafruit_LSM6DS3TRC.h>
#include <Adafruit_Sensor.h>
#include <SPI.h>
#include <SD.h>
#include <Adafruit_NeoPixel.h>

// -------- Pins & config --------
#define LSM_ADDR  0x6A

#define RGB_N           1   // one pixel
#define RGB_BRIGHTNESS  20  // 0..255
#define SAMPLE_PERIOD_MS 10UL   // ~100 Hz

// -------- Globals --------
Adafruit_LSM6DS3TRC imu;
File logFile;
bool isRecording = false;

Adafruit_NeoPixel rgb(RGB_N, PIN_RGB_LED, NEO_GRB + NEO_KHZ800);

// Debounce
uint32_t lastPBCheck = 0;
const uint32_t PB_DEBOUNCE_MS = 50;
bool pbPrevStable = HIGH;  // pull-up: HIGH when not pressed
bool pbCurrStable = HIGH;

// -------- Helpers --------
void rgbShow(uint8_t r, uint8_t g, uint8_t b) {
  rgb.setPixelColor(0, rgb.Color(r, g, b));
  rgb.show();
}

void setLEDIdle()    { rgbShow(255, 0, 0); }  // RED
void setLEDRec()     { rgbShow(0, 255, 0); }  // GREEN

void failAndHalt(const __FlashStringHelper* msg) {
  Serial.println(msg);
  setLEDIdle();
  while (true) { delay(1000); }
}

void setupIMU() {
  // Use default I2C pins from the variant (no explicit SDA/SCL)
  if (!imu.begin_I2C(LSM_ADDR, &Wire)) {
    failAndHalt(F("❌ LSM6DS3TR-C not found."));
  }
  imu.setAccelRange(LSM6DS_ACCEL_RANGE_2_G);
  imu.setGyroRange(LSM6DS_GYRO_RANGE_250_DPS);
  imu.setAccelDataRate(LSM6DS_RATE_104_HZ);
  imu.setGyroDataRate(LSM6DS_RATE_104_HZ);
}

void printSDInfo() {
  uint8_t cardType = SD.cardType();
  Serial.print(F("Card type: "));
  if (cardType == CARD_NONE)       Serial.println(F("NONE"));
  else if (cardType == CARD_MMC)   Serial.println(F("MMC"));
  else if (cardType == CARD_SD)    Serial.println(F("SDSC"));
  else if (cardType == CARD_SDHC)  Serial.println(F("SDHC/SDXC"));
  else                             Serial.println(cardType);

  uint64_t size = SD.cardSize() / (1024ULL * 1024ULL);
  uint64_t total = SD.totalBytes() / (1024ULL * 1024ULL);
  uint64_t used  = SD.usedBytes() / (1024ULL * 1024ULL);
  Serial.printf("Card size: %llu MB, Total: %llu MB, Used: %llu MB\n", size, total, used);
}

String makeFilename() {
  for (uint32_t i = 0; i < 100000; ++i) {
    char name[20];
    snprintf(name, sizeof(name), "/LOG%05u.CSV", (unsigned)i);
    if (!SD.exists(name)) return String(name);
  }
  return String("/LOG.CSV");
}

bool openLogFile(const String& fname) {
  logFile = SD.open(fname.c_str(), FILE_WRITE);
  return (bool)logFile;
}

void writeHeader() {
  logFile.println(F("millis,ax_g,ay_g,az_g,gx_dps,gy_dps,gz_dps,temp_C"));
  logFile.flush();
}

// Start/stop actions
void startRecording() {
  if (isRecording) return;
  String fname = makeFilename();
  Serial.print(F("Opening: ")); Serial.println(fname);
  if (!openLogFile(fname)) {
    failAndHalt(F("❌ Failed to open log file."));
  }
  writeHeader();
  isRecording = true;
  setLEDRec();
  Serial.println(F("▶ Recording started. Press button to stop."));
}

void stopRecording() {
  if (!isRecording) return;
  Serial.println(F("⏹ Stopping. Closing file."));
  if (logFile) { logFile.flush(); logFile.close(); }
  isRecording = false;
  setLEDIdle();
  Serial.println(F("✅ Recording stopped. Press button to start."));
}

// Debounced button polling (returns true once on press)
bool pollButtonPressed() {
  // active-LOW with INPUT_PULLUP
  bool raw = digitalRead(PB_PIN);
  uint32_t now = millis();
  if (now - lastPBCheck >= PB_DEBOUNCE_MS) {
    lastPBCheck = now;
    pbCurrStable = raw;
    if (pbPrevStable == HIGH && pbCurrStable == LOW) {
      pbPrevStable = pbCurrStable;
      return true;
    }
    pbPrevStable = pbCurrStable;
  }
  return false;
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println(F("\n=== Acc→SD Logger (PB start/stop) ==="));

  // RGB
  rgb.begin();
  rgb.setBrightness(RGB_BRIGHTNESS);
  setLEDIdle();

  // Button
  pinMode(PB_PIN, INPUT_PULLUP);

  // I2C / IMU (use variant-defined I2C pins; just set the clock speed)
  Wire.begin();                 // no explicit SDA/SCL
  Wire.setClock(400000);        // 400 kHz I2C
  setupIMU();
  Serial.println(F("IMU ready."));

  // SPI / SD — use the default SPI instance with variant-defined pins
  pinMode(SS, OUTPUT);
  digitalWrite(SS, HIGH);
  SPI.begin();  // no explicit SCK/MISO/MOSI

  // Init SD @1 MHz first
  if (!SD.begin(SS, SPI, 1000000)) {
    failAndHalt(F("❌ SD init failed @1MHz."));
  }
  Serial.println(F("SD ready @1MHz."));
  printSDInfo();

  // Try bumping to 4 MHz
  SD.end();
  delay(50);
  if (!SD.begin(SS, SPI, 4000000)) {
    Serial.println(F("⚠️ Re-init at 4MHz failed, staying at 1MHz."));
    SD.begin(SS, SPI, 1000000);
  } else {
    Serial.println(F("SD @4MHz."));
  }

  Serial.println(F("Idle — press button to START recording."));
}

void loop() {
  // Handle start/stop button
  if (pollButtonPressed()) {
    if (isRecording) stopRecording();
    else             startRecording();
  }

  // If recording, sample + write
  if (isRecording) {
    sensors_event_t accel, gyro, temp;
    imu.getEvent(&accel, &gyro, &temp);

    char line[160];
    char ax[16], ay[16], az[16], gx[16], gy[16], gz[16], tc[16];
    dtostrf(accel.acceleration.x, 0, 6, ax);
    dtostrf(accel.acceleration.y, 0, 6, ay);
    dtostrf(accel.acceleration.z, 0, 6, az);
    dtostrf(gyro.gyro.x,         0, 6, gx);
    dtostrf(gyro.gyro.y,         0, 6, gy);
    dtostrf(gyro.gyro.z,         0, 6, gz);
    dtostrf(temp.temperature,    0, 2, tc);

    snprintf(line, sizeof(line), "%lu,%s,%s,%s,%s,%s,%s,%s",
             (unsigned long)millis(), ax, ay, az, gx, gy, gz, tc);

    if (logFile) {
      logFile.println(line);
      static uint32_t lastFlush = 0;
      uint32_t now = millis();
      if (now - lastFlush >= 1000) { logFile.flush(); lastFlush = now; }
    }
  }

  // pace loop
  if (SAMPLE_PERIOD_MS > 0) {
    static uint32_t nextDue = 0;
    uint32_t now = millis();
    if (nextDue == 0) nextDue = now + SAMPLE_PERIOD_MS;
    int32_t wait = (int32_t)(nextDue - now);
    if (wait > 0) delay((uint32_t)wait);
    nextDue += SAMPLE_PERIOD_MS;
  } else {
    delay(1);
  }
}
