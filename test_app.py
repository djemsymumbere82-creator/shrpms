import pytest
from app import (create_app, db, User, Room, Tenant, Bill, Payment,
                 compute_bill_total)


@pytest.fixture
def client():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    with app.app_context():
        db.create_all()
        a = User(username="admin", role="landlord", full_name="L"); a.set_password("pw")
        r = Room(number="A1", monthly_rate=6000)
        u1 = User(username="s1", role="tenant", full_name="Stu One"); u1.set_password("pw")
        u2 = User(username="s2", role="tenant", full_name="Stu Two"); u2.set_password("pw")
        db.session.add_all([a, r, u1, u2]); db.session.flush()
        db.session.add_all([Tenant(user_id=u1.id, room_id=r.id),
                            Tenant(user_id=u2.id, room_id=r.id)])
        db.session.commit()
    return app.test_client()


def login(c, u, p="pw"):
    return c.post("/login", data={"username": u, "password": p}, follow_redirects=True)


BILL = {"tenant_id": 1, "period": "2026-08", "water_units": 10,
        "water_rate": 100, "elec_units": 20, "elec_rate": 25}

# ------------------------------------------------------- UNIT TESTS (Table 3.1)
def test_UT01_bill_total_with_utilities():
    assert compute_bill_total(6000, 10, 100, 20, 25) == 7500

def test_UT02_bill_total_without_utilities():
    assert compute_bill_total(6000, 0, 0, 0, 0) == 6000

def test_UT03_password_is_hashed(client):
    with client.application.app_context():
        u = User.query.filter_by(username="admin").first()
        assert u.password_hash != "pw" and u.check_password("pw")
        assert not u.check_password("wrong")

def test_UT04_receipt_number_format(client):
    with client.application.app_context():
        assert Payment(id=7).receipt_no == "RCT-00007"

def test_UT05_balance_calculation(client):
    with client.application.app_context():
        t = db.session.get(Tenant, 1)
        db.session.add(Bill(tenant_id=1, period="2026-08", rent=6000, total=7500))
        db.session.add(Payment(tenant_id=1, amount=2500)); db.session.commit()
        assert t.balance == 5000

def test_UT06_invalid_login_rejected(client):
    assert b"Wrong username" in login(client, "admin", "nope").data

def test_UT07_negative_payment_rejected(client):
    login(client, "admin")
    r = client.post("/payments", data={"tenant_id": 1, "amount": -50})
    assert b"Invalid payment" in r.data

def test_UT08_duplicate_room_rejected(client):
    login(client, "admin")
    r = client.post("/rooms", data={"number": "A1", "rate": 5000})
    assert b"already exists" in r.data

# ------------------------------------------------ INTEGRATION TESTS (Table 3.2)
def test_IT01_landlord_login_opens_dashboard(client):
    assert b"Revenue collected" in login(client, "admin").data

def test_IT02_tenant_login_opens_portal(client):
    assert b"Outstanding balance" in login(client, "s1").data

def test_IT03_register_tenant(client):
    login(client, "admin")
    r = client.post("/tenants", data={"full_name": "New Guy", "username": "new1",
                                      "room_id": 1}, follow_redirects=True)
    assert b"Tenant registered" in r.data and b"New Guy" in r.data

def test_IT04_bill_then_payment_updates_balance(client):
    login(client, "admin")
    client.post("/bills", data=BILL)
    client.post("/payments", data={"tenant_id": 1, "amount": 3000, "method": "Cash"})
    assert b"4,500.00" in client.get("/tenants").data

def test_IT05_receipt_pdf_generated(client):
    login(client, "admin")
    client.post("/payments", data={"tenant_id": 1, "amount": 1000})
    pdf = client.get("/receipt/1.pdf")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")

def test_IT06_duplicate_bill_rejected(client):
    login(client, "admin")
    client.post("/bills", data=BILL)
    assert b"already exists" in client.post("/bills", data=BILL).data

def test_IT07_tenant_sees_own_bill_in_portal(client):
    login(client, "admin"); client.post("/bills", data=BILL)
    client.get("/logout"); login(client, "s1")
    assert b"7,500.00" in client.get("/portal").data

def test_IT08_tenant_blocked_from_admin_pages(client):
    login(client, "s1")
    assert client.get("/rooms").status_code == 403

def test_IT09_tenant_cannot_download_other_receipt(client):
    login(client, "admin")
    client.post("/payments", data={"tenant_id": 2, "amount": 1000})
    client.get("/logout"); login(client, "s1")
    assert client.get("/receipt/1.pdf").status_code == 403

def test_IT10_dashboard_shows_pending_and_reminders(client):
    login(client, "admin"); client.post("/bills", data=BILL)
    assert b"7,500" in client.get("/dashboard").data
    assert b"1 reminder(s) sent" in client.post("/remind", follow_redirects=True).data


# ------------------------------------------------ CUSTOMISATION TESTS (v2)
def test_ET01_edit_room(client):
    login(client, "admin")
    client.post("/rooms/1/edit", data={"rate": 7000, "deposit": 1000})
    with client.application.app_context():
        assert db.session.get(Room, 1).monthly_rate == 7000

def test_ET02_delete_room_with_tenants_blocked(client):
    login(client, "admin")
    assert b"Cannot delete" in client.post("/rooms/1/delete", follow_redirects=True).data

def test_ET03_checkout_removes_tenant_from_billing(client):
    login(client, "admin")
    client.post("/tenants/1/checkout")
    assert b"Stu One (A1)" not in client.get("/bills").data

def test_ET04_change_password(client):
    login(client, "s1")
    client.post("/password", data={"old": "pw", "new": "newpass123", "confirm": "newpass123"})
    client.get("/logout")
    assert b"Outstanding balance" in login(client, "s1", "newpass123").data

def test_UT09_bill_total_with_late_fee():
    assert compute_bill_total(6000, 10, 100, 20, 25, late_fee=500) == 8000

def test_IT11_late_fee_added_to_bill_and_balance(client):
    login(client, "admin")
    client.post("/bills", data={**BILL, "late_fee": 500})
    assert b"8,000.00" in client.get("/bills").data
    assert b"8,000.00" in client.get("/tenants").data

def test_IT12_negative_late_fee_ignored(client):
    login(client, "admin")
    client.post("/bills", data={**BILL, "late_fee": -999})
    assert b"7,500.00" in client.get("/bills").data

def test_IT14_search_filters_tenants(client):
    login(client, "admin")
    r = client.get("/tenants?q=one")
    assert b"Stu One" in r.data and b"Stu Two" not in r.data
    assert b"Stu Two" in client.get("/tenants?q=A1").data
    assert b"No tenant matches" in client.get("/tenants?q=zzz").data    

def test_IT13_tenant_phone_shown_in_list(client):
    login(client, "admin")
    r = client.post("/tenants", data={"full_name": "Phone Guy", "username": "ph1",
                                      "phone": "0712345678", "room_id": 1})
    assert b"0712345678" in r.data

def test_ET05_reactivate_tenant(client):
    login(client, "admin")
    client.post("/tenants/1/checkout")
    assert b"Stu One (A1)" not in client.get("/bills").data
    client.post("/tenants/1/reactivate", data={"room_id": 1})
    assert b"Stu One (A1)" in client.get("/bills").data

def test_ET06_landlord_resets_tenant_password(client):
    login(client, "admin")
    r = client.post("/tenants/1/reset-password", follow_redirects=True)
    assert b"temporary password" in r.data
    client.get("/logout")
    assert b"Wrong username" in login(client, "s1", "pw").data

def test_ET07_tenant_cannot_reset_passwords(client):
    login(client, "s1")
    assert client.post("/tenants/2/reset-password").status_code == 403
    

def test_ET08_delete_tenant_without_history(client):
    login(client, "admin")
    client.post("/tenants", data={"full_name": "Temp Person", "username": "tmp1"})
    r = client.post("/tenants/3/delete", follow_redirects=True)
    assert b"Temp Person deleted" in r.data
    assert b"tmp1" not in client.get("/tenants").data
    client.get("/logout")
    assert b"Wrong username" in login(client, "tmp1", "tmp1").data


def test_ET09_cannot_delete_tenant_with_history(client):
    login(client, "admin")
    client.post("/bills", data=BILL)
    r = client.post("/tenants/1/delete", follow_redirects=True)
    assert b"Cannot delete" in r.data and b"Stu One" in r.data


def test_ET10_tenant_cannot_delete_tenants(client):
    login(client, "s1")
    assert client.post("/tenants/2/delete").status_code == 403