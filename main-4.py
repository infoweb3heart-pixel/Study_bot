import os
import time
import json
import asyncio
import logging
from collections import deque

import requests
from aiohttp import web
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import google.generativeai as genai

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---- Config ----
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
PORT = int(os.environ.get("PORT", 8443))
ADMIN_USER_ID = int(os.environ.get("ADMIN_USER_ID", 0))

genai.configure(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = (
    "You are Parasitopia, a patient, knowledgeable tutor bot for a 200-level "
    "Parasitology and Entomology student preparing for exams. This covers the "
    "full breadth of the department's courses — Helminthology, Protozoology, "
    "Medical Entomology, Vector Biology, Entomological Techniques, Computational "
    "Biology and Biophysics, and related courses. Teach directly and comprehensively "
    "rather than quizzing unprompted. Proactively fill in gaps a student might not "
    "know to ask about. Keep formatting simple and mobile-friendly: short paragraphs, "
    "plain text, minimal markdown, since this is read on a phone inside Telegram.\n\n"
    "Communication style: be warm and encouraging, but direct — lead with the actual "
    "answer, not a preamble or restated question. Use short, plain sentences over long, "
    "jargon-stacked ones. Give concrete examples or comparisons where they help "
    "understanding. If a student's premise or approach seems off (e.g. they've confused "
    "two species, or are studying something in a way that won't help them on the exam), "
    "say so honestly and kindly rather than just going along with it — a good tutor "
    "corrects gently, not evasively. Avoid hedging everything with 'it depends' when "
    "you can just give the clearest, most useful answer. Treat the student as a capable "
    "adult who wants real information, not a simplified or overly cautious version of it.\n\n"
    "More specific style patterns to follow:\n"
    "- Never open with filler like 'Great question!' or 'I'd be happy to help with "
    "that' — just answer.\n"
    "- For longer explanations, use short bolded headers or a brief list to break up "
    "sections rather than one dense wall of text.\n"
    "- When comparing two things (e.g. two species, two techniques), a short "
    "side-by-side breakdown is clearer than describing each in isolation.\n"
    "- It's fine to have a point of view. If one study method or way of understanding "
    "a concept is genuinely better, say so plainly instead of presenting all options "
    "as equally valid.\n"
    "- Match response length to the question — a quick factual question gets a short "
    "answer; a request to understand a whole lifecycle or mechanism earns a fuller, "
    "structured one. Don't pad short answers to seem thorough, and don't truncate "
    "genuinely complex topics.\n"
    "- Avoid corporate hedge-phrases: 'it's important to note that', 'as an AI, I...', "
    "'I hope this helps!', 'please let me know if you have any further questions'. "
    "End naturally instead, often with what to look at next rather than a generic "
    "closing line.\n"
    "- Use a normal, conversational register — contractions are fine, dry humor is "
    "fine, but don't force jokes or forced enthusiasm.\n\n"
    "If asked who created, built, or made you, or who you belong to: answer that you "
    "were built by Samuel to help parasitology and entomology students in their studies "
    "and research. Do not mention Google, Gemini, or any underlying AI provider when "
    "asked this — just credit Samuel."
)

model = genai.GenerativeModel(
    model_name="gemini-3.1-flash-lite",
    system_instruction=SYSTEM_PROMPT,
)

user_histories: dict[int, list] = {}
known_user_ids: set[int] = set()

MAX_TELEGRAM_MESSAGE = 4000
MAX_HISTORY_TURNS = 20
GLOBAL_RPM_LIMIT = 10
USER_COOLDOWN_SECONDS = 4

_global_request_times: deque = deque()
_global_lock = asyncio.Lock()
_user_last_request: dict[int, float] = {}


async def reply_formatted(update: Update, text: str):
    """Send formatted reply using Telegram MarkdownV2 directly."""
    for i in range(0, len(text), MAX_TELEGRAM_MESSAGE):
        chunk = text[i : i + MAX_TELEGRAM_MESSAGE]
        try:
            await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception:
            logger.exception("MarkdownV2 send failed, retrying as plain text")
            await update.message.reply_text(chunk)


async def record_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Track all users who interact with the bot."""
    if update.effective_user:
        known_user_ids.add(update.effective_user.id)


async def check_rate_limits(user_id: int) -> str | None:
    """Check rate limits."""
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
        "Hey! I'm your Parasitology/Entomology study bot — covering "
        "Helminthology, Protozoology, Medical Entomology, Vector Biology, "
        "Entomological Techniques, and more.\n\n"
        "Commands:\n"
        "/ask <question> - ask me anything, tutoring style\n"
        "/quiz <topic> [count] - e.g. /quiz Cestoda 10\n"
        "/topic <topic> - get a full explanation of a topic\n"
        "/flashcard <topic> [count] - e.g. /flashcard Vector Biology 12\n"
        "/clear - reset our conversation history\n\n"
        "You can also send a voice note — I'll transcribe and answer. "
        "Or send a photo of a diagram to explain it."
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


def parse_count_and_topic(args: list, default_count: int) -> tuple:
    """Parse optional count + topic from command args."""
    count = default_count
    remaining = []
    used_count = False

    for token in args:
        if not used_count and token.isdigit():
            count = max(1, min(int(token), 20))
            used_count = True
        else:
            remaining.append(token)

    topic = " ".join(remaining).strip()
    if not topic:
        topic = (
            "a high-yield, exam-relevant topic of your choice from the "
            "Parasitology and Entomology curriculum"
        )
    return count, topic


async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    count, topic = parse_count_and_topic(context.args, default_count=5)
    prompt = (
        f"Create {count} exam-style quiz questions (mix of short-answer and MCQ) on: "
        f"{topic}. Number them clearly. Do not include answers yet."
    )
    await handle_ai_reply(update, prompt)


async def topic_deep_dive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    topic = " ".join(context.args)
    if not topic:
        await update.message.reply_text("Usage: /topic <topic name>")
        return
    prompt = f"Give a comprehensive, exam-focused explanation of: {topic}"
    await handle_ai_reply(update, prompt)


async def flashcard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    limit_message = await check_rate_limits(user_id)
    if limit_message:
        await update.message.reply_text(limit_message)
        return

    count, topic = parse_count_and_topic(context.args, default_count=8)
    prompt = (
        f"Create exactly {count} exam-focused flashcards on: {topic}. "
        "Respond with ONLY a JSON array of objects, each with 'front' and 'back' keys."
    )

    await update.message.chat.send_action("typing")

    try:
        response = model.generate_content(prompt)
        cards = parse_flashcard_json(response.text)
    except Exception:
        logger.exception("Flashcard generation failed")
        cards = None

    if not cards:
        await update.message.reply_text("Couldn't generate flashcards. Try again.")
        return

    _flashcard_sessions[user_id] = {"cards": cards, "index": 0}
    text = format_flashcard_text(cards[0], 0, len(cards), revealed=False)
    keyboard = flashcard_keyboard(0, len(cards), revealed=False)
    await reply_formatted(update, text)


_flashcard_sessions: dict[int, dict] = {}


def parse_flashcard_json(raw_text: str) -> list[dict] | None:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        cards = json.loads(text[start : end + 1])
        cleaned = [
            {"front": c["front"], "back": c["back"]}
            for c in cards
            if isinstance(c, dict) and "front" in c and "back" in c
        ]
        return cleaned or None
    except Exception:
        return None


def flashcard_keyboard(index: int, total: int, revealed: bool) -> InlineKeyboardMarkup:
    if not revealed:
        buttons = [[InlineKeyboardButton("Reveal Answer", callback_data="fc:reveal")]]
    else:
        is_last = index >= total - 1
        label = "Finish" if is_last else "Next Card"
        buttons = [[InlineKeyboardButton(label, callback_data="fc:next")]]
    return InlineKeyboardMarkup(buttons)


def format_flashcard_text(card: dict, index: int, total: int, revealed: bool) -> str:
    header = f"Card {index + 1}/{total}\n\n"
    body = f"Q: {card['front']}"
    if revealed:
        body += f"\n\nA: {card['back']}"
    return header + body


async def flashcard_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    session = _flashcard_sessions.get(user_id)

    if not session:
        await query.answer("No active flashcard session.")
        return

    await query.answer()
    cards = session["cards"]
    index = session["index"]
    action = query.data.split(":", 1)[1]

    if action == "reveal":
        text = format_flashcard_text(cards[index], index, len(cards), revealed=True)
        keyboard = flashcard_keyboard(index, len(cards), revealed=True)
        await query.edit_message_text(text, reply_markup=keyboard)

    elif action == "next":
        index += 1
        if index >= len(cards):
            _flashcard_sessions.pop(user_id, None)
            await query.edit_message_text("Flashcard session complete!")
            return

        session["index"] = index
        text = format_flashcard_text(cards[index], index, len(cards), revealed=False)
        keyboard = flashcard_keyboard(index, len(cards), revealed=False)
        await query.edit_message_text(text, reply_markup=keyboard)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_ai_reply(update, update.message.text)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    limit_message = await check_rate_limits(user_id)
    if limit_message:
        await update.message.reply_text(limit_message)
        return

    photo = update.message.photo[-1]
    tg_file = await photo.get_file()
    image_bytes = await tg_file.download_as_bytearray()

    caption = update.message.caption or (
        "Explain what's shown in this image, exam-focused."
    )

    username = update.effective_user.username or update.effective_user.first_name
    logger.info("PHOTO from %s (id=%s)", username, user_id)

    await update.message.chat.send_action("typing")

    try:
        response = model.generate_content(
            [caption, {"mime_type": "image/jpeg", "data": bytes(image_bytes)}]
        )
        reply_text = response.text
    except Exception:
        logger.exception("Image analysis failed")
        await update.message.reply_text("Error analyzing image. Try again.")
        return

    await reply_formatted(update, reply_text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    limit_message = await check_rate_limits(user_id)
    if limit_message:
        await update.message.reply_text(limit_message)
        return

    voice = update.message.voice
    tg_file = await voice.get_file()
    audio_bytes = await tg_file.download_as_bytearray()

    username = update.effective_user.username or update.effective_user.first_name
    logger.info("VOICE from %s (id=%s)", username, user_id)

    await update.message.chat.send_action("typing")

    try:
        response = model.generate_content(
            [
                "Transcribe this voice note and answer the question or respond to what was said.",
                {"mime_type": "audio/ogg", "data": bytes(audio_bytes)},
            ]
        )
        reply_text = response.text
    except Exception:
        logger.exception("Voice transcription failed")
        await update.message.reply_text("Error processing voice note. Try again.")
        return

    await reply_formatted(update, reply_text)


async def handle_ai_reply(update: Update, user_text: str):
    user_id = update.effective_user.id
    username = update.effective_user.username or update.effective_user.first_name

    limit_message = await check_rate_limits(user_id)
    if limit_message:
        await update.message.reply_text(limit_message)
        return

    logger.info("MESSAGE from %s (id=%s): %s", username, user_id, user_text)

    history = user_histories.setdefault(user_id, [])
    await update.message.chat.send_action("typing")

    try:
        chat = model.start_chat(history=history)
        response = chat.send_message(user_text)
        reply_text = response.text
    except Exception:
        logger.exception("AI generation failed")
        await update.message.reply_text("Error generating response. Try again.")
        return

    history.append({"role": "user", "parts": [user_text]})
    history.append({"role": "model", "parts": [reply_text]})
    if len(history) > MAX_HISTORY_TURNS * 2:
        user_histories[user_id] = history[-MAX_HISTORY_TURNS * 2 :]

    await reply_formatted(update, reply_text)


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin-only command to send message to all users."""
    if update.effective_user.id != ADMIN_USER_ID:
        return

    message = " ".join(context.args)
    if not message:
        await update.message.reply_text("Usage: /broadcast <message>")
        return

    sent, failed = 0, 0

    for user_id in list(known_user_ids):
        try:
            await context.bot.send_message(
                chat_id=user_id, text=message, parse_mode=ParseMode.MARKDOWN_V2
            )
            sent += 1
        except Exception:
            logger.exception("Broadcast failed for user_id=%s", user_id)
            failed += 1
        await asyncio.sleep(0.05)

    await update.message.reply_text(f"Broadcast sent to {sent} users ({failed} failed).")


async def health_check(request: web.Request) -> web.Response:
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
    application.add_handler(CommandHandler("flashcard", flashcard))
    application.add_handler(CommandHandler("broadcast", broadcast))
    application.add_handler(CallbackQueryHandler(flashcard_button, pattern=r"^fc:"))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message)
    )
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    application.add_handler(MessageHandler(filters.VOICE, handle_voice))
    application.add_handler(MessageHandler(filters.ALL, record_user), group=1)

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

    logger.info("Bot is up.")
    await asyncio.Event().wait()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
