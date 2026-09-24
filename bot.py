import os
import re
import time
import datetime
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

default_clients = [
    genai.Client(api_key=GEMINI_KEY_1),
    genai.Client(api_key=GEMINI_KEY_2),
]
current_client_index = 0
MODEL = "gemini-3.6-flash"

bot = telebot.TeleBot(BOT_TOKEN)
user_data = {}

# ─── Admin ───
ADMIN_ID = 6625019627

# ─── User DB (মেমোরিতে) ───
user_db = {}
FREE_LIMIT = 3


def get_user(chat_id):
    if chat_id not in user_db:
        user_db[chat_id] = {"api_key": None, "date": None, "count": 0}
    return user_db[chat_id]


def can_use(chat_id):
    user = get_user(chat_id)
    if user["api_key"]:
        return True, None
    today = datetime.date.today().isoformat()
    if user["date"] != today:
        user["date"] = today
        user["count"] = 0
    remaining = FREE_LIMIT - user["count"]
    if remaining > 0:
        return True, remaining
    return False, 0


def use_request(chat_id):
    user = get_user(chat_id)
    if user["api_key"]:
        return
    today = datetime.date.today().isoformat()
    if user["date"] != today:
        user["date"] = today
        user["count"] = 0
    user["count"] += 1


def get_client(chat_id):
    user = get_user(chat_id)
    if user["api_key"]:
        return [genai.Client(api_key=user["api_key"])]
    return default_clients


# ─── Safe send (কিবোর্ডসহ) ───
def safe_send(chat_id, text, parse_mode=None, reply_markup=None):
    """মেসেজ পাঠায়। ফেল করলে (parse error / rate limit) কয়েকবার আবার চেষ্টা করে।"""
    for attempt in range(3):
        try:
            return bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
        except Exception as e:
            err = str(e).lower()
            if parse_mode and ("parse" in err or "entities" in err):
                parse_mode = None  # Markdown ভাঙলে প্লেইন টেক্সটে পাঠাও
                continue
            time.sleep(2)
    return None


def send_result(chat_id, anim_msg, text, parse_mode=None):
    """
    'Typing' মেসেজ ডিলেট করে, রেজাল্ট একদম নতুন মেসেজে পাঠায়
    এবং মেইন কিবোর্ড সেই মেসেজেই লাগায়। আলাদা '👇' মেসেজ আর লাগে না।
    """
    if anim_msg is not None:
        try:
            bot.delete_message(chat_id, anim_msg.message_id)
        except Exception:
            pass
    return safe_send(chat_id, text, parse_mode=parse_mode, reply_markup=main_keyboard())


# ─── Typing Animation ───
def call_gemini_with_animation(chat_id, prompt, image_data=None, mime_type="image/jpeg"):
    stop_event = threading.Event()
    msg_ready = threading.Event()
    result_holder = {}
    anim_msg = [None]

    def animate():
        dots = [".", "..", "..."]
        i = 0
        try:
            sent = bot.send_message(chat_id, "*Typing.*", parse_mode="Markdown")
            anim_msg[0] = sent
        except Exception:
            sent = None
        finally:
            msg_ready.set()
        if sent is None:
            return
        # Telegram rate limit এড়াতে ১ সেকেন্ড পরপর এডিট
        while not stop_event.wait(1.0):
            i = (i + 1) % 3
            try:
                bot.edit_message_text(f"*Typing{dots[i]}*", chat_id, sent.message_id, parse_mode="Markdown")
            except Exception:
                pass

    t = threading.Thread(target=animate, daemon=True)
    t.start()
    msg_ready.wait()

    try:
        result_holder["text"] = call_gemini(chat_id, prompt, image_data=image_data, mime_type=mime_type)
        result_holder["error"] = None
    except Exception as e:
        result_holder["text"] = None
        result_holder["error"] = e
    finally:
        stop_event.set()
        t.join()

    return anim_msg[0], result_holder


# ─── API call ───
def call_gemini(chat_id, prompt, image_data=None, mime_type="image/jpeg"):
    global current_client_index
    clients = get_client(chat_id)
    user = get_user(chat_id)

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
            if not user["api_key"]:
                current_client_index = idx
            return interaction.output_text.strip()
        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower() or "rate" in err.lower():
                continue
            else:
                raise e
    raise Exception("RATE_LIMIT")


# ─── Keyboards ───
def main_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True)
    markup.add(types.KeyboardButton("🌍 Language", style="primary"), types.KeyboardButton("🔤 Translator", style="primary"))
    markup.add(types.KeyboardButton("🌐 CC Short Name", style="success"), types.KeyboardButton("🆔 Extract UID", style="success"))
    markup.add(types.KeyboardButton("❓ Help", style="primary"), types.KeyboardButton("⚙️ Settings", style="primary"))
    return markup


def settings_keyboard(user_id=None):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, is_persistent=True)
    markup.add(types.KeyboardButton("🔑 Add API Key", style="success"), types.KeyboardButton("❌ Remove API Key", style="danger"))
    markup.add(types.KeyboardButton("📊 আমার ব্যবহার", style="primary"), types.KeyboardButton("🔙 Back", style="primary"))
    if user_id and user_id == ADMIN_ID:
        markup.add(types.KeyboardButton("👑 Admin Panel", style="primary"))
    return markup


def admin_panel_markup():
    markup = types.InlineKeyboardMarkup()
    markup.row(
        types.InlineKeyboardButton("🔽", callback_data="admin_dec"),
        types.InlineKeyboardButton(f"🆓 Free Limit: {FREE_LIMIT}", callback_data="admin_info"),
        types.InlineKeyboardButton("🔼", callback_data="admin_inc"),
    )
    return markup


# ─── Cancel ───
def cancel_previous(chat_id):
    bot.clear_step_handler_by_chat_id(chat_id)
    user_data.pop(chat_id, None)


# ─── Country Data ───
COUNTRY_DATA = {
    "A": [
        ("🇦🇫", "Afghanistan", "AF"),
        ("🇦🇱", "Albania", "AL"),
        ("🇩🇿", "Algeria", "DZ"),
        ("🇦🇩", "Andorra", "AD"),
        ("🇦🇴", "Angola", "AO"),
        ("🇦🇬", "Antigua and Barbuda", "AG"),
        ("🇦🇷", "Argentina", "AR"),
        ("🇦🇲", "Armenia", "AM"),
        ("🇦🇺", "Australia", "AU"),
        ("🇦🇹", "Austria", "AT"),
        ("🇦🇿", "Azerbaijan", "AZ"),
    ],
    "B": [
        ("🇧🇸", "Bahamas", "BS"),
        ("🇧🇭", "Bahrain", "BH"),
        ("🇧🇩", "Bangladesh", "BD"),
        ("🇧🇧", "Barbados", "BB"),
        ("🇧🇾", "Belarus", "BY"),
        ("🇧🇪", "Belgium", "BE"),
        ("🇧🇿", "Belize", "BZ"),
        ("🇧🇯", "Benin", "BJ"),
        ("🇧🇹", "Bhutan", "BT"),
        ("🇧🇴", "Bolivia", "BO"),
        ("🇧🇦", "Bosnia and Herzegovina", "BA"),
        ("🇧🇼", "Botswana", "BW"),
        ("🇧🇷", "Brazil", "BR"),
        ("🇧🇳", "Brunei", "BN"),
        ("🇧🇬", "Bulgaria", "BG"),
        ("🇧🇫", "Burkina Faso", "BF"),
        ("🇧🇮", "Burundi", "BI"),
    ],
    "C": [
        ("🇨🇻", "Cabo Verde", "CV"),
        ("🇰🇭", "Cambodia", "KH"),
        ("🇨🇲", "Cameroon", "CM"),
        ("🇨🇦", "Canada", "CA"),
        ("🇨🇫", "Central African Republic", "CF"),
        ("🇹🇩", "Chad", "TD"),
        ("🇨🇱", "Chile", "CL"),
        ("🇨🇳", "China", "CN"),
        ("🇨🇴", "Colombia", "CO"),
        ("🇰🇲", "Comoros", "KM"),
        ("🇨🇩", "Congo (DRC)", "CD"),
        ("🇨🇬", "Congo (Republic)", "CG"),
        ("🇨🇷", "Costa Rica", "CR"),
        ("🇨🇮", "Côte d'Ivoire", "CI"),
        ("🇭🇷", "Croatia", "HR"),
        ("🇨🇺", "Cuba", "CU"),
        ("🇨🇾", "Cyprus", "CY"),
        ("🇨🇿", "Czech Republic", "CZ"),
    ],
    "D": [
        ("🇩🇰", "Denmark", "DK"),
        ("🇩🇯", "Djibouti", "DJ"),
        ("🇩🇲", "Dominica", "DM"),
        ("🇩🇴", "Dominican Republic", "DO"),
    ],
    "E": [
        ("🇪🇨", "Ecuador", "EC"),
        ("🇪🇬", "Egypt", "EG"),
        ("🇸🇻", "El Salvador", "SV"),
        ("🇬🇶", "Equatorial Guinea", "GQ"),
        ("🇪🇷", "Eritrea", "ER"),
        ("🇪🇪", "Estonia", "EE"),
        ("🇸🇿", "Eswatini", "SZ"),
        ("🇪🇹", "Ethiopia", "ET"),
    ],
    "F": [
        ("🇫🇯", "Fiji", "FJ"),
        ("🇫🇮", "Finland", "FI"),
        ("🇫🇷", "France", "FR"),
    ],
    "G": [
        ("🇬🇦", "Gabon", "GA"),
        ("🇬🇲", "Gambia", "GM"),
        ("🇬🇪", "Georgia", "GE"),
        ("🇩🇪", "Germany", "DE"),
        ("🇬🇭", "Ghana", "GH"),
        ("🇬🇷", "Greece", "GR"),
        ("🇬🇩", "Grenada", "GD"),
        ("🇬🇹", "Guatemala", "GT"),
        ("🇬🇳", "Guinea", "GN"),
        ("🇬🇼", "Guinea-Bissau", "GW"),
        ("🇬🇾", "Guyana", "GY"),
    ],
    "H": [
        ("🇭🇹", "Haiti", "HT"),
        ("🇭🇳", "Honduras", "HN"),
        ("🇭🇺", "Hungary", "HU"),
    ],
    "I": [
        ("🇮🇸", "Iceland", "IS"),
        ("🇮🇳", "India", "IN"),
        ("🇮🇩", "Indonesia", "ID"),
        ("🇮🇷", "Iran", "IR"),
        ("🇮🇶", "Iraq", "IQ"),
        ("🇮🇪", "Ireland", "IE"),
        ("🇮🇱", "Israel", "IL"),
        ("🇮🇹", "Italy", "IT"),
    ],
    "J": [
        ("🇯🇲", "Jamaica", "JM"),
        ("🇯🇵", "Japan", "JP"),
        ("🇯🇴", "Jordan", "JO"),
    ],
    "K": [
        ("🇰🇿", "Kazakhstan", "KZ"),
        ("🇰🇪", "Kenya", "KE"),
        ("🇰🇮", "Kiribati", "KI"),
        ("🇰🇼", "Kuwait", "KW"),
        ("🇰🇬", "Kyrgyzstan", "KG"),
    ],
    "L": [
        ("🇱🇦", "Laos", "LA"),
        ("🇱🇻", "Latvia", "LV"),
        ("🇱🇧", "Lebanon", "LB"),
        ("🇱🇸", "Lesotho", "LS"),
        ("🇱🇷", "Liberia", "LR"),
        ("🇱🇾", "Libya", "LY"),
        ("🇱🇮", "Liechtenstein", "LI"),
        ("🇱🇹", "Lithuania", "LT"),
        ("🇱🇺", "Luxembourg", "LU"),
    ],
    "M": [
        ("🇲🇬", "Madagascar", "MG"),
        ("🇲🇼", "Malawi", "MW"),
        ("🇲🇾", "Malaysia", "MY"),
        ("🇲🇻", "Maldives", "MV"),
        ("🇲🇱", "Mali", "ML"),
        ("🇲🇹", "Malta", "MT"),
        ("🇲🇭", "Marshall Islands", "MH"),
        ("🇲🇷", "Mauritania", "MR"),
        ("🇲🇺", "Mauritius", "MU"),
        ("🇲🇽", "Mexico", "MX"),
        ("🇫🇲", "Micronesia", "FM"),
        ("🇲🇩", "Moldova", "MD"),
        ("🇲🇨", "Monaco", "MC"),
        ("🇲🇳", "Mongolia", "MN"),
        ("🇲🇪", "Montenegro", "ME"),
        ("🇲🇦", "Morocco", "MA"),
        ("🇲🇿", "Mozambique", "MZ"),
        ("🇲🇲", "Myanmar", "MM"),
    ],
    "N": [
        ("🇳🇦", "Namibia", "NA"),
        ("🇳🇷", "Nauru", "NR"),
        ("🇳🇵", "Nepal", "NP"),
        ("🇳🇱", "Netherlands", "NL"),
        ("🇳🇿", "New Zealand", "NZ"),
        ("🇳🇮", "Nicaragua", "NI"),
        ("🇳🇪", "Niger", "NE"),
        ("🇳🇬", "Nigeria", "NG"),
        ("🇰🇵", "North Korea", "KP"),
        ("🇲🇰", "North Macedonia", "MK"),
        ("🇳🇴", "Norway", "NO"),
    ],
    "O": [
        ("🇴🇲", "Oman", "OM"),
    ],
    "P": [
        ("🇵🇰", "Pakistan", "PK"),
        ("🇵🇼", "Palau", "PW"),
        ("🇵🇸", "Palestine", "PS"),
        ("🇵🇦", "Panama", "PA"),
        ("🇵🇬", "Papua New Guinea", "PG"),
        ("🇵🇾", "Paraguay", "PY"),
        ("🇵🇪", "Peru", "PE"),
        ("🇵🇭", "Philippines", "PH"),
        ("🇵🇱", "Poland", "PL"),
        ("🇵🇹", "Portugal", "PT"),
    ],
    "Q": [
        ("🇶🇦", "Qatar", "QA"),
    ],
    "R": [
        ("🇷🇴", "Romania", "RO"),
        ("🇷🇺", "Russia", "RU"),
        ("🇷🇼", "Rwanda", "RW"),
    ],
    "S": [
        ("🇰🇳", "Saint Kitts and Nevis", "KN"),
        ("🇱🇨", "Saint Lucia", "LC"),
        ("🇻🇨", "Saint Vincent and the Grenadines", "VC"),
        ("🇼🇸", "Samoa", "WS"),
        ("🇸🇲", "San Marino", "SM"),
        ("🇸🇹", "Sao Tome and Principe", "ST"),
        ("🇸🇦", "Saudi Arabia", "SA"),
        ("🇸🇳", "Senegal", "SN"),
        ("🇷🇸", "Serbia", "RS"),
        ("🇸🇨", "Seychelles", "SC"),
        ("🇸🇱", "Sierra Leone", "SL"),
        ("🇸🇬", "Singapore", "SG"),
        ("🇸🇰", "Slovakia", "SK"),
        ("🇸🇮", "Slovenia", "SI"),
        ("🇸🇧", "Solomon Islands", "SB"),
        ("🇸🇴", "Somalia", "SO"),
        ("🇿🇦", "South Africa", "ZA"),
        ("🇸🇸", "South Sudan", "SS"),
        ("🇰🇷", "South Korea", "KR"),
        ("🇪🇸", "Spain", "ES"),
        ("🇱🇰", "Sri Lanka", "LK"),
        ("🇸🇩", "Sudan", "SD"),
        ("🇸🇷", "Suriname", "SR"),
        ("🇸🇪", "Sweden", "SE"),
        ("🇨🇭", "Switzerland", "CH"),
        ("🇸🇾", "Syria", "SY"),
    ],
    "T": [
        ("🇹🇼", "Taiwan", "TW"),
        ("🇹🇯", "Tajikistan", "TJ"),
        ("🇹🇿", "Tanzania", "TZ"),
        ("🇹🇭", "Thailand", "TH"),
        ("🇹🇱", "Timor-Leste", "TL"),
        ("🇹🇬", "Togo", "TG"),
        ("🇹🇴", "Tonga", "TO"),
        ("🇹🇹", "Trinidad and Tobago", "TT"),
        ("🇹🇳", "Tunisia", "TN"),
        ("🇹🇷", "Turkey", "TR"),
        ("🇹🇲", "Turkmenistan", "TM"),
        ("🇹🇻", "Tuvalu", "TV"),
    ],
    "U": [
        ("🇺🇬", "Uganda", "UG"),
        ("🇺🇦", "Ukraine", "UA"),
        ("🇦🇪", "United Arab Emirates", "AE"),
        ("🇬🇧", "United Kingdom", "GB"),
        ("🇺🇸", "United States", "US"),
        ("🇺🇾", "Uruguay", "UY"),
        ("🇺🇿", "Uzbekistan", "UZ"),
    ],
    "V": [
        ("🇻🇺", "Vanuatu", "VU"),
        ("🇻🇦", "Vatican City", "VA"),
        ("🇻🇪", "Venezuela", "VE"),
        ("🇻🇳", "Vietnam", "VN"),
    ],
    "W": [
        ("🇼🇫", "Wallis and Futuna", "WF"),
    ],
    "Y": [
        ("🇾🇪", "Yemen", "YE"),
    ],
    "Z": [
        ("🇿🇲", "Zambia", "ZM"),
        ("🇿🇼", "Zimbabwe", "ZW"),
    ],
}


# ─── /start ───
@bot.message_handler(commands=["start"])
def start(message):
    cancel_previous(message.chat.id)
    bot.send_message(
        message.chat.id,
        "👋 স্বাগতম *𝗟𝗮𝗻𝗴𝘂𝗮𝗴𝗲 𝗧𝗼 𝗧𝗿𝗮𝗻𝘀𝗹𝗮𝘁𝗼𝗿* এ!\n\n"
        "এই বটে তুমি:\n"
        "🔤 যেকোনো লেখা বা ছবি যেকোনো ভাষায় অনুবাদ করতে পারবে\n"
        "🌍 যেকোনো লেখা বা ছবির ভাষা শনাক্ত করতে পারবে\n"
        "🌐 দেশের নাম, পতাকা ও CC কোড খুঁজতে পারবে\n"
        "🆔 Facebook UID বের করতে পারবে\n\n"
        f"🆓 তুমি প্রতিদিন *{FREE_LIMIT}টা বিনামূল্যে* ব্যবহার করতে পারবে।\n"
        "আনলিমিটেড ব্যবহার করতে চাইলে নিজের *Gemini API Key* যোগ করো।\n\n"
        "🔑 *API Key বানানো একদম সহজ!*\n\n"
        "ধাপ ১ — নিচের লিংকে যাও:\n"
        "👉 https://aistudio.google.com\n\n"
        "ধাপ ২ — Google account দিয়ে Sign in করো\n\n"
        "ধাপ ৩ — *Get API key* বাটনে ক্লিক করো\n\n"
        "ধাপ ৪ — *Create API key* তে ক্লিক করো\n\n"
        "ধাপ ৫ — Key টা কপি করো\n\n"
        "✅ Key পেলে ⚙️ Settings → 🔑 Add API Key তে গিয়ে পেস্ট করো — ব্যস! আনলিমিটেড চালু! 🎉\n\n"
        "নিচের বাটন থেকে শুরু করো 👇",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


# ─── /menu (কিবোর্ড হারিয়ে গেলে ফিরিয়ে আনার জন্য) ───
@bot.message_handler(commands=["menu"])
def menu_command(message):
    cancel_previous(message.chat.id)
    safe_send(message.chat.id, "👇 মেইন মেনু:", reply_markup=main_keyboard())


# ─── Settings ───
@bot.message_handler(func=lambda m: m.text == "⚙️ Settings")
def settings(message):
    cancel_previous(message.chat.id)
    bot.send_message(
        message.chat.id,
        "⚙️ *Settings*\n\nকী করতে চাও?",
        parse_mode="Markdown",
        reply_markup=settings_keyboard(message.from_user.id)
    )


@bot.message_handler(func=lambda m: m.text == "🔙 Back")
def back(message):
    cancel_previous(message.chat.id)
    safe_send(message.chat.id, "👇 মেইন মেনু:", reply_markup=main_keyboard())


@bot.message_handler(func=lambda m: m.text == "🔑 Add API Key")
def add_api_key(message):
    cancel_previous(message.chat.id)
    msg = bot.send_message(message.chat.id, "🔑 তোমার Gemini API Key টা পেস্ট করো:")
    bot.register_next_step_handler(msg, save_api_key)


def save_api_key(message):
    chat_id = message.chat.id
    if message.text and message.text in MENU_BUTTONS:
        cancel_previous(chat_id)
        bot.process_new_messages([message])
        return
    key = message.text.strip() if message.text else ""
    if len(key) < 10:
        safe_send(chat_id, "⚠️ এটা সঠিক API Key না।", reply_markup=main_keyboard())
        return
    bot.send_chat_action(chat_id, "typing")
    try:
        test_client = genai.Client(api_key=key)
        test_client.interactions.create(model=MODEL, input="hi")
        user = get_user(chat_id)
        user["api_key"] = key
        safe_send(chat_id, "✅ API Key সেভ হয়েছে! এখন তুমি আনলিমিটেড ব্যবহার করতে পারবে। 🎉", reply_markup=main_keyboard())
    except Exception as e:
        err = str(e)
        if "403" in err or "401" in err or "invalid" in err.lower() or "api key" in err.lower():
            safe_send(chat_id, "❌ ভুল API Key! সঠিক Key দাও:\n👉 https://aistudio.google.com", reply_markup=main_keyboard())
        else:
            safe_send(chat_id, "⚠️ Key যাচাই করতে পারিনি।", reply_markup=main_keyboard())


@bot.message_handler(func=lambda m: m.text == "❌ Remove API Key")
def remove_api_key(message):
    cancel_previous(message.chat.id)
    chat_id = message.chat.id
    user = get_user(chat_id)
    if not user["api_key"]:
        safe_send(chat_id, "⚠️ তোমার কোনো API Key সেভ নেই।", reply_markup=settings_keyboard(chat_id))
        return
    user["api_key"] = None
    safe_send(chat_id, "✅ API Key রিমুভ হয়েছে।", reply_markup=settings_keyboard(chat_id))


@bot.message_handler(func=lambda m: m.text == "📊 আমার ব্যবহার")
def my_usage(message):
    cancel_previous(message.chat.id)
    chat_id = message.chat.id
    user = get_user(chat_id)
    today = datetime.date.today().isoformat()
    if user["api_key"]:
        safe_send(chat_id, "✅ তোমার নিজের API Key আছে — *আনলিমিটেড* ব্যবহার করতে পারবে!", parse_mode="Markdown", reply_markup=settings_keyboard(chat_id))
    else:
        if user["date"] != today:
            used = 0
        else:
            used = user["count"]
        remaining = FREE_LIMIT - used
        safe_send(
            chat_id,
            f"📊 *আজকের ব্যবহার:*\n\n"
            f"✅ ব্যবহার হয়েছে: *{used}টা*\n"
            f"🆓 বাকি আছে: *{remaining}টা*\n\n"
            f"আনলিমিটেড চাইলে ⚙️ Settings → 🔑 Add API Key",
            parse_mode="Markdown",
            reply_markup=settings_keyboard(chat_id)
        )


# ─── Admin Panel ───
@bot.message_handler(func=lambda m: m.text == "👑 Admin Panel")
def admin_panel(message):
    cancel_previous(message.chat.id)
    if message.from_user.id != ADMIN_ID:
        return
    bot.send_message(
        message.chat.id,
        f"👑 *Admin Panel*\n\n🆓 বর্তমান Free Limit: *{FREE_LIMIT}*",
        parse_mode="Markdown",
        reply_markup=admin_panel_markup()
    )


@bot.callback_query_handler(func=lambda call: call.data in ["admin_inc", "admin_dec", "admin_info"])
def admin_callback(call):
    global FREE_LIMIT
    if call.from_user.id != ADMIN_ID:
        bot.answer_callback_query(call.id, "❌ তুমি admin না!")
        return

    if call.data == "admin_inc":
        FREE_LIMIT += 1
        bot.answer_callback_query(call.id, f"✅ Limit বাড়ানো হয়েছে → {FREE_LIMIT}")
    elif call.data == "admin_dec":
        if FREE_LIMIT > 1:
            FREE_LIMIT -= 1
            bot.answer_callback_query(call.id, f"✅ Limit কমানো হয়েছে → {FREE_LIMIT}")
        else:
            bot.answer_callback_query(call.id, "⚠️ Limit ১ এর নিচে যাবে না!")
            return
    elif call.data == "admin_info":
        bot.answer_callback_query(call.id, f"বর্তমান Limit: {FREE_LIMIT}")
        return

    try:
        bot.edit_message_text(
            f"👑 *Admin Panel*\n\n🆓 বর্তমান Free Limit: *{FREE_LIMIT}*",
            call.message.chat.id,
            call.message.message_id,
            parse_mode="Markdown",
            reply_markup=admin_panel_markup()
        )
    except Exception:
        pass


# ─── Rate limit check helper ───
def check_limit(chat_id):
    allowed, remaining = can_use(chat_id)
    if not allowed:
        safe_send(
            chat_id,
            f"⚠️ আজকের *{FREE_LIMIT}টা বিনামূল্যে* request শেষ!\n\n"
            "আনলিমিটেড ব্যবহার করতে নিজের Gemini API Key যোগ করো:\n"
            "⚙️ Settings → 🔑 Add API Key",
            parse_mode="Markdown",
            reply_markup=main_keyboard()
        )
        return False
    return True


MENU_BUTTONS = ["⚙️ Settings", "🔙 Back", "🌍 Language", "🔤 Translator", "🌐 CC Short Name", "🆔 Extract UID",
                "🔑 Add API Key", "❌ Remove API Key", "📊 আমার ব্যবহার", "❓ Help", "👑 Admin Panel"]

BUSY_TEXT = "⏳ এই মুহূর্তে একটু ব্যস্ত আছি!\n\nদয়া করে মাত্র ১ মিনিট পরে আবার চেষ্টা করুন 🙏"


# ─── Translator ───
@bot.message_handler(func=lambda m: m.text == "🔤 Translator")
def translator_start(message):
    cancel_previous(message.chat.id)
    if not check_limit(message.chat.id):
        return
    msg = bot.send_message(message.chat.id, "✍️ যে লেখাটা বা ছবি অনুবাদ করতে চাও সেটা পাঠাও:")
    bot.register_next_step_handler(msg, translator_get_text)


def translator_get_text(message):
    chat_id = message.chat.id

    if message.text and message.text in MENU_BUTTONS:
        cancel_previous(chat_id)
        bot.process_new_messages([message])
        return

    image_data = None
    mime_type = "image/jpeg"

    if message.content_type == "photo":
        file_id = message.photo[-1].file_id
        file_info = bot.get_file(file_id)
        downloaded = bot.download_file(file_info.file_path)
        image_data = downloaded
        user_data[chat_id] = {"original_text": "", "image_data": image_data, "mime_type": mime_type}
    elif message.content_type == "text":
        user_data[chat_id] = {"original_text": message.text, "image_data": None, "mime_type": None}
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

    if message.text and message.text in MENU_BUTTONS:
        cancel_previous(chat_id)
        bot.process_new_messages([message])
        return

    # ছবি/স্টিকার পাঠালে target_lang None হয়ে যেত — এখন আবার জিজ্ঞেস করবে
    if message.content_type != "text" or not message.text:
        msg = bot.send_message(chat_id, "🌐 দয়া করে ভাষার নাম *লিখে* পাঠাও:", parse_mode="Markdown")
        bot.register_next_step_handler(msg, translator_get_target_lang)
        return

    target_lang = message.text
    original_text = user_data.get(chat_id, {}).get("original_text", "")
    image_data = user_data.get(chat_id, {}).get("image_data", None)
    mime_type = user_data.get(chat_id, {}).get("mime_type", "image/jpeg")

    if not original_text and not image_data:
        safe_send(chat_id, "⚠️ কিছু ভুল হয়েছে, আবার /start থেকে শুরু করো।", reply_markup=main_keyboard())
        return

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

    anim_msg, result_holder = call_gemini_with_animation(chat_id, prompt, image_data=image_data, mime_type=mime_type)

    if result_holder["error"]:
        e = result_holder["error"]
        if "RATE_LIMIT" in str(e):
            send_result(chat_id, anim_msg, BUSY_TEXT)
        else:
            send_result(chat_id, anim_msg, f"⚠️ ত্রুটি হয়েছে: {e}")
        user_data.pop(chat_id, None)
        return

    translated = result_holder["text"]

    if "INVALID_LANGUAGE" in translated:
        send_result(chat_id, anim_msg, "⚠️ এটি কোনো ভাষার নাম নয়!\n\n🌐 ভাষার নাম লেখো:")
        bot.register_next_step_handler_by_chat_id(chat_id, translator_get_target_lang)
        return

    use_request(chat_id)
    escaped = translated.replace("`", "'")
    # Typing মেসেজ ডিলেট হবে, রেজাল্ট নতুন মেসেজে আসবে, কিবোর্ডও সেই মেসেজে থাকবে
    send_result(chat_id, anim_msg, f"`{escaped}`", parse_mode="Markdown")
    user_data.pop(chat_id, None)


# ─── Language ───
@bot.message_handler(func=lambda m: m.text == "🌍 Language")
def language_start(message):
    cancel_previous(message.chat.id)
    if not check_limit(message.chat.id):
        return
    msg = bot.send_message(message.chat.id, "✍️ যে লেখা বা ছবির ভাষা জানতে চাও সেটা পাঠাও:")
    bot.register_next_step_handler(msg, language_detect)


def language_detect(message):
    chat_id = message.chat.id

    if message.text and message.text in MENU_BUTTONS:
        cancel_previous(chat_id)
        bot.process_new_messages([message])
        return

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

    anim_msg, result_holder = call_gemini_with_animation(chat_id, prompt, image_data=image_data, mime_type=mime_type)

    if result_holder["error"]:
        e = result_holder["error"]
        if "RATE_LIMIT" in str(e):
            send_result(chat_id, anim_msg, BUSY_TEXT)
        else:
            send_result(chat_id, anim_msg, f"⚠️ ত্রুটি হয়েছে: {e}")
        return

    use_request(chat_id)
    result = result_holder["text"]
    lines = result.strip().split("\n")
    if len(lines) >= 2:
        native = lines[0].strip()
        bangla = lines[1].strip()
        send_result(chat_id, anim_msg, f"🔎 *{native}* / {bangla}", parse_mode="Markdown")
    else:
        send_result(chat_id, anim_msg, f"🔎 {result}")


# ─── CC Short Name ───
@bot.message_handler(func=lambda m: m.text == "🌐 CC Short Name")
def cc_short_name_start(message):
    cancel_previous(message.chat.id)
    msg = bot.send_message(
        message.chat.id,
        "🌐 *CC Short Name*\n\n"
        "যে দেশ খুঁজতে চাও তার নামের *প্রথম অক্ষর* পাঠাও।\n"
        "যেমন: `A`, `B`, `C` ...",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, cc_short_name_result)


def cc_short_name_result(message):
    chat_id = message.chat.id

    if message.text and message.text in MENU_BUTTONS:
        cancel_previous(chat_id)
        bot.process_new_messages([message])
        return

    letter = message.text.strip().upper() if message.text else ""

    if not letter or len(letter) != 1 or not letter.isalpha():
        msg = bot.send_message(chat_id, "⚠️ একটি অক্ষর পাঠাও (যেমন: A, B, C):")
        bot.register_next_step_handler(msg, cc_short_name_result)
        return

    countries = COUNTRY_DATA.get(letter)

    if not countries:
        msg = bot.send_message(
            chat_id,
            f"⚠️ '{letter}' দিয়ে শুরু হওয়া কোনো দেশ পাওয়া যায়নি।\n\nআরেকটি অক্ষর দাও:"
        )
        bot.register_next_step_handler(msg, cc_short_name_result)
        return

    lines = [f"*'{letter}' দিয়ে শুরু হওয়া দেশসমূহ:*\n"]
    for i, (flag, name, code) in enumerate(countries, 1):
        lines.append(f"{i}. {flag} `{name}` — `{code}`")

    safe_send(chat_id, "\n".join(lines), parse_mode="Markdown", reply_markup=main_keyboard())


# ─── Extract UID ───
@bot.message_handler(func=lambda m: m.text == "🆔 Extract UID")
def extract_id_start(message):
    cancel_previous(message.chat.id)
    msg = bot.send_message(
        message.chat.id,
        "🆔 ফেসবুক লিংক বা কুকি পাঠাও (প্রতিটি আলাদা লাইনে)।"
    )
    bot.register_next_step_handler(msg, extract_id_result)


def extract_id_result(message):
    chat_id = message.chat.id

    if message.text and message.text in MENU_BUTTONS:
        cancel_previous(chat_id)
        bot.process_new_messages([message])
        return

    if not message.text:
        msg = bot.send_message(chat_id, "⚠️ টেক্সট পাঠাও:")
        bot.register_next_step_handler(msg, extract_id_result)
        return

    lines = message.text.strip().split("\n")
    results = []

    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue

        found_id = None

        match = re.search(r'c_user=(\d+)', line)
        if match:
            found_id = match.group(1)
        else:
            match = re.search(r'pas=(\d+)', line)
            if match:
                found_id = match.group(1)

        if not found_id:
            match = re.search(r'profile\.php\?id=(\d+)', line)
            if match:
                found_id = match.group(1)

        if not found_id:
            match = re.search(r'facebook\.com/(?:people/[^/]+/)?(\d{5,})', line)
            if match:
                found_id = match.group(1)

        if found_id:
            results.append(f"✅ {i}. `{found_id}`")
        else:
            results.append(f"❌ {i}. ID পাওয়া যায়নি")

    if results:
        reply = "🆔 *Facebook ID Results:*\n\n" + "\n".join(results)
    else:
        reply = "⚠️ কোনো ID পাওয়া যায়নি।"

    safe_send(chat_id, reply, parse_mode="Markdown", reply_markup=main_keyboard())


# ─── Help ───
@bot.message_handler(func=lambda m: m.text == "❓ Help")
def help_handler(message):
    cancel_previous(message.chat.id)
    safe_send(
        message.chat.id,
        "❓ *বটের সাহায্য*\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🔤 *Translator*\n"
        "যেকোনো টেক্সট বা ছবির লেখা যেকোনো ভাষায় অনুবাদ করো।\n"
        "ব্যবহার: বাটনে ক্লিক → টেক্সট বা ছবি পাঠাও → ভাষার নাম লেখো → অনুবাদ পাবে।\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🌍 *Language*\n"
        "যেকোনো টেক্সট বা ছবির লেখা কোন ভাষায় সেটা শনাক্ত করো।\n"
        "ব্যবহার: বাটনে ক্লিক → টেক্সট বা ছবি পাঠাও → ভাষার নাম পাবে।\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🌐 *CC Short Name*\n"
        "যেকোনো দেশের নাম, পতাকা এবং দেশের কোড (CC) জানো।\n"
        "ব্যবহার: বাটনে ক্লিক → দেশের নামের প্রথম অক্ষর পাঠাও → সব দেশ দেখবে।\n"
        "যেমন: `B` পাঠালে Bangladesh, Bahrain সহ সব B দেশ আসবে।\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "🆔 *Extract UID*\n"
        "Facebook লিংক বা Cookie থেকে UID বের করো।\n"
        "ব্যবহার: বাটনে ক্লিক → লিংক বা কুকি পাঠাও → UID পাবে।\n"
        "একসাথে অনেকগুলো পাঠাতে পারো, প্রতিটি আলাদা লাইনে।\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "⚙️ *Settings*\n"
        "🔑 *Add API Key* — নিজের Gemini API Key যোগ করো, আনলিমিটেড ব্যবহার করো।\n"
        "❌ *Remove API Key* — যোগ করা API Key মুছে ফেলো।\n"
        "📊 *আমার ব্যবহার* — আজকে কতটা ব্যবহার হয়েছে দেখো।\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"🆓 *ফ্রি লিমিট:* প্রতিদিন {FREE_LIMIT}টা\n"
        "🔑 *আনলিমিটেড:* নিজের API Key যোগ করো\n"
        "👉 https://aistudio.google.com\n\n"
        "কিবোর্ড হারিয়ে গেলে /menu লেখো।",
        parse_mode="Markdown",
        reply_markup=main_keyboard()
    )


# ─── Fallback ───
# কিবোর্ড হারিয়ে গেলে ইউজার যেকোনো টেক্সট পাঠালেই মেনু ফিরে আসবে
@bot.message_handler(func=lambda m: True)
def fallback(message):
    safe_send(message.chat.id, "👇 মেইন মেনু:", reply_markup=main_keyboard())


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
