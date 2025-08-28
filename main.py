import os
import re
import ssl
import time
import json
import queue
import threading
import requests
import websocket

# --- Environment variables (set in Railway) ---
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID", "")
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "")
FACEBOOK_USER_ACCESS_TOKEN = os.getenv("FACEBOOK_USER_ACCESS_TOKEN", "")
KICK_USERNAME = os.getenv("KICK_USERNAME", "")
KICK_CHANNEL = os.getenv("KICK_CHANNEL", "")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "chat-notifier")
NTFY_CONTROL_TOPIC = os.getenv("NTFY_CONTROL_TOPIC", "chatcontrol")

# --- Queue for ntfy messages ---
ntfy_queue = queue.Queue()
running = True


# --- NTFY Worker (with start/stop) ---
def ntfy_worker():
    global running
    print("📡 NTFY Worker started")
    # Notify successful worker connection
    requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data="✅ NTFY Worker connected".encode("utf-8"))

    while True:
        try:
            topic, user, msg = ntfy_queue.get()
            if running:
                requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                              data=f"[{topic}] {user}: {msg}".encode("utf-8"))
                time.sleep(2)  # small delay
        except Exception as e:
            print("NTFY Worker error:", e)


def send_ntfy(platform, user, msg):
    ntfy_queue.put((platform, user, msg))


# --- NTFY Control Listener ---
def ntfy_control():
    global running
    print("📡 Listening for control messages...")
    url = f"https://ntfy.sh/{NTFY_CONTROL_TOPIC}/json"
    try:
        with requests.get(url, stream=True) as r:
            for line in r.iter_lines():
                if line:
                    try:
                        data = json.loads(line.decode("utf-8"))
                        msg = data.get("message", "").strip().lower()
                        if msg == "!stop":
                            running = False
                            print("⏹️ Control: STOP received")
                            requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data="⏹️ Chat forwarding stopped".encode("utf-8"))
                        elif msg == "!start":
                            running = True
                            print("▶️ Control: START received")
                            requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data="▶️ Chat forwarding resumed".encode("utf-8"))
                    except:
                        pass
    except Exception as e:
        print("NTFY Control error:", e)


# --- YouTube ---
def connect_youtube():
    print("🟢 Connecting to YouTube...")
    while True:
        try:
            url = f"https://www.googleapis.com/youtube/v3/search?part=snippet&channelId={YOUTUBE_CHANNEL_ID}&type=video&eventType=live&key={YOUTUBE_API_KEY}"
            r = requests.get(url).json()
            items = r.get("items", [])
            if not items:
                print("YouTube: No live stream currently.")
                time.sleep(10)
                continue

            video_id = items[0]["id"]["videoId"]
            live_url = f"https://www.googleapis.com/youtube/v3/liveChat/messages?liveChatId={get_livechat_id(video_id)}&part=snippet,authorDetails&key={YOUTUBE_API_KEY}"

            print("✅ Connected to YouTube live chat")
            requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data="✅ YouTube connected".encode("utf-8"))

            page_token = None
            while True:
                resp = requests.get(live_url + (f"&pageToken={page_token}" if page_token else ""))
                data = resp.json()
                for item in data.get("items", []):
                    user = item["authorDetails"]["displayName"]
                    msg = item["snippet"]["displayMessage"]
                    print(f"[YouTube] {user}: {msg}")
                    send_ntfy("YouTube", user, msg)

                page_token = data.get("nextPageToken")
                time.sleep(5)
        except Exception as e:
            print("YouTube error:", e)
            time.sleep(10)


def get_livechat_id(video_id):
    url = f"https://www.googleapis.com/youtube/v3/videos?part=liveStreamingDetails&id={video_id}&key={YOUTUBE_API_KEY}"
    r = requests.get(url).json()
    return r["items"][0]["liveStreamingDetails"]["activeLiveChatId"]


# --- Facebook ---
def connect_facebook():
    print("🟢 Connecting to Facebook...")
    token = FACEBOOK_USER_ACCESS_TOKEN
    if not token:
        print("❌ Facebook: No user access token set")
        return

    url = f"https://streaming-graph.facebook.com/{FACEBOOK_PAGE_ID}/live_comments"
    params = {"access_token": token, "comment_rate": 1, "fields": "from{name},message"}

    try:
        with requests.get(url, params=params, stream=True) as r:
            if r.status_code != 200:
                print("Facebook error:", r.text)
                return
            print("✅ Connected to Facebook live chat")
            requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data="✅ Facebook connected".encode("utf-8"))

            for line in r.iter_lines():
                if line:
                    try:
                        data = json.loads(line.decode("utf-8").split(":", 1)[-1])
                        if "message" in data:
                            user = data["from"]["name"]
                            msg = data["message"]
                            print(f"[Facebook] {user}: {msg}")
                            send_ntfy("Facebook", user, msg)
                    except:
                        pass
    except Exception as e:
        print("Facebook stream error:", e)


# --- Kick ---
def connect_kick():
    print("🟢 Connecting to Kick...")

    def on_message(ws, message):
        for line in message.split("\r\n"):
            if "PRIVMSG" in line:
                match = re.match(r":(.*?)!.* PRIVMSG #.* :(.*)", line)
                if match:
                    user, msg = match.groups()
                    print(f"[Kick] {user}: {msg}")
                    send_ntfy("Kick", user, msg)

    def on_open(ws):
        print("✅ Connected to Kick chat")
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data="✅ Kick connected".encode("utf-8"))
        ws.send(f"NICK {KICK_USERNAME}")
        ws.send(f"JOIN #{KICK_CHANNEL}")

    ws = websocket.WebSocketApp(
        "wss://irc-ws.chat.kick.com/",
        on_message=on_message,
        on_open=on_open,
    )
    ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE})


# --- Run all ---
if __name__ == "__main__":
    threading.Thread(target=ntfy_worker, daemon=True).start()
    threading.Thread(target=ntfy_control, daemon=True).start()
    threading.Thread(target=connect_youtube, daemon=True).start()
    threading.Thread(target=connect_facebook, daemon=True).start()
    threading.Thread(target=connect_kick, daemon=True).start()

    while True:
        time.sleep(1)
