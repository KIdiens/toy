// 经典 ESP32 WROOM，Arduino ESP32 3.x。示例未经过实物烧录验证。
// PWM 停止不能替代机械急停直接切断舵机电源。
#include <Arduino.h>
#include <stdio.h>
#include <string.h>

const int servoPins[4] = {25, 26, 27, 14};
const int buttonPins[3] = {18, 19, 21};
const int dockPin = 32, estopPin = 33, idPin = 34;
bool armed = false, pwmReady = true, haveSeq = false;
int activeRole = 0, stableRole = 0, candidateRole = 0;
int currentUs[4] = {1500, 1500, 1500, 1500};
int targetUs[4] = {1500, 1500, 1500, 1500};
uint32_t lastFrame = 0, lastTick = 0, lastReport = 0, candidateSince = 0;
unsigned long previousSeq = 0;
char buffer[100];
size_t used = 0;
bool overflowLine = false;
int buttonRaw[3] = {HIGH, HIGH, HIGH}, buttonStable[3] = {HIGH, HIGH, HIGH};
uint32_t buttonSince[3] = {0, 0, 0};

bool docked() { return digitalRead(dockPin) == LOW; }
bool healthy() { return digitalRead(estopPin) == LOW; }

int readRole() {
  // 10k 上拉至 3.3V，公仔分别用 1k、4.7k、10k 电阻接地。
  // 窗口必须根据实际万用表与 ADC 测量结果复核。
  uint32_t mv = analogReadMilliVolts(idPin);
  if (mv >= 150 && mv <= 450) return 1;
  if (mv >= 850 && mv <= 1250) return 2;
  if (mv >= 1450 && mv <= 1900) return 3;
  return 0;
}

void stopMotion() {
  armed = false;
  haveSeq = false;
  for (int i = 0; i < 4; ++i) ledcWrite(servoPins[i], 0);
}

void report() {
  Serial.printf("STATE %d %d %d %d\n", armed, docked(), healthy(), stableRole);
  bool settled = armed && haveSeq;
  for (int i = 0; i < 4; ++i) settled = settled && currentUs[i] == targetUs[i];
  if (settled) Serial.printf("SETTLED %lu\n", previousSeq);
}

void command(const char *line) {
  if (!strcmp(line, "STOP")) {
    stopMotion(); Serial.println("OK STOP"); return;
  }
  if (!strcmp(line, "STATUS")) { report(); return; }
  int expectedRole;
  char armExtra;
  if (sscanf(line, "ARM %d %c", &expectedRole, &armExtra) == 1) {
    if (!pwmReady || !docked() || !healthy() || !stableRole || readRole() != stableRole
        || expectedRole != stableRole || candidateRole != stableRole
        || millis() - candidateSince < 200) {
      Serial.println("ERR INTERLOCK"); return;
    }
    if (armed) { Serial.println("ERR ALREADY_ARMED"); return; }
    activeRole = stableRole;
    for (int i = 0; i < 4; ++i) currentUs[i] = targetUs[i] = 1500;
    haveSeq = false; lastFrame = millis(); armed = true;
    Serial.printf("OK ARM %d\n", activeRole); return;
  }
  unsigned long seq;
  int a, b, c, d;
  char extra;
  if (sscanf(line, "FRAME %lu %d %d %d %d %c", &seq, &a, &b, &c, &d, &extra) == 5) {
    int values[4] = {a, b, c, d};
    bool valid = armed && (!haveSeq || seq > previousSeq);
    for (int x : values) valid = valid && x >= 1400 && x <= 1600;
    if (!valid) { stopMotion(); Serial.println("ERR FRAME"); return; }
    for (int i = 0; i < 4; ++i) targetUs[i] = values[i];
    previousSeq = seq; haveSeq = true; lastFrame = millis(); return;
  }
  stopMotion(); Serial.println("ERR COMMAND");
}

void setup() {
  Serial.begin(115200);
  pinMode(dockPin, INPUT_PULLUP);
  pinMode(estopPin, INPUT_PULLUP);
  pinMode(idPin, INPUT);
  analogSetPinAttenuation(idPin, ADC_11db);
  for (int pin : buttonPins) pinMode(pin, INPUT_PULLUP);
  for (int pin : servoPins) {
    if (!ledcAttach(pin, 50, 14)) pwmReady = false;
    ledcWrite(pin, 0);
  }
  Serial.println(pwmReady ? "READY 1" : "FAULT PWM_INIT");
}

void loop() {
  uint32_t now = millis();
  int role = docked() ? readRole() : 0;
  if (role != candidateRole) {
    candidateRole = role; candidateSince = now; stableRole = 0;
  }
  if (!docked()) stableRole = 0;
  if (now - candidateSince >= 200) stableRole = candidateRole;
  if (armed && (!docked() || !healthy() || role != activeRole || now - lastFrame > 300)) {
    stopMotion(); Serial.println("FAULT INTERLOCK_OR_TIMEOUT");
  }
  // 每圈限制串口处理量，让通信洪泛不能阻塞安全检查。
  for (int n = 0; n < 64 && Serial.available(); ++n) {
    char ch = Serial.read();
    if (ch == '\r') continue;
    if (ch == '\n') {
      if (!overflowLine) { buffer[used] = 0; command(buffer); }
      else { stopMotion(); Serial.println("ERR LENGTH"); }
      used = 0; overflowLine = false;
    } else if (used < sizeof(buffer) - 1 && !overflowLine) buffer[used++] = ch;
    else overflowLine = true;
  }
  if (now - lastTick >= 20) {
    lastTick = now;
    if (armed) for (int i = 0; i < 4; ++i) {
      currentUs[i] += constrain(targetUs[i] - currentUs[i], -6, 6);
      uint32_t duty = (uint32_t(currentUs[i]) * 16383UL + 10000UL) / 20000UL;
      ledcWrite(servoPins[i], duty);
    }
  }
  for (int i = 0; i < 3; ++i) {
    int value = digitalRead(buttonPins[i]);
    if (value != buttonRaw[i]) { buttonRaw[i] = value; buttonSince[i] = now; }
    if (now - buttonSince[i] >= 30 && value != buttonStable[i]) {
      buttonStable[i] = value;
      if (value == LOW) Serial.printf("BUTTON %d\n", i + 1);
    }
  }
  if (now - lastReport >= 200) { lastReport = now; report(); }
}
