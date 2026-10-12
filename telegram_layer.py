"""
Telegram Layer — the bottom of the stack.

Owns the one TelegramClient instance for the whole process. Every other
layer (event handlers, plugins, dispatcher) imports `client` from here
instead of constructing its own — there is exactly one client in the
process, and this module is where it's built.
"""

from telethon import TelegramClient
from telethon.sessions import StringSession

from config import API_ID, API_HASH, SESSION_NAME, SESSION_STRING, get_proxy

session = StringSession(SESSION_STRING) if SESSION_STRING else SESSION_NAME
client = TelegramClient(session, API_ID, API_HASH, proxy=get_proxy())
