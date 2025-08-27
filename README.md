# Multi-Platform Live Chat Notifier

This script connects to **YouTube Live, Facebook Live, and Kick chat** in real-time.  
It sends chat messages to [ntfy.sh](https://ntfy.sh) so you can get **push notifications** on your phone.

## Features
- Auto-detect YouTube live video + get live chat
- Auto-refresh long-lived Facebook Page tokens
- Kick chat via WebSocket
- Pushes messages to `ntfy.sh`
- Supports `!start` and `!stop` commands from chat
- 5 second delay between notifications to avoid spam

## Deployment
1. Fork this repo
2. Set environment variables in **Railway**:
   - `YOUTUBE_API_KEY`
   - `YOUTUBE_CHANNEL_ID`
   - `FACEBOOK_APP_ID`
   - `FACEBOOK_APP_SECRET`
   - `FACEBOOK_PAGE_ID`
   - `FACEBOOK_SHORT_TOKEN`
   - `KICK_USERNAME`
   - `KICK_CHANNEL`
   - `NTFY_TOPIC`
3. Deploy to Railway 🚀
