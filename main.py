import os
import time
import asyncio
import logging
from collections import deque

from aiohttp import web
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
import google.generativeai as genai

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---- Config (all pulled from environment variables, set these on Render) ----
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]  # e.g. https://your-app.onrender.com
PORT = int(os.environ.get("PORT", 8443))

genai.configure(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = (
    "You are Parasitopia, a patient, knowledgeable tutor bot for a 200-level "
    "Parasitology and Entomology student preparing for exams (Helminthology, "
    "Vector Biology, Entomological Techniques, and related courses). Teach "
    "directly and comprehensively rather than quizzing unprompted. Proactively "
    "fill in gaps a student might not know to ask about. Keep formatting "
    "simple and mobile-friendly: short paragraphs, plain text, minimal "
    "markdown, since this is read on a phone inside Telegram.\n\n"
    "If asked who created, built, or made you, or who you belong to: answer "
    "that you were built by Samuel to help parasitology and entomology "
    "students in their studies and research. Do not mention Google, Gemini, "
    "or any underlying AI provider when asked this — just credit Samuel."
)

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash-lite",
    system_instruction=SYSTEM_PROMPT,
)

# In-memory conversation history per user. Resets if the bot restarts.
# For anything beyond casual personal use, swap this for a small database.
user_histories: dict[int, list] = {}

MAX_TELEGRAM_MESSAGE = 4000  # Telegram's real limit is 4096; leave headroom
MAX_HISTORY_TURNS = 20  # keep last N turns per user to bound memory/cost

# --- Rate limiting: protects the shared free Gemini quota across ALL users ---
GLOBAL_RPM_LIMIT = 10  # headroom under Google's free-tier ~15 RPM shared cap
USER_COOLDOWN_SECONDS = 4  # minimum gap between messages from the same person

_global_request_times: deque = deque()
_global_lock = asyncio.Lock()
_user_last_request: dict[int, float] = {}


async def check_rate_limits(user_id: int) -> str | None:
    """Returns a message to send the user if they should wait, else None."""
    now = time.monotonic()

    last = _user_last_request.get(user_id)
    if last is not None and (now - last) < USER_COOLDOWN_SECONDS:
        wait = USER_COOLDOWN_SECONDS - (now - last)
        return f"One sec — try again in {wait:.0f}s."

    async with _global_lock:
        now = time.monotonic()
        while _global_request_times and now - _global_request_times[0] > 60:
            _global_request_times.popleft()

        if len(_global_request_times) >= GLOBAL_RPM_LIMIT:
            return (
                "Lots of people are using the bot right now and we've hit the "
                "shared limit for this minute. Please try again shortly."
            )

        _global_request_times.append(now)

    _user_last_request[user_id] = now
    return None


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey! I'm your Parasitology/Entomology study bot.\n\n"
        "Commands:\n"
        "/ask <question> - ask me anything, tutoring style\n"
        "/quiz <topic> - get exam-style quiz questions on a topic\n"
        "/topic <topic> - get a full explanation of a topic\n"
        "/clear - reset our conversation history\n\n"
        "You can also just type a message directly, no command needed."
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_histories.pop(update.effective_user.id, None)
    await update.message.reply_text("Conversation cleared. Fresh start.")


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Usage: /ask <your question>")
        return
    await handle_ai_reply(update, question)


async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args) or "helminthology (Cestoda, Trematoda, Nematoda)"
    prompt = (
        f"Create 5 exam-style quiz questions (mix of short-answer and MCQ) on: "
        f"{topic}. Number them clearly. Do not include the answers yet — "
        f"I'll ask for them separately once I've attempted them."
    )
    await handle_ai_reply(update, prompt)


async def topic_deep_dive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args)
    if not topic:
        await update.message.reply_text("Usage: /topic <topic name>")
        return
    prompt = f"Give a comprehensive, exam-focused explanation of: {topic}"
    await handle_ai_reply(update, prompt)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_ai_reply(update, update.message.text)


async def handle_ai_reply(update: Update, user_text: str):
    user_id = update.effective_user.id

    limit_message = await check_rate_limits(user_id)
    if limit_message:
        await update.message.reply_text(limit_message)
        return

    history = user_histories.setdefault(user_id, [])

    await update.message.chat.send_action("typing")

    try:
        chat = model.start_chat(history=history)
        response = chat.send_message(user_text)
        reply_text = response.text
    except Exception:
        logger.exception("AI generation failed")
        reply_text = (
            "Sorry, I hit an error generating a response. Try again in a moment."
        )
        await update.message.reply_text(reply_text)
        return

    # Persist the turn
    history.append({"role": "user", "parts": [user_text]})
    history.append({"role": "model", "parts": [reply_text]})
    if len(history) > MAX_HISTORY_TURNS * 2:
        user_histories[user_id] = history[-MAX_HISTORY_TURNS * 2 :]

    # Telegram messages have a hard length limit, so split long replies
    for i in range(0, len(reply_text), MAX_TELEGRAM_MESSAGE):
        await update.message.reply_text(reply_text[i : i + MAX_TELEGRAM_MESSAGE])


async def health_check(request: web.Request) -> web.Response:
    # This is what UptimeRobot (or any pinger) should hit — always returns 200
    return web.Response(text="OK")


async def telegram_webhook(request: web.Request) -> web.Response:
    application: Application = request.app["bot_app"]
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return web.Response(text="OK")


async def run():
    application = Application.builder().token(TELEGRAM_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("clear", clear))
    application.add_handler(CommandHandler("ask", ask))
    application.add_handler(CommandHandler("quiz", quiz))
    application.add_handler(CommandHandler("topic", topic_deep_dive))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )

    await application.initialize()
    await application.bot.set_webhook(url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}")
    await application.start()

    web_app = web.Application()
    web_app["bot_app"] = application
    web_app.router.add_get("/", health_check)
    web_app.router.add_post(f"/{TELEGRAM_TOKEN}", telegram_webhook)

    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    logger.info("Bot is up. Health check on '/', webhook on '/<token>'.")
    await asyncio.Event().wait()  # run forever


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()    "that you were built by Samuel to help parasitology and entomology "
    "students in their studies and research. Do not mention Google, Gemini, "
    "or any underlying AI provider when asked this — just credit Samuel."
)

model = genai.GenerativeModel(
    model_name="gemini-flash-latest",
    system_instruction=SYSTEM_PROMPT,
)

# In-memory conversation history per user. Resets if the bot restarts.
# For anything beyond casual personal use, swap this for a small database.
user_histories: dict[int, list] = {}

MAX_TELEGRAM_MESSAGE = 4000  # Telegram's real limit is 4096; leave headroom
MAX_HISTORY_TURNS = 20  # keep last N turns per user to bound memory/cost


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey! I'm your Parasitology/Entomology study bot.\n\n"
        "Commands:\n"
        "/ask <question> - ask me anything, tutoring style\n"
        "/quiz <topic> - get exam-style quiz questions on a topic\n"
        "/topic <topic> - get a full explanation of a topic\n"
        "/clear - reset our conversation history\n\n"
        "You can also just type a message directly, no command needed."
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_histories.pop(update.effective_user.id, None)
    await update.message.reply_text("Conversation cleared. Fresh start.")


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Usage: /ask <your question>")
        return
    await handle_ai_reply(update, question)


async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args) or "helminthology (Cestoda, Trematoda, Nematoda)"
    prompt = (
        f"Create 5 exam-style quiz questions (mix of short-answer and MCQ) on: "
        f"{topic}. Number them clearly. Do not include the answers yet — "
        f"I'll ask for them separately once I've attempted them."
    )
    await handle_ai_reply(update, prompt)


async def topic_deep_dive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args)
    if not topic:
        await update.message.reply_text("Usage: /topic <topic name>")
        return
    prompt = f"Give a comprehensive, exam-focused explanation of: {topic}"
    await handle_ai_reply(update, prompt)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_ai_reply(update, update.message.text)


async def handle_ai_reply(update: Update, user_text: str):
    user_id = update.effective_user.id
    history = user_histories.setdefault(user_id, [])

    await update.message.chat.send_action("typing")

    try:
        chat = model.start_chat(history=history)
        response = chat.send_message(user_text)
        reply_text = response.text
    except Exception:
        logger.exception("AI generation failed")
        reply_text = (
            "Sorry, I hit an error generating a response. Try again in a moment."
        )
        await update.message.reply_text(reply_text)
        return

    # Persist the turn
    history.append({"role": "user", "parts": [user_text]})
    history.append({"role": "model", "parts": [reply_text]})
    if len(history) > MAX_HISTORY_TURNS * 2:
        user_histories[user_id] = history[-MAX_HISTORY_TURNS * 2 :]

    # Telegram messages have a hard length limit, so split long replies
    for i in range(0, len(reply_text), MAX_TELEGRAM_MESSAGE):
        await update.message.reply_text(reply_text[i : i + MAX_TELEGRAM_MESSAGE])


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("ask", ask))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("topic", topic_deep_dive))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}",
    )


if __name__ == "__main__":
    main()
model = genai.GenerativeModel(
    model_name="gemini-flash-latest",
    system_instruction=SYSTEM_PROMPT,
)

# In-memory conversation history per user. Resets if the bot restarts.
# For anything beyond casual personal use, swap this for a small database.
user_histories: dict[int, list] = {}

MAX_TELEGRAM_MESSAGE = 4000  # Telegram's real limit is 4096; leave headroom
MAX_HISTORY_TURNS = 20  # keep last N turns per user to bound memory/cost


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey! I'm your Parasitology/Entomology study bot.\n\n"
        "Commands:\n"
        "/ask <question> - ask me anything, tutoring style\n"
        "/quiz <topic> - get exam-style quiz questions on a topic\n"
        "/topic <topic> - get a full explanation of a topic\n"
        "/clear - reset our conversation history\n\n"
        "You can also just type a message directly, no command needed."
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_histories.pop(update.effective_user.id, None)
    await update.message.reply_text("Conversation cleared. Fresh start.")


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Usage: /ask <your question>")
        return
    await handle_ai_reply(update, question)


async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args) or "helminthology (Cestoda, Trematoda, Nematoda)"
    prompt = (
        f"Create 5 exam-style quiz questions (mix of short-answer and MCQ) on: "
        f"{topic}. Number them clearly. Do not include the answers yet — "
        f"I'll ask for them separately once I've attempted them."
    )
    await handle_ai_reply(update, prompt)


async def topic_deep_dive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args)
    if not topic:
        await update.message.reply_text("Usage: /topic <topic name>")
        return
    prompt = f"Give a comprehensive, exam-focused explanation of: {topic}"
    await handle_ai_reply(update, prompt)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_ai_reply(update, update.message.text)


async def handle_ai_reply(update: Update, user_text: str):
    user_id = update.effective_user.id
    history = user_histories.setdefault(user_id, [])

    await update.message.chat.send_action("typing")

    try:
        chat = model.start_chat(history=history)
        response = chat.send_message(user_text)
        reply_text = response.text
    except Exception:
        logger.exception("AI generation failed")
        reply_text = (
            "Sorry, I hit an error generating a response. Try again in a moment."
        )
        await update.message.reply_text(reply_text)
        return

    # Persist the turn
    history.append({"role": "user", "parts": [user_text]})
    history.append({"role": "model", "parts": [reply_text]})
    if len(history) > MAX_HISTORY_TURNS * 2:
        user_histories[user_id] = history[-MAX_HISTORY_TURNS * 2 :]

    # Telegram messages have a hard length limit, so split long replies
    for i in range(0, len(reply_text), MAX_TELEGRAM_MESSAGE):
        await update.message.reply_text(reply_text[i : i + MAX_TELEGRAM_MESSAGE])


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("ask", ask))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("topic", topic_deep_dive))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}",
    )


if __name__ == "__main__":
    main()
model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=SYSTEM_PROMPT,
)

# In-memory conversation history per user. Resets if the bot restarts.
# For anything beyond casual personal use, swap this for a small database.
user_histories: dict[int, list] = {}

MAX_TELEGRAM_MESSAGE = 4000  # Telegram's real limit is 4096; leave headroom
MAX_HISTORY_TURNS = 20  # keep last N turns per user to bound memory/cost


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey! I'm your Parasitology/Entomology study bot.\n\n"
        "Commands:\n"
        "/ask <question> - ask me anything, tutoring style\n"
        "/quiz <topic> - get exam-style quiz questions on a topic\n"
        "/topic <topic> - get a full explanation of a topic\n"
        "/clear - reset our conversation history\n\n"
        "You can also just type a message directly, no command needed."
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_histories.pop(update.effective_user.id, None)
    await update.message.reply_text("Conversation cleared. Fresh start.")


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Usage: /ask <your question>")
        return
    await handle_ai_reply(update, question)


async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args) or "helminthology (Cestoda, Trematoda, Nematoda)"
    prompt = (
        f"Create 5 exam-style quiz questions (mix of short-answer and MCQ) on: "
        f"{topic}. Number them clearly. Do not include the answers yet — "
        f"I'll ask for them separately once I've attempted them."
    )
    await handle_ai_reply(update, prompt)


async def topic_deep_dive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args)
    if not topic:
        await update.message.reply_text("Usage: /topic <topic name>")
        return
    prompt = f"Give a comprehensive, exam-focused explanation of: {topic}"
    await handle_ai_reply(update, prompt)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_ai_reply(update, update.message.text)


async def handle_ai_reply(update: Update, user_text: str):
    user_id = update.effective_user.id
    history = user_histories.setdefault(user_id, [])

    await update.message.chat.send_action("typing")

    try:
        chat = model.start_chat(history=history)
        response = chat.send_message(user_text)
        reply_text = response.text
    except Exception:
        logger.exception("AI generation failed")
        reply_text = (
            "Sorry, I hit an error generating a response. Try again in a moment."
        )
        await update.message.reply_text(reply_text)
        return

    # Persist the turn
    history.append({"role": "user", "parts": [user_text]})
    history.append({"role": "model", "parts": [reply_text]})
    if len(history) > MAX_HISTORY_TURNS * 2:
        user_histories[user_id] = history[-MAX_HISTORY_TURNS * 2 :]

    # Telegram messages have a hard length limit, so split long replies
    for i in range(0, len(reply_text), MAX_TELEGRAM_MESSAGE):
        await update.message.reply_text(reply_text[i : i + MAX_TELEGRAM_MESSAGE])


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("ask", ask))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("topic", topic_deep_dive))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}",
    )


if __name__ == "__main__":
    main()
model = genai.GenerativeModel(
    model_name="gemini-1.5-flash",
    system_instruction=SYSTEM_PROMPT,
)

# In-memory conversation history per user. Resets if the bot restarts.
# For anything beyond casual personal use, swap this for a small database.
user_histories: dict[int, list] = {}

MAX_TELEGRAM_MESSAGE = 4000  # Telegram's real limit is 4096; leave headroom
MAX_HISTORY_TURNS = 20  # keep last N turns per user to bound memory/cost


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hey! I'm your Parasitology/Entomology study bot.\n\n"
        "Commands:\n"
        "/ask <question> - ask me anything, tutoring style\n"
        "/quiz <topic> - get exam-style quiz questions on a topic\n"
        "/topic <topic> - get a full explanation of a topic\n"
        "/clear - reset our conversation history\n\n"
        "You can also just type a message directly, no command needed."
    )


async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_histories.pop(update.effective_user.id, None)
    await update.message.reply_text("Conversation cleared. Fresh start.")


async def ask(update: Update, context: ContextTypes.DEFAULT_TYPE):
    question = " ".join(context.args)
    if not question:
        await update.message.reply_text("Usage: /ask <your question>")
        return
    await handle_ai_reply(update, question)


async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args) or "helminthology (Cestoda, Trematoda, Nematoda)"
    prompt = (
        f"Create 5 exam-style quiz questions (mix of short-answer and MCQ) on: "
        f"{topic}. Number them clearly. Do not include the answers yet — "
        f"I'll ask for them separately once I've attempted them."
    )
    await handle_ai_reply(update, prompt)


async def topic_deep_dive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args)
    if not topic:
        await update.message.reply_text("Usage: /topic <topic name>")
        return
    prompt = f"Give a comprehensive, exam-focused explanation of: {topic}"
    await handle_ai_reply(update, prompt)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_ai_reply(update, update.message.text)


async def handle_ai_reply(update: Update, user_text: str):
    user_id = update.effective_user.id
    history = user_histories.setdefault(user_id, [])

    await update.message.chat.send_action("typing")

    try:
        chat = model.start_chat(history=history)
        response = chat.send_message(user_text)
        reply_text = response.text
    except Exception:
        logger.exception("AI generation failed")
        reply_text = (
            "Sorry, I hit an error generating a response. Try again in a moment."
        )
        await update.message.reply_text(reply_text)
        return

    # Persist the turn
    history.append({"role": "user", "parts": [user_text]})
    history.append({"role": "model", "parts": [reply_text]})
    if len(history) > MAX_HISTORY_TURNS * 2:
        user_histories[user_id] = history[-MAX_HISTORY_TURNS * 2 :]

    # Telegram messages have a hard length limit, so split long replies
    for i in range(0, len(reply_text), MAX_TELEGRAM_MESSAGE):
        await update.message.reply_text(reply_text[i : i + MAX_TELEGRAM_MESSAGE])


def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    app.add_handler(CommandHandler("ask", ask))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CommandHandler("topic", topic_deep_dive))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=TELEGRAM_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{TELEGRAM_TOKEN}",
    )


if __name__ == "__main__":
    main()
