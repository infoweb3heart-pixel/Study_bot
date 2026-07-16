import os
import logging

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
    "You are a patient, knowledgeable tutor for a 200-level Parasitology and "
    "Entomology student preparing for exams (Helminthology, Vector Biology, "
    "Entomological Techniques, and related courses). Teach directly and "
    "comprehensively rather than quizzing unprompted. Proactively fill in gaps "
    "a student might not know to ask about. Keep formatting simple and "
    "mobile-friendly: short paragraphs, plain text, minimal markdown, since "
    "this is read on a phone inside Telegram."
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
