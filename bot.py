import sqlite3, requests, time
from flask import Flask
import threading

BOT_TOKEN = "8665342292:AAE0WT6VGAYhfU_SfNtUvXUNJw-lfJG1HWo" # <-- এখানে তোমার টোকেন
ADMIN_ID = 8933985337 # <-- তোমার Telegram ID ( @userinfobot এ গেলে পাবে )
BKASH_NUMBER = "017XXXXXXXX"
NAGAD_NUMBER = "018XXXXXXXX"

BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

# --- ডাটাবেজ ---
def init_db():
    con = sqlite3.connect("store.db")
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 10.15, name TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS deposits (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL, trx TEXT, status TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, name TEXT, price REAL, stock TEXT)")
    # ডেমো প্রোডাক্ট
    cur.execute("SELECT COUNT(*) FROM products")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO products (category, name, price, stock) VALUES ('VPN','Panda VPN 3 Days', 24, 'panda_3d_code_123')")
        cur.execute("INSERT INTO products (category, name, price, stock) VALUES ('VPN','Panda VPN 7 Days', 22, 'panda_7d_code_456')")
        cur.execute("INSERT INTO products (category, name, price, stock) VALUES ('Hotmail','Hotmail Live 12-36M', 1.00, 'hotmail_account')")
    con.commit()
    con.close()

def get_user(uid):
    con = sqlite3.connect("store.db")
    cur = con.cursor()
    cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    u = cur.fetchone()
    con.close()
    return u

def add_user(uid, name):
    con = sqlite3.connect("store.db")
    cur = con.cursor()
    cur.execute("INSERT OR IGNORE INTO users (user_id, name) VALUES (?,?)", (uid, name))
    con.commit()
    con.close()

def api_call(method, data):
    requests.post(f"{BASE_URL}/{method}", json=data)

def send_msg(chat_id, text, kb=None):
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if kb: data["reply_markup"] = kb
    api_call("sendMessage", data)

# --- কিবোর্ড (তোমার ভিডিওর মতো কালার) ---
REPLY_KB = {
    "keyboard": [
        [{"text": "🛒 পণ্য কিনুন", "style": "success"}, {"text": "💰 ডিপোজিট করুন", "style": "primary"}],
        [{"text": "💰 আমার ব্যালেন্স", "style": "primary"}, {"text": "📋 মূল্য তালিকা", "style": "success"}],
        [{"text": "👤 কাস্টমার সাপোর্ট", "style": "primary"}, {"text": "⚠️ রিফ্রেশমেন্ট", "style": "success"}]
    ], "resize_keyboard": True
}

ADMIN_KB = {
    "keyboard": [
        [{"text": "➕ ব্যালেন্স যোগ করুন", "style": "success"}, {"text": "📦 প্রোডাক্ট যোগ করুন", "style": "primary"}],
        [{"text": "📋 পেন্ডিং ডিপোজিট", "style": "danger"}, {"text": "👥 ইউজার লিস্ট", "style": "default"}],
        [{"text": "🔙 ইউজার মোড", "style": "primary"}]
    ], "resize_keyboard": True
}

def product_categories_kb():
    return {
        "inline_keyboard": [
            [{"text": "ভিপিএন (VPN)", "callback_data": "cat_VPN", "style": "success"}],
            [{"text": "হটমেইল (Hotmail)", "callback_data": "cat_Hotmail", "style": "success"}],
            [{"text": "আউটলুক (Outlook)", "callback_data": "cat_Outlook", "style": "success"}],
            [{"text": "জি-মেইল অ্যাকাউন্ট", "callback_data": "cat_Gmail", "style": "danger"}],
            [{"text": "এডু মেইল (Edu Mail)", "callback_data": "cat_Edu", "style": "success"}],
            [{"text": "ক্যাপকাট (CapCut)", "callback_data": "cat_CapCut", "style": "success"}],
            [{"text": "অন্যান্য সার্ভিস", "callback_data": "cat_Other", "style": "success"}],
            [{"text": "🔙 ফিরে যান", "callback_data": "back_start", "style": "primary"}]
        ]
    }

def vpn_duration_kb():
    return {
        "inline_keyboard": [
            [{"text": "৩ দিন ডিউরেশন (3 Days)", "callback_data": "buy_1", "style": "success"}],
            [{"text": "৭ দিন ডিউরেশন (7 Days) 22 টাকা", "callback_data": "buy_2", "style": "success"}],
            [{"text": "১৪ দিন ডিউরেশন (14 Days) 40 টাকা", "callback_data": "buy_3", "style": "success"}],
            [{"text": "৩০ দিন ডিউরেশন (30 Days) 79 টাকা", "callback_data": "buy_4", "style": "success"}],
            [{"text": "🔙 ফিরে যান", "callback_data": "cat_VPN", "style": "primary"}]
        ]
    }

# --- লজিক ---
init_db()
print("Bot Started...")

# Flask দিয়ে 24/7 রাখার জন্য
app = Flask(__name__)
@app.route('/')
def home(): return "Work Store Bot Running"

threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

offset = 0
while True:
    try:
        resp = requests.get(f"{BASE_URL}/getUpdates", params={"offset": offset, "timeout": 30}).json()
        for upd in resp.get("result", []):
            offset = upd["update_id"] + 1

            if "message" in upd:
                msg = upd["message"]
                chat_id = msg["chat"]["id"]
                uid = msg["from"]["id"]
                name = msg["from"].get("first_name", "User")
                text = msg.get("text", "")

                add_user(uid, name)
                user = get_user(uid)
                balance = user[1] if user else 10.15

                if text == "/start":
                    send_msg(chat_id, f"👤 স্বাগতম, {name}!\n\n💰 আপনার ব্যালেন্স: <b>{balance}৳</b>", REPLY_KB)

                elif text == "/admin" and uid == ADMIN_ID:
                    send_msg(chat_id, "🔧 <b>এডমিন প্যানেল</b>\n\nএখান থেকে সব কন্ট্রোল করতে পারবেন", ADMIN_KB)

                elif text == "🛒 পণ্য কিনুন":
                    send_msg(chat_id, "👇 সকল পণ্য তালিকা\nযা নিতে চান সিলেক্ট করুন:", product_categories_kb())

                elif text == "💰 আমার ব্যালেন্স":
                    send_msg(chat_id, f"💰 আপনার বর্তমান ব্যালেন্স: <b>{balance}৳</b>", REPLY_KB)

                elif text == "💰 ডিপোজিট করুন":
                    dep_kb = {
                        "inline_keyboard": [
                            [{"text": f"বিকাশ (Send Money) {BKASH_NUMBER}", "callback_data": "dep_bkash", "style": "success"}],
                            [{"text": f"নগদ (Send Money) {NAGAD_NUMBER}", "callback_data": "dep_nagad", "style": "success"}],
                            [{"text": "USDT BEP20", "callback_data": "dep_usdt", "style": "primary"}],
                            [{"text": "🔙 ফিরে যান", "callback_data": "back_start", "style": "primary"}]
                        ]
                    }
                    send_msg(chat_id, f"💵 <b>ডিপোজিট করুন</b>\n\nবিকাশ: {BKASH_NUMBER}\nনগদ: {NAGAD_NUMBER}\n\nটাকা পাঠিয়ে TrxID দিন", dep_kb)

                elif text == "👤 কাস্টমার সাপোর্ট":
                    send_msg(chat_id, "👤 সাপোর্ট: @YourSupportUsername\n\nযেকোনো সমস্যায় মেসেজ দিন", REPLY_KB)

                elif text.startswith("ADD_BALANCE") and uid == ADMIN_ID:
                    # ফরম্যাট: ADD_BALANCE 123456 100
                    try:
                        _, target_id, amount = text.split()
                        con = sqlite3.connect("store.db")
                        cur = con.cursor()
                        cur.execute("UPDATE users SET balance = balance +? WHERE user_id=?", (float(amount), int(target_id)))
                        con.commit()
                        con.close()
                        send_msg(chat_id, f"✅ {target_id} কে {amount}৳ যোগ করা হয়েছে", ADMIN_KB)
                        send_msg(int(target_id), f"✅ আপনার ব্যালেন্সে {amount}৳ যোগ হয়েছে!")
                    except: send_msg(chat_id, "❌ ফরম্যাট: ADD_BALANCE USER_ID AMOUNT")

            if "callback_query" in upd:
                cq = upd["callback_query"]
                chat_id = cq["message"]["chat"]["id"]
                data = cq["data"]
                uid = cq["from"]["id"]
                user = get_user(uid)
                balance = user[1] if user else 0

                if data.startswith("cat_"):
                    if "VPN" in data:
                        send_msg(chat_id, "⏳ ভিপিএন মেয়াদ নির্বাচন করুন", vpn_duration_kb())
                    else:
                        send_msg(chat_id, f"📦 {data} এর প্রোডাক্ট লোড হচ্ছে...", vpn_duration_kb())

                elif data.startswith("buy_"):
                    # প্রোডাক্ট কেনা
                    con = sqlite3.connect("store.db")
                    cur = con.cursor()
                    cur.execute("SELECT * FROM products WHERE id=?", (int(data.split("_")[1]),))
                    prod = cur.fetchone()
                    con.close()
                    if prod:
                        if balance >= prod[2]:
                            # টাকা কাটা
                            con = sqlite3.connect("store.db")
                            cur = con.cursor()
                            cur.execute("UPDATE users SET balance = balance -? WHERE user_id=?", (prod[2], uid))
                            con.commit()
                            con.close()
                            send_msg(chat_id, f"✅ <b>অর্ডার সফল!</b>\n\nপণ্য: {prod[1]}\nদাম: {prod[2]}৳\n\nআপনার প্রোডাক্ট: <code>{prod[3]}</code>\n\nবাকি ব্যালেন্স: {balance-prod[2]}৳", REPLY_KB)
                        else:
                            need = prod[2] - balance
                            kb = {"inline_keyboard": [[{"text": "💰 ডিপোজিট করুন", "callback_data": "go_deposit", "style": "danger"}], [{"text": "🔙 ফিরে যান", "callback_data": "cat_VPN", "style": "primary"}]]}
                            send_msg(chat_id, f"❌ <b>অপর্যাপ্ত ব্যালেন্স!</b>\n\nপণ্য: {prod[1]}\nদাম: {prod[2]}৳\nআপনার ব্যালেন্স: {balance}৳\n\nআরো {need}৳ ডিপোজিট করুন", kb)

    except Exception as e:
        print("Error:", e)
        time.sleep(2)
