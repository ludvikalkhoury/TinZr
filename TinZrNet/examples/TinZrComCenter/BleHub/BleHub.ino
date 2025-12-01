
#include <Arduino.h>
#include "TinZrNode.h"

TinZrNode Node;

TinZrNodeConfig cfg = {
  .hostname = "TinZrBLE0",

};

void setup() {
  Node.begin(cfg);
}

void loop() {
  Node.handle();
}
