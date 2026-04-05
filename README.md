# ⚡ RFID & Mobile-Based Smart Electricity Control System

> **System #01 · IoT & Automation**
> Capstone Project — Bachelor of Science in Computer Science
> Cainta Catholic College

---

## 📖 Overview

The **RFID & Mobile-Based Smart Electricity Control System** is a prototype that manages and automates electricity usage in computer laboratories at Cainta Catholic College. RFID authentication ensures only registered users can activate lab equipment, while remote mobile and web controls give administrators real-time power management — eliminating unnecessary energy consumption caused by manual switching.

---

## ✨ Features

- **RFID Authentication** — Only authorized students and lab admins can activate the electrical supply via registered RFID cards.
- **Mobile Remote Control** — Administrators can turn electricity on/off and monitor system status in real time through a mobile interface.
- **Web Dashboard** — A web-based monitoring panel displays live device status and manages all system operations centrally.
- **Email Alerts via EmailJS** — Automated email notifications are sent when access events or anomalies occur in the laboratory.
- **Firebase Real-Time Sync** — Cloud database keeps the desktop system, web dashboard, and mobile app synchronized instantly.

---

## 🛠️ Technologies Used

| Category       | Technologies                                          |
|----------------|-------------------------------------------------------|
| **Languages**  | Python, C++, Java, HTML, CSS, JavaScript              |
| **Database**   | SQLite                                                |
| **APIs & Cloud** | Firebase, EmailJS                                  |
| **Hardware**   | Arduino, RFID Reader                                  |

---

## 🏗️ System Architecture

```
[RFID Card Scan]
       │
       ▼
[Arduino + RFID Reader]  ──►  [Python Desktop App]
       │                              │
       ▼                              ▼
[Relay/Power Control]         [SQLite Database]
                                      │
                              [Firebase Realtime DB]
                               ┌──────┴──────┐
                               ▼             ▼
                        [Web Dashboard]  [Mobile App]
                                      │
                                 [EmailJS Alerts]
```

---

## ⚙️ How It Works

1. A registered user scans their RFID card at the lab entrance.
2. The Arduino reads the RFID card ID and sends it to the Python desktop application.
3. The system authenticates the card against the SQLite database.
4. If authorized, the relay activates — supplying power to the lab equipment.
5. The event is logged and synced to Firebase in real time.
6. Administrators can monitor and override controls remotely via the web dashboard or mobile app.
7. EmailJS triggers an email notification for access events or anomalies.

---

## 📋 Prerequisites

- Python 3.x
- Arduino IDE
- Firebase account (Realtime Database enabled)
- EmailJS account
- RFID reader module (e.g., RC522) + RFID cards
- Arduino board (e.g., Uno/Mega)
- Node.js (for web dashboard dependencies, if applicable)

---

## 🚀 Getting Started

### 1. Clone the Repository
```bash
git clone https://github.com/your-repo/smart-electricity-control.git
cd smart-electricity-control
```

### 2. Configure Firebase
- Create a Firebase project and enable Realtime Database.
- Copy your Firebase config credentials into the appropriate config file.

### 3. Set Up EmailJS
- Create an EmailJS account and configure your email template.
- Update the EmailJS service ID, template ID, and user key in the config.

### 4. Flash Arduino
- Open the Arduino sketch in `/hardware/rfid_relay.ino`.
- Install required libraries: `MFRC522`, `Firebase-ESP-Client`.
- Upload the sketch to your Arduino board.

### 5. Run the Desktop Application
```bash
pip install -r requirements.txt
python main.py
```

### 6. Open the Web Dashboard
- Navigate to the `/web` directory and open `index.html` in a browser, or deploy to a web server.

---

## 👥 Team

| Name | Role |
|------|------|
| Alvarado, John Zymond D. | BSCS Student |
| Arado, Nemuel Adrian | BSCS Student |
| Bañas, JhonPaul B. | BSCS Student |
| Gabilo, Carl Allen R. | BSCS Student |
| Vicencio, Mico D. | Group Leader / Main Programmer |

---

## 🏫 Institution

**Cainta Catholic College**
Bachelor of Science in Computer Science — BSCS Tech Expo

---

*Built with ❤️ by BSCS Students of Cainta Catholic College*
