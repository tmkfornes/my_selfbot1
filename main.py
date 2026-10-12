"""
Entrypoint. Wires the layers together and runs the client — no plugin
logic, no command dispatch lives here:

    Telegram Layer   -> telegram_layer.py   (client instance)
    Event Layer      -> event_layer.py      (Telethon listeners)
    Message Parser   -> message_parser.py   (text -> ParsedCommand)
    Command Router   -> command_router.py   (exact-match dispatch)
    Plugin Executor  -> plugins/            (the actual commands)

This is a plain exact-command bot — there is no AI/agent routing layer;
.ai (plugins/ai_chat.py) is just one command among the others, calling
OpenRouter directly for a conversational answer.
"""

import asyncio

import antidelete
import clock
import event_layer
import plugins  # noqa: F401 -- importing this registers every plugin
from logger import log
from telegram_layer import client

# وارد کردن تابع استارت ربات کنترل
from control_bot import start_control_bot

antidelete.register(client)
event_layer.register_handlers()


async def main():
    await client.start()
    me = await client.get_me()
    log.ok(f"Connected as: {me.first_name}")
    log.ok("SelfBot started")

    antidelete.set_my_id(me.id)

    # استارت هم‌زمان کنترل‌بات
    try:
        await start_control_bot()
    except Exception as e:
        log.error(f"Failed to start Control Bot: {e}")

    await clock.on_selfbot_start(client)

    try:
        await client.run_until_disconnected()
    finally:
        await clock.on_selfbot_stop(client)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Stopped by user")