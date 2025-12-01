
#include <Arduino.h>
#include "TinZrNode.h"

TinZrNode Node;

TinZrNodeConfig cfg = {
  .hostname = "TinZrBLE2",

};

void setup() {
  Node.begin(cfg);
}

void loop() {
  Node.handle();
}
