# app.py - Flask railway tickets + refunds app
# pip install flask flask-sqlalchemy flask-login

from flask import Flask, request, redirect, abort, flash, render_template_string
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
from functools import wraps
from sqlalchemy import func

app = Flask(__name__)
app.config['SECRET_KEY'] = 'k'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///railway.db'
db = SQLAlchemy(app)
lm = LoginManager(app)
lm.login_view = 'login'


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True)
    email = db.Column(db.String(120), unique=True)
    pw_hash = db.Column(db.String(256))
    role = db.Column(db.String(20), default='passenger')
    def set_pw(self, p): self.pw_hash = generate_password_hash(p)
    def check_pw(self, p): return check_password_hash(self.pw_hash, p)
    @property
    def is_admin(self): return self.role == 'admin'


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
    passport = db.Column(db.String(20))
    car_number = db.Column(db.String(5))
    seat_number = db.Column(db.String(5))
    price_paid = db.Column(db.Float)
    status = db.Column(db.String(20), default='active')
    booking_date = db.Column(db.DateTime, default=datetime.utcnow)
    journey_date = db.Column(db.Date)
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


BASE = (
    '<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>ЖД Билеты</title>'
    '<link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">'
    '</head><body>'
    '<nav class="navbar navbar-dark bg-primary"><div class="container">'
    '<a class="navbar-brand" href="/">ЖД Билеты</a><div>'
    '{% if current_user.is_authenticated %}'
    '<a class="nav-link d-inline text-white" href="/search">Поиск</a> '
    '<a class="nav-link d-inline text-white" href="/my-tickets">Мои билеты</a> '
    '{% if current_user.is_admin %}<a class="nav-link d-inline text-white" href="/admin/">Админ</a> {% endif %}'
    '<a class="nav-link d-inline text-white" href="/logout">Выйти</a>'
    '{% else %}'
    '<a class="nav-link d-inline text-white" href="/login">Вход</a> '
    '<a class="nav-link d-inline text-white" href="/register">Регистрация</a>'
    '{% endif %}</div></div></nav>'
    '<div class="container my-4">'
    '{% for c,t in get_flashed_messages(with_categories=true) %}<div class="alert alert-{{c}}">{{t}}</div>{% endfor %}'
    '{{content|safe}}</div></body></html>')


def page(c): return render_template_string(BASE, content=c)


def admin_required(f):
    @wraps(f)
    @login_required
    def w(*a, **kw):
        if not current_user.is_admin: abort(403)
        return f(*a, **kw)
    return w


@app.route('/')
def index(): return page('<h1>ЖД Билеты</h1><a class="btn btn-primary" href="/search">Найти рейс</a>')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        u, e, p = request.form['username'].strip(), request.form['email'].strip(), request.form['password']
        if len(u) < 4: flash('Имя минимум 4', 'danger')
        elif len(p) < 8 or not any(c.isdigit() for c in p) or not any(c.isupper() for c in p):
            flash('Пароль: 8+ символов, цифра, заглавная', 'danger')
        elif User.query.filter((User.username == u) | (User.email == e)).first():
            flash('Уже существует', 'danger')
        else:
            x = User(username=u, email=e); x.set_pw(p); db.session.add(x); db.session.commit()
            flash('Готово, войдите', 'success'); return redirect('/login')
    return page('<h2>Регистрация</h2><form method="post">'
        '<input name="username" class="form-control mb-2" placeholder="Имя" required>'
        '<input name="email" type="email" class="form-control mb-2" placeholder="Email" required>'
        '<input name="password" type="password" class="form-control mb-2" placeholder="Пароль" required>'
        '<button class="btn btn-primary">ОК</button></form>')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = User.query.filter_by(email=request.form['email']).first()
        if u and u.check_pw(request.form['password']):
            login_user(u); return redirect(request.args.get('next') or '/')
        flash('Неверный email или пароль', 'danger')
    return page('<h2>Вход</h2><form method="post">'
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
            o = int(request.form['origin']); d = int(request.form['destination'])
            dt = datetime.strptime(request.form['date'], '%Y-%m-%d')
            rows = (Schedule.query.join(Route).filter(
                Route.origin_station_id == o, Route.destination_station_id == d,
                Schedule.is_active == True, Schedule.departure_time >= dt,
                Schedule.departure_time < dt + timedelta(days=1)).all())
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
        except Exception as ex:
            flash(f'Ошибка: {ex}', 'danger')
    form = ('<h2>Поиск</h2><form method="post" class="row g-2 mb-3">'
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
            t = Ticket(user_id=current_user.id, schedule_id=s.id, passenger_name=n,
                       passport=request.form.get('passport', ''), car_number=request.form.get('car', ''),
                       seat_number=request.form.get('seat', ''), price_paid=s.base_price,
                       journey_date=s.departure_time.date())
            db.session.add(t); db.session.commit()
            flash('Куплено', 'success'); return redirect('/my-tickets')
    return page(f'<h2>Билет {s.train.number}</h2><p>{s.route.origin.name} → {s.route.destination.name} '
                f'| {s.departure_time.strftime("%d.%m.%Y %H:%M")} | {s.base_price:.2f} ₽</p>'
                '<form method="post"><input name="name" class="form-control mb-2" placeholder="ФИО" required>'
                '<input name="passport" class="form-control mb-2" placeholder="Паспорт">'
                '<input name="car" class="form-control mb-2" placeholder="Вагон">'
                '<input name="seat" class="form-control mb-2" placeholder="Место">'
                '<button class="btn btn-success">Купить</button></form>')


@app.route('/my-tickets')
@login_required
def my_tickets():
    items = Ticket.query.filter_by(user_id=current_user.id).order_by(Ticket.id.desc()).all()
    if not items: return page('<h2>Мои билеты</h2><p>Пусто</p>')
    trs = ''
    for t in items:
        act = (f'<form method="post" action="/refund/{t.id}">'
               '<button class="btn btn-sm btn-outline-danger">Вернуть</button></form>'
               if t.status == 'active' else '')
        trs += (f'<tr><td>{t.id}</td><td>{t.schedule.train.number}</td>'
                f'<td>{t.schedule.route.origin.name} → {t.schedule.route.destination.name}</td>'
                f'<td>{t.schedule.departure_time.strftime("%d.%m.%Y %H:%M")}</td>'
                f'<td>{t.price_paid:.2f}</td><td>{t.status}</td><td>{act}</td></tr>')
    return page('<h2>Мои билеты</h2><table class="table"><tr><th>#</th><th>Поезд</th>'
                '<th>Маршрут</th><th>Отпр.</th><th>Цена</th><th>Статус</th><th></th></tr>'
                + trs + '</table>')


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


@app.route('/admin/')
@admin_required
def admin_dash():
    sales = float(db.session.query(func.coalesce(func.sum(Ticket.price_paid), 0))
                  .filter(Ticket.status == 'active').scalar())
    refs = float(db.session.query(func.coalesce(func.sum(Refund.amount_returned), 0)).scalar())
    tc = Ticket.query.filter_by(status='active').count()
    rc = Refund.query.count()
    return page(f'<h2>Админ</h2><div class="row mb-3">'
                f'<div class="col"><div class="card p-3"><h6>Продажи</h6>'
                f'<h3>{sales:.2f} ₽</h3><small>{tc} билетов</small></div></div>'
                f'<div class="col"><div class="card p-3"><h6>Возвраты</h6>'
                f'<h3>{refs:.2f} ₽</h3><small>{rc} опер.</small></div></div></div>'
                '<div class="d-flex gap-2 flex-wrap">'
                '<a class="btn btn-outline-primary" href="/admin/stations">Станции</a>'
                '<a class="btn btn-outline-primary" href="/admin/trains">Поезда</a>'
                '<a class="btn btn-outline-primary" href="/admin/routes">Маршруты</a>'
                '<a class="btn btn-outline-primary" href="/admin/schedules">Расписание</a>'
                '<a class="btn btn-outline-primary" href="/admin/tickets">Билеты</a>'
                '<a class="btn btn-outline-primary" href="/admin/refunds">Возвраты</a></div>')


@app.route('/admin/stations', methods=['GET', 'POST'])
@admin_required
def a_stations():
    if request.method == 'POST':
        db.session.add(Station(name=request.form['name'], code=request.form['code'], city=request.form['city']))
        db.session.commit(); return redirect('/admin/stations')
    rows = ''.join(f'<tr><td>{s.id}</td><td>{s.name}</td><td>{s.code}</td><td>{s.city}</td>'
                   f'<td><a href="/admin/stations/{s.id}/del">Уд</a></td></tr>' for s in Station.query.all())
    return page('<h2>Станции</h2><form method="post" class="row g-2 mb-3">'
                '<div class="col"><input name="name" class="form-control" placeholder="Название" required></div>'
                '<div class="col"><input name="code" class="form-control" placeholder="Код" required></div>'
                '<div class="col"><input name="city" class="form-control" placeholder="Город" required></div>'
                '<div class="col"><button class="btn btn-primary">Добавить</button></div></form>'
                '<table class="table">' + rows + '</table>')


@app.route('/admin/stations/<int:i>/del')
@admin_required
def a_stations_del(i):
    db.session.delete(db.session.get(Station, i) or abort(404)); db.session.commit()
    return redirect('/admin/stations')


@app.route('/admin/trains', methods=['GET', 'POST'])
@admin_required
def a_trains():
    if request.method == 'POST':
        db.session.add(Train(number=request.form['number'], name=request.form['name'],
                             total_seats=int(request.form['seats'])))
        db.session.commit(); return redirect('/admin/trains')
    rows = ''.join(f'<tr><td>{t.id}</td><td>{t.number}</td><td>{t.name}</td><td>{t.total_seats}</td>'
                   f'<td><a href="/admin/trains/{t.id}/del">Уд</a></td></tr>' for t in Train.query.all())
    return page('<h2>Поезда</h2><form method="post" class="row g-2 mb-3">'
                '<div class="col"><input name="number" class="form-control" placeholder="Номер" required></div>'
                '<div class="col"><input name="name" class="form-control" placeholder="Название"></div>'
                '<div class="col"><input name="seats" type="number" class="form-control" placeholder="Мест" required></div>'
                '<div class="col"><button class="btn btn-primary">Добавить</button></div></form>'
                '<table class="table">' + rows + '</table>')


@app.route('/admin/trains/<int:i>/del')
@admin_required
def a_trains_del(i):
    db.session.delete(db.session.get(Train, i) or abort(404)); db.session.commit()
    return redirect('/admin/trains')


@app.route('/admin/routes', methods=['GET', 'POST'])
@admin_required
def a_routes():
    if request.method == 'POST':
        db.session.add(Route(origin_station_id=int(request.form['o']),
                             destination_station_id=int(request.form['d'])))
        db.session.commit(); return redirect('/admin/routes')
    opts = ''.join(f'<option value="{s.id}">{s.name}</option>' for s in Station.query.all())
    rows = ''.join(f'<tr><td>{r.id}</td><td>{r.origin.name}</td><td>{r.destination.name}</td>'
                   f'<td><a href="/admin/routes/{r.id}/del">Уд</a></td></tr>' for r in Route.query.all())
    return page('<h2>Маршруты</h2><form method="post" class="row g-2 mb-3">'
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
            if arr <= dep:
                flash('Прибытие должно быть позже отправления', 'danger')
            else:
                db.session.add(Schedule(train_id=int(request.form['t']),
                                        route_id=int(request.form['r']),
                                        departure_time=dep, arrival_time=arr,
                                        base_price=float(request.form['price'])))
                db.session.commit(); return redirect('/admin/schedules')
        except Exception as ex:
            flash(str(ex), 'danger')
    trains = ''.join(f'<option value="{t.id}">{t.number}</option>' for t in Train.query.all())
    routes = ''.join(f'<option value="{r.id}">{r.origin.name} → {r.destination.name}</option>' for r in Route.query.all())
    rows = ''.join(f'<tr><td>{s.id}</td><td>{s.train.number}</td>'
                   f'<td>{s.route.origin.name} → {s.route.destination.name}</td>'
                   f'<td>{s.departure_time.strftime("%d.%m %H:%M")}</td>'
                   f'<td>{s.base_price:.2f}</td><td>{s.sold}/{s.train.total_seats}</td>'
                   f'<td><a href="/admin/schedules/{s.id}/del">Уд</a></td></tr>'
                   for s in Schedule.query.all())
    return page('<h2>Расписание</h2><form method="post" class="row g-2 mb-3">'
                f'<div class="col"><select name="t" class="form-select">{trains}</select></div>'
                f'<div class="col"><select name="r" class="form-select">{routes}</select></div>'
                '<div class="col"><input name="dep" type="datetime-local" class="form-control" required></div>'
                '<div class="col"><input name="arr" type="datetime-local" class="form-control" required></div>'
                '<div class="col"><input name="price" type="number" step="0.01" class="form-control" placeholder="Цена" required></div>'
                '<div class="col"><button class="btn btn-primary">Добавить</button></div></form>'
                '<table class="table">' + rows + '</table>')


@app.route('/admin/schedules/<int:i>/del')
@admin_required
def a_schedules_del(i):
    db.session.delete(db.session.get(Schedule, i) or abort(404)); db.session.commit()
    return redirect('/admin/schedules')


@app.route('/admin/tickets')
@admin_required
def a_tickets():
    rows = ''.join(f'<tr><td>{t.id}</td><td>{t.passenger_name}</td>'
                   f'<td>{t.schedule.route.origin.name} → {t.schedule.route.destination.name}</td>'
                   f'<td>{t.journey_date.strftime("%d.%m.%Y")}</td>'
                   f'<td>{t.price_paid:.2f}</td><td>{t.status}</td></tr>'
                   for t in Ticket.query.order_by(Ticket.id.desc()).all())
    return page('<h2>Все билеты</h2><table class="table">'
                '<tr><th>#</th><th>ФИО</th><th>Маршрут</th><th>Дата</th><th>Цена</th><th>Статус</th></tr>'
                + rows + '</table>')


@app.route('/admin/refunds')
@admin_required
def a_refunds():
    rows = ''.join(f'<tr><td>{r.id}</td><td>#{r.ticket_id}</td>'
                   f'<td>{r.ticket.passenger_name}</td>'
                   f'<td>{r.amount_returned:.2f}</td><td>{r.fee_charged:.2f}</td>'
                   f'<td>{r.refund_date.strftime("%d.%m.%Y %H:%M")}</td></tr>'
                   for r in Refund.query.order_by(Refund.id.desc()).all())
    return page('<h2>Возвраты</h2><table class="table">'
                '<tr><th>#</th><th>Билет</th><th>ФИО</th><th>Возвращено</th><th>Сбор</th><th>Дата</th></tr>'
                + rows + '</table>')


def seed():
    db.create_all()
    if User.query.first(): return
    a = User(username='admin', email='admin@example.com', role='admin'); a.set_pw('Admin123')
    p = User(username='ivan', email='ivan@example.com'); p.set_pw('Pass1234')
    db.session.add_all([a, p]); db.session.commit()
    msk = Station(name='Москва', code='MSK', city='Москва')
    spb = Station(name='СПб', code='SPB', city='Санкт-Петербург')
    db.session.add_all([msk, spb]); db.session.commit()
    sap = Train(number='123А', name='Сапсан', total_seats=50)
    db.session.add(sap); db.session.commit()
    r = Route(origin_station_id=msk.id, destination_station_id=spb.id)
    db.session.add(r); db.session.commit()
    n = datetime.utcnow()
    db.session.add(Schedule(train_id=sap.id, route_id=r.id,
                            departure_time=n + timedelta(days=1, hours=3),
                            arrival_time=n + timedelta(days=1, hours=7),
                            base_price=2500))
    db.session.commit()
    print('Demo loaded. admin@example.com/Admin123, ivan@example.com/Pass1234')


if __name__ == '__main__':
    with app.app_context():
        seed()
    app.run(debug=True)