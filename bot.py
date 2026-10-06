import sqlite3, requests, time, os
from flask import Flask
import threading

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8665342292:AAE0WT6VGAYhfU_SfNtUvXUNJw-lfJG1HWo")
ADMIN_ID = int(os.environ.get("ADMIN_ID", "8933985337"))
BASE_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

admin_state = {}

def init_db():
    con = sqlite3.connect("store.db")
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0, name TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, style TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS products (id INTEGER PRIMARY KEY AUTOINCREMENT, cat_id INTEGER, name TEXT, price REAL, stock_count INTEGER DEFAULT 0)")
    cur.execute("CREATE TABLE IF NOT EXISTS stocks (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER, data TEXT)")
    cur.execute("CREATE TABLE IF NOT EXISTS deposit_methods (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, number TEXT, is_active INTEGER DEFAULT 1)")
    cur.execute("CREATE TABLE IF NOT EXISTS deposits (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, amount REAL, trx TEXT, status TEXT DEFAULT 'pending')")
    # ডিফল্ট
    cur.execute("SELECT COUNT(*) FROM categories")
    if cur.fetchone()[0]==0:
        cur.execute("INSERT INTO categories (name, style) VALUES ('ভিপিএন (VPN)', 'success')")
        cur.execute("INSERT INTO categories (name, style) VALUES ('হটমেইল (Hotmail)', 'success')")
        cur.execute("INSERT INTO categories (name, style) VALUES ('জি-মেইল অ্যাকাউন্ট', 'danger')")
    cur.execute("SELECT COUNT(*) FROM deposit_methods")
    if cur.fetchone()[0]==0:
        cur.execute("INSERT INTO deposit_methods (name, number) VALUES ('বিকাশ', '017XXXXXXXX')")
    con.commit()
    con.close()

def api(method, data):
    requests.post(f"{BASE_URL}/{method}", json=data, timeout=20)

def send(chat_id, text, kb=None):
    d = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if kb: d["reply_markup"] = kb
    api("sendMessage", d)

# --- কিবোর্ড ---
def user_reply_kb():
    return {"keyboard": [[{"text": "🛒 পণ্য কিনুন", "style": "success"}, {"text": "💰 ডিপোজিট করুন", "style": "primary"}], [{"text": "💰 আমার ব্যালেন্স", "style": "primary"}, {"text": "📋 মূল্য তালিকা", "style": "success"}], [{"text": "👤 কাস্টমার সাপোর্ট", "style": "primary"}, {"text": "⚠️ রিফ্রেশমেন্ট", "style": "success"}]], "resize_keyboard": True}

def admin_reply_kb():
    return {"keyboard": [
        [{"text": "➕ ক্যাটাগরি", "style": "success"}, {"text": "➕ প্রোডাক্ট", "style": "primary"}, {"text": "🗑️ প্রোডাক্ট ডিলিট", "style": "danger"}],
        [{"text": "📥 স্টক যোগ", "style": "success"}, {"text": "📤 স্টক খালি", "style": "danger"}],
        [{"text": "💳 ডিপোজিট নাম্বার", "style": "primary"}, {"text": "💰 পেন্ডিং ডিপোজিট", "style": "success"}],
        [{"text": "🔙 ইউজার মোড", "style": "default"}]
    ], "resize_keyboard": True}

# ইউজার প্রোডাক্ট লিস্ট - স্টক শেষ হলে লাল
def get_categories_kb():
    con = sqlite3.connect("store.db")
    cur = con.cursor()
    cur.execute("SELECT id, name FROM categories")
    cats = cur.fetchall()
    con.close()
    kb = []
    for cid, cname in cats:
        # ক্যাটাগরির ভিতরে কোনো প্রোডাক্টে স্টক আছে কিনা চেক করব না, ক্যাটাগরি সবসময় সবুজ
        kb.append([{"text": cname, "callback_data": f"cat_{cid}", "style": "success"}])
    kb.append([{"text": "🔙 ফিরে যান", "callback_data": "back", "style": "primary"}])
    return {"inline_keyboard": kb}

def get_products_by_cat_kb(cat_id):
    con = sqlite3.connect("store.db")
    cur = con.cursor()
    cur.execute("SELECT id, name, price, stock_count FROM products WHERE cat_id=?", (cat_id,))
    prods = cur.fetchall()
    con.close()
    kb = []
    for pid, pname, price, stock in prods:
        if stock <= 0:
            # স্টক শেষ - লাল বাটন তোমার ছবির মতো
            kb.append([{"text": f"{pname} - {price}৳ (স্টক শেষ)", "callback_data": f"buy_{pid}", "style": "danger"}])
        else:
            # স্টক আছে - সবুজ বাটন
            kb.append([{"text": f"{pname} - {price}৳ ({stock} পিস)", "callback_data": f"buy_{pid}", "style": "success"}])
    kb.append([{"text": "🔙 ফিরে যান", "callback_data": "back_cat", "style": "primary"}])
    return {"inline_keyboard": kb}

def get_deposit_kb():
    con = sqlite3.connect("store.db")
    cur = con.cursor()
    cur.execute("SELECT name, number FROM deposit_methods WHERE is_active=1")
    methods = cur.fetchall()
    con.close()
    kb = []
    for name, number in methods:
        kb.append([{"text": f"{name} - {number}", "callback_data": f"dep_{name}", "style": "success"}])
    if not kb:
        kb.append([{"text": "❌ কোনো ডিপোজিট মাধ্যম নেই", "callback_data": "none", "style": "danger"}])
    kb.append([{"text": "🔙 ফিরে যান", "callback_data": "back", "style": "primary"}])
    return {"inline_keyboard": kb}

# --- RUN ---
init_db()
app = Flask(__name__)
@app.route('/')
def home(): return "SK DIGITAL MARKET - Running"
threading.Thread(target=lambda: app.run(host='0.0.0.0', port=8080)).start()

offset = 0
while True:
    try:
        r = requests.get(f"{BASE_URL}/getUpdates", params={"offset": offset, "timeout": 30}).json()
        for upd in r.get("result", []):
            offset = upd["update_id"]+1
            if "message" in upd:
                m = upd["message"]
                chat_id, uid = m["chat"]["id"], m["from"]["id"]
                text = m.get("text","")
                name = m["from"].get("first_name","User")

                con = sqlite3.connect("store.db")
                cur = con.cursor()
                cur.execute("INSERT OR IGNORE INTO users (user_id, name, balance) VALUES (?,?,10.15)", (uid, name))
                con.commit()
                cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
                bal = cur.fetchone()[0]
                con.close()

                # এডমিন স্টেট
                if uid==ADMIN_ID and uid in admin_state:
                    st = admin_state[uid]
                    if st["step"]=="add_cat":
                        con=sqlite3.connect("store.db")
                        con.cursor().execute("INSERT INTO categories (name, style) VALUES (?, 'success')", (text,))
                        con.commit()
                        con.close()
                        send(chat_id, f"✅ ক্যাটাগরি যোগ হলো: {text}", admin_reply_kb())
                        del admin_state[uid]
                        continue
                    elif st["step"]=="add_prod_name":
                        admin_state[uid]["pname"]=text
                        admin_state[uid]["step"]="add_prod_price"
                        send(chat_id, "এবার দাম লেখো (যেমন 22)")
                        continue
                    elif st["step"]=="add_prod_price":
                        try:
                            price=float(text)
                            s=admin_state[uid]
                            con=sqlite3.connect("store.db")
                            con.cursor().execute("INSERT INTO products (cat_id, name, price) VALUES (?,?,?)", (s["cat_id"], s["pname"], price))
                            con.commit()
                            con.close()
                            send(chat_id, f"✅ প্রোডাক্ট যোগ: {s['pname']} - {price}৳", admin_reply_kb())
                            del admin_state[uid]
                        except: send(chat_id, "❌ দাম সংখ্যায় লেখো")
                        continue
                    elif st["step"]=="add_stock":
                        pid=st["pid"]
                        lines=[l.strip() for l in text.split("\n") if l.strip()]
                        con=sqlite3.connect("store.db")
                        cur=con.cursor()
                        for l in lines:
                            cur.execute("INSERT INTO stocks (product_id, data) VALUES (?,?)", (pid, l))
                        cur.execute("UPDATE products SET stock_count=stock_count+? WHERE id=?", (len(lines), pid))
                        con.commit()
                        con.close()
                        send(chat_id, f"✅ {len(lines)} টা স্টক যোগ হয়েছে, এখন বাটন সবুজ হয়ে যাবে", admin_reply_kb())
                        del admin_state[uid]
                        continue
                    elif st["step"]=="add_deposit_name":
                        admin_state[uid]["dname"]=text
                        admin_state[uid]["step"]="add_deposit_number"
                        send(chat_id, f"{text} এর নাম্বার লেখো (যেমন 017XXXXXXXX)")
                        continue
                    elif st["step"]=="add_deposit_number":
                        s=admin_state[uid]
                        con=sqlite3.connect("store.db")
                        con.cursor().execute("INSERT INTO deposit_methods (name, number) VALUES (?,?)", (s["dname"], text))
                        con.commit()
                        con.close()
                        send(chat_id, f"✅ ডিপোজিট মাধ্যম যোগ: {s['dname']} - {text}\nএখন ইউজার ডিপোজিটে এই অপশন দেখবে", admin_reply_kb())
                        del admin_state[uid]
                        continue

                if text=="/start":
                    send(chat_id, f"👤 স্বাগতম, {name}!\n\n💰 ব্যালেন্স: <b>{bal}৳</b>", user_reply_kb())
                elif text=="/admin" and uid==ADMIN_ID:
                    send(chat_id, "🔧 <b>এডমিন প্যানেল</b>\n\n➕ ক্যাটাগরি: নতুন বাটন বানাও\n➕ প্রোডাক্ট: পণ্যের নাম+দাম\n🗑️ ডিলিট: প্রোডাক্ট মুছে ফেলো\n📥 স্টক: স্টক যোগ করলে বাটন সবুজ, 0 হলে লাল\n💳 ডিপোজিট: বিকাশ/নগদ নাম্বার এড/ডিলিট", admin_reply_kb())
                elif text=="🛒 পণ্য কিনুন":
                    send(chat_id, "👇 সকল পণ্য তালিকা\nযা নিতে চান সিলেক্ট করুন:", get_categories_kb())
                elif text=="💰 আমার ব্যালেন্স":
                    send(chat_id, f"💰 ব্যালেন্স: <b>{bal}৳</b>", user_reply_kb())
                elif text=="💰 ডিপোজিট করুন":
                    send(chat_id, "💵 <b>ডিপোজিট মাধ্যম সিলেক্ট করুন</b>\nটাকা পাঠিয়ে TrxID দিন:", get_deposit_kb())
                elif text=="➕ ক্যাটাগরি" and uid==ADMIN_ID:
                    admin_state[uid]={"step":"add_cat"}
                    send(chat_id, "নতুন ক্যাটাগরির নাম লেখো (যেমন: ভিপিএন (VPN))")
                elif text=="➕ প্রোডাক্ট" and uid==ADMIN_ID:
                    con=sqlite3.connect("store.db")
                    cur=con.cursor()
                    cur.execute("SELECT id, name FROM categories")
                    cats=cur.fetchall()
                    con.close()
                    kb={"inline_keyboard": [[{"text": c[1], "callback_data": f"aprod_{c[0]}", "style": "success"}] for c in cats]}
                    send(chat_id, "কোন ক্যাটাগরিতে প্রোডাক্ট যোগ করবে?", kb)
                elif text=="🗑️ প্রোডাক্ট ডিলিট" and uid==ADMIN_ID:
                    con=sqlite3.connect("store.db")
                    cur=con.cursor()
                    cur.execute("SELECT id, name FROM products")
                    prods=cur.fetchall()
                    con.close()
                    kb={"inline_keyboard": [[{"text": f"🗑️ {p[1]}", "callback_data": f"delprod_{p[0]}", "style": "danger"}] for p in prods]}
                    send(chat_id, "কোন প্রোডাক্ট ডিলিট করবে?", kb)
                elif text=="📥 স্টক যোগ" and uid==ADMIN_ID:
                    con=sqlite3.connect("store.db")
                    cur=con.cursor()
                    cur.execute("SELECT id, name, stock_count FROM products")
                    prods=cur.fetchall()
                    con.close()
                    kb={"inline_keyboard": [[{"text": f"{p[1]} ({p[2]} পিস)", "callback_data": f"addstock_{p[0]}", "style": "primary"}] for p in prods]}
                    send(chat_id, "কোন প্রোডাক্টে স্টক যোগ করবে?", kb)
                elif text=="💳 ডিপোজিট নাম্বার" and uid==ADMIN_ID:
                    con=sqlite3.connect("store.db")
                    cur=con.cursor()
                    cur.execute("SELECT id, name, number FROM deposit_methods")
                    meths=cur.fetchall()
                    con.close()
                    txt="💳 <b>বর্তমান ডিপোজিট নাম্বার:</b>\n"
                    for mid, mname, mnum in meths:
                        txt+=f"{mid}. {mname} - {mnum}\n"
                    txt+="\nনতুন যোগ করতে নিচের বাটন চাপো, ডিলিট করতে /del_deposit ID লেখো (যেমন /del_deposit 1)"
                    kb={"inline_keyboard": [[{"text": "➕ নতুন নাম্বার যোগ", "callback_data": "add_deposit", "style": "success"}]]}
                    send(chat_id, txt, kb)

            if "callback_query" in upd:
                cq=upd["callback_query"]
                chat_id, uid, data = cq["message"]["chat"]["id"], cq["from"]["id"], cq["data"]
                api("answerCallbackQuery", {"callback_query_id": cq["id"]})

                if data.startswith("cat_"):
                    cid=int(data.split("_")[1])
                    send(chat_id, "📦 প্রোডাক্ট সিলেক্ট করুন:", get_products_by_cat_kb(cid))
                elif data.startswith("aprod_"):
                    cat_id=int(data.split("_")[1])
                    admin_state[uid]={"step":"add_prod_name", "cat_id":cat_id}
                    send(chat_id, "প্রোডাক্টের নাম লেখো (যেমন: ৭ দিন ডিউরেশন 22 টাকা)")
                elif data.startswith("delprod_"):
                    pid=int(data.split("_")[1])
                    con=sqlite3.connect("store.db")
                    con.cursor().execute("DELETE FROM products WHERE id=?", (pid,))
                    con.cursor().execute("DELETE FROM stocks WHERE product_id=?", (pid,))
                    con.commit()
                    con.close()
                    send(chat_id, f"✅ প্রোডাক্ট {pid} ডিলিট হয়েছে", admin_reply_kb())
                elif data.startswith("addstock_"):
                    pid=int(data.split("_")[1])
                    admin_state[uid]={"step":"add_stock", "pid":pid}
                    send(chat_id, "এবার স্টকগুলো পাঠাও, এক লাইনে একটা করে।\nযেমন:\ngmail1@gmail.com:pass123\ngmail2@gmail.com:pass456")
                elif data=="add_deposit" and uid==ADMIN_ID:
                    admin_state[uid]={"step":"add_deposit_name"}
                    send(chat_id, "ডিপোজিটের নাম লেখো (যেমন: বিকাশ, নগদ, USDT)")
                elif data.startswith("buy_"):
                    pid=int(data.split("_")[1])
                    con=sqlite3.connect("store.db")
                    cur=con.cursor()
                    cur.execute("SELECT name, price, stock_count FROM products WHERE id=?", (pid,))
                    row=cur.fetchone()
                    if not row:
                        send(chat_id, "❌ প্রোডাক্ট পাওয়া যায়নি")
                    else:
                        pname, price, stock = row
                        cur.execute("SELECT balance FROM users WHERE user_id=?", (uid,))
                        bal=cur.fetchone()[0]
                        if stock<=0:
                            send(chat_id, f"❌ {pname} স্টক শেষ! বাটন লাল হয়ে গেছে। এডমিন স্টক যোগ করলে সবুজ হবে।", get_products_by_cat_kb(1))
                        elif bal < price:
                            send(chat_id, f"❌ ব্যালেন্স কম! দাম {price}৳, আপনার আছে {bal}৳", user_reply_kb())
                        else:
                            cur.execute("SELECT id, data FROM stocks WHERE product_id=? LIMIT 1", (pid,))
                            srow=cur.fetchone()
                            if srow:
                                cur.execute("DELETE FROM stocks WHERE id=?", (srow[0],))
                                cur.execute("UPDATE products SET stock_count=stock_count-1 WHERE id=?", (pid,))
                                cur.execute("UPDATE users SET balance=balance-? WHERE user_id=?", (price, uid))
                                con.commit()
                                send(chat_id, f"✅ অর্ডার সফল!\n\n📦 {pname}\n🔑 আপনার প্রোডাক্ট: <code>{srow[1]}</code>", user_reply_kb())
                            else:
                                send(chat_id, "❌ স্টক খালি, এডমিনকে বলো")
                    con.close()

    except Exception as e:
        print("Error", e)
        time.sleep(2)
