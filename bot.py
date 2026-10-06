import os
import sqlite3
from telebot import TeleBot, types

# ----------------- CONFIGURATION -----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8665342292:AAE0WT6VGAYhfU_SfNtUvXUNJw-lfJG1HWo")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8933985337"))  # আপনার টেলিগ্রাম আইডি দিন

bot = TeleBot(BOT_TOKEN, parse_mode="Markdown")

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

# Pending Deposits Table
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
    
    cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    
    if not user:
        cursor.execute("INSERT INTO users (user_id, name, balance) VALUES (?, ?, ?)", (user_id, name, 0.0))
        conn.commit()
        balance = 0.0
    else:
        balance = user[0]

    text = f"স্বাগতম, {name}!\n\nP2W তে আপনার ব্যালেন্স: {balance} টাকা"
    bot.send_message(message.chat.id, text, reply_markup=get_user_keyboard())

# ৩. আমার অ্যাকাউন্ট (Row 3, Button 1)
@bot.message_handler(func=lambda message: message.text == "👤 আমার অ্যাকাউন্ট")
def show_account(message):
    user_id = message.from_user.id
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
        bot.send_message(message.chat.id, text, reply_markup=markup)

# ৪ & ৫. ডিজিটাল পণ্য ক্যাটাগরি ও লিস্ট
@bot.message_handler(func=lambda message: message.text == "🛍️ ডিজিটাল পণ্যগুলো")
def show_categories(message):
    cursor.execute("SELECT DISTINCT category FROM products")
    categories = cursor.fetchall()

    if not categories:
        bot.send_message(message.chat.id, "বর্তমানে কোনো পণ্য এভেলেবল নেই।")
        return

    markup = types.InlineKeyboardMarkup()
    for cat in categories:
        markup.add(types.InlineKeyboardButton(cat[0], callback_data=f"cat_{cat[0]}"))

    bot.send_message(message.chat.id, "🛒 **একটি ক্যাটাগরি নির্বাচন করুন:**", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("cat_"))
def show_products_in_category(call):
    category = call.data.split("cat_")[1]
    cursor.execute("SELECT id, name, price FROM products WHERE category = ?", (category,))
    products = cursor.fetchall()

    markup = types.InlineKeyboardMarkup()
    for prod in products:
        prod_id, name, price = prod
        # চেক স্টক
        cursor.execute("SELECT COUNT(*) FROM items WHERE product_id = ?", (prod_id,))
        stock = cursor.fetchone()[0]

        if stock > 0:
            btn_text = f"🟢 {name} - {price} টাকা ({stock} টি আছে)"
        else:
            btn_text = f"🔴 {name} - {price} টাকা (স্টক আউট)"

        markup.add(types.InlineKeyboardButton(btn_text, callback_data=f"prod_{prod_id}"))

    bot.edit_message_text("📦 **পণ্য নির্বাচন করুন:**", call.message.chat.id, call.message.message_id, reply_markup=markup)

# ৬. অ্যাড ব্যালেন্স (পেমেন্ট পদ্ধতি)
@bot.message_handler(func=lambda message: message.text == "💳 অ্যাড ব্যালেন্স")
def add_balance(message):
    cursor.execute("SELECT method_name, number FROM payment_methods")
    methods = cursor.fetchall()

    msg_text = "💳 **অ্যাড ব্যালেন্স পদ্ধতি (Send Money):**\n\n"
    if methods:
        for m in methods:
            msg_text += f"🔹 **{m[0]}:** `{m[1]}`\n"
        msg_text += "\nটাকা পাঠানোর পর নিচের যেকোনো বাটন চাপুন:"
    else:
        msg_text += "বর্তমানে কোনো পেমেন্ট পদ্ধতি যুক্ত করা হয়নি।"

    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton("🟢 বিকাশ (অটো/ম্যানুয়াল)", callback_data="pay_bkash"))
    markup.add(types.InlineKeyboardButton("🔴 নগদ (অটো/ম্যানুয়াল)", callback_data="pay_nagad"))
    markup.add(types.InlineKeyboardButton("🌐 USDT BEP20 (ক্রিপ্টো)", callback_data="pay_usdt"))

    bot.send_message(message.chat.id, msg_text, reply_markup=markup)

# ৭. সাপোর্ট এবং তথ্য
@bot.message_handler(func=lambda message: message.text == "📞 সাপোর্ট এবং নিয়ম")
def support_info(message):
    text = (
        "📞 **সহায়তা ও সাপোর্ট:**\n\n"
        "যেকোনো সমস্যায় আমাদের সাপোর্ট টিমে যোগাযোগ করুন:\n"
        "📩 **সাপোর্ট আইডি:** @workstoresuport\n\n"
        "⚠️ **সতর্কতা:** ভুল TrxID বা ভুল তথ্য দিলে একাউন্ট ব্লক হতে পারে।"
    )
    bot.send_message(message.chat.id, text)

# ----------------- ADMIN HANDLERS -----------------
@bot.message_handler(commands=['admin'])
def admin_panel(message):
    if message.from_user.id == ADMIN_ID:
        bot.send_message(message.chat.id, "🛠 **এডমিন প্যানেলে স্বাগতম:**", reply_markup=get_admin_keyboard())
    else:
        bot.send_message(message.chat.id, "❌ আপনার এই কমান্ড ব্যবহারের অনুমতি নেই।")

@bot.message_handler(func=lambda message: message.text == "🏠 ইউজার প্যানেল")
def back_to_user_panel(message):
    bot.send_message(message.chat.id, "🏠 ইউজার প্যানেলে ফিরে আসলেন।", reply_markup=get_user_keyboard())

# ----------------- BOT START -----------------
if __name__ == "__main__":
    print("Bot is running...")
    bot.infinity_polling(threaded=False)
