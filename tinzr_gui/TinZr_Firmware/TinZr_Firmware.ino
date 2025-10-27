/*
  TinZr legacy BLE firmware (RX command support; IMU+PPG normalized)
  - ESP32-C3 + classic ESP32 BLE (BLEDevice.h)
  - Service UUID:        4fafc201-1fb5-459e-8fcc-c5c9c331914b
  - Notify Characteristic beb5483e-36e1-4688-b7f5-ea07361b26a8  (TX -> App)
  - RX Characteristic     e7810a71-73ae-499d-8c15-faa9aef0c3f2  (App -> FW)

  Sends (when enabled):
    IMU,ax,ay,az,gx,gy,gz,temp
    PPG,ir,red,green
  Also sends:
    BAT,<volts>  (on boot, post-connect, periodic; and on READ_BAT)
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
#define PIN_RGB_LED  8      // NeoPixel data (GPIO8 on many C3 boards)
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

  static uint32_t Wheel(uint8_t pos) {
    if (pos < 85) return strip.Color(pos * 3, 255 - pos * 3, 0);
    if (pos < 170) { pos -= 85; return strip.Color(255 - pos * 3, 0, pos * 3); }
    pos -= 170; return strip.Color(0, pos * 3, 255 - pos * 3);
  }

  static void led_apply(uint8_t r, uint8_t g, uint8_t b) {
    strip.setPixelColor(0, strip.Color(r, g, b));
    strip.show();
  }

  static void led_set(uint8_t r, uint8_t g, uint8_t b){
    strip.setBrightness(gBrightness);
    led_apply(r, g, b);
  }

  static void led_brightness(uint8_t b){
    gBrightness = b;
    strip.setBrightness(gBrightness);
    strip.show();
  }
#endif

// ===== IMU =====
#if FEAT_IMU
  #include <Adafruit_LSM6DS3TRC.h>
  #include <Adafruit_Sensor.h>
  Adafruit_LSM6DS3TRC lsm6;
  bool     gIMUEnabled     = true;         // start ON for easy testing
  uint32_t gLastImuMs      = 0;
  uint32_t IMU_INTERVAL_MS = 100;          // 10 Hz (set ~25 for ~40 Hz)
  // latest
  float ax=0, ay=0, az=0;
  float gx=0, gy=0, gz=0;
  float tC=0;
#endif

// ===== PPG =====
#if FEAT_PPG
  #include "MAX30105.h"
  MAX30105 particleSensor;
  bool     gPPGEnabled      = true;        // start ON for easy testing
  uint32_t gLastPpgMs       = 0;
  uint32_t PPG_INTERVAL_MS  = 100;         // 10 Hz (set ~25 for ~40 Hz)
  const uint32_t NO_FINGER_THRESHOLD = 1000;
  // latest
  uint32_t ir=0, red=0, green=0;
#endif

// ===== Battery =====
#if FEAT_BATTERY
  uint32_t gLastBatMs = 0;
  const uint32_t BAT_INTERVAL_MS = 5UL * 60UL * 1000UL; // 5 minutes

  static float readVBat() {
    #if defined(analogSetPinAttenuation)
      analogSetPinAttenuation(VBAT_PIN, ADC_ATTEN_DB_11); // ~3.9V FS on ADC pin
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

BLEServer*         pServer     = nullptr;
BLECharacteristic* pNotifyChar = nullptr;
BLECharacteristic* pRxChar     = nullptr;
bool deviceConnected = false;

static String gCmdBuf;
volatile bool gJustConnected = false;

// --- helper: TX notify + Serial echo for debug ---
static void bleSendLine(const String& line){
  if (!pNotifyChar) return;
  std::string s = (line + "\n").c_str();
  pNotifyChar->setValue((uint8_t*)s.data(), s.size());
  if (deviceConnected) pNotifyChar->notify();
  Serial.print(F("TX ")); Serial.println(line);
}

class MyServerCallbacks : public BLEServerCallbacks {
  void onConnect(BLEServer* server) override {
    deviceConnected = true;
    gJustConnected = true;
  }
  void onDisconnect(BLEServer* server) override {
    deviceConnected = false;
    server->getAdvertising()->start();
  }
};

// ---- Command handling ----
static void handleCommand(const String& raw) {
  String s = raw; s.trim(); if (!s.length()) return;

#if FEAT_BATTERY
  if (s.equalsIgnoreCase("READ_BAT")) {
    float v = readVBat();
    Serial.print(F("CMD READ_BAT -> ")); Serial.println(v, 3);
    bleSendLine(String("BAT,") + String(v, 3));
    return;
  }
#endif

#if FEAT_LED
  if (s.startsWith("RGB ")) {
    int r=0,g=0,b=0;
    if (sscanf(s.c_str(),"RGB %d %d %d",&r,&g,&b)==3) {
      r = constrain(r,0,255); g = constrain(g,0,255); b = constrain(b,0,255);
      gRainbow = false;
      led_set((uint8_t)r,(uint8_t)g,(uint8_t)b);
      Serial.printf("CMD RGB -> %d,%d,%d\n", r,g,b);
      bleSendLine(String("ECHO,RGB ") + r + " " + g + " " + b);
    }
    return;
  }
  if (s.startsWith("BRIGHT ")) {
    int v=0;
    if (sscanf(s.c_str(),"BRIGHT %d",&v)==1) {
      v = constrain(v,0,255);
      led_brightness((uint8_t)v);
      Serial.printf("CMD BRIGHT -> %d\n", v);
      bleSendLine(String("ECHO,BRIGHT ") + v);
    }
    return;
  }
  if (s.equalsIgnoreCase("RAINBOW ON"))  { gRainbow = true;  Serial.println(F("CMD RAINBOW ON"));  bleSendLine("ECHO,RAINBOW ON");  return; }
  if (s.equalsIgnoreCase("RAINBOW OFF")) { gRainbow = false; Serial.println(F("CMD RAINBOW OFF")); bleSendLine("ECHO,RAINBOW OFF"); return; }
#endif

#if FEAT_IMU
  if (s.equalsIgnoreCase("START_IMU")) { gIMUEnabled = true;  Serial.println(F("CMD START_IMU")); bleSendLine("ECHO,START_IMU"); return; }
  if (s.equalsIgnoreCase("STOP_IMU"))  { gIMUEnabled = false; Serial.println(F("CMD STOP_IMU"));  bleSendLine("ECHO,STOP_IMU");  return; }
#endif

#if FEAT_PPG
  if (s.equalsIgnoreCase("START_PPG")) { gPPGEnabled = true;  Serial.println(F("CMD START_PPG")); bleSendLine("ECHO,START_PPG"); return; }
  if (s.equalsIgnoreCase("STOP_PPG"))  { gPPGEnabled = false; Serial.println(F("CMD STOP_PPG"));  bleSendLine("ECHO,STOP_PPG");  return; }
  if (s.equalsIgnoreCase("START_ALL")) { gIMUEnabled = true; gPPGEnabled = true; Serial.println(F("CMD START_ALL")); bleSendLine("ECHO,START_ALL"); return; }
  if (s.equalsIgnoreCase("STOP_ALL"))  { gIMUEnabled = false; gPPGEnabled = false; Serial.println(F("CMD STOP_ALL")); bleSendLine("ECHO,STOP_ALL");  return; }
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
  Serial.println(F("\nTinZr legacy BLE (IMU+PPG normalized, solid BAT)"));

  Wire.begin();

#if FEAT_LED
  strip.begin();
  strip.setBrightness(gBrightness);
  led_apply(10,0,0); // boot color
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
    particleSensor.setup();            // default config
    particleSensor.setPulseAmplitudeIR(0x0A);    // small LED currents to start
    particleSensor.setPulseAmplitudeRed(0x0A);
    particleSensor.setPulseAmplitudeGreen(0x08); // enable GREEN, too
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
  // initial battery announce
  float v0 = readVBat();
  Serial.print(F("BOOT BAT -> ")); Serial.println(v0, 3);
  bleSendLine(String("BAT,") + String(v0, 3));
#endif

  Serial.println(F("Setup done. Advertising as TinZr…"));
}

// ================== helpers to TX ==================
static void sendIMU(){
#if FEAT_IMU
  char buf[96];
  snprintf(buf, sizeof(buf), "IMU,%.3f,%.3f,%.3f,%.2f,%.2f,%.2f,%.2f",
           ax, ay, az, gx, gy, gz, tC);
  bleSendLine(String(buf));
#if FEAT_SDLOG
  sd_append("IMU", String(buf));
#endif
#endif
}

static void sendPPG(){
#if FEAT_PPG
  char buf[64];
  snprintf(buf, sizeof(buf), "PPG,%lu,%lu,%lu",
           (unsigned long)ir, (unsigned long)red, (unsigned long)green);
  bleSendLine(String(buf));
#if FEAT_SDLOG
  sd_append("PPG", String(buf));
#endif
#endif
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

  // Re-announce battery right after a new connection (give CCCD time)
  static uint32_t connectAnnounceAt = 0;
  if (gJustConnected) {
    gJustConnected = false;
    connectAnnounceAt = now + 750;   // ~0.75s later: client likely subscribed
    Serial.println(F("BLE connected, scheduling BAT announce..."));
  }
  if (connectAnnounceAt && (int32_t)(now - connectAnnounceAt) >= 0) {
    connectAnnounceAt = 0;
#if FEAT_BATTERY
    float vc = readVBat();
    Serial.print(F("POST-CONNECT BAT -> ")); Serial.println(vc, 3);
    bleSendLine(String("BAT,") + String(vc, 3));
#endif
  }

#if FEAT_BATTERY
  if (now - gLastBatMs >= BAT_INTERVAL_MS) {
    gLastBatMs = now;
    float vp = readVBat();
    Serial.print(F("PERIODIC BAT -> ")); Serial.println(vp, 3);
    bleSendLine(String("BAT,") + String(vp, 3));
  }
#endif

#if FEAT_IMU
  if (gIMUEnabled && (now - gLastImuMs >= IMU_INTERVAL_MS)) {
    gLastImuMs = now;
    sensors_event_t a, g, t;
    lsm6.getEvent(&a, &g, &t);
    ax = a.acceleration.x;   // m/s^2
    ay = a.acceleration.y;
    az = a.acceleration.z;
    gx = g.gyro.x * 180.0f / PI;  // convert rad/s -> deg/s if needed
    gy = g.gyro.y * 180.0f / PI;
    gz = g.gyro.z * 180.0f / PI;
    tC = t.temperature;      // °C
  }
#endif

#if FEAT_PPG
  if (gPPGEnabled && (now - gLastPpgMs >= PPG_INTERVAL_MS)) {
    gLastPpgMs = now;
    uint32_t irv   = particleSensor.getIR();
    uint32_t redv  = particleSensor.getRed();
    uint32_t grnv  = particleSensor.getGreen();
    if (irv < NO_FINGER_THRESHOLD) { ir=red=green=0; }
    else { ir = irv; red = redv; green = grnv; }
  }
#endif

  // ===== Send streams at the fastest enabled cadence =====
  const uint32_t minPeriod = min(
    (uint32_t)(FEAT_IMU && gIMUEnabled ? IMU_INTERVAL_MS : 1000000),
    (uint32_t)(FEAT_PPG && gPPGEnabled ? PPG_INTERVAL_MS : 1000000)
  );

  static uint32_t lastSend = 0;
  if (deviceConnected && (now - lastSend >= minPeriod)) {
    lastSend = now;
    if (FEAT_IMU && gIMUEnabled) sendIMU();
    if (FEAT_PPG && gPPGEnabled) sendPPG();
  }
}
