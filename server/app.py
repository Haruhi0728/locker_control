from flask import Flask, render_template, request, jsonify
import datetime
import json
import threading
import atexit
import os
from dotenv import load_dotenv
import paho.mqtt.client as mqtt


# ===== .env 読み込み =====
load_dotenv()


app = Flask(__name__)


# ===== Supabase / アプリURL設定 =====
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
APP_URL = os.getenv("APP_URL", "http://localhost:5000")


# ===== MQTT設定 =====
MQTT_BROKER = os.getenv("MQTT_BROKER", "broker.hivemq.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "8884"))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "m5stack/device_02/servo")
MQTT_STATUS_TOPIC = os.getenv("MQTT_STATUS_TOPIC", "m5stack/device_02/status")

MQTT_CLIENT = None
MQTT_LOCK = threading.Lock()

# ===== 鍵状態 (main.cppのcheckLockStateが "2,<code>" 形式で送信) =====
LOCK_STATE_LABELS = {
    "1": "UNLOCK",
    "2": "LOCK",
    "3": "MIDDLE",
    "4": "ERROR"
}

STATUS_LOCK = threading.Lock()
latest_status = {
    "code": None,
    "label": "unknown",
    "raw": None,
    "updated_at": None
}


# ===== JSONログ設定 =====
JSON_LOG_FILE = os.getenv("JSON_LOG_FILE", "access_logs.json")
JSON_LOG_LOCK = threading.Lock()


# ===== JSONログ初期化 =====
def init_json_log():
    if not os.path.exists(JSON_LOG_FILE):
        with open(JSON_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)


# ===== JSONログ読み込み =====
def load_json_logs():
    init_json_log()

    with JSON_LOG_LOCK:
        with open(JSON_LOG_FILE, "r", encoding="utf-8") as f:
            try:
                logs = json.load(f)
            except json.JSONDecodeError:
                logs = []

    return logs


# ===== JSONログ保存 =====
def save_log(
    user_email,
    user_name,
    action,
    angle,
    duration,
    result,
    ip_address
):
    init_json_log()

    jst = datetime.timezone(datetime.timedelta(hours=9))
    timestamp = datetime.datetime.now(jst).isoformat(timespec="seconds")

    with JSON_LOG_LOCK:
        with open(JSON_LOG_FILE, "r", encoding="utf-8") as f:
            try:
                logs = json.load(f)
            except json.JSONDecodeError:
                logs = []

        next_id = 1

        if len(logs) > 0:
            next_id = logs[-1].get("id", 0) + 1

        log_item = {
            "id": next_id,
            "timestamp": timestamp,
            "user_email": user_email,
            "user_name": user_name,
            "action": action,
            "angle": angle,
            "duration": duration,
            "mqtt_topic": MQTT_TOPIC,
            "result": result,
            "ip_address": ip_address
        }

        logs.append(log_item)

        with open(JSON_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(
                logs,
                f,
                ensure_ascii=False,
                indent=2
            )

    return log_item


# ===== MQTT接続成功時 =====
def on_connect(client, userdata, flags, reason_code, properties):
    print("MQTT接続結果:", reason_code)
    client.subscribe(MQTT_STATUS_TOPIC)


# ===== MQTT送信完了時 =====
def on_publish(client, userdata, mid, reason_code, properties):
    print("MQTT送信完了 mid:", mid, "reason_code:", reason_code)


# ===== MQTT状態受信時 =====
def on_message(client, userdata, msg):
    global latest_status

    payload = msg.payload.decode("utf-8", errors="replace")

    print("===== MQTT状態受信 =====")
    print("TOPIC:", msg.topic)
    print("PAYLOAD:", payload)

    code = payload.split(",")[-1].strip()
    label = LOCK_STATE_LABELS.get(code, "unknown")

    jst = datetime.timezone(datetime.timedelta(hours=9))

    with STATUS_LOCK:
        latest_status = {
            "code": code,
            "label": label,
            "raw": payload,
            "updated_at": datetime.datetime.now(jst).isoformat(timespec="seconds")
        }


# ===== MQTT初期化 =====
def init_mqtt():
    global MQTT_CLIENT

    MQTT_CLIENT = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        transport="websockets"
    )

    MQTT_CLIENT.on_connect = on_connect
    MQTT_CLIENT.on_publish = on_publish
    MQTT_CLIENT.on_message = on_message

    # wss://broker.hivemq.com:8884/mqtt 相当
    MQTT_CLIENT.tls_set()
    MQTT_CLIENT.ws_set_options(path="/mqtt")

    print("MQTT接続中...")
    MQTT_CLIENT.connect(MQTT_BROKER, MQTT_PORT, 60)

    MQTT_CLIENT.loop_start()

    print("MQTT client started")


# ===== MQTT終了処理 =====
def close_mqtt():
    global MQTT_CLIENT

    if MQTT_CLIENT is not None:
        try:
            MQTT_CLIENT.loop_stop()
            MQTT_CLIENT.disconnect()
            print("MQTT client stopped")
        except Exception as e:
            print("MQTT終了時エラー:", e)


atexit.register(close_mqtt)


# ===== MQTT送信 =====
def publish_mqtt(angle, duration):
    global MQTT_CLIENT

    msg = {
        "angle": angle,
        "duration": duration
    }

    payload = json.dumps(msg, separators=(",", ":"))

    print("===== MQTT送信 =====")
    print("TOPIC:", MQTT_TOPIC)
    print("PAYLOAD:", payload)

    if MQTT_CLIENT is None:
        raise RuntimeError("MQTTクライアントが初期化されていません")

    with MQTT_LOCK:
        if not MQTT_CLIENT.is_connected():
            print("MQTTが切断されています。再接続します。")
            MQTT_CLIENT.reconnect()

        result = MQTT_CLIENT.publish(MQTT_TOPIC, payload, qos=0)
        result.wait_for_publish()

        print("publish rc:", result.rc)
        print("is_published:", result.is_published())

        if result.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(f"MQTT publish failed rc={result.rc}")

    return msg


# ===== トップページ =====
@app.route("/")
def index():
    return render_template(
        "index.html",
        supabase_url=SUPABASE_URL,
        supabase_anon_key=SUPABASE_ANON_KEY,
        app_url=APP_URL
    )


# ===== サーボ操作API =====
@app.route("/api/servo", methods=["POST"])
def servo():
    data = request.get_json(silent=True) or {}

    print("===== 受信データ =====")
    print(data)

    user_email = data.get("email", "unknown-user")
    user_name = data.get("name", "unknown-name")

    action = str(data.get("action", "CUSTOM")).upper()

    try:
        angle = int(data.get("angle", 90))
        duration = int(data.get("duration", 500))
    except (ValueError, TypeError):
        return jsonify({
            "status": "error",
            "message": "angle または duration が数値ではありません"
        }), 400

    angle = max(0, min(angle, 180))
    duration = max(0, min(duration, 5000))

    ip_address = request.remote_addr

    try:
        mqtt_message = publish_mqtt(angle, duration)

        log_item = save_log(
            user_email=user_email,
            user_name=user_name,
            action=action,
            angle=angle,
            duration=duration,
            result="sent",
            ip_address=ip_address
        )

        return jsonify({
            "status": "ok",
            "user_email": user_email,
            "user_name": user_name,
            "action": action,
            "mqtt_topic": MQTT_TOPIC,
            "mqtt_message": mqtt_message,
            "log": log_item
        })

    except Exception as e:
        log_item = save_log(
            user_email=user_email,
            user_name=user_name,
            action=action,
            angle=angle,
            duration=duration,
            result=f"error: {str(e)}",
            ip_address=ip_address
        )

        return jsonify({
            "status": "error",
            "message": str(e),
            "log": log_item
        }), 500


# ===== 鍵状態取得API =====
@app.route("/api/status")
def api_status():
    with STATUS_LOCK:
        return jsonify(dict(latest_status))


# ===== ログ取得API =====
@app.route("/logs")
def logs():
    logs = load_json_logs()

    latest_logs = list(reversed(logs))[:50]

    return jsonify(latest_logs)


# ===== JSONログファイル全体取得 =====
@app.route("/logs/file")
def logs_file():
    logs = load_json_logs()

    return jsonify(logs)


# ===== ログ削除API =====
@app.route("/logs/clear", methods=["POST"])
def clear_logs():
    with JSON_LOG_LOCK:
        with open(JSON_LOG_FILE, "w", encoding="utf-8") as f:
            json.dump([], f, ensure_ascii=False, indent=2)

    return jsonify({
        "status": "ok",
        "message": "ログを削除しました"
    })


# ===== 設定確認用API =====
@app.route("/config-check")
def config_check():
    return jsonify({
        "supabase_url_set": bool(SUPABASE_URL),
        "supabase_anon_key_set": bool(SUPABASE_ANON_KEY),
        "app_url": APP_URL,
        "mqtt_broker": MQTT_BROKER,
        "mqtt_port": MQTT_PORT,
        "mqtt_topic": MQTT_TOPIC,
        "mqtt_status_topic": MQTT_STATUS_TOPIC,
        "json_log_file": JSON_LOG_FILE
    })


# ===== 動作確認用 =====
@app.route("/health")
def health():
    mqtt_status = False

    if MQTT_CLIENT is not None:
        mqtt_status = MQTT_CLIENT.is_connected()

    return jsonify({
        "status": "ok",
        "mqtt_connected": mqtt_status,
        "mqtt_broker": MQTT_BROKER,
        "mqtt_port": MQTT_PORT,
        "mqtt_topic": MQTT_TOPIC,
        "json_log_file": JSON_LOG_FILE
    })


if __name__ == "__main__":
    if not SUPABASE_URL:
        print("警告: .env に SUPABASE_URL が設定されていません")

    if not SUPABASE_ANON_KEY:
        print("警告: .env に SUPABASE_ANON_KEY が設定されていません")

    print("APP_URL:", APP_URL)

    init_json_log()
    init_mqtt()

    app.run(host="0.0.0.0", port=5000, debug=False)