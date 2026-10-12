"""
Run this ONCE, locally (on your own PC, with the proxy working), to log in
and produce a session string. Paste the printed value into Render as the
SESSION_STRING environment variable. Do not run this on Render itself.
"""

from telethon.sync import TelegramClient
from telethon.sessions import StringSession

from config import API_ID, API_HASH, get_proxy

with TelegramClient(StringSession(), API_ID, API_HASH, proxy=get_proxy()) as client:
    print("\n=== Your SESSION_STRING (keep this secret!) ===\n")
    print(client.session.save())
    print("\n=== Copy the line above into Render's SESSION_STRING env var ===\n")
