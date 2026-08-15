import os
import threading
import telebot
from telebot import types
from google import genai
from flask import Flask

BOT_TOKEN = os.environ.get("BOT_TOKEN")
GEMINI_KEY = os.environ.get("GEMINI_KEY")

if not BOT_TOKEN or not GEMINI_KEY:
    raise ValueError("BOT_TOKEN এবং GEMINI_KEY environment variable হিসেবে সেট করতে হবে")

client = genai.Client(api_key=GEMINI_KEY)
MODEL = "gemini-2.5-flash-lite"

bot = telebot.TeleBot(BOT_TOKEN)
user_data = {}

def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(types.KeyboardButton("Translator"), types.KeyboardButton("Language"))
    return markup

@bot.message_handler(commands=["start"])
def start(message):
    bot.send_message(
        message.chat.id,
        "👋 স্বাগতম!\n\n🔤 Translator — যেকোনো ভাষায় অনুবাদ করতে\n🌍 Language — কোন ভাষায় লেখা তা জানতে\n\nনিচ থেকে একটা অপশন বাছাই করো।",
        reply_markup=main_keyboard()
    )

@bot.message_handler(func=lambda m: m.text == "Translator")
def translator_start(message):
    msg = bot.send_message(message.chat.id, "✍️ যে লেখাটা অনুবাদ করতে চাও সেটা পাঠাও:")
    bot.register_next_step_handler(msg, translator_get_text)

def translator_get_text(message):
    chat_id = message.chat.id
    user_data[chat_id] = {"original_text": message.text}
    msg = bot.send_message(chat_id, "🌐 কোন ভাষায় অনুবাদ করতে চাও? (যেমন: বাংলা, English, French)")
    bot.register_next_step_handler(msg, translator_get_target_lang)

def translator_get_target_lang(message):
    chat_id = message.chat.id
    target_lang = message.text
    original_text = user_data.get(chat_id, {}).get("original_text", "")

    if not original_text:
        bot.send_message(chat_id, "কিছু ভুল হয়েছে, আবার /start থেকে শুরু করো।", reply_markup=main_keyboard())
        return

    bot.send_chat_action(chat_id, "typing")

    prompt = (
        f"Translate the following text into the language: \"{target_lang}\". "
        f"Only output the translated text, nothing else, no explanation, no quotes.\n\n"
        f"Text to translate: {original_text}"
    )

    try:
        interaction = client.interactions.create(model=MODEL, input=prompt)
        translated = interaction.output_text.strip()
        bot.send_message(chat_id, f"✅ অনুবাদ:\n\n{translated}", reply_markup=main_keyboard())
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ ত্রুটি হয়েছে: {e}", reply_markup=main_keyboard())

    user_data.pop(chat_id, None)

@bot.message_handler(func=lambda m: m.text == "Language")
def language_start(message):
    msg = bot.send_message(message.chat.id, "✍️ যে লেখাটার ভাষা জানতে চাও সেটা পাঠাও:")
    bot.register_next_step_handler(msg, language_detect)

def language_detect(message):
    chat_id = message.chat.id
    text = message.text

    bot.send_chat_action(chat_id, "typing")

    prompt = (
        "তুমি একজন ভাষা শনাক্তকারী এক্সপার্ট। ইউজারের দেওয়া টেক্সটে ব্র্যান্ড নেম বা "
        "লোনওয়ার্ড থাকতে পারে — সেগুলো উপেক্ষা করে মূল ব্যাকরণ ও শব্দ দেখে আসল ভাষাটা বলবে। "
        "শুধু ভাষার নাম বাংলায়, এক লাইনে, অতিরিক্ত ব্যাখ্যা ছাড়া লিখবে।\n\n"
        f"টেক্সট: {text}"
    )

    try:
        interaction = client.interactions.create(model=MODEL, input=prompt)
        result = interaction.output_text.strip()
        bot.send_message(chat_id, f"🔎 {result}", reply_markup=main_keyboard())
    except Exception as e:
        bot.send_message(chat_id, f"⚠️ ত্রুটি হয়েছে: {e}", reply_markup=main_keyboard())

@bot.message_handler(func=lambda m: True)
def fallback(message):
    bot.send_message(message.chat.id, "নিচ থেকে Translator বা Language বাটন চাপো, অথবা /start দাও।", reply_markup=main_keyboard())

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
