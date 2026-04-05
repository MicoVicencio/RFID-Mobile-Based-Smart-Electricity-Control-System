import os
import re
import sys
import time
import threading
import sqlite3
import pandas as pd
import requests
import serial
import firebase_admin

from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, session, jsonify
from firebase_admin import credentials, db


# --- FLASK CONFIGURATION ---
app = Flask(__name__)
app.secret_key = "rfid_secret_key"
DB = "RFID_System.db"

ADMIN_HEXCODE = "0687606595"

# Global reference to RFIDModule (set at bottom)
rfid_module = None

# Simple in-memory cache to avoid hammering SQLite on every poll
_user_cache      = {"data": None, "ts": 0}
_logs_cache      = {"data": None, "ts": 0}
CACHE_TTL        = 1.0   # seconds


# --- INTEGRATED EMAIL SERVICE ---
class EmailService:
    def __init__(self, service_id, template_id, user_id, private_key):
        self.service_id   = service_id
        self.template_id  = template_id
        self.user_id      = user_id
        self.private_key  = private_key
        self.url          = "https://api.emailjs.com/api/v1.0/email/send"

    def send_email(self, recipient_email, user_name, message):
        payload = {
            "service_id":  self.service_id,
            "template_id": self.template_id,
            "user_id":     self.user_id,
            "accessToken": self.private_key,
            "template_params": {
                "user_name":   user_name,
                "from_name":   "RFID System",
                "message":     message,
                "to_email":    recipient_email,
                "system_time": datetime.now().strftime("%I:%M %p | %B %d, %Y"),
                "section":     "Laboratory Access",
                "reply_to":    "micovicencio55@gmail.com"
            }
        }
        try:
            response = requests.post(self.url, json=payload)
            if response.status_code == 200:
                print(f"DEBUG [Email]: Success -> {user_name}")
            else:
                print(f"DEBUG [Email]: Failed -> {response.status_code} | {response.text}")
        except Exception as e:
            print(f"DEBUG [Email Error]: {e}")


# ---------------------------------------------------------------------------
# KEYBOARD / USB HID RFID LISTENER
# ---------------------------------------------------------------------------
# USB RFID readers act as keyboards: they type the card's hex code and press
# Enter.  On Windows we read from the console (msvcrt); on Linux/macOS we read
# from /dev/tty (or stdin when it is a real terminal).
# ---------------------------------------------------------------------------
class KeyboardRFIDListener:
    """
    Listens for RFID card data typed by a USB HID reader (keyboard-emulation mode).

    The reader sends a burst of characters followed by a newline/Enter.
    We accumulate characters until Enter is received, then treat the whole
    line as the hex code and hand it off to RFIDModule for processing.
    """

    def __init__(self, rfid_mod):
        self.rfid_mod       = rfid_mod
        self.hex_pattern    = re.compile(r"^[0-9a-fA-F]+$")
        self._buffer        = ""
        self._last_hex      = None
        self._last_time     = 0
        self.DEBOUNCE_SECS  = 5

    # ── platform-aware single character read ──────────────────────────────
    def _read_char_windows(self):
        import msvcrt
        ch = msvcrt.getwch()
        return ch

    def _readline_stdin(self):
        """Blocking readline from stdin (works on Linux/macOS)."""
        return sys.stdin.readline()

    # ── main loop ─────────────────────────────────────────────────────────
    def run_windows(self):
        """Windows: use msvcrt for non-blocking character reads."""
        import msvcrt
        print("DEBUG [KBD RFID]: Windows keyboard listener active.")
        buf = ""
        while True:
            if msvcrt.kbhit():
                ch = msvcrt.getwch()
                if ch in ('\r', '\n'):
                    if buf:
                        self._process(buf.strip())
                    buf = ""
                else:
                    buf += ch
            else:
                time.sleep(0.01)

    def run_linux(self):
        """
        Linux/macOS: open /dev/tty directly so we can read keyboard input
        even when Flask has replaced sys.stdin.  Falls back to a raw-mode
        stdin reader if /dev/tty is unavailable (e.g. inside a container).
        """
        print("DEBUG [KBD RFID]: Linux/macOS keyboard listener active.")
        try:
            tty_file = open("/dev/tty", "r")
            while True:
                line = tty_file.readline()
                if line:
                    self._process(line.strip())
        except Exception as e:
            print(f"DEBUG [KBD RFID]: /dev/tty unavailable ({e}), falling back to stdin.")
            # Fallback – useful when running in a terminal that keeps stdin
            try:
                while True:
                    line = sys.stdin.readline()
                    if line:
                        self._process(line.strip())
            except Exception as ex:
                print(f"DEBUG [KBD RFID]: stdin fallback failed: {ex}")

    def _process(self, raw: str):
        """Validate and dispatch a raw scanned string."""
        # Strip any non-hex characters the reader might append (spaces, etc.)
        hex_code = raw.strip()
        if not hex_code:
            return

        # Only accept strings that look like hex codes (digits + A-F)
        if not self.hex_pattern.match(hex_code):
            print(f"DEBUG [KBD RFID]: Ignoring non-hex input -> {repr(hex_code)}")
            return

        now = time.time()

        # Debounce: same card within DEBOUNCE_SECS → ignore
        if hex_code == self._last_hex and (now - self._last_time) < self.DEBOUNCE_SECS:
            print(f"DEBUG [KBD RFID]: Debounced repeat scan -> {hex_code} (ignored)")
            return

        self._last_hex  = hex_code
        self._last_time = now
        print(f"DEBUG [KBD RFID]: Scanned Hex -> {hex_code}")

        # ── Admin card ────────────────────────────────────────────────────
        if hex_code == ADMIN_HEXCODE:
            print("DEBUG [KBD RFID]: Admin card scanned — flagging for web login.")
            self.rfid_mod.admin_scan_pending = True
            return

        # ── Scan-mode (card registration) ─────────────────────────────────
        if self.rfid_mod.scan_mode_active:
            print(f"DEBUG [KBD RFID]: Captured hex for registration -> {hex_code}")
            self.rfid_mod.scan_mode_result = hex_code
            self.rfid_mod.scan_mode_active = False
            return

        # ── Normal user lookup ────────────────────────────────────────────
        conn   = self.rfid_mod.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM users WHERE hexcode=?", (hex_code,))
        user = cursor.fetchone()
        conn.close()

        if user:
            print(f"DEBUG [KBD RFID]: Recognised user -> {user[0]}")
            self.rfid_mod.add_log_entry(user[0])
        else:
            print(f"DEBUG [KBD RFID]: Unknown RFID -> {hex_code}")

    def start(self):
        """Spawn the appropriate listener in a daemon thread."""
        if sys.platform.startswith("win"):
            t = threading.Thread(target=self.run_windows, daemon=True)
        else:
            t = threading.Thread(target=self.run_linux, daemon=True)
        t.start()
        print("DEBUG [KBD RFID]: Listener thread started.")


# --- RFID & BACKGROUND MODULE ---
class RFIDModule:
    def __init__(self, db_path=DB):
        self.db_path            = db_path
        self.first_timein       = None
        self.running_timer      = False
        self.seconds            = 0
        self.port               = "COM5"
        self.admin_scan_pending = False
        self.ADMIN_EMAIL        = "micovicencio55@gmail.com"

        # ── SCAN MODE (for registering new RFID cards via admin panel) ──
        self.scan_mode_active   = False
        self.scan_mode_result   = None
        # ────────────────────────────────────────────────────────────────

        # EmailJS Configuration
        self.email_service = EmailService(
            service_id='service_vueoden',
            template_id='template_5stcyv9',
            user_id='p-vEdPnblIB1wBjtt',
            private_key='cSstALTbIHFgvKtdXxm3A'
        )

        # Firebase Configuration
        self.firebase_json_path = "firebase.json"
        self.firebase_db_url    = "https://thesis-86ff4-default-rtdb.firebaseio.com/"

        try:
            if not firebase_admin._apps:
                self.firebase_cred = credentials.Certificate(self.firebase_json_path)
                firebase_admin.initialize_app(
                    self.firebase_cred, {'databaseURL': self.firebase_db_url}
                )
                print("DEBUG [Firebase]: Initialized successfully.")
        except Exception as e:
            print(f"DEBUG [Firebase Error]: {e}")

        # Firebase References
        self.status_ref  = db.reference("/status/electricity")
        self.hexcode_ref = db.reference("/status/hexcode")
        self.esp32_ref   = db.reference("/status/esp32")
        self.mode_ref    = db.reference("/status/mode")
        self.user_ref    = db.reference("/status/current_user")

        self.last_status = None
        self.last_mode   = None

        # Serial Setup
        self.esp32       = self.try_connect_serial()
        # Matches "Received Hex Code: 1234" OR just "1234" on its own line
        self.hex_pattern = re.compile(r"(?:Received Hex Code:\s*)?([0-9a-fA-F]+)")

    # ------------------------------------------------------------------
    def try_connect_serial(self):
        try:
            ser = serial.Serial(self.port, baudrate=115200, timeout=1)
            print(f"DEBUG [Serial]: Connected to ESP32 on {self.port}")
            return ser
        except Exception as e:
            print(f"DEBUG [Serial Error]: {e}. Hardware not found.")
            return None

    def get_db_connection(self):
        return sqlite3.connect(self.db_path, check_same_thread=False, timeout=10)

    def set_online_status(self, status):
        try:
            self.esp32_ref.set(status)
            print(f"DEBUG [Mobile Connection]: System set to {status}")
        except:
            pass

    def generate_email_message(self, name, action, recipient="user"):
        now     = datetime.now()
        day_str = now.strftime("%A, %B %d at %I:%M %p")

        if action == "timein":
            if recipient == "user":
                return (
                    f"Hello {name},\n\n"
                    f"Your RFID card has been successfully scanned. "
                    f"You have been granted access and entered the laboratory on {day_str}.\n\n"
                    f"Please ensure you follow all laboratory protocols during your session.\n\n"
                    f"If you did not perform this action, please report it immediately."
                )
            else:  # admin
                return (
                    f"This is an automated notification from the RFID System.\n\n"
                    f"{name} has been granted access and entered the laboratory on {day_str}.\n\n"
                    f"If this activity is unexpected or unauthorized, please take action immediately."
                )

        elif action == "timeout":
            if recipient == "user":
                return (
                    f"Hello {name},\n\n"
                    f"Your laboratory session has been closed. "
                    f"You exited the laboratory on {day_str}.\n\n"
                    f"Thank you for using the RFID System. Please make sure all equipment is properly shut down.\n\n"
                    f"If you did not perform this action, please report it immediately."
                )
            else:  # admin
                return (
                    f"This is an automated notification from the RFID System.\n\n"
                    f"{name} has exited the laboratory on {day_str}. Their session has been closed.\n\n"
                    f"If this activity is unexpected or unauthorized, please take action immediately."
                )

        elif action == "timer":
            if recipient == "user":
                return (
                    f"Hello {name},\n\n"
                    f"This is a 45-minute energy-saving reminder. "
                    f"You have been inside the laboratory for 45 minutes.\n\n"
                    f"Please remember to turn off all laboratory equipment and electricity before leaving the premises."
                )
            else:  # admin
                return (
                    f"This is an automated energy-saving alert from the RFID System.\n\n"
                    f"{name} has been inside the laboratory for 45 minutes. "
                    f"Please ensure that all laboratory equipment and electricity are turned off before they leave.\n\n"
                    f"This alert was triggered 45 minutes after the last recorded entry."
                )
        return ""

    def start_45min_timer(self, name):
        if self.running_timer:
            return
        self.running_timer = True
        self.seconds = 45 * 60

        def run_timer():
            print(f"DEBUG [Timer]: Started 45-minute countdown for {name}")
            while self.seconds > 0 and self.running_timer:
                time.sleep(1)
                self.seconds -= 1
            if self.running_timer and self.esp32:
                try:
                    self.esp32.write(b'ring\n')
                    print(f"DEBUG [Timer]: 45 Minutes up! Ringing buzzer for {name}")
                except:
                    pass

        threading.Thread(target=run_timer, daemon=True).start()

    # ------------------------------------------------------------------
    # Admin login (called when switch turns ON via Firebase)
    # ------------------------------------------------------------------
    def admin_login(self):
        conn         = self.get_db_connection()
        cursor       = conn.cursor()
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("SELECT id FROM laba_logs WHERE name='Admin' AND timeout IS NULL")
        if cursor.fetchone():
            print("DEBUG [Admin]: Already logged in, skipping login.")
            conn.close()
            return

        cursor.execute("INSERT INTO laba_logs (name, timein) VALUES ('Admin', ?)", (current_time,))
        self.status_ref.set("on")
        self.user_ref.set("Admin")
        self.first_timein  = "Admin"
        self.running_timer = True
        if self.esp32:
            try:
                self.esp32.write(b'on\n')
            except:
                pass

        conn.commit()
        conn.close()
        print("DEBUG [Action]: Admin Logged IN")

    # ------------------------------------------------------------------
    # Admin logout (called when switch turns OFF via Firebase)
    # ------------------------------------------------------------------
    def admin_logout(self):
        conn         = self.get_db_connection()
        cursor       = conn.cursor()
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("SELECT id FROM laba_logs WHERE name='Admin' AND timeout IS NULL")
        record = cursor.fetchone()
        if not record:
            print("DEBUG [Admin]: Already logged out, skipping logout.")
            conn.close()
            return

        cursor.execute("UPDATE laba_logs SET timeout=? WHERE id=?", (current_time, record[0]))
        self.status_ref.set("off")
        self.user_ref.set("")
        self.running_timer = False
        self.first_timein  = None
        if self.esp32:
            try:
                self.esp32.write(b'off\n')
            except:
                pass

        conn.commit()
        conn.close()
        print("DEBUG [Action]: Admin Logged OUT")

    # ------------------------------------------------------------------
    # Normal RFID user log entry
    # ------------------------------------------------------------------
    def add_log_entry(self, name):
        conn         = self.get_db_connection()
        cursor       = conn.cursor()
        current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        cursor.execute("SELECT id FROM laba_logs WHERE name=? AND timeout IS NULL", (name,))
        record = cursor.fetchone()

        cursor.execute("SELECT email FROM users WHERE name=?", (name,))
        user_data  = cursor.fetchone()
        user_email = user_data[0] if user_data else None

        if record:
            # User is IN → log OUT
            print(f"DEBUG [Action]: Timing OUT -> {name}")
            if self.esp32:
                try:
                    self.esp32.write(b'off\n')
                except:
                    pass
            cursor.execute("UPDATE laba_logs SET timeout=? WHERE id=?", (current_time, record[0]))

            # ── Send to USER ──
            if user_email:
                user_msg = self.generate_email_message(name, "timeout", recipient="user")
                self.email_service.send_email(user_email, name, user_msg)
                print(f"DEBUG [Email]: Timeout email sent to user -> {user_email}")

            # ── Send to ADMIN ──
            admin_msg = self.generate_email_message(name, "timeout", recipient="admin")
            self.email_service.send_email(self.ADMIN_EMAIL, name, admin_msg)
            print(f"DEBUG [Email]: Timeout email sent to admin -> {self.ADMIN_EMAIL}")

            self.user_ref.set("")
            self.status_ref.set("off")
            self.running_timer = False
            self.first_timein  = None

        else:
            # User is not IN → log IN (only if lab is free)
            if self.first_timein is None:
                print(f"DEBUG [Action]: Timing IN -> {name}")
                if self.esp32:
                    try:
                        self.esp32.write(b'on\n')
                    except:
                        pass
                cursor.execute(
                    "INSERT INTO laba_logs (name, timein) VALUES (?, ?)", (name, current_time)
                )

                # ── Send to USER ──
                if user_email:
                    user_msg = self.generate_email_message(name, "timein", recipient="user")
                    self.email_service.send_email(user_email, name, user_msg)
                    print(f"DEBUG [Email]: Timein email sent to user -> {user_email}")

                # ── Send to ADMIN ──
                admin_msg = self.generate_email_message(name, "timein", recipient="admin")
                self.email_service.send_email(self.ADMIN_EMAIL, name, admin_msg)
                print(f"DEBUG [Email]: Timein email sent to admin -> {self.ADMIN_EMAIL}")

                self.user_ref.set(name)
                self.status_ref.set("on")
                self.first_timein = name
                self.start_45min_timer(name)
            else:
                print(f"DEBUG [Action]: Lab occupied by {self.first_timein}. {name} denied.")

        conn.commit()
        conn.close()

    # ------------------------------------------------------------------
    # Background thread: RFID serial listener
    # ------------------------------------------------------------------
    def read_serial_loop(self):
        print("DEBUG [Serial]: RFID Serial Listener Active.")
        last_scan_hex  = None
        last_scan_time = 0
        DEBOUNCE_SECS  = 5   # ignore same card scanned within 5 seconds

        while True:
            if self.esp32:
                try:
                    if self.esp32.in_waiting > 0:
                        line  = self.esp32.readline().decode("utf-8").strip()
                        match = self.hex_pattern.search(line)
                        if match:
                            hex_code = match.group(1)
                            now      = time.time()

                            # ── DEBOUNCE: ignore if same card scanned too recently ──
                            if hex_code == last_scan_hex and (now - last_scan_time) < DEBOUNCE_SECS:
                                print(f"DEBUG [Serial]: Debounced repeat scan -> {hex_code} (ignored)")
                                continue

                            last_scan_hex  = hex_code
                            last_scan_time = now
                            print(f"DEBUG [Serial]: Scanned Hex -> {hex_code}")

                            # ── ADMIN CARD SCAN ──────────────────────────
                            if hex_code == ADMIN_HEXCODE:
                                print("DEBUG [Serial]: Admin card scanned — flagging for web login.")
                                self.admin_scan_pending = True
                                continue
                            # ─────────────────────────────────────────────

                            # ── SCAN MODE: capture hex for registration form ──
                            if self.scan_mode_active:
                                print(f"DEBUG [Scan Mode]: Captured hex for registration -> {hex_code}")
                                self.scan_mode_result = hex_code
                                self.scan_mode_active = False
                                continue
                            # ─────────────────────────────────────────────────

                            conn   = self.get_db_connection()
                            cursor = conn.cursor()
                            cursor.execute("SELECT name FROM users WHERE hexcode=?", (hex_code,))
                            user = cursor.fetchone()
                            conn.close()

                            if user:
                                self.add_log_entry(user[0])
                            else:
                                print(f"DEBUG [Database]: Unknown RFID -> {hex_code}")
                except Exception as e:
                    print(f"DEBUG [Serial Error]: {e}")
                    self.esp32 = None
            else:
                time.sleep(5)
                self.esp32 = self.try_connect_serial()
            time.sleep(0.1)

    # ------------------------------------------------------------------
    # Background thread: Firebase listener
    # ------------------------------------------------------------------
    def monitor_firebase_loop(self):
        print("DEBUG [Firebase]: Mobile Sync Listener Active.")

        try:
            raw            = self.mode_ref.get()
            self.last_mode = (str(raw).lower() == "admin")
            print(f"DEBUG [Firebase]: Initial mode seeded -> {self.last_mode}")
        except:
            self.last_mode = False

        while True:
            try:
                # ── 1. Admin mode toggle (mobile app switch) ─────────────
                raw_mode            = self.mode_ref.get()
                current_mode_active = (str(raw_mode).lower() == "admin")

                if current_mode_active != self.last_mode:
                    print(f"DEBUG [Firebase]: Mode changed -> {current_mode_active}")
                    if current_mode_active:
                        self.admin_login()
                    else:
                        self.admin_logout()
                    self.last_mode = current_mode_active

                # ── 2. Manual electricity relay toggle ───────────────────
                current_status = self.status_ref.get()
                if current_status != self.last_status:
                    if self.esp32:
                        try:
                            if current_status == "on":
                                self.esp32.write(b'on\n')
                            elif current_status == "off":
                                self.esp32.write(b'off\n')
                        except:
                            pass
                    self.last_status = current_status

            except Exception as e:
                print(f"DEBUG [Firebase Sync Error]: {e}")

            time.sleep(1)


# --- FLASK DATABASE HELPERS ---
def connect_db():
    conn             = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn   = sqlite3.connect(DB)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL,
            department TEXT    NOT NULL,
            hexcode    TEXT    NOT NULL UNIQUE,
            email      TEXT    NOT NULL
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS laba_logs (
            id      INTEGER  PRIMARY KEY AUTOINCREMENT,
            name    TEXT     NOT NULL,
            timein  DATETIME NOT NULL,
            timeout DATETIME
        )
    """)
    conn.commit()
    conn.close()


# --- FLASK ROUTES ---

@app.route("/")
def index():
    if session.get("admin"):
        return redirect("/admin")

    conn = connect_db()

    logs_list = conn.execute("""
        SELECT l.name, l.timein, l.timeout, u.department
        FROM laba_logs l
        LEFT JOIN users u ON l.name = u.name
        ORDER BY l.timein DESC
    """).fetchall()
    print(f"DEBUG [Logs]: Total rows -> {len(logs_list)}")

    active_user_row = conn.execute("""
        SELECT l.name, u.department, l.timein
        FROM laba_logs l
        LEFT JOIN users u ON l.name = u.name
        WHERE l.timeout IS NULL
        ORDER BY l.timein DESC
        LIMIT 1
    """).fetchone()

    current_user = None
    if active_user_row:
        current_user = {
            "name":       active_user_row["name"],
            "department": active_user_row["department"],
            "timein":     active_user_row["timein"]
        }

    conn.close()
    return render_template("index.html", logs=logs_list, current_user=current_user)


@app.route("/login", methods=["POST"])
def login():
    hexcode = request.form.get("hexcode")
    if hexcode == ADMIN_HEXCODE:
        session["admin"] = True
        return redirect("/admin")
    return "Access Denied", 401


# ── Frontend polls this every second on the login/index page ──
@app.route("/check_admin_scan")
def check_admin_scan():
    global rfid_module
    if rfid_module and rfid_module.admin_scan_pending:
        rfid_module.admin_scan_pending = False
        session["admin"] = True
        return jsonify({"redirect": "/admin"})
    return jsonify({"redirect": None})


# ── Activate RFID scan mode for registration ──────────────────
@app.route("/start_scan")
def start_scan():
    global rfid_module
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    if rfid_module:
        rfid_module.scan_mode_active = True
        rfid_module.scan_mode_result = None
        print("DEBUG [Scan Mode]: Activated — waiting for RFID card...")
    return jsonify({"status": "listening"})


# ── Poll for scanned hex result ───────────────────────────────
@app.route("/get_scan_result")
def get_scan_result():
    global rfid_module
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    if rfid_module and rfid_module.scan_mode_result:
        hex_val = rfid_module.scan_mode_result
        rfid_module.scan_mode_result = None
        return jsonify({"hex": hex_val})
    return jsonify({"hex": None})


# ── Cancel scan mode ──────────────────────────────────────────
@app.route("/cancel_scan")
def cancel_scan():
    global rfid_module
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    if rfid_module:
        rfid_module.scan_mode_active = False
        rfid_module.scan_mode_result = None
        print("DEBUG [Scan Mode]: Cancelled.")
    return jsonify({"status": "cancelled"})


@app.route("/logs_json")
def logs_json():
    global _logs_cache
    now = time.time()
    if _logs_cache["data"] is not None and (now - _logs_cache["ts"]) < CACHE_TTL:
        return jsonify(_logs_cache["data"])
    conn = connect_db()
    rows = conn.execute("""
        SELECT l.name, l.timein, l.timeout, u.department
        FROM laba_logs l
        LEFT JOIN users u ON l.name = u.name
        ORDER BY l.timein DESC
        LIMIT 50
    """).fetchall()
    conn.close()
    result = [dict(r) for r in rows]
    _logs_cache = {"data": result, "ts": now}
    return jsonify(result)


@app.route("/current_user")
def get_current_user():
    global _user_cache
    now = time.time()
    if _user_cache["data"] is not None and (now - _user_cache["ts"]) < CACHE_TTL:
        return jsonify(_user_cache["data"])
    conn   = connect_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT l.name, u.department, l.timein
        FROM laba_logs l
        LEFT JOIN users u ON l.name = u.name
        WHERE l.timeout IS NULL
        ORDER BY l.timein DESC
        LIMIT 1
    """)
    user = cursor.fetchone()
    conn.close()
    result = {"name": user[0], "department": user[1], "timein": user[2]} if user else {"name": None}
    _user_cache = {"data": result, "ts": now}
    return jsonify(result)


@app.route("/admin")
def admin():
    if not session.get("admin"):
        return redirect("/")

    conn   = connect_db()
    cursor = conn.cursor()

    total_users  = cursor.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    total_visits = cursor.execute("SELECT COUNT(*) FROM laba_logs").fetchone()[0]
    users_list   = cursor.execute("SELECT * FROM users").fetchall()
    logs_list    = cursor.execute(
        "SELECT name, timein, timeout FROM laba_logs ORDER BY timein DESC LIMIT 5"
    ).fetchall()

    dept_stats  = cursor.execute(
        "SELECT department, COUNT(*) as count FROM users GROUP BY department"
    ).fetchall()
    dept_labels = [row['department'] for row in dept_stats]
    dept_values = [row['count']      for row in dept_stats]

    today       = datetime.now().date()
    last_7_days = [
        (today - timedelta(days=i)).strftime('%Y-%m-%d')
        for i in reversed(range(7))
    ]
    freq_labels = [datetime.strptime(d, '%Y-%m-%d').strftime('%m-%d') for d in last_7_days]
    freq_values = [
        cursor.execute(
            "SELECT COUNT(*) FROM laba_logs WHERE date(timein)=?", (day,)
        ).fetchone()[0]
        for day in last_7_days
    ]

    conn.close()
    return render_template(
        "admin_panel.html",
        total_users=total_users, total_visits=total_visits,
        users=users_list, logs=logs_list,
        labels=dept_labels, values=dept_values,
        freq_labels=freq_labels, freq_values=freq_values,
        usage_labels=freq_labels,
        usage_values=freq_values
    )


@app.route("/api/users", methods=["GET", "POST"])
def handle_users():
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    conn = connect_db()
    if request.method == "GET":
        users = conn.execute("SELECT * FROM users").fetchall()
        conn.close()
        return jsonify({"status": "success", "data": [dict(u) for u in users]})
    data = request.get_json()
    try:
        conn.execute(
            "INSERT INTO users (name, department, hexcode, email) VALUES (?, ?, ?, ?)",
            (data["name"], data["department"], data["hexcode"], data["email"])
        )
        conn.commit()
        return jsonify({"status": "success", "message": "Teacher added"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400
    finally:
        conn.close()


@app.route("/api/users/<int:user_id>", methods=["PUT", "DELETE"])
def update_delete_user(user_id):
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    conn = connect_db()
    if request.method == "DELETE":
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": "Deleted"})
    data = request.get_json()
    conn.execute(
        "UPDATE users SET name=?, department=?, email=?, hexcode=? WHERE id=?",
        (data["name"], data["department"], data["email"], data["hexcode"], user_id)
    )
    conn.commit()
    conn.close()
    return jsonify({"status": "success", "message": "Updated"})


@app.route("/bulk_import", methods=["POST"])
def bulk_import():
    if not session.get("admin"):
        return jsonify({"error": "Unauthorized"}), 401
    file = request.files.get('file')
    if not file:
        return jsonify({"status": "error", "message": "No file"}), 400
    try:
        df       = pd.read_csv(file) if file.filename.endswith('.csv') else pd.read_excel(file)
        conn     = connect_db()
        imported = 0
        for _, row in df.iterrows():
            try:
                conn.execute(
                    "INSERT INTO users (hexcode, name, email, department) VALUES (?, ?, ?, ?)",
                    (str(row['rfid']), row['teachername'], row['email'], row['department'])
                )
                imported += 1
            except:
                continue
        conn.commit()
        conn.close()
        return jsonify({"status": "success", "message": f"Imported {imported} teachers"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route("/rfid_scan", methods=["POST"])
def rfid_scan():
    """
    Called by the frontend keyboard RFID listener (index.html).
    Receives a hex code from a USB HID RFID reader and processes it
    exactly like the serial/ESP32 path does.
    """
    global rfid_module
    data    = request.get_json(silent=True) or {}
    hexcode = str(data.get("hexcode", "")).strip()

    if not hexcode:
        return jsonify({"status": "error", "message": "No hex code provided"}), 400

    # Admin card — flag pending and let the frontend redirect
    if hexcode == ADMIN_HEXCODE:
        if rfid_module:
            rfid_module.admin_scan_pending = True
        return jsonify({"status": "admin", "message": "Admin scan detected"})

    # Scan-mode — capture hex for registration form
    if rfid_module and rfid_module.scan_mode_active:
        rfid_module.scan_mode_result = hexcode
        rfid_module.scan_mode_active = False
        return jsonify({"status": "scan_captured", "hex": hexcode})

    # Normal user lookup
    conn   = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM users WHERE hexcode=?", (hexcode,))
    user = cursor.fetchone()
    conn.close()

    if not user:
        print(f"DEBUG [/rfid_scan]: Unknown RFID -> {hexcode}")
        return jsonify({"status": "unknown", "message": "Card not registered"})

    name = user["name"]

    # Check if lab is occupied by someone else (time-in denied)
    if rfid_module:
        if rfid_module.first_timein and rfid_module.first_timein != name:
            return jsonify({
                "status":  "denied",
                "message": f"Lab occupied by {rfid_module.first_timein}"
            })
        rfid_module.add_log_entry(name)

    # Determine what action was taken so the frontend can show the right toast
    conn2   = sqlite3.connect(DB)
    conn2.row_factory = sqlite3.Row
    cursor2 = conn2.cursor()
    cursor2.execute("SELECT timeout FROM laba_logs WHERE name=? ORDER BY id DESC LIMIT 1", (name,))
    last = cursor2.fetchone()
    conn2.close()

    if last and last["timeout"] is None:
        action = "timein"
        msg    = f"{name} timed IN"
    else:
        action = "timeout"
        msg    = f"{name} timed OUT"

    # ── Bust both caches so the next poll gets fresh data immediately ──
    _user_cache["ts"] = 0
    _logs_cache["ts"] = 0

    # ── Return fresh current_user + latest logs IN THE SAME RESPONSE ──
    # This lets the frontend update the UI without waiting for the next poll.
    conn3 = sqlite3.connect(DB)
    conn3.row_factory = sqlite3.Row
    cur3  = conn3.cursor()

    cur3.execute("""
        SELECT l.name, u.department, l.timein
        FROM laba_logs l
        LEFT JOIN users u ON l.name = u.name
        WHERE l.timeout IS NULL
        ORDER BY l.timein DESC LIMIT 1
    """)
    active = cur3.fetchone()
    current_user_data = (
        {"name": active["name"], "department": active["department"], "timein": active["timein"]}
        if active else {"name": None}
    )

    cur3.execute("""
        SELECT l.name, l.timein, l.timeout, u.department
        FROM laba_logs l
        LEFT JOIN users u ON l.name = u.name
        ORDER BY l.timein DESC LIMIT 50
    """)
    logs_data = [dict(r) for r in cur3.fetchall()]
    conn3.close()

    print(f"DEBUG [/rfid_scan]: {msg}")
    return jsonify({
        "status":       action,
        "message":      msg,
        "name":         name,
        "current_user": current_user_data,
        "logs":         logs_data
    })


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")


# --- ENTRY POINT ---
if __name__ == "__main__":
    init_db()

    rfid_module = RFIDModule()
    rfid_module.set_online_status("ONLINE")

    threading.Thread(target=rfid_module.read_serial_loop,      daemon=True).start()
    threading.Thread(target=rfid_module.monitor_firebase_loop, daemon=True).start()

    # ── USB HID / Keyboard RFID reader ──────────────────────────────────
    kbd_listener = KeyboardRFIDListener(rfid_module)
    kbd_listener.start()
    # ────────────────────────────────────────────────────────────────────

    app.run(debug=False, host='0.0.0.0', port=5000)