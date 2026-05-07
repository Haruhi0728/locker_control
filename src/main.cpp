#include <M5Stack.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include "../include/env.h"

// --- Wi-Fi設定 ---

// --- MQTT設定 ---
const char* mqtt_server = "broker.hivemq.com";
const int mqtt_port = 1883;

// ※重要：トピック名は他の人と被らないようにユニークな名前に変更してください
const char* topic_publish = "m5stack/device_01/status";
const char* topic_subscribe = "m5stack/device_01/command";

WiFiClient espClient;
PubSubClient client(espClient);

// 画面の初期化関数
void initScreen() {
  M5.Lcd.fillScreen(BLACK);
  M5.Lcd.setCursor(10, 10);
  M5.Lcd.setTextColor(WHITE);
  M5.Lcd.setTextSize(2);
  M5.Lcd.println("MQTT communication");
  M5.Lcd.println("Waiting for connect...");
}

// MQTTメッセージを受信したときに呼ばれるコールバック関数
void callback(char* topic, byte* payload, unsigned int length) {
  // ペイロード（データ）を文字列に変換
  String message = "";
  for (int i = 0; i < length; i++) {
    message += (char)payload[i];
  }

  Serial.print("Message arrived [");
  Serial.print(topic);
  Serial.print("] ");
  Serial.println(message);

  // 受信したメッセージに応じて画面の色を変える
  if (message.startsWith("0x")) {
    // 文字列を16進数の数値に変換 (例: "0xFF00" -> 0xFF00)
    uint16_t color = (uint16_t)strtol(message.c_str(), NULL, 0);

    M5.Lcd.fillScreen(color);
    M5.Lcd.setCursor(10, 10);
    M5.Lcd.printf("Color: %s", message.c_str());
  }
  else if (message == "red") {
    M5.Lcd.fillScreen(RED);
    M5.Lcd.setCursor(10, 10);
    M5.Lcd.println("Color: RED");
  }
  else if (message == "green") {
    M5.Lcd.fillScreen(GREEN);
    M5.Lcd.setCursor(10, 10);
    M5.Lcd.println("Color: GREEN");
  }
  else if (message == "blue") {
    M5.Lcd.fillScreen(BLUE);
    M5.Lcd.setCursor(10, 10);
    M5.Lcd.println("Color: BLUE");
  }
  else {
    M5.Lcd.fillScreen(BLACK);
    M5.Lcd.setCursor(10, 10);
    M5.Lcd.print("Msg: ");
    M5.Lcd.println(message);
  }
}

// Wi-FiとMQTTサーバーへの再接続処理
void reconnect() {
  // MQTTに接続されるまでループ
  while (!client.connected()) {
    Serial.print("Attempting MQTT connection...");
    // クライアントIDを生成（ランダム）
    String clientId = "M5StackClient-";
    clientId += String(random(0xffff), HEX);

    // 接続試行
    if (client.connect(clientId.c_str())) {
      Serial.println("connected");
      M5.Lcd.fillScreen(BLACK);
      M5.Lcd.setCursor(10, 10);
      M5.Lcd.println("MQTT Connected!");

      // 接続成功したらSubscribe（受信待機）する
      client.subscribe(topic_subscribe);
    } else {
      Serial.print("failed, rc=");
      Serial.print(client.state());
      Serial.println(" try again in 5 seconds");
      delay(5000);
    }
  }
}

void setup() {
  M5.begin();
  initScreen();

  // Wi-Fi接続
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
    M5.Lcd.print(".");
  }
  Serial.println("\nWiFi connected");

  // MQTT設定
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
}

void loop() {
  M5.update(); // ボタン状態の更新

  // MQTTの接続が切れたら再接続
  if (!client.connected()) {
    reconnect();
  }
  client.loop(); // MQTTのバックグラウンド処理を維持

  // ボタンAが押されたらメッセージをPublish(送信)する
  if (M5.BtnA.wasPressed()) {
    String msg = "Button A Pressed!";
    client.publish(topic_publish, msg.c_str());
    Serial.println("Published: " + msg);

    // 送信したことを画面に一瞬表示
    M5.Lcd.fillRect(0, 200, 320, 40, BLACK);
    M5.Lcd.setCursor(10, 210);
    M5.Lcd.print("Sent: Button A");
  }
  // ボタンBが押されたらメッセージをPublish(送信)する
  if (M5.BtnB.wasPressed()) {
    String msg = "Button B Pressed!";
    client.publish(topic_publish, msg.c_str());
    Serial.println("Published: " + msg);

    // 送信したことを画面に一瞬表示
    M5.Lcd.fillRect(0, 200, 320, 40, BLACK);
    M5.Lcd.setCursor(10, 210);
    M5.Lcd.print("Sent: Button B");
  }
  // ボタンCが押されたらメッセージをPublish(送信)する
  if (M5.BtnC.wasPressed()) {
    String msg = "Button C Pressed!";
    client.publish(topic_publish, msg.c_str());
    Serial.println("Published: " + msg);

    // 送信したことを画面に一瞬表示
    M5.Lcd.fillRect(0, 200, 320, 40, BLACK);
    M5.Lcd.setCursor(10, 210);
    M5.Lcd.print("Sent: Button C");
  }
}
