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
def call_gemini(prompt, image_data=None, mime_type="image/jpeg"):
    global current_client_index
    for i in range(len(clients)):
        idx = (current_client_index + i) % len(clients)
        try:
            if image_data:
                import base64
                input_parts = [
                    {"type": "text", "text": prompt},
                    {"type": "image", "data": base64.b64encode(image_data).decode(), "mime_type": mime_type}
                ]
                interaction = clients[idx].interactions.create(model=MODEL, input=input_parts)
            else:
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
    markup.add(types.KeyboardButton("🌍 Language"), types.KeyboardButton("🔤 Translator"))
    return markup


# ─── /start ───
def cancel_previous(chat_id):
    bot.clear_step_handler_by_chat_id(chat_id)
    user_data.pop(chat_id, None)


@bot.message_handler(commands=["start"])
def start(message):
    cancel_previous(message.chat.id)
    bot.send_message(
        message.chat.id,
        "🤖 *স্বাগতম AI Translator Bot-এ!*\n\n"
        "এই বটটি দিয়ে তুমি:\n\n"
        "🔤 *Translator* — যেকোনো লেখা যেকোনো ভাষায় অনুবাদ করতে পারবে। লেখাটা পাঠাও, তারপর কোন ভাষায় চাও বলো — ব্যস!\n\n"
        "🌍 *Language* — যেকোনো লেখা পাঠালে বলে দেবে এটা কোন ভাষায় লেখা। সেই ভাষার নিজস্ব লিপিতে এবং বাংলায় দুটোই জানাবে।\n\n"
        "নিচের বাটন থেকে শুরু করো 👇",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


# ─── Translator ───
@bot.message_handler(func=lambda m: m.text == "🔤 Translator")
def translator_start(message):
    cancel_previous(message.chat.id)
    msg = bot.send_message(message.chat.id, "✍️ যে লেখাটা বা ছবি অনুবাদ করতে চাও সেটা পাঠাও:")
    bot.register_next_step_handler(msg, translator_get_text)


def translator_get_text(message):
    chat_id = message.chat.id
    image_data = None
    mime_type = "image/jpeg"

    if message.content_type == "photo":
        file_id = message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        downloaded = bot.download_file(file_info.file_path)
        image_data = downloaded
        user_data[chat_id] = {"original_text": "", "image_data": image_data, "mime_type": mime_type, "lang_attempts": 0}
    elif message.content_type == "text":
        user_data[chat_id] = {"original_text": message.text, "image_data": None, "mime_type": None, "lang_attempts": 0}
    else:
        msg = bot.send_message(chat_id, "✍️ টেক্সট বা ছবি পাঠাও:")
        bot.register_next_step_handler(msg, translator_get_text)
        return
    msg = bot.send_message(
        chat_id,
        "🌐 কোন ভাষায় অনুবাদ করতে চাও?\n\n"
        "`বাংলা`, `English`, `Arabic`, `Hindi`, `Chinese`, `Japanese`, `Korean`, `French`, `Spanish`, `Portuguese`, `Russian`, `German`, `Italian`, `Turkish`, `Urdu`, `Nepali`, `Indonesian`, `Malay`, `Persian`, `Punjabi`, `Tamil`, `Telugu`, `Gujarati`, `Marathi`, `Sinhala`, `Burmese`, `Thai`, `Vietnamese`, `Dutch`, `Polish`, `Swedish`, `Norwegian`, `Danish`, `Finnish`, `Greek`, `Hebrew`, `Romanian`, `Hungarian`, `Czech`, `Ukrainian`",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, translator_get_target_lang)


def translator_get_target_lang(message):
    chat_id = message.chat.id
    target_lang = message.text
    original_text = user_data.get(chat_id, {}).get("original_text", "")
    image_data = user_data.get(chat_id, {}).get("image_data", None)
    mime_type = user_data.get(chat_id, {}).get("mime_type", "image/jpeg")
    attempts = user_data.get(chat_id, {}).get("lang_attempts", 0)

    if not original_text and not image_data:
        bot.send_message(chat_id, "⚠️ কিছু ভুল হয়েছে, আবার /start থেকে শুরু করো।", reply_markup=main_keyboard())
        return

    bot.send_chat_action(chat_id, "typing")

    if image_data:
        prompt = (
            f"You are a smart translator. The user wants to translate the text in this image into a specific language.\n"
            f"The user wrote: \"{target_lang}\"\n"
            f"Even if it is misspelled, written in Bengali, or approximate (like 'চাইনা' for Chinese, 'বাংল্লয়া' for Bengali, 'ফারাঞ্চ' for French), "
            f"try your best to figure out which language they mean and translate the image text into that language.\n"
            f"Only reply with INVALID_LANGUAGE if the input is completely nonsensical.\n"
            f"Only output the translated text, nothing else, no explanation, no quotes, no labels."
        )
    else:
        prompt = (
            f"You are a smart translator. The user wants to translate text into a specific language.\n"
            f"The user wrote: \"{target_lang}\"\n"
            f"Even if it is misspelled, written in Bengali, or approximate (like 'চাইনা' for Chinese, 'বাংল্লয়া' for Bengali, 'ফারাঞ্চ' for French), "
            f"try your best to figure out which language they mean and translate into that language.\n"
            f"Only reply with INVALID_LANGUAGE if the input is completely nonsensical like random numbers, food names, or gibberish that has no resemblance to any language name.\n"
            f"Only output the translated text, nothing else, no explanation, no quotes, no labels.\n\n"
            f"Text to translate: {original_text}"
        )

    try:
        translated = call_gemini(prompt, image_data=image_data, mime_type=mime_type)

        if "INVALID_LANGUAGE" in translated:
            msg = bot.send_message(
                chat_id,
                "⚠️ এটি কোনো ভাষার নাম নয়!\n\n"
                "🌐 কোন ভাষায় অনুবাদ করতে চাও?",
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
    cancel_previous(message.chat.id)
    msg = bot.send_message(message.chat.id, "✍️ যে লেখা বা ছবির ভাষা জানতে চাও সেটা পাঠাও:")
    bot.register_next_step_handler(msg, language_detect)


def language_detect(message):
    chat_id = message.chat.id
    image_data = None
    mime_type = "image/jpeg"

    if message.content_type == "photo":
        file_id = message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        image_data = bot.download_file(file_info.file_path)
    elif message.content_type == "text":
        text = message.text
        if not text or not any(c.isalpha() for c in text):
            msg = bot.send_message(chat_id, "✍️ দয়া করে কিছু টেক্সট বা ছবি পাঠাও:")
            bot.register_next_step_handler(msg, language_detect)
            return
    else:
        msg = bot.send_message(chat_id, "✍️ দয়া করে টেক্সট বা ছবি পাঠাও:")
        bot.register_next_step_handler(msg, language_detect)
        return

    bot.send_chat_action(chat_id, "typing")

    if image_data:
        prompt = (
            "তুমি একজন ভাষা শনাক্তকারী এক্সপার্ট। এই ছবিতে যে লেখা আছে সেটা কোন ভাষায় লেখা তা বলো।\n"
            "উত্তর দাও ঠিক এই ফরম্যাটে (দুই লাইনে):\n"
            "প্রথম লাইন: সেই ভাষার নিজস্ব লিপিতে ভাষার নাম (যেমন: English, العربية, हिन्दी, Français)\n"
            "দ্বিতীয় লাইন: বাংলায় ভাষার নাম (যেমন: ইংরেজি, আরবি, হিন্দি, ফরাসি)\n"
            "শুধু এই দুটো লাইন, কোনো বাড়তি কথা নয়।"
        )
    else:
        text = message.text
        prompt = (
            "তুমি একজন ভাষা শনাক্তকারী এক্সপার্ট। নিচের টেক্সটটি কোন ভাষায় লেখা তা বলো।\n"
            "উত্তর দাও ঠিক এই ফরম্যাটে (দুই লাইনে):\n"
            "প্রথম লাইন: সেই ভাষার নিজস্ব লিপিতে ভাষার নাম (যেমন: English, العربية, हिन्दी, Français)\n"
            "দ্বিতীয় লাইন: বাংলায় ভাষার নাম (যেমন: ইংরেজি, আরবি, হিন্দি, ফরাসি)\n"
            "শুধু এই দুটো লাইন, কোনো বাড়তি কথা নয়।\n\n"
            f"টেক্সট: {text}"
        )

    try:
        result = call_gemini(prompt, image_data=image_data, mime_type=mime_type)
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
    pass


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
