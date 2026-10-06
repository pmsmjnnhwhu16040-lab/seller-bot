import os
import sqlite3
from telebot import TeleBot, types

# ----------------- CONFIGURATION -----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8665342292:AAE0WT6VGAYhfU_SfNtUvXUNJw-lfJG1HWo")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8933985337"))  # আপনার আইডি দিন
CHANNEL_USERNAME = "@A_ToolsX"  # আপনার চ্যানেলের ইউজারনেম (সঠিকটি দিন)
CHANNEL_URL = "https://t.me/A_ToolsX"

# bot instance (threaded=False আপনার রিকোয়ারমেন্ট অনুযায়ী)
bot = TeleBot(BOT_TOKEN, parse_mode="Markdown", threaded=False)

# ----------------- DATABASE SETUP -----------------
conn = sqlite3.connect("bot_database.db", check_same_thread=False)
cursor = conn.cursor()

# Users Table
cursor.execute('''
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    balance REAL DEFAULT 0.0,
    total_buy REAL DEFAULT 0.0,
    total_count INTEGER DEFAULT 0,
    today_buy REAL DEFAULT 0.0
)
''')

# Products Table
cursor.execute('''
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT,
    name TEXT,
    price REAL,
    details TEXT
)
''')

# Product Items (Stock) Table
cursor.execute('''
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER,
    item_data TEXT
)
''')

# Payment Methods Table
cursor.execute('''
CREATE TABLE IF NOT EXISTS payment_methods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    method_name TEXT,
    number TEXT
)
''')

# Deposits Table
cursor.execute('''
CREATE TABLE IF NOT EXISTS deposits (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    method TEXT,
    trx_id TEXT,
    amount REAL,
    status TEXT DEFAULT 'PENDING'
)
''')
conn.commit()

# ----------------- FORCE JOIN CHECKER -----------------
def is_user_joined(user_id):
    try:
        member = bot.get_chat_member(CHANNEL_USERNAME, user_id)
        if member.status in ['creator', 'administrator', 'member']:
            return True
        return False
    except Exception:
        # কোনো কারণে চেক করতে ব্যর্থ হলে বা চ্যানেলে বট এডমিন না থাকলে True ধরে নেওয়া হবে
        return True

def send_force_join_msg(chat_id):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("📢 Join Channel", url=CHANNEL_URL))
    markup.add(types.InlineKeyboardButton("✅ Joined / Check", callback_data="check_join"))
    
    # disable_web_page_preview=True দেওয়া হয়েছে যেন প্রিভিউ কার্ড না আসে
    bot.send_message(
        chat_id,
        f"🚀 **To use this bot, you must join our channel first:**\n{CHANNEL_URL}",
        reply_markup=markup,
        disable_web_page_preview=True
    )

# ----------------- KEYBOARDS -----------------
def get_user_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(types.KeyboardButton("🛍️ ডিজিটাল পণ্যগুলো"), types.KeyboardButton("👤 আমার অ্যাকাউন্ট"))
    markup.row(types.KeyboardButton("💳 অ্যাড ব্যালেন্স"), types.KeyboardButton("📜 ক্রয়ের ইতিহাস"))
    markup.row(types.KeyboardButton("📞 সাপোর্ট এবং নিয়ম"), types.KeyboardButton("📢 আমাদের টেলিগ্রাম চ্যানেল"))
    return markup

def get_admin_keyboard():
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row(types.KeyboardButton("➕ ক্যাটাগরি/পণ্য যোগ"), types.KeyboardButton("🗑️ পণ্য মুছুন"))
    markup.row(types.KeyboardButton("📦 স্টক যোগ করুন"), types.KeyboardButton("📊 বিক্রয়ের বিবরণী"), types.KeyboardButton("👥 ইউজার লিস্ট"))
    markup.row(types.KeyboardButton("💳 পেমেন্ট পদ্ধতি যোগ"), types.KeyboardButton("🗑️ পেমেন্ট পদ্ধতি মুছুন"), types.KeyboardButton("⏳ ডিপোজিট রিকোয়েস্ট"))
    markup.row(types.KeyboardButton("💰 ম্যানুয়াল ব্যালেন্স যোগ/বিয়োগ"), types.KeyboardButton("📢 ব্রডকাস্ট মেসেজ"))
    markup.row(types.KeyboardButton("⚙️ বটের সেটিংস"), types.KeyboardButton("🏠 ইউজার প্যানেল"))
    return markup

# ----------------- USER HANDLERS -----------------
@bot.message_handler(commands=['start'])
def send_welcome(message):
    user_id = message.from_user.id
    name = message.from_user.first_name

    # Force Join Check
    if not is_user_joined(user_id):
        send_force_join_msg(message.chat.id)
        return

    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()

    if not user:
        cursor.execute("INSERT INTO users (user_id, name, balance) VALUES (?, ?, ?)", (user_id, name, 0.0))
        conn.commit()
        balance = 0.0
    else:
        balance = user[0]

    text = f"স্বাগতম, {name}!\n\nP2W তে আপনার ব্যালেন্স: {balance} টাকা"
    bot.send_message(message.chat.id, text, reply_markup=get_user_keyboard(), disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda call: call.data == "check_join")
def check_join_callback(call):
    if is_user_joined(call.from_user.id):
        bot.delete_message(call.message.chat.id, call.message.message_id)
        bot.send_message(call.message.chat.id, "✅ ধন্যবাদ! আপনি জয়েন করেছেন। এখন বট ব্যবহার করতে পারেন।", reply_markup=get_user_keyboard())
    else:
        bot.answer_callback_query(call.id, "❌ আপনি এখনো চ্যানেলে জয়েন করেননি!", show_alert=True)

# ৩. আমার অ্যাকাউন্ট
@bot.message_handler(func=lambda message: message.text == "👤 আমার অ্যাকাউন্ট")
def show_account(message):
    user_id = message.from_user.id
    if not is_user_joined(user_id):
        send_force_join_msg(message.chat.id)
        return

    cursor.execute("SELECT name, balance, total_buy, total_count, today_buy FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()

    if user:
        text = (
            "👤 **আপনার অ্যাকাউন্ট বিবরণী:**\n\n"
            f"🆔 **আপনার আইডি:** `{user_id}`\n"
            f"👤 **নাম:** {user[0]}\n"
            f"💰 **বর্তমান ব্যালেন্স:** {user[1]} টাকা\n"
            f"🛒 **মোট ক্রয়:** {user[2]} টাকা ({user[3]} টি পণ্য)\n"
            f"📅 **আজকের ক্রয়:** {user[4]} টাকা"
        )
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("💳 অ্যাড ব্যালেন্স", callback_data="add_balance_inline"))
        markup.add(types.InlineKeyboardButton("📜 ক্রয়ের ইতিহাস", callback_data="buy_history_inline"))
        bot.send_message(message.chat.id, text, reply_markup=markup, disable_web_page_preview=True)

# ৪ & ৫. ডিজিটাল পণ্যগুলো
@bot.message_handler(func=lambda message: message.text == "🛍️ ডিজিটাল পণ্যগুলো")
def show_categories(message):
    if not is_user_joined(message.from_user.id):
        send_force_join_msg(message.chat.id)
        return

    cursor.execute("SELECT DISTINCT category FROM products")
    categories = cursor.fetchall()

    if not categories:
        bot.send_message(message.chat.id, "বর্তমানে কোনো পণ্য পাওয়া যায়নি।")
        return

    markup = types.InlineKeyboardMarkup()
    for cat in categories:
        markup.add(types.InlineKeyboardButton(cat[0], callback_data=f"cat_{cat[0]}"))

    bot.send_message(message.chat.id, "🛒 **একটি ক্যাটাগরি নির্বাচন করুন:**", reply_markup=markup, disable_web_page_preview=True)

@bot.callback_query_handler(func=lambda call: call.data.startswith("cat_"))
def show_products_in_category(call):
    category = call.data.split("cat_")[1]
    cursor.execute("SELECT id, name, price FROM products WHERE category = ?", (category,))
    products = cursor.fetchall()

    markup = types.InlineKeyboardMarkup()
    for prod in products:
        prod_id, name, price = prod
        cursor.execute("SELECT COUNT(*) FROM items WHERE product_id = ?", (prod_id,))
        stock = cursor.fetchone()[0]

        if stock > 0:
            btn_text = f"🟢 {name} - {price} টাকা ({stock} টি আছে)"
        else:
            btn_text = f"🔴 {name} - {price} টাকা (স্টক আউট)"

        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"prod_{prod_id}"))

    bot.edit_message_text("📦 **পণ্য নির্বাচন করুন:**", call.message.chat.id, call.message.message_id, reply_markup=markup)

# ৬. অ্যাড ব্যালেন্স
@bot.message_handler(func=lambda message: message.text == "💳 অ্যাড ব্যালেন্স")
def add_balance(message):
    if not is_user_joined(message.from_user.id):
        send_force_join_msg(message.chat.id)
        return

    cursor.execute("SELECT method_name, number FROM payment_methods")
    methods = cursor.fetchall()

    msg_text = "💳 **অ্যাড ব্যালেন্স (Send Money):**\n\n"
    if methods:
        for m in methods:
            msg_text += f"🔹 **{m[0]}:** `{m[1]}`\n"
        msg_text += "\nটাকা পাঠানোর পর নিচের বাটনে ক্লিক করে TrxID পাঠ পাঠান:"
    else:
        msg_text += "বর্তমানে কোনো পেমেন্ট পদ্ধতি যুক্ত করা হয়নি।"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🟢 বিকাশ (ম্যানুয়াল)", callback_data="pay_bkash"))
    markup.add(types.InlineKeyboardButton("🔴 নগদ (ম্যানুয়াল)", callback_data="pay_nagad"))
    markup.add(types.InlineKeyboardButton("🌐 USDT BEP20 (ক্রিপ্টো)", callback_data="pay_usdt"))

    bot.send_message(message.chat.id, msg_text, reply_markup=markup, disable_web_page_preview=True)

# ৭. সাপোর্ট এবং নিয়ম
@bot.message_handler(func=lambda message: message.text == "📞 সাপোর্ট এবং নিয়ম")
def support_info(message):
    if not is_user_joined(message.from_user.id):
        send_force_join_msg(message.chat.id)
        return

    text = (
        "📞 **সহায়তা ও সাপোর্ট:**\n\n"
        "যেকোনো তথ্যের জন্য আমাদের চ্যানেলে জয়েন রাখুন এবং সহায়তার জন্য সাপোর্ট টিমে মেসেজ দিন।\n"
        "📩 **সাপোর্ট আইডি:** @workstoresuport\n"
        "📢 **চ্যানেল:** https://t.me/A_ToolsX\n\n"
        "⚠️ **সতর্কতা:** ভুল তথ্য দিলে আপনার অ্যাকাউন্ট ব্লক হতে পারে।"
    )
    bot.send_message(message.chat.id, text, disable_web_page_preview=True)

@bot.message_handler(func=lambda message: message.text == "📢 আমাদের টেলিগ্রাম চ্যানেল")
def channel_info(message):
    bot.send_message(
        message.chat.id,
        f"📢 আমাদের অফিশিয়াল চ্যানেলে জয়েন হতে নিচের লিংকে ক্লিক করুন:\n{CHANNEL_URL}",
        disable_web_page_preview=True
    )

# ----------------- ADMIN PANEL -----------------
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id == ADMIN_ID:
        bot.send_message(message.chat.id, "🛠 **এডমিন প্যানেলে স্বাগতম:**", reply_markup=get_admin_keyboard(), disable_web_page_preview=True)
    else:
        bot.send_message(message.chat.id, "❌ আপনার এই কমান্ড ব্যবহারের অনুমতি নেই।", disable_web_page_preview=True)

@bot.message_handler(func=lambda message: message.text == "🏠 ইউজার প্যানেল")
def back_to_user_panel(message):
    bot.send_message(message.chat.id, "🏠 ইউজার প্যানেলে ফিরে আসলেন।", reply_markup=get_user_keyboard(), disable_web_page_preview=True)

# ----------------- BOT START -----------------
if __name__ == "__main__":
    print("Bot is running successfully...")
    bot.infinity_polling(threaded=False)
