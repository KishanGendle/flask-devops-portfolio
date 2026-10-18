
# import os
# import smtplib
# from email.message import EmailMessage

# import mysql.connector
# from dotenv import load_dotenv
# from flask import Flask, render_template, request, redirect, url_for, flash

# load_dotenv()

# app = Flask(__name__)

# app.config["SECRET_KEY"] = os.environ["FLASK_SECRET_KEY"]


# def get_db_connection():
#     return mysql.connector.connect(
#         host=os.environ["MYSQL_HOST"],
#         port=int(os.getenv("MYSQL_PORT", "3306")),
#         user=os.environ["MYSQL_USER"],
#         password=os.environ["MYSQL_PASSWORD"],
#         database=os.environ["MYSQL_DATABASE"],
#         connection_timeout=5,
#     )


# def send_contact_email(name, email, message):
#     mail = EmailMessage()
#     mail["Subject"] = f"Portfolio contact message from {name}"
#     mail["From"] = os.environ["MAIL_USERNAME"]
#     mail["To"] = os.environ["MAIL_RECEIVER"]
#     mail["Reply-To"] = email

#     mail.set_content(
#         f"New message received from your portfolio.\n\n"
#         f"Name: {name}\n"
#         f"Email: {email}\n\n"
#         f"Message:\n{message}"
#     )

#     with smtplib.SMTP(
#         os.environ["MAIL_SERVER"],
#         int(os.getenv("MAIL_PORT", "587")),
#         timeout=15,
#     ) as server:
#         server.starttls()
#         server.login(
#             os.environ["MAIL_USERNAME"],
#             os.environ["MAIL_PASSWORD"],
#         )
#         server.send_message(mail)


# @app.route("/", methods=["GET", "POST"])
# def home():
#     if request.method == "POST":
#         name = request.form.get("name", "").strip()
#         email = request.form.get("email", "").strip()
#         message = request.form.get("message", "").strip()

#         if not name or not email or not message:
#             flash("Please fill all fields.", "error")
#             return redirect(url_for("home") + "#contact")

#         if len(name) > 100 or len(email) > 255 or len(message) > 10000:
#             flash("One or more fields exceed the allowed length.", "error")
#             return redirect(url_for("home") + "#contact")

#         connection = None
#         cursor = None

#         try:
#             connection = get_db_connection()
#             cursor = connection.cursor()

#             cursor.execute(
#                 """
#                 INSERT INTO contact_messages (name, email, message)
#                 VALUES (%s, %s, %s)
#                 """,
#                 (name, email, message),
#             )
#             connection.commit()

#         except mysql.connector.Error:
#             app.logger.exception("Failed to save contact message")

#             if connection and connection.is_connected():
#                 connection.rollback()

#             flash("Unable to save your message. Please try again later.", "error")
#             return redirect(url_for("home") + "#contact")

#         finally:
#             if cursor:
#                 cursor.close()
#             if connection and connection.is_connected():
#                 connection.close()

#         # Database save is complete. Email failure must not erase the record.
#         try:
#             send_contact_email(name, email, message)
#             flash("Message saved and sent successfully!", "success")
#         except (OSError, smtplib.SMTPException, KeyError, ValueError):
#             app.logger.exception("Contact email notification failed")
#             flash(
#                 "Your message was saved, but email notification failed. "
#                 "Please try again later if needed.",
#                 "warning",
#             )

#         return redirect(url_for("home") + "#contact")

#     return render_template("index.html")


# @app.get("/health")
# def health():
#     return {"status": "healthy"}, 200


# if __name__ == "__main__":
#     app.run(debug=True)



import os
import re
import hmac
import hashlib
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps

import mysql.connector
from dotenv import load_dotenv
from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session
)
from authlib.integrations.flask_client import OAuth
from flask_wtf.csrf import CSRFProtect

load_dotenv()

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ["FLASK_SECRET_KEY"],
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("COOKIE_SECURE", "false").lower() == "true",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)

csrf = CSRFProtect(app)

oauth = OAuth(app)
oauth.register(
    name="google",
    server_metadata_url=(
        "https://accounts.google.com/.well-known/openid-configuration"
    ),
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    client_kwargs={"scope": "openid email profile"},
)


def get_db_connection():
    return mysql.connector.connect(
        host=os.environ["MYSQL_HOST"],
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.environ["MYSQL_USER"],
        password=os.environ["MYSQL_PASSWORD"],
        database=os.environ["MYSQL_DATABASE"],
        connection_timeout=5,
    )


def send_email(to_address, subject, body):
    mail = EmailMessage()
    mail["Subject"] = subject
    mail["From"] = os.environ["MAIL_USERNAME"]
    mail["To"] = to_address
    mail.set_content(body)

    with smtplib.SMTP(
        os.environ["MAIL_SERVER"],
        int(os.getenv("MAIL_PORT", "587")),
        timeout=15,
    ) as server:
        server.starttls()
        server.login(
            os.environ["MAIL_USERNAME"],
            os.environ["MAIL_PASSWORD"],
        )
        server.send_message(mail)


def send_contact_email(name, email, message):
    send_email(
        os.environ["MAIL_RECEIVER"],
        f"Portfolio contact message from {name}",
        f"Name: {name}\nEmail: {email}\n\nMessage:\n{message}",
    )


def save_user(email, name, provider, google_id=None):
    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            "SELECT id, email, name, google_id FROM users WHERE email=%s",
            (email,),
        )
        user = cursor.fetchone()

        if user:
            # Do not silently link a Google account to an existing
            # email account. Require the existing account owner to
            # authenticate through their established method.
            if provider == "google" and user["google_id"] not in (
                None, google_id
            ):
                raise ValueError("Google account does not match this user")

            if provider == "google" and not user["google_id"]:
                cursor.execute(
                    "UPDATE users SET google_id=%s WHERE id=%s",
                    (google_id, user["id"]),
                )
                connection.commit()

            return user["id"]

        cursor.execute(
            """
            INSERT INTO users (email, name, google_id, auth_provider)
            VALUES (%s, %s, %s, %s)
            """,
            (email, name, google_id, provider),
        )
        connection.commit()
        return cursor.lastrowid

    finally:
        cursor.close()
        connection.close()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            flash("Please login before sending a message.", "error")
            return redirect(url_for("login", next="contact"))
        return view(*args, **kwargs)
    return wrapped


def set_logged_in_user(user_id, email, name):
    session.clear()
    session["user_id"] = user_id
    session["user_email"] = email
    session["user_name"] = name
    session.permanent = True


@app.get("/login")
def login():
    return render_template("login.html")


@app.get("/login/google")
def google_login():
    callback = url_for("google_callback", _external=True)
    return oauth.google.authorize_redirect(callback)


@app.get("/auth/google/callback")
def google_callback():
    try:
        token = oauth.google.authorize_access_token()
        profile = token["userinfo"]

        email = (profile.get("email") or "").strip().lower()
        name = profile.get("name") or email

        if not email or profile.get("email_verified") is not True:
            flash("Google could not verify your email.", "error")
            return redirect(url_for("login"))

        user_id = save_user(
            email=email,
            name=name,
            provider="google",
            google_id=profile["sub"],
        )
        set_logged_in_user(user_id, email, name)
        flash("Google login successful.", "success")
        return redirect(url_for("home") + "#contact")

    except Exception:
        app.logger.exception("Google login failed")
        flash("Google login failed. Please try again.", "error")
        return redirect(url_for("login"))


@app.route("/login/email", methods=["GET", "POST"])
def email_login():
    if request.method == "GET":
        return render_template("email_login.html")

    email = request.form.get("email", "").strip().lower()

    if len(email) > 255 or not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+", email
    ):
        flash("Enter a valid email address.", "error")
        return redirect(url_for("email_login"))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        # Limit sending: one OTP per 60 seconds and at most 5
        # requests per email in a rolling hour.
        cursor.execute(
            """
            SELECT COUNT(*) AS total,
                   MAX(created_at) AS latest
            FROM email_otps
            WHERE email=%s
              AND created_at >= UTC_TIMESTAMP() - INTERVAL 1 HOUR
            """,
            (email,),
        )
        row = cursor.fetchone()

        if row["total"] >= 5 or (
            row["latest"] and
            row["latest"] > datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=60)
        ):
            flash("Please wait before requesting another OTP.", "error")
            return redirect(url_for("email_login"))

        otp = f"{secrets.randbelow(1000000):06d}"
        otp_hash = hmac.new(
            app.config["SECRET_KEY"].encode(),
            f"{email}:{otp}".encode(),
            hashlib.sha256,
        ).hexdigest()

        cursor.execute(
            "DELETE FROM email_otps WHERE email=%s",
            (email,),
        )
        cursor.execute(
            """
            INSERT INTO email_otps (email, otp_hash, expires_at, attempts)
            VALUES (%s, %s, UTC_TIMESTAMP() + INTERVAL 5 MINUTE, 0)
            """,
            (email, otp_hash),
        )
        connection.commit()

    except mysql.connector.Error:
        connection.rollback()
        app.logger.exception("Could not create email OTP")
        flash("Unable to send OTP right now.", "error")
        return redirect(url_for("email_login"))

    finally:
        cursor.close()
        connection.close()

    try:
        send_email(
            email,
            "Your portfolio login OTP",
            f"Your verification code is {otp}. "
            "It expires in 5 minutes. Do not share this code.",
        )
        session["pending_email"] = email
        return redirect(url_for("verify_otp"))

    except (OSError, smtplib.SMTPException, KeyError, ValueError):
        app.logger.exception("OTP email delivery failed")
        flash("OTP email could not be sent. Please try again later.", "error")
        return redirect(url_for("email_login"))


@app.route("/verify-otp", methods=["GET", "POST"])
def verify_otp():
    email = session.get("pending_email")
    if not email:
        return redirect(url_for("email_login"))

    if request.method == "GET":
        return render_template("verify_otp.html", email=email)

    otp = request.form.get("otp", "").strip()
    if not re.fullmatch(r"\d{6}", otp):
        flash("Enter the six-digit OTP.", "error")
        return redirect(url_for("verify_otp"))

    connection = get_db_connection()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT id, otp_hash, expires_at, attempts
            FROM email_otps
            WHERE email=%s
            ORDER BY id DESC
            LIMIT 1
            """,
            (email,),
        )
        record = cursor.fetchone()

        if (
            not record
            or record["expires_at"] <= datetime.utcnow()
            or record["attempts"] >= 5
        ):
            cursor.execute("DELETE FROM email_otps WHERE email=%s", (email,))
            connection.commit()
            flash("OTP expired or attempt limit reached. Request a new one.", "error")
            return redirect(url_for("email_login"))

        submitted_hash = hmac.new(
            app.config["SECRET_KEY"].encode(),
            f"{email}:{otp}".encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(submitted_hash, record["otp_hash"]):
            cursor.execute(
                "UPDATE email_otps SET attempts=attempts+1 WHERE id=%s",
                (record["id"],),
            )
            connection.commit()
            flash("Incorrect OTP.", "error")
            return redirect(url_for("verify_otp"))

        # Delete OTP before creating the authenticated session.
        cursor.execute("DELETE FROM email_otps WHERE id=%s", (record["id"],))
        connection.commit()

    finally:
        cursor.close()
        connection.close()

    try:
        user_id = save_user(email, email.split("@")[0], "email")
        session.pop("pending_email", None)
        set_logged_in_user(user_id, email, email.split("@")[0])
        flash("Email verified. You are now logged in.", "success")
        return redirect(url_for("home") + "#contact")

    except Exception:
        app.logger.exception("Could not create authenticated user")
        flash("Login failed. Please try again.", "error")
        return redirect(url_for("email_login"))


@app.post("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("home"))


@app.route("/", methods=["GET", "POST"])
def home():
    if request.method == "POST":
        if not session.get("user_id"):
            flash("Please login before sending a message.", "error")
            return redirect(url_for("login", next="contact"))

        name = request.form.get("name", "").strip()
        message = request.form.get("message", "").strip()
        email = session.get("user_email", "")

        if not name or not message:
            flash("Please fill all fields.", "error")
            return redirect(url_for("home") + "#contact")

        if len(name) > 100 or len(message) > 10000:
            flash("One or more fields exceed the allowed length.", "error")
            return redirect(url_for("home") + "#contact")

        connection = None
        cursor = None

        try:
            connection = get_db_connection()
            cursor = connection.cursor()
            cursor.execute(
                """
                INSERT INTO contact_messages (name, email, message)
                VALUES (%s, %s, %s)
                """,
                (name, email, message),
            )
            connection.commit()

        except mysql.connector.Error:
            app.logger.exception("Failed to save contact message")
            if connection and connection.is_connected():
                connection.rollback()
            flash("Unable to save your message.", "error")
            return redirect(url_for("home") + "#contact")

        finally:
            if cursor:
                cursor.close()
            if connection and connection.is_connected():
                connection.close()

        try:
            send_contact_email(name, email, message)
            flash("Message sent successfully!", "success")
        except (OSError, smtplib.SMTPException, KeyError, ValueError):
            app.logger.exception("Contact email notification failed")
            flash("Message saved, but email notification failed.", "warning")

        return redirect(url_for("home") + "#contact")

    return render_template(
        "index.html",
        logged_in=bool(session.get("user_id")),
        user_name=session.get("user_name"),
        user_email=session.get("user_email"),
    )


@app.get("/health")
def health():
    return {"status": "healthy"}, 200


if __name__ == "__main__":
    app.run(debug=True)
