from flask import Flask, render_template, request, jsonify
import datetime
import json
import threading
import atexit
import os
import sqlite3
import urllib.request
import urllib.error
from dotenv import load_dotenv
import paho.mqtt.client as mqtt


# ===== .env 読み込み =====
load_dotenv()


app = Flask(__name__)


# ===== Supabase / アプリURL設定 =====
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")
APP_URL = os.getenv("APP_URL", "http://localhost:5000")


# ===== アクセス許可アカウント設定 =====
ALLOWED_EMAILS = set(
    email.strip().lower()
    for email in os.getenv("ALLOWED_EMAILS", "").split(",")
    if email.strip()
)


# ===== 未許可ログイン通知設定 (Discord Webhook) =====
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
NOTIFY_COOLDOWN_SECONDS = 300

_last_notified_at = {}
_notify_lock = threading.Lock()


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


# ===== ログDB設定 (SQLite / 永久保存) =====
LOG_DB_FILE = os.getenv("LOG_DB_FILE", "logs.db")
LEGACY_JSON_LOG_FILE = os.getenv("JSON_LOG_FILE", "access_logs.json")
LOG_DB_LOCK = threading.Lock()


# ===== ログDB初期化 =====
def init_log_db():
    with LOG_DB_LOCK:
        conn = sqlite3.connect(LOG_DB_FILE)
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS access_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    user_email TEXT,
                    user_name TEXT,
                    action TEXT NOT NULL,
                    angle INTEGER,
                    duration INTEGER,
                    mqtt_topic TEXT,
                    result TEXT,
                    ip_address TEXT
                )
            """)
            conn.commit()
        finally:
            conn.close()

    migrate_legacy_json_logs()


# ===== 旧JSONログをDBへ取り込み (初回起動時のみ) =====
def migrate_legacy_json_logs():
    if not os.path.exists(LEGACY_JSON_LOG_FILE):
        return

    with open(LEGACY_JSON_LOG_FILE, "r", encoding="utf-8") as f:
        try:
            legacy_logs = json.load(f)
        except json.JSONDecodeError:
            legacy_logs = []

    if legacy_logs:
        with LOG_DB_LOCK:
            conn = sqlite3.connect(LOG_DB_FILE)
            try:
                conn.executemany(
                    """
                    INSERT INTO access_logs
                        (timestamp, user_email, user_name, action, angle,
                         duration, mqtt_topic, result, ip_address)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            log.get("timestamp"),
                            log.get("user_email"),
                            log.get("user_name"),
                            log.get("action"),
                            log.get("angle"),
                            log.get("duration"),
                            log.get("mqtt_topic"),
                            log.get("result"),
                            log.get("ip_address")
                        )
                        for log in legacy_logs
                    ]
                )
                conn.commit()
            finally:
                conn.close()

        print(f"旧JSONログ {len(legacy_logs)}件をDBへ移行しました")

    os.remove(LEGACY_JSON_LOG_FILE)


# ===== ログ保存 (SQLite) =====
def save_log(
    user_email,
    user_name,
    action,
    angle,
    duration,
    result,
    ip_address
):
    jst = datetime.timezone(datetime.timedelta(hours=9))
    timestamp = datetime.datetime.now(jst).isoformat(timespec="seconds")

    with LOG_DB_LOCK:
        conn = sqlite3.connect(LOG_DB_FILE)
        try:
            cur = conn.execute(
                """
                INSERT INTO access_logs
                    (timestamp, user_email, user_name, action, angle,
                     duration, mqtt_topic, result, ip_address)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp, user_email, user_name, action, angle,
                    duration, MQTT_TOPIC, result, ip_address
                )
            )
            conn.commit()
            log_id = cur.lastrowid
        finally:
            conn.close()

    return {
        "id": log_id,
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


# ===== ログ読み込み (SQLite) =====
def load_logs(limit=None):
    query = (
        "SELECT id, timestamp, user_email, user_name, action, angle, "
        "duration, mqtt_topic, result, ip_address "
        "FROM access_logs ORDER BY id DESC"
    )

    params = ()

    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)

    conn = sqlite3.connect(LOG_DB_FILE)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    return [dict(row) for row in rows]


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


# ===== Supabaseトークン検証 =====
def verify_supabase_user(access_token):
    if not SUPABASE_URL or not SUPABASE_ANON_KEY:
        return None

    req = urllib.request.Request(
        f"{SUPABASE_URL}/auth/v1/user",
        headers={
            "Authorization": f"Bearer {access_token}",
            "apikey": SUPABASE_ANON_KEY
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=5) as res:
            return json.loads(res.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError):
        return None


# ===== Discord通知送信 =====
def send_discord_notification(message):
    if not DISCORD_WEBHOOK_URL:
        print("警告: DISCORD_WEBHOOK_URL が未設定のため通知を送信できません")
        return

    payload = json.dumps({"content": message}).encode("utf-8")

    req = urllib.request.Request(
        DISCORD_WEBHOOK_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (locker_control notifier)"
        },
        method="POST"
    )

    try:
        urllib.request.urlopen(req, timeout=5)
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print("Discord通知送信エラー:", e)


# ===== 未許可ログインの通知 (同一アカウントは一定時間クールダウン) =====
def notify_unauthorized_login(user, ip_address):
    email = user.get("email", "unknown")

    now = datetime.datetime.now(datetime.timezone.utc).timestamp()

    with _notify_lock:
        last = _last_notified_at.get(email, 0)

        if now - last < NOTIFY_COOLDOWN_SECONDS:
            return

        _last_notified_at[email] = now

    name = (user.get("user_metadata") or {}).get("name", "unknown")

    jst = datetime.timezone(datetime.timedelta(hours=9))
    timestamp = datetime.datetime.now(jst).isoformat(timespec="seconds")

    message = (
        "許可されていないGoogleアカウントがログインを試みました\n"
        f"メール: {email}\n"
        f"名前: {name}\n"
        f"IP: {ip_address}\n"
        f"日時: {timestamp}"
    )

    threading.Thread(
        target=send_discord_notification,
        args=(message,),
        daemon=True
    ).start()


# ===== 許可アカウントチェック =====
# 戻り値: (user情報, None) が成功時 / (None, (HTTPステータス, メッセージ)) が失敗時
# notify_on_deny=True の場合、未許可アカウントの検出時にDiscordへ通知する
def require_allowed_user(notify_on_deny=False):
    auth_header = request.headers.get("Authorization", "")

    if not auth_header.startswith("Bearer "):
        return None, (401, "ログインしてください。")

    access_token = auth_header[len("Bearer "):]

    user = verify_supabase_user(access_token)

    if user is None:
        return None, (401, "認証に失敗しました。再度ログインしてください。")

    email = (user.get("email") or "").strip().lower()

    if not email or email not in ALLOWED_EMAILS:
        if notify_on_deny:
            notify_unauthorized_login(user, request.remote_addr)

        return None, (403, "このGoogleアカウントには操作権限がありません。管理者に連絡してください。")

    return user, None


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
    user, err = require_allowed_user()

    if err:
        status_code, message = err
        return jsonify({"status": "error", "message": message}), status_code

    data = request.get_json(silent=True) or {}

    print("===== 受信データ =====")
    print(data)

    user_email = user.get("email", "unknown-user")
    user_name = (user.get("user_metadata") or {}).get("name", user_email)

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


# ===== 認証確認用API =====
@app.route("/api/whoami")
def whoami():
    user, err = require_allowed_user(notify_on_deny=True)

    if err:
        status_code, message = err
        return jsonify({"status": "error", "message": message}), status_code

    return jsonify({
        "status": "ok",
        "email": user.get("email"),
        "name": (user.get("user_metadata") or {}).get("name")
    })


# ===== 鍵状態取得API =====
@app.route("/api/status")
def api_status():
    user, err = require_allowed_user()

    if err:
        status_code, message = err
        return jsonify({"status": "error", "message": message}), status_code

    with STATUS_LOCK:
        return jsonify(dict(latest_status))


# ===== ログ取得API =====
@app.route("/logs")
def logs():
    user, err = require_allowed_user()

    if err:
        status_code, message = err
        return jsonify({"status": "error", "message": message}), status_code

    return jsonify(load_logs(limit=50))


# ===== ログ全件取得 (DB全履歴) =====
@app.route("/logs/file")
def logs_file():
    user, err = require_allowed_user()

    if err:
        status_code, message = err
        return jsonify({"status": "error", "message": message}), status_code

    return jsonify(load_logs())


# ===== ログ削除API =====
@app.route("/logs/clear", methods=["POST"])
def clear_logs():
    user, err = require_allowed_user()

    if err:
        status_code, message = err
        return jsonify({"status": "error", "message": message}), status_code

    with LOG_DB_LOCK:
        conn = sqlite3.connect(LOG_DB_FILE)
        try:
            conn.execute("DELETE FROM access_logs")
            conn.commit()
        finally:
            conn.close()

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
        "log_db_file": LOG_DB_FILE,
        "allowed_emails_count": len(ALLOWED_EMAILS),
        "discord_webhook_set": bool(DISCORD_WEBHOOK_URL)
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
        "log_db_file": LOG_DB_FILE
    })


if __name__ == "__main__":
    if not SUPABASE_URL:
        print("警告: .env に SUPABASE_URL が設定されていません")

    if not SUPABASE_ANON_KEY:
        print("警告: .env に SUPABASE_ANON_KEY が設定されていません")

    if not ALLOWED_EMAILS:
        print("警告: .env に ALLOWED_EMAILS が設定されていません（全アカウントの操作が拒否されます）")

    if not DISCORD_WEBHOOK_URL:
        print("警告: .env に DISCORD_WEBHOOK_URL が設定されていません（未許可ログインの通知は送信されません）")

    print("APP_URL:", APP_URL)

    init_log_db()
    init_mqtt()

    app.run(host="0.0.0.0", port=5000, debug=False)