import os
import threading
import telebot
from telebot import types
from google import genai
from flask import Flask

# ─── কনফিগারেশন ───
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_KEY_1 = os.environ.get("GEMINI_KEY_1")
GEMINI_KEY_2 = os.environ.get("GEMINI_KEY_2")

if not BOT_TOKEN or not GEMINI_KEY_1 or not GEMINI_KEY_2:
    raise ValueError("BOT_TOKEN, GEMINI_KEY_1 এবং GEMINI_KEY_2 environment variable সেট করতে হবে")

clients = [
    genai.Client(api_key=GEMINI_KEY_1),
    genai.Client(api_key=GEMINI_KEY_2),
]
current_client_index = 0
MODEL = "gemini-3.6-flash"

bot = telebot.TeleBot(BOT_TOKEN)
user_data = {}


# ─── API call with fallback ───
def call_gemini(prompt):
    global current_client_index
    for i in range(len(clients)):
        idx = (current_client_index + i) % len(clients)
        try:
            interaction = clients[idx].interactions.create(model=MODEL, input=prompt)
            current_client_index = idx
            return interaction.output_text.strip()
        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower() or "rate" in err.lower():
                continue
            else:
                raise e
    raise Exception("RATE_LIMIT")


# ─── Keyboard ───
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("🔤 Translator"), types.KeyboardButton("🌍 Language"))
    return markup


# ─── /start ───
@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(
        message.chat.id,
        "🤖 *স্বাগতম AI Translator Bot-এ!*\n\n"
        "এই বটটি দিয়ে তুমি:\n\n"
        "🔤 *Translator* — যেকোনো লেখা যেকোনো ভাষায় অনুবাদ করতে পারবে। লেখাটা পাঠাও, তারপর কোন ভাষায় চাও বলো — ব্যস!\n\n"
        "🌍 *Language* — যেকোনো লেখা পাঠালে বলে দেবে এটা কোন ভাষায় লেখা। সেই ভাষার নিজস্ব লিপিতে এবং বাংলায় দুটোই জানাবে।\n\n"
        "⚡ *Powered by Google Gemini AI*\n\n"
        "নিচের বাটন থেকে শুরু করো 👇",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


# ─── Translator ───
@bot.message_handler(func=lambda m: m.text == "🔤 Translator")
def translator_start(message):
    msg = bot.send_message(message.chat.id, "✍️ যে লেখাটা অনুবাদ করতে চাও সেটা পাঠাও:")
    bot.register_next_step_handler(msg, translator_get_text)


def translator_get_text(message):
    chat_id = message.chat.id
    user_data[chat_id] = {"original_text": message.text, "lang_attempts": 0}
    msg = bot.send_message(
        chat_id,
        "🌐 কোন ভাষায় অনুবাদ করতে চাও?\n\n_(যেমন: বাংলা, English, French, Arabic, Hindi)_",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, translator_get_target_lang)


def translator_get_target_lang(message):
    chat_id = message.chat.id
    target_lang = message.text
    original_text = user_data.get(chat_id, {}).get("original_text", "")
    attempts = user_data.get(chat_id, {}).get("lang_attempts", 0)

    if not original_text:
        bot.send_message(chat_id, "⚠️ কিছু ভুল হয়েছে, আবার /start থেকে শুরু করো।", reply_markup=main_keyboard())
        return

    bot.send_chat_action(chat_id, "typing")

    prompt = (
        f"First check if \"{target_lang}\" is a valid real human language name. "
        f"If it is NOT a valid language name, reply with exactly one word: INVALID_LANGUAGE "
        f"If it IS a valid language, translate the following text into that language. "
        f"Only output the translated text, nothing else, no explanation, no quotes, no labels.\n\n"
        f"Text to translate: {original_text}"
    )

    try:
        translated = call_gemini(prompt)

        if "INVALID_LANGUAGE" in translated:
            attempts += 1
            user_data[chat_id]["lang_attempts"] = attempts

            if attempts >= 2:
                user_data.pop(chat_id, None)
                bot.send_message(
                    chat_id,
                    "❌ ২ বার ভুল হয়েছে! মেইন মেনুতে ফিরে যাও।",
                    reply_markup=main_keyboard()
                )
                return

            msg = bot.send_message(
                chat_id,
                f"⚠️ এটি কোনো ভাষার নাম নয়! আরও ১ বার চেষ্টা করো।\n\n"
                "সঠিক ভাষার নাম লেখো, যেমন:\n"
                "বাংলা, English, French, Arabic, Hindi...",
            )
            bot.register_next_step_handler(msg, translator_get_target_lang)
            return

        escaped = translated.replace("`", "'")
        bot.send_message(chat_id, f"`{escaped}`", parse_mode="Markdown", reply_markup=main_keyboard())

    except Exception as e:
        if "RATE_LIMIT" in str(e):
            bot.send_message(
                chat_id,
                "⏳ এই মুহূর্তে একটু ব্যস্ত আছি!\n\n"
                "দয়া করে মাত্র ১ মিনিট পরে আবার চেষ্টা করুন 🙏",
                reply_markup=main_keyboard()
            )
        else:
            bot.send_message(chat_id, f"⚠️ ত্রুটি হয়েছে: {e}", reply_markup=main_keyboard())

    user_data.pop(chat_id, None)


# ─── Language ───
@bot.message_handler(func=lambda m: m.text == "🌍 Language")
def language_start(message):
    msg = bot.send_message(message.chat.id, "✍️ যে লেখাটার ভাষা জানতে চাও সেটা পাঠাও:")
    bot.register_next_step_handler(msg, language_detect)


def language_detect(message):
    chat_id = message.chat.id
    text = message.text

    bot.send_chat_action(chat_id, "typing")

    prompt = (
        "তুমি একজন ভাষা শনাক্তকারী এক্সপার্ট। নিচের টেক্সটটি কোন ভাষায় লেখা তা বলো।\n"
        "উত্তর দাও ঠিক এই ফরম্যাটে (দুই লাইনে):\n"
        "প্রথম লাইন: সেই ভাষার নিজস্ব লিপিতে ভাষার নাম (যেমন: English, العربية, हिन्दी, Français)\n"
        "দ্বিতীয় লাইন: বাংলায় ভাষার নাম (যেমন: ইংরেজি, আরবি, হিন্দি, ফরাসি)\n"
        "শুধু এই দুটো লাইন, কোনো বাড়তি কথা নয়।\n\n"
        f"টেক্সট: {text}"
    )

    try:
        result = call_gemini(prompt)
        lines = result.strip().split("\n")
        if len(lines) >= 2:
            native = lines[0].strip()
            bangla = lines[1].strip()
            bot.send_message(
                chat_id,
                f"🔎 *{native}* / {bangla}",
                parse_mode="Markdown",
                reply_markup=main_keyboard()
            )
        else:
            bot.send_message(chat_id, f"🔎 {result}", reply_markup=main_keyboard())

    except Exception as e:
        if "RATE_LIMIT" in str(e):
            bot.send_message(
                chat_id,
                "⏳ এই মুহূর্তে একটু ব্যস্ত আছি!\n\n"
                "দয়া করে মাত্র ১ মিনিট পরে আবার চেষ্টা করুন 🙏",
                reply_markup=main_keyboard()
            )
        else:
            bot.send_message(chat_id, f"⚠️ ত্রুটি হয়েছে: {e}", reply_markup=main_keyboard())


# ─── Fallback ───
@bot.message_handler(func=lambda m: True)
def fallback(message):
    bot.send_message(
        message.chat.id,
        "👇 নিচের বাটন থেকে অপশন বাছাই করো অথবা /start দাও।",
        reply_markup=main_keyboard()
    )


# ─── Flask ───
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    threading.Thread(target=run_flask).start()
    print("Bot polling শুরু হয়েছে...")
    bot.infinity_polling()
