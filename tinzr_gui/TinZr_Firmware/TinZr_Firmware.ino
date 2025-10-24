/*
  TinZr legacy BLE firmware (with RX command support, fixed RGB/BRIGHT)
  - ESP32-C3 + classic ESP32 BLE (BLEDevice.h)
  - Service UUID:        4fafc201-1fb5-459e-8fcc-c5c9c331914b
  - Notify Characteristic beb5483e-36e1-4688-b7f5-ea07361b26a8  (TX -> App)
  - RX Characteristic     e7810a71-73ae-499d-8c15-faa9aef0c3f2  (App -> FW)
  - Streams: "ax,ay,az,ir,red"
  - Also sends: "BAT,<volts>" every 5 minutes
*/

#include <Arduino.h>
#include <Wire.h>

// ========= Feature flags =========
#define FEAT_LED        1
#define FEAT_IMU        1
#define FEAT_PPG        1
#define FEAT_BATTERY    1
#define FEAT_SDLOG      0
#define FEAT_BUTTON_REC 0
// =================================

// -------- Pins & board specifics --------
#define PIN_RGB_LED  8      // NeoPixel data (change if your board differs)
#define NUM_LEDS     1
#define VBAT_PIN     A1     // Battery divider input (top 220k -> VBAT, bottom 150k -> GND)
// If you enable FEAT_BUTTON_REC, define PB_PIN here (to GND, INPUT_PULLUP)
// #define PB_PIN    9

// Battery divider math
static const float VREF      = 3.3f;
static const float ADC_MAX   = 4095.0f; // ESP32-C3 12-bit
static const float DIV_RATIO = 150000.0f / (220000.0f + 150000.0f);  // ≈0.405

// ===== LED (NeoPixel) =====
#if FEAT_LED
  #include <Adafruit_NeoPixel.h>
  Adafruit_NeoPixel strip(NUM_LEDS, PIN_RGB_LED, NEO_GRB + NEO_KHZ800);

  uint8_t  gBrightness = 60;
  bool     gRainbow    = false;
  uint32_t gLastLedMs  = 0;
  uint8_t  gHue        = 0;

  // Track last solid color so BRIGHT and RAINBOW OFF re-apply it
  static uint8_t gLastR = 10, gLastG = 0, gLastB = 0; // default boot color

  static uint32_t Wheel(uint8_t pos) {
    if (pos < 85) return strip.Color(pos * 3, 255 - pos * 3, 0);
    if (pos < 170) { pos -= 85; return strip.Color(255 - pos * 3, 0, pos * 3); }
    pos -= 170; return strip.Color(0, pos * 3, 255 - pos * 3);
  }

  // Apply exactly these RGB bytes at current brightness
  static void led_apply(uint8_t r, uint8_t g, uint8_t b) {
    // Optional nicer brightness perception:
    // uint32_t c = strip.gamma32(strip.Color(r, g, b));
    // strip.setPixelColor(0, c);
    strip.setPixelColor(0, strip.Color(r, g, b)); // linear
    strip.show();
  }

  static void led_set(uint8_t r, uint8_t g, uint8_t b){
    gLastR = r; gLastG = g; gLastB = b;
    led_apply(r, g, b);
  }

  static void led_brightness(uint8_t b){
    gBrightness = b;
    strip.setBrightness(gBrightness);
    if (!gRainbow) {
      // re-apply the last solid color at the new brightness
      led_apply(gLastR, gLastG, gLastB);
    } else {
      // rainbow loop will refresh on next frame
      strip.show();
    }
  }
#endif

// ===== IMU =====
#if FEAT_IMU
  #include <Adafruit_LSM6DS3TRC.h>
  #include <Adafruit_Sensor.h>
  Adafruit_LSM6DS3TRC lsm6;
  bool     gIMUEnabled   = true;         // start ON for easy testing
  uint32_t gLastImuMs    = 0;
  uint32_t IMU_INTERVAL_MS = 100;        // 10 Hz (set 25 for ~40 Hz)
#endif

// ===== PPG =====
#if FEAT_PPG
  #include "MAX30105.h"
  #include "spo2_algorithm.h"  // unused now, kept for future
  MAX30105 particleSensor;
  bool     gPPGEnabled   = true;          // start ON for easy testing
  uint32_t gLastPpgMs    = 0;
  uint32_t PPG_INTERVAL_MS = 100;         // 10 Hz (set 25 for ~40 Hz)
  const uint32_t NO_FINGER_THRESHOLD = 30000;
#endif

// ===== Battery =====
#if FEAT_BATTERY
  uint32_t gLastBatMs = 0;
  const uint32_t BAT_INTERVAL_MS = 5UL * 60UL * 1000UL; // 5 minutes
  static float readVBat() {
    #if defined(analogSetPinAttenuation)
      analogSetPinAttenuation(VBAT_PIN, ADC_11db);
    #endif
    uint32_t acc = 0; const int N = 16;
    for (int i=0;i<N;++i){ acc += analogRead(VBAT_PIN); delay(2); }
    float raw   = float(acc)/N;
    float v_div = raw * (VREF / ADC_MAX);
    return v_div / DIV_RATIO;
  }
#endif

// ===== SD logging (optional) =====
#if FEAT_SDLOG
  #include <SPI.h>
  #include <SD.h>
  bool gLogSD=false; File gSDFile; uint32_t gLastFileFlushMs=0;
  static String makeLogName(const char* base) {
    for (uint32_t i=0;i<100000;i++){ char n[24]; snprintf(n,sizeof(n),"/%s%05lu.CSV",base,(unsigned long)i);
      if(!SD.exists(n)) return String(n); }
    return String("/LOG.CSV");
  }
  static void sd_start(){
    if (gLogSD) return;
    if (!SD.begin(SS, SPI, 1000000)) { Serial.println(F("SD begin @1MHz FAIL")); return; }
    SD.end(); delay(50);
    if (!SD.begin(SS, SPI, 4000000)) { Serial.println(F("SD re-begin @4MHz FAIL, fallback 1MHz")); SD.begin(SS, SPI, 1000000); }
    String fn = makeLogName("LOG");
    gSDFile = SD.open(fn, FILE_WRITE);
    if (!gSDFile){ Serial.println(F("SD open FAIL")); return; }
    gSDFile.println(F("millis,source,vals"));
    gSDFile.flush();
    gLogSD = true; Serial.print(F("SD logging -> ")); Serial.println(fn);
  }
  static void sd_stop(){ if (!gLogSD) return; if (gSDFile){ gSDFile.flush(); gSDFile.close(); } gLogSD=false; }
  static void sd_append(const char* src, const String& csv){
    if (!gLogSD || !gSDFile) return;
    gSDFile.print(millis()); gSDFile.print(','); gSDFile.print(src); gSDFile.print(','); gSDFile.println(csv);
    if (millis() - gLastFileFlushMs > 1000){ gLastFileFlushMs = millis(); gSDFile.flush(); }
  }
#endif

// ======== CLASSIC ESP32 BLE (legacy profile) ========
#include <BLEDevice.h>
#include <BLEServer.h>
#include <BLEUtils.h>
#include <BLE2902.h>

// UUIDs (legacy)
#define SERVICE_UUID        "4fafc201-1fb5-459e-8fcc-c5c9c331914b"
// TX notify to app:
#define CHAR_TX_UUID        "beb5483e-36e1-4688-b7f5-ea07361b26a8"
// RX write from app:
#define CHAR_RX_UUID        "e7810a71-73ae-499d-8c15-faa9aef0c3f2"

BLEServer*         pServer    = nullptr;
BLECharacteristic* pNotifyChar= nullptr;
BLECharacteristic* pRxChar    = nullptr;
bool deviceConnected = false;

static String gCmdBuf;

class MyServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* server) override { deviceConnected = true; }
  void onDisconnect(BLEServer* server) override {
    deviceConnected = false;
    server->getAdvertising()->start();
  }
};

static void bleSendLine(const String& line){
  if (!deviceConnected || !pNotifyChar) return;
  std::string s = (line + "\n").c_str();
  pNotifyChar->setValue((uint8_t*)s.data(), s.size());
  pNotifyChar->notify();
}

// ---- Command handling ----
static void handleCommand(const String& raw) {
  String s = raw; s.trim(); if (!s.length()) return;

#if FEAT_BATTERY
  if (s.equalsIgnoreCase("READ_BAT")) {
    bleSendLine(String("BAT,") + String(readVBat(), 3));
    return;
  }
#endif

#if FEAT_LED
  if (s.startsWith("RGB ")) {
    int r=0,g=0,b=0;
    if (sscanf(s.c_str(),"RGB %d %d %d",&r,&g,&b)==3) {
      r = constrain(r,0,255); g = constrain(g,0,255); b = constrain(b,0,255);
      gRainbow = false;                   // leave rainbow
      led_set((uint8_t)r,(uint8_t)g,(uint8_t)b);
    }
    return;
  }
  if (s.startsWith("BRIGHT ")) {
    int v=0;
    if (sscanf(s.c_str(),"BRIGHT %d",&v)==1) {
      v = constrain(v,0,255);
      led_brightness((uint8_t)v);         // re-applies last solid color if not rainbow
    }
    return;
  }
  if (s.equalsIgnoreCase("RAINBOW ON"))  { gRainbow = true;  return; }
  if (s.equalsIgnoreCase("RAINBOW OFF")) {
    gRainbow = false;
    led_apply(gLastR, gLastG, gLastB);    // restore last solid color
    return;
  }
#endif

#if FEAT_IMU
  if (s.equalsIgnoreCase("START_IMU")) { gIMUEnabled = true;  return; }
  if (s.equalsIgnoreCase("STOP_IMU"))  { gIMUEnabled = false; return; }
#endif

#if FEAT_PPG
  if (s.equalsIgnoreCase("START_PPG")) { gPPGEnabled = true;  return; }
  if (s.equalsIgnoreCase("STOP_PPG"))  { gPPGEnabled = false; return; }
  if (s.equalsIgnoreCase("START_ALL")) { gIMUEnabled = true; gPPGEnabled = true; return; }
  if (s.equalsIgnoreCase("STOP_ALL"))  { gIMUEnabled = false; gPPGEnabled = false; return; }
#endif

  // echo for debug
  bleSendLine(String("ECHO,") + s);
}

// RX write callback (Arduino String–safe)
class RxCharCallbacks : public BLECharacteristicCallbacks {
  void onWrite(BLECharacteristic* c) override {
    String val = c->getValue();   // returns Arduino String
    for (size_t i=0;i<val.length();++i) {
      char ch = val[i];
      if (ch=='\r') continue;
      if (ch=='\n') { handleCommand(gCmdBuf); gCmdBuf=""; }
      else if (gCmdBuf.length()<256) gCmdBuf += ch;
    }
  }
};

// ================== setup ==================
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println(F("\nTinZr legacy BLE (with RX, fixed RGB/BRIGHT)"));

  Wire.begin();

#if FEAT_LED
  strip.begin();
  strip.setBrightness(gBrightness);
  // explicit boot color (stored in gLastR/G/B)
  led_apply(gLastR, gLastG, gLastB);
#endif

#if FEAT_IMU
  if (lsm6.begin_I2C()) {
    lsm6.setAccelRange(LSM6DS_ACCEL_RANGE_2_G);
    lsm6.setGyroRange(LSM6DS_GYRO_RANGE_250_DPS);
    lsm6.setAccelDataRate(LSM6DS_RATE_104_HZ);
    lsm6.setGyroDataRate(LSM6DS_RATE_104_HZ);
    Serial.println(F("IMU ready."));
  } else Serial.println(F("IMU not found."));
#endif

#if FEAT_PPG
  if (particleSensor.begin(Wire, I2C_SPEED_FAST)) {
    particleSensor.setup();
    particleSensor.setPulseAmplitudeRed(0x0A);
    particleSensor.setPulseAmplitudeIR(0x0A);
    particleSensor.setPulseAmplitudeGreen(0x00);
    Serial.println(F("MAX30105 ready."));
  } else Serial.println(F("MAX30105 not found."));
#endif

  // ---- BLE init (legacy w/ RX) ----
  BLEDevice::init("TinZr");                  // name goes into ADV
  pServer = BLEDevice::createServer();
  pServer->setCallbacks(new MyServerCallbacks());

  BLEService* pService = pServer->createService(SERVICE_UUID);

  // TX notify characteristic
  pNotifyChar = pService->createCharacteristic(
    CHAR_TX_UUID, BLECharacteristic::PROPERTY_NOTIFY
  );
  pNotifyChar->addDescriptor(new BLE2902());

  // RX write characteristic (for commands)
  pRxChar = pService->createCharacteristic(
    CHAR_RX_UUID, BLECharacteristic::PROPERTY_WRITE | BLECharacteristic::PROPERTY_WRITE_NR
  );
  pRxChar->setCallbacks(new RxCharCallbacks());

  pService->start();

  BLEAdvertising* adv = pServer->getAdvertising();
  adv->addServiceUUID(SERVICE_UUID);
  adv->start();

#if FEAT_BATTERY
  // initial battery announce (GUI will show it)
  bleSendLine(String("BAT,") + String(readVBat(), 3));
#endif

  Serial.println(F("Setup done. Advertising as TinZr…"));
}

// ================== loop ==================
void loop() {
  const uint32_t now = millis();

#if FEAT_LED
  if (gRainbow && now - gLastLedMs >= 20) {
    gLastLedMs = now;
    strip.setPixelColor(0, Wheel(gHue++));
    strip.show();
  }
#endif

#if FEAT_BATTERY
  if (now - gLastBatMs >= BAT_INTERVAL_MS) {
    gLastBatMs = now;
    bleSendLine(String("BAT,") + String(readVBat(), 3));
  }
#endif

#if FEAT_IMU || FEAT_PPG
  // Accumulate and send at min(IMU,PPG) cadence
  static float ax=0, ay=0, az=0;
  static uint32_t ir=0, red=0;
  static uint32_t lastLegacySend = 0;
#endif

#if FEAT_IMU
  if (gIMUEnabled && (now - gLastImuMs >= IMU_INTERVAL_MS)) {
    gLastImuMs = now;
    sensors_event_t accel, gyro, temp;
    lsm6.getEvent(&accel, &gyro, &temp);
    ax = accel.acceleration.x;
    ay = accel.acceleration.y;
    az = accel.acceleration.z;
  }
#endif

#if FEAT_PPG
  if (gPPGEnabled && (now - gLastPpgMs >= PPG_INTERVAL_MS)) {
    gLastPpgMs = now;
    uint32_t irv = particleSensor.getIR();
    uint32_t redv = particleSensor.getRed();
    if (irv < NO_FINGER_THRESHOLD) { ir = 0; red = 0; }
    else { ir = irv; red = redv; }
  }
#endif

#if FEAT_IMU || FEAT_PPG
  const uint32_t minPeriod = min(
    (uint32_t)(FEAT_IMU ? IMU_INTERVAL_MS : 1000),
    (uint32_t)(FEAT_PPG ? PPG_INTERVAL_MS : 1000)
  );
  if (deviceConnected && (now - lastLegacySend >= minPeriod)) {
    lastLegacySend = now;

    // LEGACY FORMAT: "ax,ay,az,ir,red"
    char line[96];
    snprintf(line, sizeof(line), "%.2f,%.2f,%.2f,%lu,%lu",
             ax, ay, az, (unsigned long)ir, (unsigned long)red);
    bleSendLine(String(line));
  }
#endif
}
