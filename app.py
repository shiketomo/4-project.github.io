from flask import Flask, render_template, request, redirect, url_for, session, flash
import os, json, re
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "super_secret_key_change_this"

# 保存先
UPLOAD_FOLDER = os.path.join(app.root_path, 'static', 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

DATA_FILE = os.path.join(app.root_path, 'static', 'data.json')       # 投稿データ（ユーザーごと）
USER_FILE = os.path.join(app.root_path, 'static', 'users.json')     # ユーザーパスワード
COMMENT_FILE = os.path.join(app.root_path, 'static', 'comments.json')  # コメント（リスト）

# ----------------- JSON ヘルパー -----------------
def load_json(path):
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def save_json(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ----------------- ユーティリティ -----------------
def highlight(text, keyword):
    if not keyword:
        return text
    return re.sub(f"({re.escape(keyword)})", r"<mark>\1</mark>", text, flags=re.IGNORECASE)

def parse_price_for_sort(price_str):
    # "1200円" -> 1200, fallback None
    if not price_str:
        return None
    m = re.search(r'(\d[\d,]*)', price_str.replace(',', ''))
    if m:
        try:
            return int(m.group(1))
        except:
            return None
    return None

# ----------------- ルート -----------------
@app.route('/')
def index():
    # マイページ（自分の出品のみ）
    if 'user' not in session:
        return redirect(url_for('login'))
    data = load_json(DATA_FILE)
    user = session['user']
    user_data = data.get(user, {})  # {book_key: info}
    # show notifications count
    comments = load_json(COMMENT_FILE)
    unread_count = 0
    for thread_key, comment_list in comments.items():
        # thread_key format "owner::book"
        owner, _ = thread_key.split("::", 1)
        if owner == user:
            for c in comment_list:
                if not c.get('read_by') or user not in c.get('read_by', []):
                    unread_count += 1
    return render_template('index.html', data=user_data, user=user, unread_count=unread_count)

# ----------------- 公開ページ（検索 + ソート） -----------------
@app.route('/public')
def public():
    keyword = request.args.get('q', '').strip()
    sort = request.args.get('sort', 'new')  # 'new' | 'price_asc' | 'price_desc'
    data = load_json(DATA_FILE)

    filtered = {}
    # filter by keyword (部分一致 across fields)
    if keyword:
        for user, books in data.items():
            matched_books = {}
            for book_key, info in books.items():
                text_combined = " ".join([
                    info.get('title',''),
                    info.get('author',''),
                    info.get('note',''),
                    info.get('condition',''),
                    info.get('course','')
                ])
                if keyword.lower() in text_combined.lower():
                    # deep copy and highlight strings
                    info_high = {}
                    for k,v in info.items():
                        info_high[k] = highlight(v, keyword) if isinstance(v, str) else v
                    matched_books[book_key] = info_high
            if matched_books:
                filtered[user] = matched_books
    else:
        filtered = data

    # Sorting within each user's books
    for user, books in filtered.items():
        # convert dict to list of tuples to sort, with created_at or price
        items = []
        for bk, info in books.items():
            created = info.get('created_at')
            price_num = parse_price_for_sort(info.get('price',''))
            items.append( (bk, info, created, price_num) )

        if sort == 'price_asc':
            items.sort(key=lambda x: (x[3] is None, x[3] if x[3] is not None else 0))
        elif sort == 'price_desc':
            items.sort(key=lambda x: (x[3] is None, -(x[3] if x[3] is not None else 0)))
        else:  # new: sort by created_at desc (newest first)
            items.sort(key=lambda x: x[2] or '', reverse=True)

        # rebuild dict preserving order
        filtered[user] = {it[0]: it[1] for it in items}

    return render_template('public.html', data=filtered, keyword=keyword, sort=sort, current_user=session.get('user'))

# ----------------- 詳細ページ -----------------
@app.route('/book/<owner>/<book>')
def book_detail(owner, book):
    data = load_json(DATA_FILE)
    comments = load_json(COMMENT_FILE)
    if owner not in data or book not in data[owner]:
        flash("指定の投稿は存在しません。")
        return redirect(url_for('public'))
    info = data[owner][book]
    thread_key = f"{owner}::{book}"
    thread_comments = comments.get(thread_key, [])
    return render_template('book_detail.html', user=owner, book=book, info=info, comments=thread_comments, current_user=session.get('user'))

# ----------------- コメント投稿 -----------------
@app.route('/book/<owner>/<book>/comment', methods=['POST'])
def post_comment(owner, book):
    if 'user' not in session:
        flash("コメントにはログインが必要です。")
        return redirect(url_for('login', next=url_for('book_detail', owner=owner, book=book)))

    text = request.form.get('comment','').strip()
    if not text:
        flash("コメントを入力してください。")
        return redirect(url_for('book_detail', owner=owner, book=book))

    comments = load_json(COMMENT_FILE)
    thread_key = f"{owner}::{book}"
    thread = comments.setdefault(thread_key, [])

    comment = {
        "author": session['user'],
        "text": text,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "read_by": []  # list of users who have read this comment
    }
    thread.append(comment)
    save_json(COMMENT_FILE, comments)

    # コメント通知のため： nothing extra to store, notifications inferred by read_by missing for owner

    return redirect(url_for('book_detail', owner=owner, book=book))

# ----------------- 通知一覧 -----------------
@app.route('/notifications')
def notifications():
    if 'user' not in session:
        return redirect(url_for('login', next=url_for('notifications')))
    user = session['user']
    comments = load_json(COMMENT_FILE)
    notifications = []
    for thread_key, thread in comments.items():
        owner, book = thread_key.split("::",1)
        if owner != user: 
            continue
        for c in thread:
            if user not in c.get('read_by', []):
                notifications.append({
                    "book": book,
                    "author": c['author'],
                    "text": c['text'],
                    "time": c['time'],
                    "thread": thread_key
                })
    return render_template('notifications.html', notifications=notifications)

# mark notifications read (visit a thread)
@app.route('/notifications/mark_read/<owner>/<book>', methods=['POST'])
def mark_read(owner, book):
    if 'user' not in session:
        return redirect(url_for('login'))
    user = session['user']
    comments = load_json(COMMENT_FILE)
    thread_key = f"{owner}::{book}"
    thread = comments.get(thread_key, [])
    changed = False
    for c in thread:
        if user not in c.get('read_by', []):
            c.setdefault('read_by', []).append(user)
            changed = True
    if changed:
        save_json(COMMENT_FILE, comments)
    return redirect(url_for('notifications'))

# ----------------- ユーザー登録 / ログイン -----------------
@app.route('/register', methods=['GET','POST'])
def register():
    users = load_json(USER_FILE)
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        if not username or not password:
            flash("ユーザー名とパスワードは必須です。")
            return redirect(url_for('register'))
        if username in users:
            flash("そのユーザー名は既に存在します。")
            return redirect(url_for('register'))
        users[username] = generate_password_hash(password)
        save_json(USER_FILE, users)
        flash("登録しました。ログインしてください。")
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET','POST'])
def login():
    users = load_json(USER_FILE)
    next_url = request.args.get('next')
    if request.method == 'POST':
        username = request.form.get('username','').strip()
        password = request.form.get('password','')
        if username in users and check_password_hash(users[username], password):
            session['user'] = username
            flash(f"{username} さん、ログインしました。")
            return redirect(next_url or url_for('index'))
        else:
            flash("ユーザー名またはパスワードが違います。")
    return render_template('login.html', next_url=next_url)

@app.route('/logout')
def logout():
    session.pop('user', None)
    flash("ログアウトしました。")
    return redirect(url_for('public'))

# ----------------- 投稿（アップロード） -----------------
@app.route('/upload', methods=['POST'])
def upload():
    if 'user' not in session:
        flash("ログインが必要です。")
        return redirect(url_for('login'))
    user = session['user']
    data = load_json(DATA_FILE)

    book_key = request.form.get('title','').strip()
    if not book_key:
        flash("教科書名は必須です。")
        return redirect(url_for('index'))

    files = request.files.getlist('images')
    if not files or all(f.filename=='' for f in files):
        flash("画像を選択してください。")
        return redirect(url_for('index'))

    user_entry = data.setdefault(user, {})
    if book_key not in user_entry:
        user_entry[book_key] = {
            "title": book_key,
            "author": request.form.get('author',''),
            "price": request.form.get('price',''),
            "condition": request.form.get('condition',''),
            "note": request.form.get('note',''),
            "course": request.form.get('course',''),
            "images": [],
            "created_at": datetime.now().isoformat()
        }

    existing_count = len(user_entry[book_key]['images'])
    remaining = 5 - existing_count
    if remaining <= 0:
        flash("この出品は既に画像が5枚あります。")
        return redirect(url_for('index'))

    # trim to remaining
    upload_files = [f for f in files if f.filename][:remaining]

    for f in upload_files:
        filename_raw = secure_filename(f.filename)
        # unique filename
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
        filename = f"{user}_{book_key}_{timestamp}_{filename_raw}"
        path = os.path.join(UPLOAD_FOLDER, filename)
        f.save(path)
        user_entry[book_key]['images'].append(filename)

    save_json(DATA_FILE, data)
    return redirect(url_for('index'))

# ----------------- 投稿削除（info + images） -----------------
@app.route('/delete_info/<book>', methods=['POST'])
def delete_info(book):
    if 'user' not in session:
        return redirect(url_for('login'))
    user = session['user']
    data = load_json(DATA_FILE)
    if user in data and book in data[user]:
        for img in data[user][book].get('images', []):
            path = os.path.join(UPLOAD_FOLDER, img)
            if os.path.exists(path):
                os.remove(path)
        # optionally remove comments thread
        thread_key = f"{user}::{book}"
        comments = load_json(COMMENT_FILE)
        if thread_key in comments:
            del comments[thread_key]
            save_json(COMMENT_FILE, comments)
        del data[user][book]
        save_json(DATA_FILE, data)
    return redirect(url_for('index'))

# ----------------- 画像個別削除 -----------------
@app.route('/delete_image/<book>/<filename>', methods=['POST'])
def delete_image(book, filename):
    if 'user' not in session:
        return redirect(url_for('login'))
    user = session['user']
    data = load_json(DATA_FILE)
    if user in data and book in data[user] and filename in data[user][book]['images']:
        path = os.path.join(UPLOAD_FOLDER, filename)
        if os.path.exists(path):
            os.remove(path)
        data[user][book]['images'].remove(filename)
        save_json(DATA_FILE, data)
    return redirect(url_for('index'))

# ----------------- 実行 -----------------
if __name__ == '__main__':
    print("公開ページ: http://127.0.0.1:5000/public")
    print("ログインページ: http://127.0.0.1:5000/login")
    app.run(debug=True)