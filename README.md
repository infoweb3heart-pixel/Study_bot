# Parasitology/Entomology Study Bot (Telegram + Gemini AI)

A Telegram bot that tutors you on your PAE coursework — direct teaching,
quizzes, and topic deep-dives, right inside Telegram.

## Step 1 — Create the Telegram bot (2 minutes)

1. Open Telegram, search for **@BotFather**, start a chat.
2. Send `/newbot`, follow the prompts (pick a name and a username ending in `bot`).
3. BotFather gives you a **token** like `123456789:AAExampleTokenHere`. Save it.

## Step 2 — Get a free Gemini API key (2 minutes)

1. Go to https://aistudio.google.com/apikey (sign in with any Google account).
2. Click **Create API key**. Copy it.
3. This is free — Google's free tier is generous enough for personal study use
   and doesn't require a credit card.

## Step 3 — Deploy on Render (free, no credit card)

1. Go to https://render.com and sign up (GitHub login is easiest).
2. Push this `study_bot` folder to a new GitHub repo (or use Render's
   "Deploy from a folder" option if offered).
3. On Render: **New +** → **Web Service** → connect your repo.
4. Settings:
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python main.py`
   - **Instance Type**: Free
5. Add Environment Variables (Render dashboard → Environment):
   - `TELEGRAM_BOT_TOKEN` = the token from Step 1
   - `GEMINI_API_KEY` = the key from Step 2
   - `WEBHOOK_URL` = your Render URL once it's assigned, e.g.
     `https://study-bot-xxxx.onrender.com` (no trailing slash)
6. Deploy. Render will build and start the service.

Note: on Render's free tier, the service can "sleep" after ~15 minutes of no
traffic and takes a few seconds to wake on the next message — totally fine
for a personal study bot, just don't expect instant replies if you haven't
messaged it in a while.

## Step 4 — Talk to your bot

Open Telegram, find your bot by its username, hit Start, and try:

- `/quiz Cestoda` — generates 5 exam-style questions
- `/topic Schistosoma haematobium lifecycle` — full explanation
- `/ask why does praziquantel dosing differ for S. japonicum` — direct Q&A
- Or just type any question with no command at all

## Notes on limitations

- Conversation memory is in-process (a Python dictionary), so it resets if
  the free-tier service restarts or redeploys. Fine for study sessions;
  not built for long-term history.
- This is a single-user-friendly design but technically anyone who finds your
  bot's username can talk to it and use your Gemini quota. If you want to
  lock it to just yourself, tell me and I'll add a Telegram user-ID allowlist.
