import telebot, sqlite3, os
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
import threading

TOKEN = os.environ.get("BOT_TOKEN", "8665342292:AAE0WT6VGAYhfU_SfNtUvXUNJw-lfJG1HWo")
ADMIN = int(os.environ.get("ADMIN_ID", "8933985337"))
SUPPORT = "@workstoresuport"

bot = telebot.TeleBot(TOKEN, threaded=True)
app = Flask(__name__)

# --- DB ---
def db():
    con = sqlite3.connect("store.db", check_same_thread=False)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, bal REAL DEFAULT 4, total_buy REAL DEFAULT 0)")
    cur.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY, name TEXT, price REAL, stock INTEGER DEFAULT 0, emoji TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS stocks (id INTEGER PRIMARY KEY AUTOINCREMENT, pid INTEGER, data TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS deposits (id INTEGER PRIMARY KEY, name TEXT, number TEXT, active INTEGER DEFAULT 1)")
    cur.execute("SELECT COUNT(*) FROM products")
    if cur.fetchone()[0]==0:
        cur.execute("INSERT INTO products (name, price, stock, emoji) VALUES ('নর্ড ভিপিএন (৭ দিন)', 25, 0, '🛡️')")
        cur.execute("INSERT INTO products (name, price, stock, emoji) VALUES ('হটমেইল (বিভিন্ন কোয়ালিটি)', 1.00, 106602, '🧚')")
        cur.execute("INSERT INTO products (name, price, stock, emoji) VALUES ('জি-মেইল অ্যাকাউন্ট', 20, 0, '📧')")
        cur.execute("INSERT INTO products (name, price, stock, emoji) VALUES ('জেমিনি প্রো (১৮ মাস)', 63.05, 15406, 'G')")
    cur.execute("SELECT COUNT(*) FROM deposits")
    if cur.fetchone()[0]==0:
        cur.execute("INSERT INTO deposits (name, number) VALUES ('বিকাশ (পার্সোনাল Send Money)', '017XXXXXXXX')")
        cur.execute("INSERT INTO deposits (name, number) VALUES ('নগদ (পার্সোনাল Send Money)', '018XXXXXXXX')")
        cur.execute("INSERT INTO deposits (name, number) VALUES ('বিটগেট USDT BEP20 (অটো)', '0xXXXXXXXX')")
    con.commit()
    return con

# --- কিবোর্ড (তোমার ছবির মতো কালার) ---
def user_kb():
    # Bot API 7.0+ style: success=সবুজ, primary=নীল, danger=লাল
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🛒 পণ্য কিনুন", style="success"), KeyboardButton("💰 ডিপোজিট করুন", style="primary"))
    markup.add(KeyboardButton("💰 আমার ব্যালেন্স", style="primary"), KeyboardButton("📋 মূল্য তালিকা", style="success"))
    markup.add(KeyboardButton("👤 কাস্টমার সাপোর্ট", style="primary"), KeyboardButton("⚠️ রিফ্রেশমেন্ট", style="success"))
    return markup

def admin_kb():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("➕ প্রোডাক্ট এড", style="success"), KeyboardButton("🗑️ প্রোডাক্ট ডিলিট", style="danger"))
    markup.add(KeyboardButton("📥 স্টক এড", style="success"), KeyboardButton("📤 স্টক ডিলিট", style="danger"))
    markup.add(KeyboardButton("💳 ডিপোজিট নাম্বার এড", style="primary"), KeyboardButton("❌ ডিপোজিট ডিলিট", style="danger"))
    markup.add(KeyboardButton("🔙 ইউজার মোড", style="default"))
    return markup

# তোমার ছবির মতো মূল্য তালিকা মেসেজ
def price_list_text():
    con = db()
    cur = con.cursor()
    cur.execute("SELECT name, price, stock, emoji FROM products")
    rows = cur.fetchall()
    txt = "📢 <b>অফিসিয়াল স্টোর মূল্য তালিকা</b> G\n"
    txt += "━━━━━━━━━━━━━━━━━━━━\n\n"
    for name, price, stock, emoji in rows:
        # স্টক 0 হলে লাল ইমোজি ইফেক্ট, স্টক থাকলে সবুজ
        status = f"{stock} টি এভেইলেবল" if stock>0 else "0 টি এভেইলেবল"
        txt += f"{emoji} <b>{name}</b>\n"
        txt += f"├ 🚀 মূল্য: {price} ৳\n"
        txt += f"└ 👀 স্টক: {status}\n\n"
    txt += "━━━━━━━━━━━━━━━━━━━━\n"
    txt += "<i>⚡ অর্ডার করার সাথে সাথে ইনস্ট্যান্ট ডেলিভারি পেয়ে যাবেন!</i>"
    return txt

# --- কমান্ড ---
@bot.message_handler(commands=['start'])
def start(m):
    con = db()
    cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO users (id) VALUES (?)", (m.from_user.id,))
    cur.execute("SELECT bal FROM users WHERE id=?", (m.from_user.id,))
    bal = cur.fetchone()[0]
    con.close()
    bot.send_message(m.chat.id, f"✈️ <b>স্বাগতম, {m.from_user.first_name}!</b>\n━━━━━━━━━━━━\nP2W আপনার ব্যালেন্স: <b>{bal} ৳</b>", reply_markup=user_kb(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text=="💰 আমার ব্যালেন্স")
def my_bal(m):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT bal, total_buy FROM users WHERE id=?", (m.from_user.id,))
    bal, total = cur.fetchone()
    txt = f"P2W <b>অ্যাকাউন্ট ব্যালেন্স / ওয়ালেট</b> G\n━━━━━━━━━━━━\n"
    txt += f"• ইউজার আইডি: {m.from_user.id}\n"
    txt += f"✈️ বর্তমান ব্যালেন্স: {bal} ৳\n"
    txt += f"🛒 টোটাল বাই: {total} ৳\n"
    txt += f"👀 আজকের বাই: 0 ৳ (0 টি)\n\n"
    txt += f"💳 ব্যালেন্স রিচার্জ করতে নিচের ডিপোজিট বাটনে ক্লিক করুন!"
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("💰 ডিপোজিট করুন", callback_data="go_deposit", style="primary"))
    markup.add(InlineKeyboardButton("🛒 পণ্য কিনুন", callback_data="go_buy", style="success"))
    bot.send_message(m.chat.id, txt, reply_markup=markup, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text=="📋 মূল্য তালিকা")
def price_list(m):
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🛒 পণ্য কিনুন", callback_data="go_buy", style="success"), InlineKeyboardButton("💰 ডিপোজিট করুন", callback_data="go_deposit", style="primary"))
    bot.send_message(m.chat.id, price_list_text(), reply_markup=markup, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text=="💰 ডিপোজিট করুন")
def deposit(m):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT name, number FROM deposits WHERE active=1")
    rows = cur.fetchall()
    con.close()
    txt = "💰 <b>ডিপোজিট / ব্যালেন্স রিচার্জ (Send Money)</b> G\n━━━━━━━━━━━━\n"
    txt += "আমাদের পার্সোনাল নাম্বারে Send Money করে স্বয়ংক্রিয়ভাবে ব্যালেন্স যোগ করুন:\n\n"
    for name, num in rows:
        txt += f"📌 {name}: {num}\n"
    txt += "\n🔗 পছন্দের মাধ্যমে ক্লিক করুন:"
    markup = InlineKeyboardMarkup()
    for name, num in rows:
        # যদি নগদ না থাকে, তাহলে বাটন আসবে না - তোমার চাওয়া মতো
        markup.add(InlineKeyboardButton(f"{name}", callback_data=f"dep_{name}", style="success"))
    bot.send_message(m.chat.id, txt, reply_markup=markup, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text=="🛒 পণ্য কিনুন")
def buy_menu(m):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, name, price, stock FROM products")
    rows = cur.fetchall()
    con.close()
    markup = InlineKeyboardMarkup()
    for pid, name, price, stock in rows:
        # এখানেই লাল/সবুজ লজিক - তোমার ছবির মতো
        if stock <= 0:
            markup.add(InlineKeyboardButton(f"{name} - {price}৳ (স্টক শেষ)", callback_data=f"buy_{pid}", style="danger"))
        else:
            markup.add(InlineKeyboardButton(f"{name} - {price}৳ ({stock} টি)", callback_data=f"buy_{pid}", style="success"))
    bot.send_message(m.chat.id, "👇 পণ্য সিলেক্ট করুন:", reply_markup=markup)

# অটো ডেলিভারি - এটাই মেইন
@bot.callback_query_handler(func=lambda c: c.data.startswith("buy_"))
def auto_delivery(c):
    pid = int(c.data.split("_")[1])
    con = db()
    cur = con.cursor()
    cur.execute("SELECT name, price, stock FROM products WHERE id=?", (pid,))
    name, price, stock = cur.fetchone()
    cur.execute("SELECT bal FROM users WHERE id=?", (c.from_user.id,))
    bal = cur.fetchone()[0]

    if stock <= 0:
        bot.answer_callback_query(c.id, "❌ স্টক শেষ! বাটন লাল হয়ে গেছে", show_alert=True)
        return
    if bal < price:
        bot.answer_callback_query(c.id, f"❌ ব্যালেন্স কম! লাগবে {price}৳, আছে {bal}৳", show_alert=True)
        return

    cur.execute("SELECT id, data FROM stocks WHERE pid=? LIMIT 1", (pid,))
    s = cur.fetchone()
    if not s:
        bot.answer_callback_query(c.id, "❌ স্টক ফাইলে মাল নেই, এডমিনকে বলো", show_alert=True)
        return

    # ডেলিভারি
    cur.execute("DELETE FROM stocks WHERE id=?", (s[0],))
    cur.execute("UPDATE products SET stock=stock-1 WHERE id=?", (pid,))
    cur.execute("UPDATE users SET bal=bal-?, total_buy=total_buy+? WHERE id=?", (price, price, c.from_user.id))
    con.commit()
    con.close()
    bot.send_message(c.message.chat.id, f"✅ <b>অর্ডার সফল!</b>\n\n📦 {name}\n🔑 প্রোডাক্ট:\n<code>{s[1]}</code>\n\n💰 বাকি ব্যালেন্স: {bal-price} ৳", parse_mode="HTML")

# --- এডমিন প্যানেল ---
@bot.message_handler(commands=['admin'])
def admin(m):
    if m.from_user.id!= ADMIN: return
    bot.send_message(m.chat.id, "🔧 <b>এডমিন প্যানেল</b>\nএখান থেকে সব কন্ট্রোল", reply_markup=admin_kb(), parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text=="➕ প্রোডাক্ট এড" and m.from_user.id==ADMIN)
def add_prod_ask(m):
    bot.send_message(m.chat.id, "ফরম্যাটে লেখো:\nনাম | দাম | ইমোজি\nযেমন:\nপান্ডা ভিপিএন ৭ দিন | 22 | 🐼")
    bot.register_next_step_handler(m, add_prod_save)

def add_prod_save(m):
    try:
        name, price, emoji = [x.strip() for x in m.text.split("|")]
        con = db()
        con.cursor().execute("INSERT INTO products (name, price, emoji) VALUES (?,?,?)", (name, float(price), emoji))
        con.commit()
        con.close()
        bot.send_message(m.chat.id, f"✅ প্রোডাক্ট এড হলো: {name}", reply_markup=admin_kb())
    except:
        bot.send_message(m.chat.id, "❌ ফরম্যাট ভুল")

@bot.message_handler(func=lambda m: m.text=="📥 স্টক এড" and m.from_user.id==ADMIN)
def stock_ask(m):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, name FROM products")
    rows = cur.fetchall()
    con.close()
    markup = InlineKeyboardMarkup()
    for pid, name in rows:
        markup.add(InlineKeyboardButton(name, callback_data=f"addstock_{pid}", style="primary"))
    bot.send_message(m.chat.id, "কোন প্রোডাক্টে স্টক যোগ করবে?", reply_markup=markup)

@bot.callback_query_handler(func=lambda c: c.data.startswith("addstock_"))
def stock_save_ask(c):
    pid = int(c.data.split("_")[1])
    bot.send_message(c.message.chat.id, f"ID {pid} এর জন্য স্টক পাঠাও, এক লাইনে একটা করে।")
    bot.register_next_step_handler(c.message, lambda m: stock_save(m, pid))

def stock_save(m, pid):
    lines = [l.strip() for l in m.text.split("\n") if l.strip()]
    con = db()
    cur = con.cursor()
    for l in lines:
        cur.execute("INSERT INTO stocks (pid, data) VALUES (?,?)", (pid, l))
    cur.execute("UPDATE products SET stock=stock+? WHERE id=?", (len(lines), pid))
    con.commit()
    con.close()
    bot.send_message(m.chat.id, f"✅ {len(lines)} টা স্টক এড হয়েছে, এখন বাটন সবুজ হয়ে যাবে", reply_markup=admin_kb())

@app.route('/')
def home(): return "Work Store Bot Running"

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()
    bot.infinity_polling(none_stop=True)
