# SHRPMS - Flask

    python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
    pip install -r requirements.txt
    flask --app app init-db      # creates DB + admin/admin123 + demo student1/student123
    flask --app app run          # http://127.0.0.1:5000
    pytest                       # run the tests

Optional email: set MAIL_SERVER, MAIL_PORT, MAIL_USER, MAIL_PASS, MAIL_FROM.
Without it, notifications are printed in the console.
