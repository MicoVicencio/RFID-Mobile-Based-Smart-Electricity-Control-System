import sqlite3
from datetime import datetime, timedelta
import random
import string

DB = "RFID_System.db"

def generate_rfid():
    """Generates a random 8-character hex string."""
    return ''.join(random.choices("0123456789ABCDEF", k=8))

def init_db():
    conn = sqlite3.connect(DB)
    cursor = conn.cursor()

    # Teacher/User table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            department TEXT NOT NULL,
            hexcode TEXT NOT NULL,
            email TEXT NOT NULL
        )
    """)

    # Lab Access Logs
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS laba_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            timein DATETIME NOT NULL,
            timeout DATETIME
        )
    """)

    # Clear existing data
    cursor.execute("DELETE FROM users")
    cursor.execute("DELETE FROM laba_logs")

    # 1. GENERATE 20 TEACHERS
    departments = ["IT", "Math", "Science", "English", "College", "Junior High"]
    first_names = ["Juan", "Maria", "Ricardo", "Elena", "Antonio", "Sonia", "Pedro", "Liza", "Roberto", "Gina"]
    last_names = ["Dela Cruz", "Santos", "Reyes", "Pascual", "Bautista", "Garcia", "Mendoza", "Torres", "Aquino", "Lopez"]
    
    teachers = []
    # Use a set to ensure unique names
    unique_names = set()
    
    while len(teachers) < 20:
        full_name = f"{random.choice(first_names)} {random.choice(last_names)}"
        if full_name not in unique_names:
            unique_names.add(full_name)
            dept = random.choice(departments)
            rfid = generate_rfid()
            email = f"{full_name.lower().replace(' ', '.')}@ccc.edu.ph"
            teachers.append((full_name, dept, rfid, email))

    for t in teachers:
        cursor.execute("INSERT INTO users (name, department, hexcode, email) VALUES (?, ?, ?, ?)", t)

    # 2. GENERATE 200 LOG VISITS
    # Distribute 200 visits randomly among the 20 teachers
    for _ in range(200):
        teacher = random.choice(teachers)
        teacher_name = teacher[0]
        
        # Random date within the last 30 days
        days_ago = random.randint(0, 30)
        # Random start hour between 7:00 AM and 5:00 PM
        start_time = datetime.now() - timedelta(days=days_ago)
        start_time = start_time.replace(hour=random.randint(7, 17), minute=random.randint(0, 59))
        
        # Random duration between 30 minutes and 4 hours
        duration_minutes = random.randint(30, 240)
        end_time = start_time + timedelta(minutes=duration_minutes)
        
        cursor.execute(
            "INSERT INTO laba_logs (name, timein, timeout) VALUES (?, ?, ?)",
            (teacher_name, start_time.strftime("%Y-%m-%d %H:%M:%S"), end_time.strftime("%Y-%m-%d %H:%M:%S"))
        )

    conn.commit()
    conn.close()
    print("--- Database Initialized ---")
    print(f"Teachers Created: 20")
    print(f"Log Entries Created: 200")

if __name__ == "__main__":
    init_db()