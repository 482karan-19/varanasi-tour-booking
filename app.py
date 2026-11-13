from flask import Flask, render_template, request, session, redirect, url_for
import sqlite3
import csv
from io import StringIO
from flask import Response
from flask_mail import Mail, Message
import os
from dotenv import load_dotenv
load_dotenv()

import razorpay


# Create Flask application
app = Flask(__name__)

app.config["MAIL_SERVER"] = os.getenv("MAIL_SERVER")
app.config["MAIL_PORT"] = int(os.getenv("MAIL_PORT", 587))
app.config["MAIL_USE_TLS"] = (
    os.getenv("MAIL_USE_TLS", "True").lower() == "true"
)
app.config["MAIL_USERNAME"] = os.getenv("MAIL_USERNAME")
app.config["MAIL_PASSWORD"] = os.getenv("MAIL_PASSWORD")
app.config["MAIL_DEFAULT_SENDER"] = os.getenv("MAIL_DEFAULT_SENDER")

mail = Mail(app)
def send_booking_confirmation(booking, total_amount, payment_status):
    try:
        msg = Message(
            subject="Your Varanasi Tour Booking Confirmation",
            recipients=[booking["email"]]
        )

        msg.body = f"""
Hello {booking["name"]},

Thank you for booking with Varanasi Tour Booking!

Here are your booking details:

Customer Name: {booking["name"]}
Tour: {booking["tour"]}
Travel Date: {booking["date"]}
Number of Guests: {booking["guests"]}
Total Amount: Rs. {total_amount}
Payment Status: {payment_status}

Please keep this email for your records.

We look forward to welcoming you to Varanasi!

Regards,
Varanasi Tour Booking Team
"""

        mail.send(msg)
        return True

    except Exception as e:
        app.logger.exception("Failed to send booking confirmation email")
        return False


TOUR_PRICES = {
    "Ganga Aarti Experience": 999,
    "Sunrise Boat Ride": 799,
    "Varanasi Temple Tour": 1499,
    "Sarnath Half-Day Tour": 1299,
    "Varanasi Food Tour": 1099,
    "Heritage Ghats Walking Tour": 899
}

razorpay_key_id = os.getenv("RAZORPAY_KEY_ID")
razorpay_key_secret = os.getenv("RAZORPAY_KEY_SECRET")

client = razorpay.Client(
    auth=(razorpay_key_id, razorpay_key_secret)
)

app.secret_key = os.getenv("FLASK_SECRET_KEY")





def init_db():
    conn = sqlite3.connect("bookings.db")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            phone TEXT NOT NULL,
            tour TEXT NOT NULL,
            date TEXT NOT NULL,
            guests INTEGER NOT NULL,
            payment TEXT,
            message TEXT,
            razorpay_payment_id TEXT UNIQUE,
            status TEXT DEFAULT 'Confirmed'
        )
    """)

    cursor.execute("PRAGMA table_info(bookings)")
    columns = [column[1] for column in cursor.fetchall()]

    migrations = {
        "payment": "TEXT",
        "razorpay_payment_id": "TEXT",
        "status": "TEXT DEFAULT 'Confirmed'"
    }

    for column, definition in migrations.items():
        if column not in columns:
            cursor.execute(
                f"ALTER TABLE bookings ADD COLUMN {column} {definition}"
            )

    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS
        idx_bookings_razorpay_payment_id
        ON bookings(razorpay_payment_id)
    """)

    conn.commit()
    conn.close()

init_db()
# Home page
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/tours")
def tours():
    return render_template("tours.html")

@app.route("/destinations")
def destinations():
    return render_template("destinations.html")

@app.route("/gallery")
def gallery():
    return render_template("gallery.html")

@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        phone = request.form["phone"]
        message = request.form["message"]

        print("----- NEW CONTACT MESSAGE -----")
        print("Name:", name)
        print("Email:", email)
        print("Phone:", phone)
        print("Message:", message)
        print("-------------------------------")

        return render_template("contact_success.html")

    return render_template("contact.html")

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        password = request.form["password"]

        if password == os.getenv("ADMIN_PASSWORD"):
            session["admin_logged_in"] = True
            return redirect(url_for("admin_bookings"))

        return render_template(
            "admin_login.html",
            error="Incorrect password. Please try again."
        )

    return render_template("admin_login.html")

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("admin_login"))


@app.route("/admin/bookings")
def admin_bookings():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))
    
    conn = sqlite3.connect("bookings.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT
            id, name, email, phone, tour, date,
            guests, payment, message, razorpay_payment_id, status
        FROM bookings
        ORDER BY id DESC
    """)

    bookings = cursor.fetchall()

    cursor.execute("SELECT COUNT(*) FROM bookings")
    total_bookings = cursor.fetchone()[0]

    cursor.execute(
        "SELECT COUNT(*) FROM bookings WHERE status = 'Completed'"
    )
    completed_tours = cursor.fetchone()[0]

    cursor.execute("""
        SELECT COUNT(*)
        FROM bookings
        WHERE razorpay_payment_id IS NOT NULL
          AND razorpay_payment_id != ''
    """)
    online_payments = cursor.fetchone()[0]

    online_revenue = sum(
        TOUR_PRICES.get(booking[4], 0) * booking[6]
        for booking in bookings
        if booking[9]
    )

    bookings = [
        booking + (
            TOUR_PRICES.get(booking[4], 0) * booking[6],
        )
        for booking in bookings
    ]

    conn.close()

    return render_template(
        "admin_bookings.html",
        bookings=bookings,
        total_bookings=total_bookings,
        completed_tours=completed_tours,
        online_payments=online_payments,
        online_revenue=online_revenue
    )


@app.route("/create-order", methods=["POST"])
def create_order():
    booking_data = session.get("pending_booking")

    if not booking_data:
        return redirect(url_for("booking"))

    tour = booking_data["tour"]
    guests = int(booking_data["guests"])

    tour_price = TOUR_PRICES.get(tour)

    if tour_price is None or guests < 1:
        return "Invalid booking details.", 400

    total_amount = tour_price * guests
    booking_data["total_amount"] = total_amount
    session["pending_booking"] = booking_data

    order_data = {
        "amount": total_amount * 100,
        "currency": "INR",
        "receipt": "booking_payment"
    }
    app.logger.info(
    "Razorpay order amount: %s paise (₹%.2f)",
    order_data["amount"],
    order_data["amount"] / 100
)

    try:
        order = client.order.create(data=order_data)

        session["razorpay_order_id"] = order["id"]

        return render_template(
            "payment.html",
            order_id=order["id"],
            amount=order_data["amount"],
            key_id=razorpay_key_id
        )

    except Exception:
        app.logger.exception("Razorpay order creation failed")
        return "Unable to start payment. Please try again.", 500


@app.route("/verify-payment", methods=["POST"])
def verify_payment():
    booking_data = session.get("pending_booking")
    order_id = session.get("razorpay_order_id")

    if not booking_data or not order_id:
        return "Booking session expired. Please try again.", 400

    payment_id = request.form.get("razorpay_payment_id")
    submitted_order_id = request.form.get("razorpay_order_id")
    signature = request.form.get("razorpay_signature")

    if not payment_id or submitted_order_id != order_id or not signature:
        return "Payment details are incomplete.", 400

    try:
        client.utility.verify_payment_signature({
            "razorpay_order_id": order_id,
            "razorpay_payment_id": payment_id,
            "razorpay_signature": signature
        })

        payment_details = client.payment.fetch(payment_id)

        if payment_details.get("status") != "captured":
            return "Payment has not been captured. Please try again.", 400

        if payment_details.get("order_id") != order_id:
            return "Payment order does not match.", 400

        expected_price = TOUR_PRICES.get(booking_data["tour"])
        expected_guests = int(booking_data["guests"])

        if expected_price is None or expected_guests < 1:
            return "Invalid booking details.", 400

        expected_amount = expected_price * expected_guests * 100

        if payment_details.get("amount") != expected_amount:
            return "Payment amount does not match.", 400

        if payment_details.get("currency") != "INR":
            return "Payment currency does not match.", 400


    except Exception:
        app.logger.exception("Payment verification failed")
        return "Payment verification failed. Please contact support.", 400

    conn = sqlite3.connect("bookings.db")
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO bookings
        (name, email, phone, tour, date, guests, payment, message,
        razorpay_payment_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        booking_data["name"],
        booking_data["email"],
        booking_data["phone"],
        booking_data["tour"],
        booking_data["date"],
        booking_data["guests"],
        "Paid Online",
        booking_data["message"],
        payment_id
    ))

    conn.commit()
    conn.close()

    session.pop("pending_booking", None)
    session.pop("razorpay_order_id", None)

    send_booking_confirmation(
        booking_data,
        expected_price * expected_guests,
        "Payment Successful"
    )

    return render_template("booking_success.html",
        booking=booking_data,
        total_amount=expected_price * expected_guests,
        payment_method="Paid Online",
        payment_status="Payment Successful"
    )



@app.route("/booking", methods=["GET", "POST"])
def booking():
    if request.method == "POST":
        name = request.form["name"]
        email = request.form["email"]
        phone = request.form["phone"]
        tour = request.form["tour"]
        date = request.form["date"]
        guests = request.form["guests"]
        tour_price = TOUR_PRICES.get(tour)

        if tour_price is None:
            return "Invalid tour selected.", 400

        try:
            guest_count = int(guests)
            if guest_count < 1:
                return "Number of guests must be at least 1.", 400
        except (ValueError, TypeError):
            return "Invalid number of guests.", 400

        total_amount = tour_price * guest_count
        
        message = request.form["message"]
        payment = request.form["payment"]

        # Send online payments to Razorpay
        if payment == "Pay Online":
            session["pending_booking"] = {
                "name": name,
                "email": email,
                "phone": phone,
                "tour": tour,
                "date": date,
                "guests": guests,
                "message": message,
                "payment": payment,
                "total_amount": total_amount
            }

            return render_template(
                "start_payment.html"
            )

        # Save Pay at Venue bookings directly
        conn = sqlite3.connect("bookings.db")
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO bookings
            (name, email, phone, tour, date, guests, payment, message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            name, email, phone, tour, date,
            guests, payment, message
        ))

        conn.commit()
        conn.close()

        booking_data = {
            "name": name,
            "email": email,
            "tour": tour,
            "date": date,
            "guests": guest_count
        }

        send_booking_confirmation(
            booking_data,
            total_amount,
            "Pay at Venue"
        )
        return render_template(
            "booking_success.html",
            booking=booking_data,
            total_amount=total_amount,
            payment_method="Pay at Venue",
            payment_status="Payment Due at Venue")

    selected_tour = request.args.get("tour", "")

    return render_template(
        "booking.html",
        selected_tour=selected_tour
    )  

@app.route("/admin/update-status/<int:booking_id>", methods=["POST"])
def update_booking_status(booking_id):
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    status = request.form.get("status")
    allowed_statuses = ["Confirmed", "Completed", "Cancelled"]

    if status not in allowed_statuses:
        return redirect(url_for("admin_bookings"))

    conn = sqlite3.connect("bookings.db")
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE bookings SET status = ? WHERE id = ?",
        (status, booking_id)
    )
    conn.commit()
    conn.close()

    return redirect(url_for("admin_bookings"))


@app.route("/admin/export-bookings")
def export_bookings():
    if not session.get("admin_logged_in"):
        return redirect(url_for("admin_login"))

    conn = sqlite3.connect("bookings.db")
    cursor = conn.cursor()

    cursor.execute("""
        SELECT id, name, email, phone, tour, date,
               guests, payment, message,
               razorpay_payment_id, status
        FROM bookings
        ORDER BY id DESC
    """)

    bookings = cursor.fetchall()
    conn.close()

    output = StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "ID", "Name", "Email", "Phone", "Tour",
        "Date", "Guests", "Payment Method", "Message",
        "Razorpay Payment ID", "Status", "Total Amount"
    ])

    for booking in bookings:
        tour_price = TOUR_PRICES.get(booking[4], 0)
        total_amount = tour_price * booking[6]

        writer.writerow([
            *booking,
            total_amount
        ])

    response = Response(
        output.getvalue(),
        mimetype="text/csv",
        headers={
            "Content-Disposition":
                "attachment; filename=varanasi_bookings.csv"
        }
    )

    return response


# Initialize database when application starts
init_db()

# Run the application locally
if __name__ == "__main__":
    app.run(debug=True)