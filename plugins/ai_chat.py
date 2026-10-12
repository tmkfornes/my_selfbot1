"""The `.ai` plugin — direct conversational access to the AI layer."""

import ai_agent
from config import PREFIX


async def cmd_ai(event, question: str):
    if not ai_agent.has_api_key():
        await event.edit(
            "این قابلیت فعال نیست — تو `.env` مقدار `OPENROUTER_API_KEY` رو ست کن "
            "(رایگانه: https://openrouter.ai/keys)"
        )
        return
    if not question:
        await event.edit(f"استفاده: `{PREFIX}ai <سوال یا درخواستت>`")
        return
    await event.edit("🤖 …")
    try:
        answer = await ai_agent.chat(question)
        await event.edit(f"🤖 {answer}"[:4090])
    except Exception as e:
        await event.edit(f"⚠️ {ai_agent.format_error(e)}")
