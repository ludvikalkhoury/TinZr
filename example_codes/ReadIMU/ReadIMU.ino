#include <Wire.h>
#include <Adafruit_LSM6DS3TRC.h>
#include <Adafruit_Sensor.h>

// ——— IMU address ———
#define LSM_ADDR 0x6A  

// Create the TR-C–aware IMU object
Adafruit_LSM6DS3TRC imu;

void setup() {
  Serial.begin(115200);
  delay(300);  // give USB-UART a moment

  Serial.println();
  Serial.println("▶ Initializing I²C and LSM6DS3TR-C via library");

  // bring up the bus on your chosen pins
  Wire.begin();
  Wire.setClock(400000);
  delay(50);

  // begin_I2C will do WHO_AM_I + register config under the hood
  if (! imu.begin_I2C(LSM_ADDR)) {
    Serial.print("✖ Failed to find LSM6DS3TR-C at 0x");
    Serial.println(LSM_ADDR, HEX);
    while (1) delay(10);
  }
  Serial.print("✔ Found LSM6DS3TR-C at 0x");
  Serial.println(LSM_ADDR, HEX);
  Serial.println("✅ Sensor init complete");
}

void loop() {
  // getEvent() reads & converts accel, gyro, temp for you
  sensors_event_t accel, gyro, temp;
  imu.getEvent(&accel, &gyro, &temp);

  Serial.print("Accel (m/s²): ");
  Serial.print(accel.acceleration.x, 3); Serial.print(", ");
  Serial.print(accel.acceleration.y, 3); Serial.print(", ");
  Serial.println(accel.acceleration.z, 3);

  Serial.print("Gyro  (°/s):   ");
  Serial.print(gyro.gyro.x, 2); Serial.print(", ");
  Serial.print(gyro.gyro.y, 2); Serial.print(", ");
  Serial.println(gyro.gyro.z, 2);

  Serial.print("Temp  (°C):    ");
  Serial.println(temp.temperature, 1);

  Serial.println("-----------------------");
  delay(200);  // yields to RTOS watchdog
}
