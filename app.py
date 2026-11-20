"""SHRPMS - Student Hostel and Rental Property Management System (Flask)."""
import io, os, secrets, smtplib
from datetime import date
from email.message import EmailMessage
from functools import wraps

from flask import (Flask, abort, flash, redirect, render_template, request,
                   send_file, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from flask_sqlalchemy import SQLAlchemy
from reportlab.lib.pagesizes import A5
from reportlab.pdfgen import canvas
from werkzeug.security import check_password_hash, generate_password_hash
HOSTEL_ADDRESS = os.getenv("HOSTEL_ADDRESS", "Votre adresse ici")
HOSTEL_PHONE = os.getenv("HOSTEL_PHONE", "+254 7XX XXX XXX")

db = SQLAlchemy()
login_manager = LoginManager()
login_manager.login_view = "login"


# ---------------------------------------------------------------- models
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False)  # landlord | tenant
    full_name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120))
    phone = db.Column(db.String(20))

    def set_password(self, pw): self.password_hash = generate_password_hash(pw)
    def check_password(self, pw): return check_password_hash(self.password_hash, pw)


class Room(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    number = db.Column(db.String(20), unique=True, nullable=False)
    monthly_rate = db.Column(db.Float, nullable=False)
    deposit = db.Column(db.Float, default=0)
    tenants = db.relationship("Tenant", back_populates="room")


class Tenant(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.ForeignKey("user.id"), nullable=False)
    room_id = db.Column(db.ForeignKey("room.id"))
    reg_no = db.Column(db.String(30))
    active = db.Column(db.Boolean, default=True)
    user = db.relationship("User")
    room = db.relationship("Room", back_populates="tenants")
    bills = db.relationship("Bill", back_populates="tenant")
    payments = db.relationship("Payment", back_populates="tenant")

    @property
    def balance(self):
        return round(sum(b.total for b in self.bills) -
                     sum(p.amount for p in self.payments), 2)


class Bill(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.ForeignKey("tenant.id"), nullable=False)
    period = db.Column(db.String(7), nullable=False)  # YYYY-MM
    rent = db.Column(db.Float, nullable=False)
    water_units = db.Column(db.Float, default=0)
    water_rate = db.Column(db.Float, default=0)
    elec_units = db.Column(db.Float, default=0)
    elec_rate = db.Column(db.Float, default=0)
    late_fee = db.Column(db.Float, default=0)
    total = db.Column(db.Float, nullable=False)
    tenant = db.relationship("Tenant", back_populates="bills")
    __table_args__ = (db.UniqueConstraint("tenant_id", "period"),)
    
    @property
    def status(self):
        return bill_status(self.tenant)[self.id]


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tenant_id = db.Column(db.ForeignKey("tenant.id"), nullable=False)
    amount = db.Column(db.Float, nullable=False)
    method = db.Column(db.String(20), default="M-Pesa")
    reference = db.Column(db.String(50))
    paid_on = db.Column(db.Date, default=date.today)
    tenant = db.relationship("Tenant", back_populates="payments")

    @property
    def receipt_no(self): return f"RCT-{self.id:05d}"


# --------------------------------------------------------------- helpers
def compute_bill_total(rent, w_units, w_rate, e_units, e_rate,  late_fee=0):
    """Automated billing: rent + water + electricity+  optional late fee."""
    return round(rent + w_units * w_rate + e_units * e_rate + late_fee, 2)


def bill_status(tenant):
    """Share the tenant's payments over the bills, oldest month first.
    Returns {bill_id: 'Paid' | 'Partial' | 'Unpaid'}."""
    remaining = round(sum(p.amount for p in tenant.payments), 2)
    result = {}
    for b in sorted(tenant.bills, key=lambda b: b.period):
        if remaining >= b.total:
            result[b.id] = "Paid"
            remaining = round(remaining - b.total, 2)
        elif remaining > 0:
            result[b.id] = "Partial"
            remaining = 0
        else:
            result[b.id] = "Unpaid"
    return result


def notify(tenant, subject, body):
    """Email via SMTP when configured (MAIL_SERVER env); otherwise log it.
    An SMS gateway (e.g. Africa's Talking) can be plugged in here."""
    host, to = os.getenv("MAIL_SERVER"), tenant.user.email
    if host and to:
        msg = EmailMessage()
        msg["Subject"], msg["To"] = subject, to
        msg["From"] = os.getenv("MAIL_FROM", "noreply@shrpms.local")
        msg.set_content(body)
        with smtplib.SMTP(host, int(os.getenv("MAIL_PORT", 587))) as s:
            s.starttls()
            s.login(os.getenv("MAIL_USER", ""), os.getenv("MAIL_PASS", ""))
            s.send_message(msg)
    else:
        print(f"[NOTIFY] {tenant.user.full_name}: {subject} - {body}")


def landlord_only(f):
    @wraps(f)
    @login_required
    def wrapper(*a, **kw):
        if current_user.role != "landlord":
            abort(403)
        return f(*a, **kw)
    return wrapper


def make_receipt_pdf(p):
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A5)
    w, h = A5
    logo = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "logo.png")
    if os.path.exists(logo):
        c.drawImage(logo, w - 100, h - 85, width=60, height=60,
                    preserveAspectRatio=True, mask="auto")
    c.setFont("Helvetica-Bold", 16); c.drawString(40, h - 50, "PAYMENT RECEIPT")
    c.setFont("Helvetica", 10); c.drawString(40, h - 68, os.getenv("HOSTEL_NAME", "SHRPMS"))
    c.setFont("Helvetica", 9)
    if HOSTEL_ADDRESS: c.drawString(40, h - 82, HOSTEL_ADDRESS)
    if HOSTEL_PHONE: c.drawString(40, h - 94, "Tel: " + HOSTEL_PHONE)
    rows = [("Receipt No", p.receipt_no), ("Date", p.paid_on.isoformat()),
            ("Tenant", p.tenant.user.full_name), ("Reg. No", p.tenant.reg_no or "-"),
            ("Room", p.tenant.room.number if p.tenant.room else "-"),
            ("Method", p.method), ("Reference", p.reference or "-"),
            ("Amount paid", f"KES {p.amount:,.2f}"),
                       ("Balance after payment" if p.tenant.balance >= 0 else "Advance credit",
             f"KES {abs(p.tenant.balance):,.2f}")]
    y = h - 130
    for k, v in rows:
        c.setFont("Helvetica-Bold", 10); c.drawString(40, y, k)
        c.setFont("Helvetica", 10); c.drawString(190, y, str(v)); y -= 22
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(40, 40, "Computer-generated receipt. Valid without signature.")
    c.save(); buf.seek(0)
    return buf


# ----------------------------------------------------------------- app
def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.getenv("SECRET_KEY", "change-me-in-production"),
        SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///shrpms.db"))
    if config:
        app.config.update(config)
    db.init_app(app); login_manager.init_app(app)

    @app.context_processor
    def inject_hostel():
        return {"hostel_name": os.getenv("HOSTEL_NAME", "SHRPMS")}

    @login_manager.user_loader
    def load_user(uid): return db.session.get(User, int(uid))

    @app.route("/", methods=["GET", "POST"])
    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            u = User.query.filter_by(username=request.form["username"].strip()).first()
            if u and u.check_password(request.form["password"]):
                login_user(u)
                return redirect(url_for("dashboard" if u.role == "landlord" else "portal"))
            flash("Wrong username or password.", "danger")
        return render_template("login.html")

    @app.route("/logout")
    def logout():
        logout_user(); return redirect(url_for("login"))

    @app.route("/dashboard")
    @landlord_only
    def dashboard():
        tenants = Tenant.query.filter_by(active=True).all()
        rooms = Room.query.all()
        occupied = sum(1 for r in rooms if any(t.active for t in r.tenants))
        stats = dict(
            revenue=sum(p.amount for p in Payment.query.all()),
            pending=sum(t.balance for t in tenants if t.balance > 0),
            occupancy=round(100 * occupied / len(rooms)) if rooms else 0,
            rooms=len(rooms), occupied=occupied)
        debtors = sorted((t for t in tenants if t.balance > 0),
                         key=lambda t: -t.balance)
        return render_template("dashboard.html", s=stats, debtors=debtors)

    @app.route("/rooms", methods=["GET", "POST"])
    @landlord_only
    def rooms():
        if request.method == "POST":
            try:
                db.session.add(Room(number=request.form["number"].strip(),
                                    monthly_rate=float(request.form["rate"]),
                                    deposit=float(request.form.get("deposit") or 0)))
                db.session.commit()
                flash("Room added.", "success")
            except Exception:
                db.session.rollback(); flash("Room number already exists or data invalid.", "danger")
        return render_template("rooms.html", rooms=Room.query.order_by(Room.number).all())

    @app.route("/tenants", methods=["GET", "POST"])
    @landlord_only
    def tenants():
        if request.method == "POST":
            f = request.form
            if User.query.filter_by(username=f["username"].strip()).first():
                flash("Username already taken.", "danger")
            else:
                u = User(username=f["username"].strip(), role="tenant",
                         full_name=f["full_name"].strip(), email=f.get("email"),
                         phone=f.get("phone"))
                u.set_password(f.get("password") or f["username"])
                db.session.add(u); db.session.flush()
                db.session.add(Tenant(user_id=u.id, reg_no=f.get("reg_no"),
                                      room_id=int(f["room_id"]) if f.get("room_id") else None))
                db.session.commit(); flash("Tenant registered.", "success")
        q = request.args.get("q", "").strip()
        query = Tenant.query.join(User).outerjoin(Room)
        if q:
            like = f"%{q}%"
            query = query.filter(db.or_(User.full_name.ilike(like), Tenant.reg_no.ilike(like),
                                        User.phone.ilike(like), Room.number.ilike(like)))
        return render_template("tenants.html", tenants=query.order_by(User.full_name).all(),
                               rooms=Room.query.order_by(Room.number).all(), q=q)

    @app.route("/bills", methods=["GET", "POST"])
    @landlord_only
    def bills():
        if request.method == "POST":
            f = request.form
            t = db.session.get(Tenant, int(f["tenant_id"]))
            if not t or not t.room:
                flash("Tenant must be assigned to a room first.", "danger")
            else:
                num = lambda k: float(f.get(k) or 0)
                total = compute_bill_total(t.room.monthly_rate, num("water_units"),
                                           num("water_rate"), num("elec_units"), num("elec_rate"),
                                           max(num("late_fee"), 0))
                b = Bill(tenant_id=t.id, period=f["period"], rent=t.room.monthly_rate,
                         water_units=num("water_units"), water_rate=num("water_rate"),
                         elec_units=num("elec_units"), elec_rate=num("elec_rate"),
                         late_fee=max(num("late_fee"), 0), total=total)
                try:
                    db.session.add(b); db.session.commit()
                    flash(f"Bill created: KES {total:,.2f}", "success")
                except Exception:
                    db.session.rollback(); flash("A bill already exists for this tenant and month.", "danger")
        return render_template("bills.html", bills=Bill.query.order_by(Bill.period.desc()).all(),
                               tenants=Tenant.query.filter_by(active=True).all(),
                               this_month=date.today().strftime("%Y-%m"))

    @app.route("/payments", methods=["GET", "POST"])
    @landlord_only
    def payments():
        if request.method == "POST":
            f = request.form
            try:
                amount = float(f["amount"])
                assert amount > 0
                p = Payment(tenant_id=int(f["tenant_id"]), amount=amount,
                            method=f.get("method"), reference=f.get("reference"))
                db.session.add(p); db.session.commit()
                notify(p.tenant, f"Payment received {p.receipt_no}",
                       f"We received KES {amount:,.2f}. Balance: KES {p.tenant.balance:,.2f}.")
                flash(f"Payment recorded. Receipt {p.receipt_no} ready.", "success")
            except Exception:
                db.session.rollback(); flash("Invalid payment data.", "danger")
        return render_template("payments.html",
                               payments=Payment.query.order_by(Payment.id.desc()).all(),
                               tenants=Tenant.query.filter_by(active=True).all())

    @app.route("/rooms/<int:rid>/edit", methods=["POST"])
    @landlord_only
    def room_edit(rid):
        r = db.get_or_404(Room, rid)
        try:
            r.monthly_rate = float(request.form["rate"])
            r.deposit = float(request.form.get("deposit") or 0)
            db.session.commit(); flash("Room updated.", "success")
        except ValueError:
            db.session.rollback(); flash("Invalid amount.", "danger")
        return redirect(url_for("rooms"))

    @app.route("/rooms/<int:rid>/delete", methods=["POST"])
    @landlord_only
    def room_delete(rid):
        r = db.get_or_404(Room, rid)
        if r.tenants:
            flash("Cannot delete a room that has tenant records.", "danger")
        else:
            db.session.delete(r); db.session.commit(); flash("Room deleted.", "success")
        return redirect(url_for("rooms"))

    @app.route("/tenants/<int:tid>/checkout", methods=["POST"])
    @landlord_only
    def checkout(tid):
        t = db.get_or_404(Tenant, tid)
        t.active = False; db.session.commit()
        flash(f"{t.user.full_name} checked out. Balance kept: KES {t.balance:,.2f}.", "success")
        return redirect(url_for("tenants"))

    @app.route("/tenants/<int:tid>/reactivate", methods=["POST"])
    @landlord_only
    def reactivate(tid):
        t = db.get_or_404(Tenant, tid)
        room = db.session.get(Room, int(request.form["room_id"])) if request.form.get("room_id") else None
        if room:
            t.room_id = room.id
        t.active = True
        db.session.commit()
        flash(f"{t.user.full_name} is active again. Balance: KES {t.balance:,.2f}.", "success")
        return redirect(url_for("tenants"))

    @app.route("/tenants/<int:tid>/reset-password", methods=["POST"])
    @landlord_only
    def reset_password(tid):
        t = db.get_or_404(Tenant, tid)
        alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
        temp = "".join(secrets.choice(alphabet) for _ in range(8))
        t.user.set_password(temp)
        db.session.commit()
        flash(f"New temporary password for {t.user.username}: {temp}. "
              "Give it to the tenant, who should change it with the Password button.", "warning")
        return redirect(url_for("tenants"))
    
    @app.route("/tenants/<int:tid>/delete", methods=["POST"])
    @landlord_only
    def delete_tenant(tid):
        t = db.get_or_404(Tenant, tid)
        name = t.user.full_name
        if t.bills or t.payments:
            flash(f"Cannot delete {name}: bills or payments exist. Use Check out instead.", "danger")
        else:
            user = t.user
            db.session.delete(t); db.session.delete(user); db.session.commit()
            flash(f"{name} deleted.", "success")
        return redirect(url_for("tenants"))

    @app.route("/password", methods=["GET", "POST"])
    @login_required
    def change_password():
        if request.method == "POST":
            f = request.form
            if not current_user.check_password(f["old"]):
                flash("Current password is wrong.", "danger")
            elif len(f["new"]) < 8 or f["new"] != f["confirm"]:
                flash("New password must have 8+ characters and match the confirmation.", "danger")
            else:
                current_user.set_password(f["new"]); db.session.commit()
                flash("Password changed.", "success")
        return render_template("password.html")

    @app.route("/remind", methods=["POST"])
    @landlord_only
    def remind():
        n = 0
        for t in Tenant.query.filter_by(active=True):
            if t.balance > 0:
                notify(t, "Rent reminder", f"Outstanding balance: KES {t.balance:,.2f}. Please pay soon.")
                n += 1
        flash(f"{n} reminder(s) sent.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/receipt/<int:pid>.pdf")
    @login_required
    def receipt(pid):
        p = db.get_or_404(Payment, pid)
        if current_user.role == "tenant" and p.tenant.user_id != current_user.id:
            abort(403)
        return send_file(make_receipt_pdf(p), mimetype="application/pdf",
                         download_name=f"{p.receipt_no}.pdf")

    @app.route("/portal")
    @login_required
    def portal():
        t = Tenant.query.filter_by(user_id=current_user.id).first()
        if not t:
            abort(403)
        return render_template("portal.html", t=t)

    @app.cli.command("init-db")
    def init_db():
        """Create tables, a landlord account and demo data."""
        db.create_all()
        if not User.query.filter_by(username="admin").first():
            a = User(username="admin", role="landlord", full_name="Landlord")
            a.set_password("admin123"); db.session.add(a)
            r = Room(number="A1", monthly_rate=6000, deposit=3000); db.session.add(r)
            u = User(username="student1", role="tenant", full_name="Demo Student",
                     email="student@example.com"); u.set_password("student123")
            db.session.add(u); db.session.flush()
            db.session.add(Tenant(user_id=u.id, room_id=r.id, reg_no="DCS-00-0001/2025"))
            db.session.commit()
        print("Database ready. Login: admin / admin123")

    return app


app = create_app()
