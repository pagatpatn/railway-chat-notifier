import os
import time
import json
import requests
import asyncio
import websockets
from googleapiclient.discovery import build

# =====================
# Environment Variables
# =====================
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")
YOUTUBE_VIDEO_ID = os.getenv("YOUTUBE_VIDEO_ID")  # Optional: avoids quota burn

KICK_CHANNEL = os.getenv("KICK_CHANNEL")  # username, e.g. "trainwreckstv"

FACEBOOK_APP_ID = os.getenv("FACEBOOK_APP_ID")
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET")
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID")
FACEBOOK_USER_TOKEN = os.getenv("FACEBOOK_USER_TOKEN")

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "streamchats123")
NTFY_CONTROL_TOPIC = os.getenv("NTFY_CONTROL_TOPIC", "chatcontrol")

# =====================
# Facebook Token Logic
# =====================
def get_long_lived_user_token():
    url = f"https://graph.facebook.com/v18.0/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": FACEBOOK_APP_ID,
        "client_secret": FACEBOOK_APP_SECRET,
        "fb_exchange_token": FACEBOOK_USER_TOKEN
    }
    r = requests.get(url, params=params)
    data = r.json()
    if "access_token" in data:
        print("✅ Got long-lived user token")
        return data["access_token"]
    else:
        print("❌ Failed to refresh user token:", data)
        return None

def get_page_token(user_token):
    url = f"https://graph.facebook.com/v18.0/{FACEBOOK_PAGE_ID}"
    params = {"fields": "access_token", "access_token": user_token}
    r = requests.get(url, params=params)
    data = r.json()
    if "access_token" in data:
        print("✅ Got page token")
        return data["access_token"]
    else:
        print("❌ Failed to get Page Token:", data)
        return None

def init_facebook_tokens():
    user_token = get_long_lived_user_token()
    if not user_token:
        return None
    return get_page_token(user_token)

FACEBOOK_PAGE_TOKEN = init_facebook_tokens()

# =====================
# NTFY Notification
# =====================
def send_ntfy(msg):
    url = f"https://ntfy.sh/{NTFY_TOPIC}"
    requests.post(url, data=msg.encode("utf-8"))
    print(f"📢 Sent to NTFY: {msg}")

def notify_connected(service):
    send_ntfy(f"✅ {service} connected successfully!")

# =====================
# YouTube Live Chat
# =====================
def get_live_chat_id():
    if YOUTUBE_VIDEO_ID:  # use fixed video id
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
        response = youtube.videos().list(part="liveStreamingDetails", id=YOUTUBE_VIDEO_ID).execute()
        items = response.get("items", [])
        if items and "liveStreamingDetails" in items[0]:
            return items[0]["liveStreamingDetails"].get("activeLiveChatId")
    else:  # fallback to search (quota heavy)
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
        request = youtube.search().list(
            part="id",
            channelId=YOUTUBE_CHANNEL_ID,
            eventType="live",
            type="video"
        )
        response = request.execute()
        items = response.get("items", [])
        if items:
            vid = items[0]["id"]["videoId"]
            response = youtube.videos().list(part="liveStreamingDetails", id=vid).execute()
            return response["items"][0]["liveStreamingDetails"]["activeLiveChatId"]
    return None

async def youtube_chat():
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
    chat_id = get_live_chat_id()
    if not chat_id:
        print("❌ No YouTube live chat found.")
        return

    notify_connected("YouTube")

    while True:
        try:
            request = youtube.liveChatMessages().list(
                liveChatId=chat_id, part="snippet,authorDetails"
            )
            response = request.execute()
            for item in response.get("items", []):
                author = item["authorDetails"]["displayName"]
                message = item["snippet"]["displayMessage"]
                send_ntfy(f"[YouTube] {author}: {message}")
            time.sleep(10)
        except Exception as e:
            print("YouTube API error:", e)
            time.sleep(30)

# =====================
# Kick Live Chat
# =====================
async def kick_chat():
    uri = "wss://ws-secure.chat.kick.com/socket.io/?EIO=4&transport=websocket"
    try:
        async with websockets.connect(uri) as ws:
            notify_connected("Kick")
            while True:
                msg = await ws.recv()
                print("Kick msg:", msg)
                # TODO: parse properly if needed
    except Exception as e:
        print("Kick connection error:", e)

# =====================
# Main Runner
# =====================
async def main():
    await asyncio.gather(
        youtube_chat(),
        kick_chat()
    )

if __name__ == "__main__":
    asyncio.run(main())
