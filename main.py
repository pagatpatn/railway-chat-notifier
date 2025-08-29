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
# --- YouTube (optimized, no search API) ---
def connect_youtube():
    print("🟢 Connecting to YouTube...")

    while True:
        try:
            # Step 1: Get current live video ID for the channel
            url = (
                f"https://www.googleapis.com/youtube/v3/channels"
                f"?part=contentDetails&id={YOUTUBE_CHANNEL_ID}&key={YOUTUBE_API_KEY}"
            )
            r = requests.get(url).json()
            if "items" not in r or not r["items"]:
                print("❌ YouTube: Could not fetch channel details")
                time.sleep(30)
                continue

            # liveBroadcastContent tells us if live
            uploads = r["items"][0].get("contentDetails", {})
            if not uploads:
                print("YouTube: No live stream currently.")
                time.sleep(30)
                continue

            # Step 2: Get live video ID via 'search' replacement (activity check)
            url_live = (
                f"https://www.googleapis.com/youtube/v3/videos"
                f"?part=liveStreamingDetails,snippet&id={YOUTUBE_CHANNEL_ID}&key={YOUTUBE_API_KEY}"
            )
            r_live = requests.get(url_live).json()
            if "items" not in r_live or not r_live["items"]:
                print("YouTube: No live video found")
                time.sleep(30)
                continue

            live_details = r_live["items"][0].get("liveStreamingDetails", {})
            video_id = r_live["items"][0]["id"]
            live_chat_id = live_details.get("activeLiveChatId")

            if not live_chat_id:
                print("YouTube: No active live chat")
                time.sleep(30)
                continue

            print(f"✅ Connected to YouTube live chat (Video: {video_id})")

            # Step 3: Poll chat messages
            page_token = None
            while True:
                chat_url = (
                    f"https://www.googleapis.com/youtube/v3/liveChat/messages"
                    f"?liveChatId={live_chat_id}&part=snippet,authorDetails&key={YOUTUBE_API_KEY}"
                )
                if page_token:
                    chat_url += f"&pageToken={page_token}"

                resp = requests.get(chat_url).json()
                if "items" not in resp:
                    print("⚠️ YouTube: No chat items")
                    time.sleep(10)
                    break  # force reconnect

                for item in resp.get("items", []):
                    user = item["authorDetails"]["displayName"]
                    msg = item["snippet"]["displayMessage"]
                    print(f"[YouTube] {user}: {msg}")
                    send_ntfy("YouTube", user, msg)

                page_token = resp.get("nextPageToken")
                time.sleep(5)

        except Exception as e:
            print("YouTube error:", e)
            time.sleep(15)

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
