# app.py - Flask railway tickets + refunds + Admin Control Center
# pip install flask flask-sqlalchemy flask-login flask-wtf

import os
import shutil
from flask import Flask, request, redirect, abort, flash, render_template_string
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect, CSRFError, generate_csrf
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timedelta
from functools import wraps
from sqlalchemy import func

app = Flask(__name__)
app.config['SECRET_KEY'] = 'super-secret-key-railway'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///railway.db'
app.config['WTF_CSRF_ENABLED'] = True

db = SQLAlchemy(app)
lm = LoginManager(app)
lm.login_view = 'login'
csrf = CSRFProtect(app)

# ============ МОДЕЛИ ============
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True)
    email = db.Column(db.String(120), unique=True)
    pw_hash = db.Column(db.String(256))
    role = db.Column(db.String(20), default='passenger')
    failed_attempts = db.Column(db.Integer, default=0) # Для блокировки
    
    def set_pw(self, p): self.pw_hash = generate_password_hash(p)
    def check_pw(self, p): return check_password_hash(self.pw_hash, p)
    @property
    def is_admin(self): return self.role == 'admin'

class AppSetting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True)
    value = db.Column(db.String(255))

class BackupLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    finished_at = db.Column(db.DateTime)
    status = db.Column(db.String(20)) # success, failed, running
    filename = db.Column(db.String(255))

class AdminLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    admin_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    action = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    admin = db.relationship('User', backref='admin_logs')

class Station(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True)
    code = db.Column(db.String(10), unique=True)
    city = db.Column(db.String(80))

class Train(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(10), unique=True)
    name = db.Column(db.String(100))
    total_seats = db.Column(db.Integer, default=100)

class Route(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    origin_station_id = db.Column(db.Integer, db.ForeignKey('station.id'))
    destination_station_id = db.Column(db.Integer, db.ForeignKey('station.id'))
    origin = db.relationship('Station', foreign_keys=[origin_station_id])
    destination = db.relationship('Station', foreign_keys=[destination_station_id])

class Schedule(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    train_id = db.Column(db.Integer, db.ForeignKey('train.id'))
    route_id = db.Column(db.Integer, db.ForeignKey('route.id'))
    departure_time = db.Column(db.DateTime)
    arrival_time = db.Column(db.DateTime)
    base_price = db.Column(db.Float)
    is_active = db.Column(db.Boolean, default=True)
    train = db.relationship('Train', backref='schedules')
    route = db.relationship('Route', backref='schedules')
    @property
    def sold(self): return Ticket.query.filter_by(schedule_id=self.id, status='active').count()
    @property
    def free(self): return self.train.total_seats - self.sold

class Ticket(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    schedule_id = db.Column(db.Integer, db.ForeignKey('schedule.id'))
    passenger_name = db.Column(db.String(120))
    price_paid = db.Column(db.Float)
    status = db.Column(db.String(20), default='active')
    booking_date = db.Column(db.DateTime, default=datetime.utcnow)
    schedule = db.relationship('Schedule', backref='tickets')

class Refund(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey('ticket.id'), unique=True)
    refund_date = db.Column(db.DateTime, default=datetime.utcnow)
    amount_returned = db.Column(db.Float)
    fee_charged = db.Column(db.Float)
    ticket = db.relationship('Ticket', backref='refund')

@lm.user_loader
def load_user(uid): return db.session.get(User, int(uid))

# ============ УТИЛИТЫ ============
def get_setting(key, default):
    s = AppSetting.query.filter_by(key=key).first()
    return s.value if s else default

def set_setting(key, value):
    s = AppSetting.query.filter_by(key=key).first()
    if not s:
        s = AppSetting(key=key)
        db.session.add(s)
    s.value = str(value)

# ============ ОБРАБОТКА ОШИБОК ============
@app.errorhandler(403)
def err_403(e): return page('<div class="alert alert-danger"><h2>403 - Доступ запрещен</h2><a href="/" class="btn btn-secondary mt-3">На главную</a></div>'), 403
@app.errorhandler(404)
def err_404(e): return page('<div class="alert alert-warning"><h2>404 - Страница не найдена</h2><a href="/" class="btn btn-secondary mt-3">На главную</a></div>'), 404
@app.errorhandler(500)
def err_500(e):
    db.session.rollback()
    return page('<div class="alert alert-danger"><h2>500 - Ошибка сервера</h2><a href="/" class="btn btn-secondary mt-3">На главную</a></div>'), 500
@app.errorhandler(CSRFError)
def handle_csrf_error(e): return page('<div class="alert alert-danger"><h2>Ошибка безопасности (CSRF)</h2><p>Обновите страницу и попробуйте снова.</p></div>'), 400

# ============ ШАБЛОН ============
BASE = (
    '<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>ЖД Билеты</title>'
    '<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">'
    '<style>body { background-color: #f8f9fa; } .card { border-radius: 10px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); }</style>'
    '</head><body>'
    '<nav class="navbar navbar-dark bg-primary mb-4"><div class="container">'
    '<a class="navbar-brand" href="/">ЖД Билеты</a><div>'
    '{% if current_user.is_authenticated %}'
    '<a class="nav-link d-inline text-white" href="/search">Поиск</a> '
    '<a class="nav-link d-inline text-white" href="/my-tickets">Мои билеты</a> '
    '{% if current_user.is_admin %}<a class="nav-link d-inline text-white fw-bold" href="/admin/">Админ-панель</a> {% endif %}'
    '<a class="nav-link d-inline text-white" href="/logout">Выйти</a>'
    '{% else %}'
    '<a class="nav-link d-inline text-white" href="/login">Вход</a> '
    '<a class="nav-link d-inline text-white" href="/register">Регистрация</a>'
    '{% endif %}</div></div></nav>'
    '<div class="container pb-5">'
    '{% for c,t in get_flashed_messages(with_categories=true) %}<div class="alert alert-{{c}} alert-dismissible">{{t}}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>{% endfor %}'
    '{{content|safe}}</div></body></html>')

def page(c, **kwargs):
    c = c.replace('{{ csrf_token() }}', generate_csrf())
    rendered_content = render_template_string(c, **kwargs)
    return render_template_string(BASE, content=rendered_content)
CSRF_TOKEN = '<input type="hidden" name="csrf_token" value="{{ csrf_token() }}">'

def admin_required(f):
    @wraps(f)
    @login_required
    def w(*a, **kw):
        if not current_user.is_admin: abort(403)
        return f(*a, **kw)
    return w

def log_action(action_text):
    db.session.add(AdminLog(admin_id=current_user.id, action=action_text))

# ============ ПУБЛИЧНЫЕ МАРШРУТЫ ============
@app.route('/')
def index(): return page('<h1>ЖД Билеты</h1><a class="btn btn-primary" href="/search">Найти рейс</a>')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u, e, p = request.form['username'].strip(), request.form['email'].strip(), request.form['password']
        
        # Читаем настройки безопасности из БД
        min_len = int(get_setting('pwd_min_len', 8))
        req_num = get_setting('pwd_req_num', '1') == '1'
        req_up = get_setting('pwd_req_up', '1') == '1'

        err = None
        if len(u) < 4: err = 'Имя минимум 4 символа'
        elif len(p) < min_len: err = f'Пароль должен быть не менее {min_len} символов'
        elif req_num and not any(c.isdigit() for c in p): err = 'Пароль должен содержать цифру'
        elif req_up and not any(c.isupper() for c in p): err = 'Пароль должен содержать заглавную букву'
        elif User.query.filter((User.username == u) | (User.email == e)).first(): err = 'Пользователь уже существует'

        if err:
            flash(err, 'danger')
        else:
            x = User(username=u, email=e); x.set_pw(p); db.session.add(x); db.session.commit()
            flash('Готово, войдите', 'success'); return redirect('/login')

    return page('<h2>Регистрация</h2><form method="post" style="max-width:400px">' + CSRF_TOKEN +
        '<input name="username" class="form-control mb-2" placeholder="Имя" required>'
        '<input name="email" type="email" class="form-control mb-2" placeholder="Email" required>'
        '<input name="password" type="password" class="form-control mb-2" placeholder="Пароль" required>'
        '<button class="btn btn-primary">ОК</button></form>')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(email=request.form['email']).first()
        lockout = int(get_setting('lockout_attempts', 5))
        
        if u:
            if u.failed_attempts >= lockout:
                flash('Аккаунт заблокирован из-за множества неудачных попыток.', 'danger')
                return redirect('/login')
            
            if u.check_pw(request.form['password']):
                u.failed_attempts = 0
                db.session.commit()
                login_user(u); return redirect(request.args.get('next') or '/')
            else:
                u.failed_attempts += 1
                db.session.commit()
                flash(f'Неверный пароль. Попытка {u.failed_attempts} из {lockout}', 'danger')
        else:
            flash('Неверный email или пароль', 'danger')
            
    return page('<h2>Вход</h2><form method="post" style="max-width:400px">' + CSRF_TOKEN +
        '<input name="email" type="email" class="form-control mb-2" placeholder="Email" required>'
        '<input name="password" type="password" class="form-control mb-2" placeholder="Пароль" required>'
        '<button class="btn btn-primary">Войти</button></form>')

@app.route('/logout')
def logout(): logout_user(); return redirect('/')

@app.route('/search', methods=['GET', 'POST'])
def search():
    stations = Station.query.all()
    opts = ''.join(f'<option value="{s.id}">{s.city} — {s.name}</option>' for s in stations)
    res = ''
    if request.method == 'POST':
        try:
            o, d = int(request.form['origin']), int(request.form['destination'])
            dt = datetime.strptime(request.form['date'], '%Y-%m-%d')
            rows = Schedule.query.join(Route).filter(
                Route.origin_station_id == o, Route.destination_station_id == d,
                Schedule.is_active == True, Schedule.departure_time >= dt,
                Schedule.departure_time < dt + timedelta(days=1)).all()
            if not rows: res = '<p>Не найдено</p>'
            else:
                trs = ''
                for s in rows:
                    btn = f'<a class="btn btn-sm btn-success" href="/book/{s.id}">Купить</a>' if s.free > 0 else 'нет'
                    trs += (f'<tr><td>{s.train.number} {s.train.name or ""}</td>'
                            f'<td>{s.route.origin.name} → {s.route.destination.name}</td>'
                            f'<td>{s.departure_time.strftime("%d.%m %H:%M")}</td>'
                            f'<td>{s.base_price:.2f}</td><td>{s.free}</td><td>{btn}</td></tr>')
                res = ('<table class="table"><tr><th>Поезд</th><th>Маршрут</th>'
                       '<th>Отпр.</th><th>Цена</th><th>Своб.</th><th></th></tr>' + trs + '</table>')
        except Exception as ex: flash(f'Ошибка: {ex}', 'danger')
    form = ('<h2>Поиск</h2><form method="post" class="row g-2 mb-3">' + CSRF_TOKEN +
            f'<div class="col"><select name="origin" class="form-select">{opts}</select></div>'
            f'<div class="col"><select name="destination" class="form-select">{opts}</select></div>'
            '<div class="col"><input name="date" type="date" class="form-control" required></div>'
            '<div class="col"><button class="btn btn-primary w-100">Найти</button></div></form>')
    return page(form + res)

@app.route('/book/<int:sid>', methods=['GET', 'POST'])
@login_required
def book(sid):
    s = db.session.get(Schedule, sid) or abort(404)
    if s.free <= 0: flash('Нет мест', 'danger'); return redirect('/search')
    if request.method == 'POST':
        n = request.form['name'].strip()
        if len(n) < 3: flash('ФИО минимум 3', 'danger')
        else:
            t = Ticket(user_id=current_user.id, schedule_id=s.id, passenger_name=n, price_paid=s.base_price)
            db.session.add(t); db.session.commit()
            flash('Куплено', 'success'); return redirect('/my-tickets')
    return page(f'<h2>Билет {s.train.number}</h2><p>{s.route.origin.name} → {s.route.destination.name} '
                f'| {s.departure_time.strftime("%d.%m.%Y %H:%M")} | {s.base_price:.2f} ₽</p>'
                '<form method="post">' + CSRF_TOKEN +
                '<input name="name" class="form-control mb-2" placeholder="ФИО" required>'
                '<button class="btn btn-success">Купить</button></form>')

@app.route('/my-tickets')
@login_required
def my_tickets():
    items = Ticket.query.filter_by(user_id=current_user.id).order_by(Ticket.id.desc()).all()
    if not items: return page('<h2>Мои билеты</h2><p>Пусто</p>')
    trs = ''
    for t in items:
        act = (f'<form method="post" action="/refund/{t.id}" style="display:inline">' + CSRF_TOKEN +
               '<button class="btn btn-sm btn-outline-danger">Вернуть</button></form>' if t.status == 'active' else '')
        trs += (f'<tr><td>{t.id}</td><td>{t.schedule.train.number}</td>'
                f'<td>{t.schedule.route.origin.name} → {t.schedule.route.destination.name}</td>'
                f'<td>{t.schedule.departure_time.strftime("%d.%m.%Y %H:%M")}</td>'
                f'<td>{t.price_paid:.2f}</td><td>{t.status}</td><td>{act}</td></tr>')
    return page('<h2>Мои билеты</h2><table class="table"><tr><th>#</th><th>Поезд</th>'
                '<th>Маршрут</th><th>Отпр.</th><th>Цена</th><th>Статус</th><th></th></tr>' + trs + '</table>')

@app.route('/refund/<int:tid>', methods=['POST'])
@login_required
def refund(tid):
    t = db.session.get(Ticket, tid) or abort(404)
    if t.user_id != current_user.id: abort(403)
    if t.status != 'active': flash('Нельзя вернуть', 'danger')
    else:
        fee = round(t.price_paid * 0.1, 2)
        db.session.add(Refund(ticket_id=t.id, amount_returned=round(t.price_paid - fee, 2), fee_charged=fee))
        t.status = 'refunded'; db.session.commit()
        flash(f'Возврат: {t.price_paid - fee:.2f} (сбор {fee:.2f})', 'success')
    return redirect('/my-tickets')


# ============ АДМИНКА: ГЛАВНАЯ ============
@app.route('/admin/')
@admin_required
def admin_dash():
    sales = float(db.session.query(func.coalesce(func.sum(Ticket.price_paid), 0)).filter(Ticket.status == 'active').scalar())
    refs = float(db.session.query(func.coalesce(func.sum(Refund.amount_returned), 0)).scalar())
    return page(f'''
    <h2 class="mb-4">Панель администратора</h2>
    <div class="row mb-4">
        <div class="col-md-4"><div class="card bg-primary text-white p-3"><h6>Продажи</h6><h3>{sales:.2f} ₽</h3></div></div>
        <div class="col-md-4"><div class="card bg-warning text-dark p-3"><h6>Возвраты</h6><h3>{refs:.2f} ₽</h3></div></div>
    </div>
    <div class="card p-3 mb-4">
        <h5>Управление</h5>
        <div class="d-flex gap-2 flex-wrap mt-2">
            <a class="btn btn-outline-primary" href="/admin/stations">Станции</a>
            <a class="btn btn-outline-primary" href="/admin/trains">Поезда</a>
            <a class="btn btn-outline-primary" href="/admin/routes">Маршруты</a>
            <a class="btn btn-outline-primary" href="/admin/schedules">Расписание</a>
            <a class="btn btn-outline-dark" href="/admin/logs">Журнал действий</a>
        </div>
    </div>
    <div class="row">
        <div class="col-md-6">
            <div class="card p-4 h-100">
                <h4 class="text-warning">Безопасность</h4>
                <form method="post" action="/admin/settings/security">
                    ''' + CSRF_TOKEN + f'''
                    <label class="form-label mt-2">Мин. длина пароля</label>
                    <input type="number" name="min_len" class="form-control mb-2" value="{get_setting('pwd_min_len', 8)}">
                    <div class="form-check"><input class="form-check-input" type="checkbox" name="req_num" value="1" {'checked' if get_setting('pwd_req_num', '1')=='1' else ''}><label class="form-check-label">Требовать цифры</label></div>
                    <div class="form-check mb-2"><input class="form-check-input" type="checkbox" name="req_up" value="1" {'checked' if get_setting('pwd_req_up', '1')=='1' else ''}><label class="form-check-label">Требовать заглавные буквы</label></div>
                    <label class="form-label">Блокировка после N неудачных входов</label>
                    <input type="number" name="lockout" class="form-control mb-3" value="{get_setting('lockout_attempts', 5)}">
                    <button class="btn btn-warning">Сохранить политику</button>
                </form>
            </div>
        </div>
        <div class="col-md-6">
            <div class="card p-4 h-100">
                <h4 class="text-success">Резервное копирование</h4>
                <form method="post" action="/admin/backup">
                    ''' + CSRF_TOKEN + '''
                    <button class="btn btn-success mb-3">Создать бэкап сейчас</button>
                </form>
                <table class="table table-sm">
                    <tr><th>#</th><th>Начало</th><th>Статус</th><th>Файл</th></tr>
                    {% for b in backups %}
                    <tr>
                        <td>{{ b.id }}</td>
                        <td>{{ b.started_at.strftime('%d.%m %H:%M') }}</td>
                        <td><span class="badge bg-{{ 'success' if b.status == 'success' else 'danger' }}">{{ b.status }}</span></td>
                        <td><small>{{ b.filename or '-' }}</small></td>
                    </tr>
                    {% endfor %}
                </table>
            </div>
        </div>
    </div>
    ''', backups=BackupLog.query.order_by(BackupLog.id.desc()).limit(5).all())

@app.route('/admin/settings/security', methods=['POST'])
@admin_required
def admin_settings_security():
    set_setting('pwd_min_len', request.form.get('min_len', 8))
    set_setting('pwd_req_num', '1' if request.form.get('req_num') else '0')
    set_setting('pwd_req_up', '1' if request.form.get('req_up') else '0')
    set_setting('lockout_attempts', request.form.get('lockout', 5))
    log_action("Обновлены настройки безопасности")
    db.session.commit()
    flash('Настройки безопасности сохранены', 'success')
    return redirect('/admin/')

@app.route('/admin/backup', methods=['POST'])
@admin_required
def admin_backup():
    log = BackupLog(status='running')
    db.session.add(log)
    db.session.commit()
    
    try:
        # Указываем точный путь к базе данных (в папке instance)
        db_path = os.path.join(app.instance_path, 'railway.db')
        
        # Создаем папку для бэкапов там же
        backup_dir = os.path.join(app.instance_path, 'backups')
        os.makedirs(backup_dir, exist_ok=True)
        
        filename = f"railway_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        dest = os.path.join(backup_dir, filename)
        
        # Копируем файл
        shutil.copy2(db_path, dest)
        
        log.status = 'success'
        log.filename = filename
        log_action("Создан бэкап БД")
    except Exception as e:
        # Если произойдет ошибка, выводим её в консоль, чтобы мы её увидели
        print(f"❌ ОШИБКА БЭКАПА: {e}") 
        log.status = 'failed'
    
    log.finished_at = datetime.utcnow()
    db.session.commit()
    flash('Процесс резервного копирования завершен', 'info')
    return redirect('/admin/')

# ============ АДМИНКА: CRUD ============
@app.route('/admin/stations', methods=['GET', 'POST'])
@admin_required
def a_stations():
    if request.method == 'POST':
        try:
            s = Station(name=request.form['name'], code=request.form['code'], city=request.form['city'])
            db.session.add(s); log_action(f"Добавлена станция {s.name}"); db.session.commit()
            flash('Станция добавлена', 'success')
        except IntegrityError:
            db.session.rollback(); flash('Ошибка: Станция уже существует!', 'danger')
        return redirect('/admin/stations')
    rows = ''.join(f'<tr><td>{s.id}</td><td>{s.name}</td><td>{s.code}</td><td>{s.city}</td>'
                   f'<td><a href="/admin/stations/{s.id}/del" class="text-danger">Уд</a></td></tr>' for s in Station.query.all())
    return page('<h2>Станции</h2><form method="post" class="row g-2 mb-3">' + CSRF_TOKEN +
                '<div class="col"><input name="name" class="form-control" placeholder="Название" required></div>'
                '<div class="col"><input name="code" class="form-control" placeholder="Код" required></div>'
                '<div class="col"><input name="city" class="form-control" placeholder="Город" required></div>'
                '<div class="col"><button class="btn btn-primary">Добавить</button></div></form>'
                '<table class="table">' + rows + '</table>')

@app.route('/admin/stations/<int:i>/del')
@admin_required
def a_stations_del(i):
    s = db.session.get(Station, i) or abort(404)
    log_action(f"Удалена станция {s.name}")
    db.session.delete(s); db.session.commit(); flash('Удалено', 'success')
    return redirect('/admin/stations')

@app.route('/admin/trains', methods=['GET', 'POST'])
@admin_required
def a_trains():
    if request.method == 'POST':
        try:
            t = Train(number=request.form['number'], name=request.form['name'], total_seats=int(request.form['seats']))
            db.session.add(t); log_action(f"Добавлен поезд {t.number}"); db.session.commit()
            flash('Поезд добавлен', 'success')
        except IntegrityError:
            db.session.rollback(); flash('Ошибка: Поезд уже существует!', 'danger')
        return redirect('/admin/trains')
    rows = ''.join(f'<tr><td>{t.id}</td><td>{t.number}</td><td>{t.name}</td><td>{t.total_seats}</td>'
                   f'<td><a href="/admin/trains/{t.id}/del" class="text-danger">Уд</a></td></tr>' for t in Train.query.all())
    return page('<h2>Поезда</h2><form method="post" class="row g-2 mb-3">' + CSRF_TOKEN +
                '<div class="col"><input name="number" class="form-control" placeholder="Номер" required></div>'
                '<div class="col"><input name="name" class="form-control" placeholder="Название"></div>'
                '<div class="col"><input name="seats" type="number" class="form-control" placeholder="Мест" required></div>'
                '<div class="col"><button class="btn btn-primary">Добавить</button></div></form>'
                '<table class="table">' + rows + '</table>')

@app.route('/admin/trains/<int:i>/del')
@admin_required
def a_trains_del(i):
    t = db.session.get(Train, i) or abort(404)
    log_action(f"Удален поезд {t.number}")
    db.session.delete(t); db.session.commit(); flash('Удалено', 'success')
    return redirect('/admin/trains')

@app.route('/admin/routes', methods=['GET', 'POST'])
@admin_required
def a_routes():
    if request.method == 'POST':
        db.session.add(Route(origin_station_id=int(request.form['o']), destination_station_id=int(request.form['d'])))
        log_action("Добавлен маршрут"); db.session.commit(); return redirect('/admin/routes')
    opts = ''.join(f'<option value="{s.id}">{s.name}</option>' for s in Station.query.all())
    rows = ''.join(f'<tr><td>{r.id}</td><td>{r.origin.name}</td><td>{r.destination.name}</td>'
                   f'<td><a href="/admin/routes/{r.id}/del" class="text-danger">Уд</a></td></tr>' for r in Route.query.all())
    return page('<h2>Маршруты</h2><form method="post" class="row g-2 mb-3">' + CSRF_TOKEN +
                f'<div class="col"><select name="o" class="form-select">{opts}</select></div>'
                f'<div class="col"><select name="d" class="form-select">{opts}</select></div>'
                '<div class="col"><button class="btn btn-primary">Добавить</button></div></form>'
                '<table class="table">' + rows + '</table>')

@app.route('/admin/routes/<int:i>/del')
@admin_required
def a_routes_del(i):
    db.session.delete(db.session.get(Route, i) or abort(404)); db.session.commit()
    return redirect('/admin/routes')

@app.route('/admin/schedules', methods=['GET', 'POST'])
@admin_required
def a_schedules():
    if request.method == 'POST':
        try:
            dep = datetime.strptime(request.form['dep'], '%Y-%m-%dT%H:%M')
            arr = datetime.strptime(request.form['arr'], '%Y-%m-%dT%H:%M')
            if arr <= dep: flash('Прибытие должно быть позже отправления', 'danger')
            else:
                db.session.add(Schedule(train_id=int(request.form['t']), route_id=int(request.form['r']), departure_time=dep, arrival_time=arr, base_price=float(request.form['price'])))
                log_action("Добавлено расписание"); db.session.commit(); return redirect('/admin/schedules')
        except Exception as ex: flash(str(ex), 'danger')
    trains = ''.join(f'<option value="{t.id}">{t.number}</option>' for t in Train.query.all())
    routes = ''.join(f'<option value="{r.id}">{r.origin.name} → {r.destination.name}</option>' for r in Route.query.all())
    rows = ''.join(f'<tr><td>{s.id}</td><td>{s.train.number}</td><td>{s.route.origin.name} → {s.route.destination.name}</td>'
                   f'<td>{s.departure_time.strftime("%d.%m %H:%M")}</td><td>{s.base_price:.2f}</td>'
                   f'<td><a href="/admin/schedules/{s.id}/del" class="text-danger">Уд</a></td></tr>' for s in Schedule.query.all())
    return page('<h2>Расписание</h2><form method="post" class="row g-2 mb-3">' + CSRF_TOKEN +
                f'<div class="col"><select name="t" class="form-select">{trains}</select></div>'
                f'<div class="col"><select name="r" class="form-select">{routes}</select></div>'
                '<div class="col"><input name="dep" type="datetime-local" class="form-control" required></div>'
                '<div class="col"><input name="arr" type="datetime-local" class="form-control" required></div>'
                '<div class="col"><input name="price" type="number" class="form-control" placeholder="Цена" required></div>'
                '<div class="col"><button class="btn btn-primary">Добавить</button></div></form>'
                '<table class="table">' + rows + '</table>')

@app.route('/admin/schedules/<int:i>/del')
@admin_required
def a_schedules_del(i):
    db.session.delete(db.session.get(Schedule, i) or abort(404)); db.session.commit()
    return redirect('/admin/schedules')

@app.route('/admin/logs')
@admin_required
def a_logs():
    logs = AdminLog.query.order_by(AdminLog.timestamp.desc()).all()
    rows = ''.join(f'<tr><td>{l.timestamp.strftime("%d.%m.%Y %H:%M")}</td><td>{l.admin.username if l.admin else "Sys"}</td><td>{l.action}</td></tr>' for l in logs)
    return page('<h2>Журнал действий</h2><table class="table table-sm"><tr><th>Время</th><th>Админ</th><th>Действие</th></tr>' + rows + '</table>')

# ============ ЗАПУСК ============
def seed():
    db.create_all()
    if not AppSetting.query.first():
        # Дефолтные настройки безопасности
        set_setting('pwd_min_len', 8); set_setting('pwd_req_num', '1'); set_setting('pwd_req_up', '1'); set_setting('lockout_attempts', 5)
        db.session.commit()
        
    if User.query.first(): return
    a = User(username='admin', email='admin@example.com', role='admin'); a.set_pw('Admin123')
    db.session.add(a); db.session.commit()
    
    msk = Station(name='Москва', code='MSK', city='Москва')
    spb = Station(name='СПб', code='SPB', city='Санкт-Петербург')
    db.session.add_all([msk, spb]); db.session.commit()
    sap = Train(number='123А', name='Сапсан', total_seats=50)
    db.session.add(sap); db.session.commit()
    r = Route(origin_station_id=msk.id, destination_station_id=spb.id)
    db.session.add(r); db.session.commit()
    n = datetime.utcnow()
    db.session.add(Schedule(train_id=sap.id, route_id=r.id, departure_time=n+timedelta(days=1, hours=3), arrival_time=n+timedelta(days=1, hours=7), base_price=2500))
    db.session.commit()

if __name__ == '__main__':
    with app.app_context():
        seed()
    app.run(debug=True)