import os
import time
import asyncio
import requests
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ==============================
# Environment Variables (Railway)
# ==============================
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
YOUTUBE_CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")
YOUTUBE_VIDEO_ID = os.getenv("YOUTUBE_VIDEO_ID", None)

FACEBOOK_APP_ID = os.getenv("FACEBOOK_APP_ID")
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET")
FACEBOOK_PAGE_ID = os.getenv("FACEBOOK_PAGE_ID")
FACEBOOK_USER_TOKEN = os.getenv("FACEBOOK_USER_TOKEN")

KICK_CHANNEL = os.getenv("KICK_CHANNEL")

NTFY_TOPIC = os.getenv("NTFY_TOPIC", "chat-notifier")
NTFY_CONTROL_TOPIC = os.getenv("NTFY_CONTROL_TOPIC", "chat-control")

# ==============================
# Helpers
# ==============================
def send_ntfy(msg: str):
    try:
        requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode("utf-8"))
    except Exception as e:
        print("❌ NTFY error:", e)

def notify_connected():
    send_ntfy("✅ Chat notifier connected successfully!")

# ==============================
# YouTube
# ==============================
def get_youtube_chat_id():
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

    if YOUTUBE_VIDEO_ID:
        # Directly get liveChatId (saves quota)
        request = youtube.videos().list(part="liveStreamingDetails", id=YOUTUBE_VIDEO_ID)
    else:
        # Fallback: search live stream
        request = youtube.search().list(
            part="id", channelId=YOUTUBE_CHANNEL_ID,
            eventType="live", type="video"
        )
    response = request.execute()

    if "items" not in response or not response["items"]:
        return None

    if YOUTUBE_VIDEO_ID:
        return response["items"][0]["liveStreamingDetails"]["activeLiveChatId"]
    else:
        vid_id = response["items"][0]["id"]["videoId"]
        request = youtube.videos().list(part="liveStreamingDetails", id=vid_id)
        response = request.execute()
        return response["items"][0]["liveStreamingDetails"]["activeLiveChatId"]

async def youtube_chat():
    try:
        chat_id = get_youtube_chat_id()
        if not chat_id:
            print("❌ No active YouTube live found.")
            return

        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
        next_page = None

        while True:
            try:
                request = youtube.liveChatMessages().list(
                    liveChatId=chat_id,
                    part="snippet,authorDetails",
                    pageToken=next_page
                )
                response = request.execute()

                for msg in response.get("items", []):
                    author = msg["authorDetails"]["displayName"]
                    text = msg["snippet"]["displayMessage"]
                    send_ntfy(f"[YouTube] {author}: {text}")

                next_page = response.get("nextPageToken")
                await asyncio.sleep(5)

            except HttpError as e:
                print("YouTube API error:", e)
                await asyncio.sleep(15)
    except Exception as e:
        print("YouTube error:", e)

# ==============================
# Facebook
# ==============================
def get_long_lived_user_token():
    url = (
        f"https://graph.facebook.com/oauth/access_token?"
        f"grant_type=fb_exchange_token&client_id={FACEBOOK_APP_ID}"
        f"&client_secret={FACEBOOK_APP_SECRET}&fb_exchange_token={FACEBOOK_USER_TOKEN}"
    )
    for _ in range(3):  # retry 3 times
        resp = requests.get(url).json()
        if "access_token" in resp:
            return resp["access_token"]
        print("Retrying Facebook token refresh...", resp)
        time.sleep(5)
    return None

def get_page_token(user_token):
    url = f"https://graph.facebook.com/{FACEBOOK_PAGE_ID}?fields=access_token&access_token={user_token}"
    resp = requests.get(url).json()
    return resp.get("access_token")

def get_live_video_id(page_token):
    url = f"https://graph.facebook.com/{FACEBOOK_PAGE_ID}/live_videos?status=LIVE&access_token={page_token}"
    resp = requests.get(url).json()
    if "data" in resp and resp["data"]:
        return resp["data"][0]["id"]
    return None

async def facebook_chat():
    try:
        user_token = get_long_lived_user_token()
        if not user_token:
            print("❌ Failed to refresh Facebook token.")
            return

        page_token = get_page_token(user_token)
        if not page_token:
            print("❌ Failed to get Facebook page token.")
            return

        video_id = get_live_video_id(page_token)
        if not video_id:
            print("❌ No active Facebook live found.")
            return

        url = f"https://graph.facebook.com/{video_id}/comments?access_token={page_token}"
        last_seen = set()

        while True:
            resp = requests.get(url).json()
            for comment in resp.get("data", []):
                if comment["id"] not in last_seen:
                    last_seen.add(comment["id"])
                    author = comment.get("from", {}).get("name", "Unknown")
                    text = comment.get("message", "")
                    send_ntfy(f"[Facebook] {author}: {text}")
            await asyncio.sleep(5)

    except Exception as e:
        print("Facebook error:", e)

# ==============================
# Kick (via API polling, not WebSocket)
# ==============================
async def kick_chat():
    if not KICK_CHANNEL:
        return
    url = f"https://kick.com/api/v2/channels/{KICK_CHANNEL}/chat/messages"
    last_seen = set()
    while True:
        try:
            resp = requests.get(url).json()
            for msg in resp.get("data", []):
                if msg["id"] not in last_seen:
                    last_seen.add(msg["id"])
                    author = msg["sender"]["username"]
                    text = msg["content"]
                    send_ntfy(f"[Kick] {author}: {text}")
            await asyncio.sleep(5)
        except Exception as e:
            print("Kick error:", e)
            await asyncio.sleep(10)

# ==============================
# Main
# ==============================
async def main():
    notify_connected()
    await asyncio.gather(
        youtube_chat(),
        facebook_chat(),
        kick_chat(),
    )

if __name__ == "__main__":
    asyncio.run(main())
