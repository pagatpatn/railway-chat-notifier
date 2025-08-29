import os
import re
import ssl
import time
import json
import queue
import threading
import requests
import websocket

# --- Environment variables (set these in Railway) ---
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID", "")
FACEBOOK_APP_ID = os.getenv("FACEBOOK_APP_ID", "")
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET", "")
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID", "")
FACEBOOK_SHORT_TOKEN = os.getenv("FACEBOOK_SHORT_TOKEN", "")
KICK_USERNAME = os.getenv("KICK_USERNAME", "")
KICK_CHANNEL = os.getenv("KICK_CHANNEL", "")
NTFY_TOPIC = os.getenv("NTFY_TOPIC", "chat-notifier")

# --- Queue for ntfy messages ---
ntfy_queue = queue.Queue()
running = True


# --- NTFY Worker (with start/stop) ---
def ntfy_worker():
    global running
    print("📡 NTFY Worker started")
    while True:
        try:
            topic, user, msg = ntfy_queue.get()
            if msg.strip().lower() == "!stop":
                running = False
                print("⏹️ Received STOP command")
                continue
            elif msg.strip().lower() == "!start":
                running = True
                print("▶️ Received START command")
                continue

            if running:
                requests.post(f"https://ntfy.sh/{NTFY_TOPIC}",
                              data=f"[{topic}] {user}: {msg}".encode("utf-8"))
                time.sleep(5)  # delay 5s between messages
        except Exception as e:
            print("NTFY Worker error:", e)


def send_ntfy(platform, user, msg):
    ntfy_queue.put((platform, user, msg))


# --- YouTube ---
def get_youtube_live_chat_id():
    """Get the active YouTube live stream video + live chat ID"""
    try:
        url = (
            f"https://www.googleapis.com/youtube/v3/liveBroadcasts"
            f"?part=snippet,contentDetails,status"
            f"&broadcastStatus=active"
            f"&broadcastType=all"
            f"&key={YOUTUBE_API_KEY}"
        )
        r = requests.get(url).json()
        if "items" not in r or not r["items"]:
            print("❌ No active YouTube live stream found.")
            return None, None

        broadcast = r["items"][0]
        video_id = broadcast["id"]
        live_chat_id = broadcast["snippet"].get("liveChatId")

        if not live_chat_id:
            print("❌ Active stream has no live chat.")
            return None, None

        print(f"✅ Found YouTube live: Video ID {video_id}")
        send_ntfy("YouTube", "System", f"✅ Connected to YouTube live {video_id}")
        return video_id, live_chat_id

    except Exception as e:
        print("YouTube API error:", e)
        return None, None


def connect_youtube():
    print("🟢 Connecting to YouTube...")

    while True:
        try:
            # Get live broadcast & chat id
            video_id, live_chat_id = get_youtube_live_chat_id()
            if not live_chat_id:
                time.sleep(10)
                continue

            url = (
                f"https://www.googleapis.com/youtube/v3/liveChat/messages"
                f"?liveChatId={live_chat_id}"
                f"&part=snippet,authorDetails"
                f"&key={YOUTUBE_API_KEY}"
            )

            print("✅ Connected to YouTube live chat")
            page_token = None

            while True:
                resp = requests.get(
                    url + (f"&pageToken={page_token}" if page_token else "")
                )
                data = resp.json()

                # If no items → maybe stream ended, restart
                if "items" not in data:
                    print("⚠️ No messages, rechecking live status...")
                    break

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



# --- Facebook ---
def get_facebook_page_token():
    try:
        url = f"https://graph.facebook.com/v17.0/oauth/access_token"
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": FACEBOOK_APP_ID,
            "client_secret": FACEBOOK_APP_SECRET,
            "fb_exchange_token": FACEBOOK_SHORT_TOKEN,
        }
        r = requests.get(url, params=params).json()
        long_token = r.get("access_token")
        if not long_token:
            print("Facebook: Failed to refresh token:", r)
            return None

        url = f"https://graph.facebook.com/{FACEBOOK_PAGE_ID}"
        params = {
            "fields": "access_token",
            "access_token": long_token
        }
        r = requests.get(url, params=params).json()
        return r.get("access_token")
    except Exception as e:
        print("Facebook token error:", e)
        return None


def connect_facebook():
    print("🟢 Connecting to Facebook...")
    token = get_facebook_page_token()
    if not token:
        print("❌ Facebook: Could not get page token")
        return

    url = f"https://streaming-graph.facebook.com/{FACEBOOK_PAGE_ID}/live_comments"
    params = {"access_token": token, "comment_rate": 1, "fields": "from{name},message"}

    try:
        with requests.get(url, params=params, stream=True) as r:
            if r.status_code != 200:
                print("Facebook error:", r.text)
                return
            print("✅ Connected to Facebook live chat")
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
    threading.Thread(target=connect_youtube, daemon=True).start()
    threading.Thread(target=connect_facebook, daemon=True).start()
    threading.Thread(target=connect_kick, daemon=True).start()

    while True:
        time.sleep(1)
