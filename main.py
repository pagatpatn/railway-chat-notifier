import os
import time
import threading
import requests
from googleapiclient.discovery import build

# --- ENV VARIABLES FROM RAILWAY ---
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")

KICK_CHANNEL = os.getenv("KICK_CHANNEL")  # Kick username

FACEBOOK_APP_ID = os.getenv("FACEBOOK_APP_ID")
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET")
FACEBOOK_USER_TOKEN = os.getenv("FACEBOOK_USER_TOKEN")  # Long-lived
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID")

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "chat-notifier")
NTFY_CONTROL_TOPIC = os.getenv("NTFY_CONTROL_TOPIC", "chatcontrol")

# --- GLOBALS ---
RUNNING = True
FACEBOOK_PAGE_TOKEN = None


# --- NTFY ---
def send_ntfy(platform, user, msg):
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=f"[{platform}] {user}: {msg}".encode("utf-8")
        )
    except Exception as e:
        print("NTFY error:", e)


def notify_status(message):
    """Send status messages like 'connected' to ntfy"""
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=message.encode("utf-8"))
    except:
        pass


# --- CONTROL ---
def control_listener():
    global RUNNING
    print("🟢 Listening for control commands...")
    while True:
        try:
            r = requests.get(f"https://ntfy.sh/{NTFY_CONTROL_TOPIC}/json", stream=True)
            for line in r.iter_lines():
                if line:
                    data = line.decode("utf-8")
                    if '"message":"stop"' in data.lower():
                        RUNNING = False
                        notify_status("⏹️ Chat notifier stopped")
                        print("⏹️ Stopped by ntfy")
                    elif '"message":"start"' in data.lower():
                        RUNNING = True
                        notify_status("▶️ Chat notifier started")
                        print("▶️ Started by ntfy")
        except Exception as e:
            print("Control error:", e)
            time.sleep(5)


# --- YOUTUBE ---
def connect_youtube():
    print("🟢 Connecting to YouTube...")
    notify_status("✅ YouTube connected")
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

    live_chat_id = None
    try:
        request = youtube.search().list(
            part="id", channelId=YOUTUBE_CHANNEL_ID, eventType="live", type="video"
        )
        response = request.execute()
        if response["items"]:
            video_id = response["items"][0]["id"]["videoId"]
            video_response = youtube.videos().list(
                part="liveStreamingDetails", id=video_id
            ).execute()
            live_chat_id = video_response["items"][0]["liveStreamingDetails"]["activeLiveChatId"]
    except Exception as e:
        print("YouTube error:", e)
        return

    next_page = None
    while True:
        if not RUNNING:
            time.sleep(2)
            continue
        try:
            chat_request = youtube.liveChatMessages().list(
                liveChatId=live_chat_id,
                part="snippet,authorDetails",
                pageToken=next_page
            )
            chat_response = chat_request.execute()
            for item in chat_response["items"]:
                user = item["authorDetails"]["displayName"]
                msg = item["snippet"]["displayMessage"]
                print(f"[YouTube] {user}: {msg}")
                send_ntfy("YouTube", user, msg)
            next_page = chat_response.get("nextPageToken")
            time.sleep(5)
        except Exception as e:
            print("YouTube chat error:", e)
            time.sleep(10)


# --- KICK (simple poller) ---
def connect_kick():
    print("🟢 Connecting to Kick...")
    notify_status("✅ Kick connected")
    last_seen = set()
    while True:
        if not RUNNING:
            time.sleep(2)
            continue
        try:
            url = f"https://kick.com/api/v2/channels/{KICK_CHANNEL}/messages"
            r = requests.get(url).json()
            for msg in r:
                mid = msg.get("id")
                if mid not in last_seen:
                    last_seen.add(mid)
                    user = msg["sender"]["username"]
                    text = msg["content"]
                    print(f"[Kick] {user}: {text}")
                    send_ntfy("Kick", user, text)
            time.sleep(5)
        except Exception as e:
            print("Kick error:", e)
            time.sleep(10)


# --- FACEBOOK ---
def refresh_facebook_token():
    """Refresh the long-lived user access token"""
    global FACEBOOK_USER_TOKEN
    try:
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
            print("🔄 Facebook user token refreshed")
            return FACEBOOK_USER_TOKEN
        else:
            print("⚠️ Failed to refresh Facebook token:", r)
            return None
    except Exception as e:
        print("Facebook refresh error:", e)
        return None


def get_page_token():
    global FACEBOOK_PAGE_TOKEN
    try:
        url = f"https://graph.facebook.com/{FACEBOOK_PAGE_ID}"
        params = {"fields": "access_token", "access_token": FACEBOOK_USER_TOKEN}
        r = requests.get(url, params=params).json()
        if "access_token" in r:
            FACEBOOK_PAGE_TOKEN = r["access_token"]
            print("✅ Got Facebook Page token")
            return FACEBOOK_PAGE_TOKEN
        else:
            print("⚠️ Could not get Facebook Page token:", r)
            return None
    except Exception as e:
        print("Facebook page token error:", e)
        return None


def connect_facebook():
    print("🟢 Connecting to Facebook...")
    notify_status("✅ Facebook connected")

    refresh_facebook_token()
    get_page_token()

    live_video_id = None
    while True:
        if not RUNNING:
            time.sleep(2)
            continue
        try:
            # find active live
            url = f"https://graph.facebook.com/{FACEBOOK_PAGE_ID}/live_videos"
            params = {"fields": "id,status", "access_token": FACEBOOK_PAGE_TOKEN}
            r = requests.get(url, params=params).json()
            if "data" in r:
                lives = [v for v in r["data"] if v.get("status") == "LIVE"]
                if lives:
                    live_video_id = lives[0]["id"]

            if live_video_id:
                comments_url = f"https://graph.facebook.com/{live_video_id}/comments"
                params = {"fields": "from{name},message", "access_token": FACEBOOK_PAGE_TOKEN}
                comments = requests.get(comments_url, params=params).json()
                if "data" in comments:
                    for c in comments["data"]:
                        user = c["from"]["name"]
                        msg = c["message"]
                        print(f"[Facebook] {user}: {msg}")
                        send_ntfy("Facebook", user, msg)

            time.sleep(5)
        except Exception as e:
            print("Facebook error:", e)
            time.sleep(10)


def token_refresher():
    while True:
        new_token = refresh_facebook_token()
        if new_token:
            get_page_token()
        time.sleep(24 * 3600)


# --- MAIN ---
if __name__ == "__main__":
    # Control listener
    threading.Thread(target=control_listener, daemon=True).start()

    # Refresh Facebook tokens daily
    threading.Thread(target=token_refresher, daemon=True).start()

    # Chat connectors
    threading.Thread(target=connect_youtube, daemon=True).start()
    threading.Thread(target=connect_kick, daemon=True).start()
    threading.Thread(target=connect_facebook, daemon=True).start()

    # Keep alive
    while True:
        time.sleep(60)
