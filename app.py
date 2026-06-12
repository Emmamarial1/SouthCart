import os
import sqlite3
import json
import uuid
import random
import re
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g, send_from_directory
from werkzeug.utils import secure_filename
from flask_bcrypt import Bcrypt

app = Flask(__name__)
app.secret_key = 'southcart-secret-key-change-in-production'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

bcrypt = Bcrypt(app)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def normalise_phone(phone):
    """Remove spaces, dashes, parentheses, keep digits and optional leading +."""
    if not phone:
        return ''
    cleaned = re.sub(r'[^\d+]', '', phone.strip())
    return cleaned

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        db = g._database = sqlite3.connect('database.db')
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def init_db():
    with app.app_context():
        db = get_db()
        cursor = db.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT UNIQUE,
            password TEXT NOT NULL,
            address TEXT,
            city TEXT,
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        for col, col_type in [
            ('phone', 'TEXT UNIQUE'),
            ('address', 'TEXT'),
            ('city', 'TEXT'),
            ('is_admin', 'INTEGER DEFAULT 0'),
            ('created_at', 'TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
        ]:
            try:
                cursor.execute(f'ALTER TABLE users ADD COLUMN {col} {col_type}')
            except sqlite3.OperationalError:
                pass

        cursor.execute('''CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, description TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, description TEXT, price_ugx INTEGER NOT NULL,
            category_id INTEGER, image_url TEXT,
            status TEXT DEFAULT 'In Stock', rating REAL DEFAULT 0,
            review_count INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories(id)
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_number TEXT UNIQUE NOT NULL,
            user_id INTEGER NOT NULL, total_amount_ugx INTEGER NOT NULL,
            shipping_address TEXT NOT NULL, city TEXT NOT NULL, phone TEXT NOT NULL,
            payment_method TEXT, payment_status TEXT DEFAULT 'pending',
            order_status TEXT DEFAULT 'pending', tracking_info TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL, quantity INTEGER NOT NULL, price_ugx INTEGER NOT NULL
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS cart (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, session_id TEXT,
            product_id INTEGER NOT NULL, quantity INTEGER DEFAULT 1
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT, product_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL, rating INTEGER NOT NULL, comment TEXT,
            approved INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS support_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, name TEXT NOT NULL,
            email TEXT NOT NULL, subject TEXT NOT NULL, message TEXT NOT NULL,
            reply TEXT, status TEXT DEFAULT 'unread', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS product_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, customer_name TEXT NOT NULL,
            email TEXT NOT NULL, product_name TEXT NOT NULL, description TEXT,
            status TEXT DEFAULT 'pending', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        cursor.execute('''CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY, value TEXT
        )''')

        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('currency_rates', '{\"UGX\":1,\"SSP\":0.026,\"USD\":0.00027}')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('site_name', 'South Cart')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('delivery_fee', '15000')")
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('whatsapp_number', '+256782713764')")

        cursor.execute("INSERT OR IGNORE INTO categories (id, name) VALUES (1, 'Fashion'), (2, 'Electronics'), (3, 'Shoes'), (4, 'Bags'), (5, 'Accessories'), (6, 'Watches'), (7, 'Beauty'), (8, 'Phones')")

        admin_password = bcrypt.generate_password_hash('admin123').decode('utf-8')
        cursor.execute("INSERT OR IGNORE INTO users (name, phone, password, is_admin) VALUES ('Admin', 'admin', ?, 1)", (admin_password,))

        db.commit()
        user_count = cursor.execute('SELECT COUNT(*) FROM users').fetchone()[0]
        print(f"Database initialised. Existing users: {user_count}")

init_db()

def get_weekly_revenue():
    db = get_db()
    today = datetime.now().date()
    result = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        day_str = d.strftime('%Y-%m-%d')
        day_name = d.strftime('%a')
        row = db.execute('SELECT COALESCE(SUM(total_amount_ugx), 0) as revenue FROM orders WHERE payment_status = "paid" AND date(created_at) = ?', (day_str,)).fetchone()
        revenue = row['revenue'] if row else 0
        result.append({'day': day_name, 'revenue': revenue})
    return result

@app.context_processor
def utility_processor():
    db = get_db()
    rates_json = db.execute("SELECT value FROM settings WHERE key = 'currency_rates'").fetchone()
    rates = json.loads(rates_json['value']) if rates_json else {'UGX':1,'SSP':0.026,'USD':0.00027}
    whatsapp = db.execute("SELECT value FROM settings WHERE key = 'whatsapp_number'").fetchone()
    whatsapp_number = whatsapp['value'] if whatsapp else '+256782713764'
    categories = db.execute('SELECT id, name FROM categories ORDER BY name').fetchall()
    return dict(rates=rates, whatsapp_number=whatsapp_number, categories=categories)

def get_cart_count():
    if 'user_id' in session:
        db = get_db()
        cart = db.execute('SELECT SUM(quantity) as count FROM cart WHERE user_id = ?', (session['user_id'],)).fetchone()
        return cart['count'] or 0
    elif 'cart' in session:
        return sum(session['cart'].values())
    return 0

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Please login', 'warning')
            return redirect(url_for('login'))
        db = get_db()
        user = db.execute('SELECT is_admin FROM users WHERE id = ?', (session['user_id'],)).fetchone()
        if not user or user['is_admin'] != 1:
            flash('Admin access required', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated

# ---------- Customer routes ----------
@app.route('/')
def index():
    db = get_db()
    products = db.execute('SELECT p.*, c.name as category_name FROM products p LEFT JOIN categories c ON p.category_id = c.id ORDER BY p.created_at DESC LIMIT 8').fetchall()
    return render_template('index.html', products=products, cart_count=get_cart_count())

@app.route('/shop')
def shop():
    db = get_db()
    category_id = request.args.get('category', type=int)
    search = request.args.get('search', '')
    query = 'SELECT p.*, c.name as category_name FROM products p LEFT JOIN categories c ON p.category_id = c.id WHERE p.status = "In Stock"'
    params = []
    if category_id:
        query += ' AND p.category_id = ?'
        params.append(category_id)
    if search:
        query += ' AND (p.name LIKE ? OR p.description LIKE ?)'
        params.extend([f'%{search}%', f'%{search}%'])
    query += ' ORDER BY p.created_at DESC'
    products = db.execute(query, params).fetchall()
    categories = db.execute('SELECT * FROM categories').fetchall()
    return render_template('shop.html', products=products, categories=categories, selected_category=category_id, search=search, cart_count=get_cart_count())

@app.route('/product/<int:id>')
def product(id):
    db = get_db()
    product = db.execute('SELECT p.*, c.name as category_name FROM products p LEFT JOIN categories c ON p.category_id = c.id WHERE p.id = ?', (id,)).fetchone()
    if not product:
        flash('Product not found', 'danger')
        return redirect(url_for('shop'))
    reviews = db.execute('SELECT r.*, u.name as user_name FROM reviews r JOIN users u ON r.user_id = u.id WHERE r.product_id = ? AND r.approved = 1 ORDER BY r.created_at DESC', (id,)).fetchall()
    related = db.execute('SELECT * FROM products WHERE category_id = ? AND id != ? LIMIT 4', (product['category_id'], id)).fetchall()
    return render_template('product.html', product=product, reviews=reviews, related=related, cart_count=get_cart_count())

@app.route('/add-to-cart/<int:product_id>', methods=['POST'])
def add_to_cart(product_id):
    if session.get('is_admin'):
        return jsonify({'success': False, 'error': 'Admin cannot add to cart'}) if request.headers.get('X-Requested-With') else redirect(url_for('index'))
    quantity = int(request.form.get('quantity', 1))
    if 'user_id' in session:
        db = get_db()
        existing = db.execute('SELECT id, quantity FROM cart WHERE user_id = ? AND product_id = ?', (session['user_id'], product_id)).fetchone()
        if existing:
            db.execute('UPDATE cart SET quantity = quantity + ? WHERE id = ?', (quantity, existing['id']))
        else:
            db.execute('INSERT INTO cart (user_id, product_id, quantity) VALUES (?, ?, ?)', (session['user_id'], product_id, quantity))
        db.commit()
    else:
        if 'cart' not in session:
            session['cart'] = {}
        session['cart'][str(product_id)] = session['cart'].get(str(product_id), 0) + quantity
        session.modified = True

    new_count = get_cart_count()
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.content_type == 'application/json':
        return jsonify({'success': True, 'cart_count': new_count})
    flash('Product added to cart', 'success')
    return redirect(request.referrer or url_for('shop'))

@app.route('/cart')
@login_required
def cart():
    if session.get('is_admin'):
        flash('Admins cannot use the shopping cart.', 'warning')
        return redirect(url_for('index'))
    cart_items = []
    total = 0
    db = get_db()
    if 'user_id' in session:
        items = db.execute('SELECT c.*, p.name, p.price_ugx, p.image_url FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?', (session['user_id'],)).fetchall()
        for item in items:
            subtotal = item['price_ugx'] * item['quantity']
            total += subtotal
            cart_items.append({'id': item['product_id'], 'name': item['name'], 'price_ugx': item['price_ugx'], 'quantity': item['quantity'], 'image_url': item['image_url'], 'subtotal': subtotal})
    elif 'cart' in session:
        for prod_id, qty in session['cart'].items():
            product = db.execute('SELECT id, name, price_ugx, image_url FROM products WHERE id = ?', (int(prod_id),)).fetchone()
            if product:
                subtotal = product['price_ugx'] * qty
                total += subtotal
                cart_items.append({'id': product['id'], 'name': product['name'], 'price_ugx': product['price_ugx'], 'quantity': qty, 'image_url': product['image_url'], 'subtotal': subtotal})
    delivery_fee = int(db.execute("SELECT value FROM settings WHERE key='delivery_fee'").fetchone()['value'])
    return render_template('cart.html', cart_items=cart_items, total=total, delivery_fee=delivery_fee, cart_count=get_cart_count())

@app.route('/update-cart', methods=['GET', 'POST'])
@login_required
def update_cart():
    if session.get('is_admin'):
        return redirect(url_for('index'))
    if request.method == 'GET' and 'delete' in request.args:
        product_id = request.args.get('delete')
        if 'user_id' in session:
            db = get_db()
            db.execute('DELETE FROM cart WHERE user_id = ? AND product_id = ?', (session['user_id'], product_id))
            db.commit()
        else:
            if 'cart' in session and product_id in session['cart']:
                del session['cart'][product_id]
                session.modified = True
        flash('Item removed', 'success')
        return redirect(url_for('cart'))
    if request.method == 'POST':
        product_id = request.form.get('product_id')
        quantity = int(request.form.get('quantity', 0))
        if 'user_id' in session:
            db = get_db()
            if quantity <= 0:
                db.execute('DELETE FROM cart WHERE user_id = ? AND product_id = ?', (session['user_id'], product_id))
            else:
                db.execute('UPDATE cart SET quantity = ? WHERE user_id = ? AND product_id = ?', (quantity, session['user_id'], product_id))
            db.commit()
        else:
            if 'cart' in session and product_id in session['cart']:
                if quantity <= 0:
                    del session['cart'][product_id]
                else:
                    session['cart'][product_id] = quantity
                session.modified = True
        return redirect(url_for('cart'))
    return redirect(url_for('cart'))

@app.route('/place-order', methods=['POST'])
@login_required
def place_order():
    if session.get('is_admin'):
        flash('Admins cannot place orders.', 'warning')
        return redirect(url_for('index'))
    db = get_db()
    cart_items = db.execute('SELECT c.*, p.name, p.price_ugx FROM cart c JOIN products p ON c.product_id = p.id WHERE c.user_id = ?', (session['user_id'],)).fetchall()
    if not cart_items:
        flash('Your cart is empty.', 'danger')
        return redirect(url_for('cart'))
    total = sum(item['price_ugx'] * item['quantity'] for item in cart_items)
    delivery_fee = int(db.execute("SELECT value FROM settings WHERE key='delivery_fee'").fetchone()['value'])
    total_amount = total + delivery_fee

    order_number = f"SC-{random.randint(100000, 999999)}"
    while db.execute('SELECT id FROM orders WHERE order_number = ?', (order_number,)).fetchone():
        order_number = f"SC-{random.randint(100000, 999999)}"

    shipping_address = request.form['address']
    city = request.form['city']
    phone = request.form['phone']
    payment_method = request.form['payment_method']

    db.execute('INSERT INTO orders (order_number, user_id, total_amount_ugx, shipping_address, city, phone, payment_method, payment_status, order_status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
               (order_number, session['user_id'], total_amount, shipping_address, city, phone, payment_method, 'pending', 'pending'))
    order_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
    for item in cart_items:
        db.execute('INSERT INTO order_items (order_id, product_id, quantity, price_ugx) VALUES (?, ?, ?, ?)',
                   (order_id, item['product_id'], item['quantity'], item['price_ugx']))
    db.execute('DELETE FROM cart WHERE user_id = ?', (session['user_id'],))
    db.commit()

    whatsapp = db.execute("SELECT value FROM settings WHERE key='whatsapp_number'").fetchone()['value']
    flash(f'✅ Order #{order_number} placed! Total: UGX {total_amount:,}. 📱 Send payment to {whatsapp} with your order number as reference. We will confirm once received.', 'success')
    return redirect(url_for('account'))

@app.route('/account')
@login_required
def account():
    if session.get('is_admin'):
        flash('Admins do not have a customer account.', 'warning')
        return redirect(url_for('admin_dashboard'))
    db = get_db()
    user_id = session['user_id']
    orders = db.execute('SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC', (user_id,)).fetchall()
    user = db.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
    return render_template('account.html', orders=orders, user=user, cart_count=get_cart_count())

@app.route('/track-order', methods=['GET'])
def track_order_page():
    order_number = request.args.get('order_number')
    if not order_number:
        return render_template('track-order.html', order=None, cart_count=get_cart_count())
    db = get_db()
    order = db.execute('SELECT * FROM orders WHERE order_number = ?', (order_number,)).fetchone()
    return render_template('track-order.html', order=order, cart_count=get_cart_count())

@app.route('/track-order/<order_number>')
def track_order(order_number):
    db = get_db()
    order = db.execute('SELECT * FROM orders WHERE order_number = ?', (order_number,)).fetchone()
    return render_template('track-order.html', order=order, cart_count=get_cart_count())

@app.route('/request-product', methods=['GET', 'POST'])
def request_product():
    if request.method == 'POST':
        db = get_db()
        db.execute('INSERT INTO product_requests (user_id, customer_name, email, product_name, description) VALUES (?, ?, ?, ?, ?)',
                   (session.get('user_id'), request.form['name'], request.form['email'], request.form['product_name'], request.form.get('description', '')))
        db.commit()
        flash('Request sent! We will notify you.', 'success')
        return redirect(url_for('shop'))
    return render_template('request-product.html', cart_count=get_cart_count())

@app.route('/contact', methods=['GET', 'POST'])
def contact():
    if request.method == 'POST':
        db = get_db()
        db.execute('INSERT INTO support_messages (user_id, name, email, subject, message) VALUES (?, ?, ?, ?, ?)',
                   (session.get('user_id'), request.form['name'], request.form['email'], request.form['subject'], request.form['message']))
        db.commit()
        flash('Message sent!', 'success')
        return redirect(url_for('index'))
    return render_template('contact.html', cart_count=get_cart_count())

@app.route('/submit-review', methods=['POST'])
@login_required
def submit_review():
    if session.get('is_admin'):
        flash('Admins cannot write reviews.', 'warning')
        return redirect(url_for('index'))
    db = get_db()
    db.execute('INSERT INTO reviews (product_id, user_id, rating, comment, approved) VALUES (?, ?, ?, ?, 0)',
               (request.form['product_id'], session['user_id'], int(request.form['rating']), request.form['comment']))
    db.commit()
    flash('Review submitted (awaiting approval)', 'success')
    return redirect(url_for('product', id=request.form['product_id']))

# ---------- Auth routes with phone normalisation ----------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        credential = request.form.get('login_credential', '').strip()
        password = request.form.get('password', '')
        if not credential or not password:
            flash('Please enter both name/phone and password', 'danger')
            return redirect(url_for('login'))
        db = get_db()
        normalised = normalise_phone(credential)
        user = None
        if normalised:
            user = db.execute('SELECT * FROM users WHERE phone = ?', (normalised,)).fetchone()
        if not user:
            user = db.execute('SELECT * FROM users WHERE name = ?', (credential,)).fetchone()
        if user and bcrypt.check_password_hash(user['password'], password):
            session.clear()
            session['user_id'] = user['id']
            session['user_name'] = user['name']
            session['is_admin'] = user['is_admin']
            flash('Logged in successfully', 'success')
            return redirect(request.args.get('next') or url_for('index'))
        flash('Invalid name/phone or password', 'danger')
    return render_template('login.html', cart_count=get_cart_count())

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        phone_raw = request.form.get('phone', '').strip()
        password = request.form.get('password', '')
        confirm = request.form.get('confirm_password', '')
        if not name or not phone_raw or not password:
            flash('Name, phone number and password are required', 'danger')
            return redirect(url_for('signup'))
        if password != confirm:
            flash('Passwords do not match', 'danger')
            return redirect(url_for('signup'))
        phone = normalise_phone(phone_raw)
        if not phone:
            flash('Invalid phone number', 'danger')
            return redirect(url_for('signup'))
        db = get_db()
        existing = db.execute('SELECT id FROM users WHERE phone = ?', (phone,)).fetchone()
        if existing:
            flash('Phone number already registered', 'danger')
            return redirect(url_for('signup'))
        hashed = bcrypt.generate_password_hash(password).decode('utf-8')
        db.execute('INSERT INTO users (name, phone, password) VALUES (?, ?, ?)', (name, phone, hashed))
        db.commit()
        flash('Account created! Please login.', 'success')
        return redirect(url_for('login'))
    return render_template('signup.html', cart_count=get_cart_count())

@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        credential = request.form.get('phone', '').strip()
        new_password = request.form.get('new_password', '')
        confirm = request.form.get('confirm_password', '')
        if not credential or not new_password:
            flash('Please enter your name/phone and new password', 'danger')
            return redirect(url_for('forgot_password'))
        if new_password != confirm:
            flash('Passwords do not match', 'danger')
            return redirect(url_for('forgot_password'))
        db = get_db()
        normalised = normalise_phone(credential)
        user = None
        if normalised:
            user = db.execute('SELECT id, name, phone FROM users WHERE phone = ?', (normalised,)).fetchone()
        if not user:
            user = db.execute('SELECT id, name, phone FROM users WHERE name = ?', (credential,)).fetchone()
        if not user:
            flash('No account found with that name or phone number', 'danger')
            return redirect(url_for('forgot_password'))
        hashed = bcrypt.generate_password_hash(new_password).decode('utf-8')
        db.execute('UPDATE users SET password = ? WHERE id = ?', (hashed, user['id']))
        db.commit()
        flash('Password reset successfully! Please login with your new password.', 'success')
        return redirect(url_for('login'))
    return render_template('forgot-password.html', cart_count=get_cart_count())

@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out', 'info')
    return redirect(url_for('index'))

# ---------- Admin routes ----------
@app.route('/admin')
@admin_required
def admin_dashboard():
    db = get_db()
    total_orders = db.execute('SELECT COUNT(*) as count FROM orders').fetchone()['count']
    total_revenue = db.execute('SELECT SUM(total_amount_ugx) as sum FROM orders WHERE payment_status = "paid"').fetchone()['sum'] or 0
    total_products = db.execute('SELECT COUNT(*) as count FROM products').fetchone()['count']
    total_customers = db.execute('SELECT COUNT(*) as count FROM users WHERE is_admin = 0').fetchone()['count']
    pending_orders = db.execute('SELECT COUNT(*) as count FROM orders WHERE order_status = "pending"').fetchone()['count']
    recent_orders = db.execute('SELECT o.*, u.name as customer_name FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.created_at DESC LIMIT 5').fetchall()
    weekly_revenue = get_weekly_revenue()
    return render_template('admin/dashboard.html', 
                          total_orders=total_orders, total_revenue=total_revenue,
                          total_products=total_products, total_customers=total_customers,
                          pending_orders=pending_orders, recent_orders=recent_orders,
                          weekly_revenue=weekly_revenue, cart_count=get_cart_count())

@app.route('/admin/products')
@admin_required
def admin_products():
    db = get_db()
    products = db.execute('SELECT p.*, c.name as category_name FROM products p LEFT JOIN categories c ON p.category_id = c.id ORDER BY p.created_at DESC').fetchall()
    categories = db.execute('SELECT * FROM categories').fetchall()
    return render_template('admin/products.html', products=products, categories=categories, cart_count=get_cart_count())

@app.route('/admin/product/add', methods=['POST'])
@admin_required
def admin_add_product():
    name = request.form['name']
    price_ugx = int(request.form['price_ugx'])
    category_id = request.form.get('category_id') or None
    description = request.form.get('description', '')
    image = request.files.get('image')
    image_url = ''
    if image and allowed_file(image.filename):
        filename = secure_filename(f"{uuid.uuid4().hex}_{image.filename}")
        image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        image_url = f'/static/uploads/{filename}'
    db = get_db()
    db.execute('INSERT INTO products (name, description, price_ugx, category_id, image_url) VALUES (?, ?, ?, ?, ?)',
               (name, description, price_ugx, category_id, image_url))
    db.commit()
    flash('Product added', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/product/edit/<int:id>', methods=['POST'])
@admin_required
def admin_edit_product(id):
    name = request.form['name']
    price_ugx = int(request.form['price_ugx'])
    category_id = request.form.get('category_id') or None
    description = request.form.get('description', '')
    image = request.files.get('image')
    db = get_db()
    if image and allowed_file(image.filename):
        filename = secure_filename(f"{uuid.uuid4().hex}_{image.filename}")
        image.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        image_url = f'/static/uploads/{filename}'
        db.execute('UPDATE products SET name=?, description=?, price_ugx=?, category_id=?, image_url=? WHERE id=?',
                   (name, description, price_ugx, category_id, image_url, id))
    else:
        db.execute('UPDATE products SET name=?, description=?, price_ugx=?, category_id=? WHERE id=?',
                   (name, description, price_ugx, category_id, id))
    db.commit()
    flash('Product updated', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/product/delete/<int:id>')
@admin_required
def admin_delete_product(id):
    db = get_db()
    db.execute('DELETE FROM products WHERE id = ?', (id,))
    db.commit()
    flash('Product deleted', 'success')
    return redirect(url_for('admin_products'))

@app.route('/admin/orders')
@admin_required
def admin_orders():
    db = get_db()
    orders = db.execute('SELECT o.*, u.name as customer_name FROM orders o JOIN users u ON o.user_id = u.id ORDER BY o.created_at DESC').fetchall()
    return render_template('admin/orders.html', orders=orders, cart_count=get_cart_count())

@app.route('/admin/order/<int:order_id>', methods=['POST'])
@admin_required
def admin_update_order(order_id):
    order_status = request.form['order_status']
    payment_status = request.form['payment_status']
    tracking_info = request.form.get('tracking_info', '')
    db = get_db()
    db.execute('UPDATE orders SET order_status=?, payment_status=?, tracking_info=? WHERE id=?',
               (order_status, payment_status, tracking_info, order_id))
    db.commit()
    flash('Order updated', 'success')
    return redirect(url_for('admin_orders'))

@app.route('/admin/customers')
@admin_required
def admin_customers():
    db = get_db()
    customers = db.execute('SELECT id, name, phone, created_at FROM users WHERE is_admin = 0 ORDER BY created_at DESC').fetchall()
    return render_template('admin/customers.html', customers=customers, cart_count=get_cart_count())

@app.route('/admin/categories', methods=['GET', 'POST'])
@admin_required
def admin_categories():
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        if not name:
            flash('Category name is required', 'danger')
            return redirect(url_for('admin_categories'))
        try:
            db.execute('INSERT INTO categories (name, description) VALUES (?, ?)', (name, description))
            db.commit()
            flash(f'Category "{name}" added', 'success')
        except Exception as e:
            flash(f'Error: {str(e)}', 'danger')
            print(f"DB error: {e}")
        return redirect(url_for('admin_categories'))
    categories = db.execute('SELECT * FROM categories ORDER BY name').fetchall()
    return render_template('admin/categories.html', categories=categories, cart_count=get_cart_count())

@app.route('/admin/category/edit/<int:id>', methods=['POST'])
@admin_required
def admin_edit_category(id):
    name = request.form.get('name', '').strip()
    description = request.form.get('description', '').strip()
    db = get_db()
    db.execute('UPDATE categories SET name = ?, description = ? WHERE id = ?', (name, description, id))
    db.commit()
    flash('Category updated', 'success')
    return redirect(url_for('admin_categories'))

@app.route('/admin/settings', methods=['GET', 'POST'])
@admin_required
def admin_settings():
    db = get_db()
    if request.method == 'POST':
        db.execute("UPDATE settings SET value = ? WHERE key = 'site_name'", (request.form['site_name'],))
        db.execute("UPDATE settings SET value = ? WHERE key = 'delivery_fee'", (request.form['delivery_fee'],))
        db.execute("UPDATE settings SET value = ? WHERE key = 'whatsapp_number'", (request.form['whatsapp_number'],))
        rates = json.dumps({'UGX': 1, 'SSP': float(request.form['rate_ssp']), 'USD': float(request.form['rate_usd'])})
        db.execute("UPDATE settings SET value = ? WHERE key = 'currency_rates'", (rates,))
        db.commit()
        flash('Settings saved', 'success')
        return redirect(url_for('admin_settings'))
    site_name = db.execute("SELECT value FROM settings WHERE key='site_name'").fetchone()['value']
    delivery_fee = db.execute("SELECT value FROM settings WHERE key='delivery_fee'").fetchone()['value']
    whatsapp = db.execute("SELECT value FROM settings WHERE key='whatsapp_number'").fetchone()['value']
    rates_json = db.execute("SELECT value FROM settings WHERE key='currency_rates'").fetchone()['value']
    rates = json.loads(rates_json)
    return render_template('admin/settings.html', site_name=site_name, delivery_fee=delivery_fee, whatsapp=whatsapp, rates=rates, cart_count=get_cart_count())

@app.route('/admin/update-profile', methods=['POST'])
@admin_required
def admin_update_profile():
    new_name = request.form.get('admin_name', '').strip()
    new_password = request.form.get('new_password', '')
    confirm = request.form.get('confirm_password', '')
    db = get_db()
    user_id = session['user_id']
    if new_name:
        db.execute('UPDATE users SET name = ? WHERE id = ?', (new_name, user_id))
        session['user_name'] = new_name
    if new_password:
        if new_password != confirm:
            flash('Passwords do not match', 'danger')
            return redirect(url_for('admin_settings'))
        hashed = bcrypt.generate_password_hash(new_password).decode('utf-8')
        db.execute('UPDATE users SET password = ? WHERE id = ?', (hashed, user_id))
        flash('Password updated', 'success')
    db.commit()
    flash('Profile updated', 'success')
    return redirect(url_for('admin_settings'))

# Serve uploaded images directly
@app.route('/static/uploads/<path:filename>')
def serve_uploaded_file(filename):
    return send_from_directory('static/uploads', filename)

# ---------- Static pages ----------
@app.route('/about')
def about():
    return render_template('about.html', cart_count=get_cart_count())

@app.route('/how-it-works')
def how_it_works():
    return render_template('how-it-works.html', cart_count=get_cart_count())

@app.route('/delivery')
def delivery():
    return render_template('delivery.html', cart_count=get_cart_count())

@app.route('/faq')
def faq():
    return render_template('faq.html', cart_count=get_cart_count())

if __name__ == '__main__':
    app.run(debug=True, port=8088)
