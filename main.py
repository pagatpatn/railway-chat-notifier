import os
import time
import threading
import requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from websocket import create_connection

# -------------------
# ENVIRONMENT VARIABLES (Railway)
# -------------------
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")
KICK_CHANNEL = os.getenv("KICK_CHANNEL")

FACEBOOK_APP_ID = os.getenv("FACEBOOK_APP_ID")
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET")
FACEBOOK_USER_TOKEN = os.getenv("FACEBOOK_USER_TOKEN")  # short-lived (script refreshes it)
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID")

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "chat-notifier")  # chat messages
NTFY_CONTROL_TOPIC = os.getenv("NTFY_CONTROL_TOPIC", "chatcontrol")  # start/stop control

# -------------------
# STATE
# -------------------
running = True
current_page_token = None

# -------------------
# Helpers
# -------------------
def send_ntfy(source, user, message):
    """Send chat message to ntfy"""
    try:
        url = f"https://ntfy.sh/{NTFY_TOPIC}"
        requests.post(url, data=f"[{source}] {user}: {message}".encode("utf-8"))
    except Exception as e:
        print("NTFY send error:", e)

def send_ntfy_status(msg):
    """Send status/connection updates"""
    try:
        url = f"https://ntfy.sh/{NTFY_TOPIC}"
        requests.post(url, data=f"🔔 {msg}".encode("utf-8"))
    except Exception as e:
        print("NTFY status error:", e)

# -------------------
# YOUTUBE
# -------------------
def poll_youtube():
    global running
    print("🟢 Connecting to YouTube...")
    send_ntfy_status("YouTube connected successfully")

    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

    live_chat_id = None
    try:
        request = youtube.search().list(
            part="id",
            channelId=YOUTUBE_CHANNEL_ID,
            eventType="live",
            type="video"
        )
        response = request.execute()
        if response["items"]:
            video_id = response["items"][0]["id"]["videoId"]
            live = youtube.videos().list(part="liveStreamingDetails", id=video_id).execute()
            live_chat_id = live["items"][0]["liveStreamingDetails"]["activeLiveChatId"]
    except HttpError as e:
        print("YouTube API error:", e)

    if not live_chat_id:
        print("❌ No active YouTube live chat")
        return

    next_page_token = None
    while running:
        try:
            chat = youtube.liveChatMessages().list(
                liveChatId=live_chat_id,
                part="snippet,authorDetails",
                pageToken=next_page_token
            ).execute()

            for item in chat["items"]:
                user = item["authorDetails"]["displayName"]
                msg = item["snippet"]["displayMessage"]
                print(f"[YouTube] {user}: {msg}")
                send_ntfy("YouTube", user, msg)

            next_page_token = chat.get("nextPageToken")
            time.sleep(5)
        except Exception as e:
            print("YouTube error:", e)
            time.sleep(10)

# -------------------
# KICK
# -------------------
def poll_kick():
    global running
    print("🟢 Connecting to Kick...")
    send_ntfy_status("Kick connected successfully")

    try:
        ws = create_connection(f"wss://chat.kick.com/socket.io/?EIO=3&transport=websocket")
        ws.send("40/chat,")  # connect
        ws.send(f'42/chat,["join",{{"room":"channel:{KICK_CHANNEL}"}}]')
    except Exception as e:
        print("Kick connection error:", e)
        return

    while running:
        try:
            msg = ws.recv()
            if "message" in msg:
                print(f"[Kick] {msg}")
                send_ntfy("Kick", "User", msg)
        except Exception as e:
            print("Kick error:", e)
            time.sleep(5)

# -------------------
# FACEBOOK
# -------------------
def refresh_user_token():
    global FACEBOOK_USER_TOKEN
    url = "https://graph.facebook.com/v17.0/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": FACEBOOK_APP_ID,
        "client_secret": FACEBOOK_APP_SECRET,
        "fb_exchange_token": FACEBOOK_USER_TOKEN,
    }
    r = requests.get(url, params=params).json()
    if "access_token" in r:
        FACEBOOK_USER_TOKEN = r["access_token"]
        print("🔄 Got long-lived User Token")
    else:
        print("❌ Failed to refresh User Token:", r)

def get_page_token():
    global current_page_token
    url = f"https://graph.facebook.com/v17.0/{FACEBOOK_PAGE_ID}"
    params = {
        "fields": "access_token",
        "access_token": FACEBOOK_USER_TOKEN,
    }
    r = requests.get(url, params=params).json()
    if "access_token" in r:
        current_page_token = r["access_token"]
        print("✅ Got Page Token")
    else:
        print("❌ Failed to get Page Token:", r)

def poll_facebook():
    global running, current_page_token
    print("🟢 Connecting to Facebook...")
    refresh_user_token()
    get_page_token()
    send_ntfy_status("Facebook connected successfully")

    while running:
        try:
            if not current_page_token:
                get_page_token()
                time.sleep(5)
                continue

            # find live video
            url = f"https://graph.facebook.com/v17.0/{FACEBOOK_PAGE_ID}/live_videos"
            params = {
                "access_token": current_page_token,
                "fields": "id,status",
                "broadcast_status": "LIVE"
            }
            res = requests.get(url, params=params).json()
            videos = res.get("data", [])
            if not videos:
                time.sleep(10)
                continue

            live_id = videos[0]["id"]

            # poll comments
            comments_url = f"https://graph.facebook.com/v17.0/{live_id}/comments"
            comments_params = {
                "access_token": current_page_token,
                "order": "reverse_chronological",
                "fields": "from{name},message"
            }
            comments = requests.get(comments_url, params=comments_params).json()
            for c in comments.get("data", []):
                user = c["from"]["name"]
                msg = c["message"]
                print(f"[Facebook] {user}: {msg}")
                send_ntfy("Facebook", user, msg)

        except Exception as e:
            print("Facebook error:", e)

        time.sleep(5)

# -------------------
# NTFY CONTROL
# -------------------
def poll_control():
    global running
    url = f"https://ntfy.sh/{NTFY_CONTROL_TOPIC}/json"
    print(f"🟢 Listening for control commands at {url}")
    send_ntfy_status("NTFY control connected successfully")

    with requests.get(url, stream=True) as r:
        for line in r.iter_lines():
            if line:
                data = line.decode("utf-8")
                if '"message":"start"' in data.lower():
                    running = True
                    send_ntfy_status("✅ Chat monitoring started")
                    threading.Thread(target=start_all, daemon=True).start()
                elif '"message":"stop"' in data.lower():
                    running = False
                    send_ntfy_status("⏹ Chat monitoring stopped")

# -------------------
# START ALL
# -------------------
def start_all():
    threading.Thread(target=poll_youtube, daemon=True).start()
    threading.Thread(target=poll_kick, daemon=True).start()
    threading.Thread(target=poll_facebook, daemon=True).start()

# -------------------
# MAIN
# -------------------
if __name__ == "__main__":
    print("🚀 Chat notifier starting...")
    threading.Thread(target=poll_control, daemon=True).start()
    start_all()
    while True:
        time.sleep(1)
