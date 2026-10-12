"""
Event Layer.

The only place that subscribes to Telethon events: the outgoing-message
handler — non-command text gets font styling applied in place;
`PREFIX`-commands go to message_parser then command_router.
"""

from telethon import events

import command_router
import fontstyle
import message_parser
import state
from logger import log
from telegram_layer import client


def register_handlers():
    @client.on(events.NewMessage(outgoing=True))
    async def _outgoing_handler(event):
        text = event.raw_text
        if text is None:
            return

        parsed = message_parser.parse(text)
        if parsed is None:
            if state.paused:
                return
            if fontstyle.get_active():
                styled = fontstyle.apply(text)
                if styled != text:
                    try:
                        await event.edit(styled)
                    except Exception as e:
                        log.error(f"Font styling failed: {e}")
            return

        await command_router.route(event, parsed.cmd, parsed.arg, parsed.body)
