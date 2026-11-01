<p align="center">
  <img src="assets/brand_icon.jpg" width="180" style="border-radius: 50%; box-shadow: 0 4px 20px rgba(0,0,0,0.5);" alt="VYUHA Brand Icon"/>
</p>

# 📱 Mobile Installation & Local Network Access Guide

This guide explains how to install, run, and access the **UMX Vyuha Institutional Trading Engine & Dashboard** entirely from an Android mobile phone, and how to view the interactive dashboard on a laptop or tablet over the same Wi-Fi or mobile hotspot.

---

## 📑 Table of Contents
1. [Prerequisites](#1-prerequisites)
2. [First-Time Installation on Android (Termux)](#2-first-time-installation-on-android-termux)
3. [Running Scanners & Backtests on Mobile](#3-running-scanners--backtests-on-mobile)
4. [Launching the Web Dashboard](#4-launching-the-web-dashboard)
5. [Viewing from Laptop / Tablet on the Same Network](#5-viewing-from-laptop--tablet-on-the-same-network)
6. [Mobile Hotspot Mode (No Wi-Fi Router Needed)](#6-mobile-hotspot-mode-no-wi-fi-router-needed)
7. [Keeping Termux Alive in the Background (24/7)](#7-keeping-termux-alive-in-the-background-247)
8. [Termux Navigation & Keyboard Shortcuts](#8-termux-navigation--keyboard-shortcuts)
9. [Troubleshooting & Common Errors](#9-troubleshooting--common-errors)

---

## 1. Prerequisites

* **Android Device** (Android 8.0 or newer, minimum 3 GB RAM recommended).
* **Termux App**: Download from [F-Droid](https://f-droid.org/en/packages/com.termux/) or [GitHub Releases](https://github.com/termux/termux-app/releases). *(Do NOT install from Google Play Store as that version is deprecated).*

---

## 2. First-Time Installation on Android (Termux)

Open the Termux app and run the following commands step-by-step:

### Step 2.1: Setup Ubuntu Subsystem
```bash
# Update Termux packages
pkg update -y && pkg install -y proot-distro git

# Install Ubuntu environment
proot-distro install ubuntu

# Log in to Ubuntu
proot-distro login ubuntu
```

### Step 2.2: Install Python & Clone Repository
Inside Ubuntu:
```bash
# Install Python and build essentials
apt update && apt install -y python3 python3-pip git

# Clone your repository
git clone https://github.com/Sivachandra1988/vyuha_stock_capture.git
cd vyuha_stock_capture

# Install all required Python packages
pip install -r requirements.txt --break-system-packages --ignore-installed typing-extensions
```

### Step 2.3: Configure Angel One Credentials (Optional)
If you trade or pull live tokens via Angel One SmartAPI:
```bash
cp config/angelone_config.json.example config/angelone_config.json
nano config/angelone_config.json
```
Fill in your `api_key`, `client_code`, `pin`, and `totp_secret`.  
*(To save and exit nano: Press `CTRL + O`, press `Enter`, then press `CTRL + X`).*

---

## 3. Running Scanners & Backtests on Mobile

Navigate into the project directory:
```bash
cd ~/vyuha_stock_capture
```

### A. Run Daily Vyuha Confluence Scan
Scans 50+ liquid F&O stocks for institutional Demand/Supply Action Zones and prints a clean table:
```bash
python3 breakout_scanner.py --test-vyuha-daily
```

### B. Run Intraday 15-Minute Confluence Scan
Evaluates current 15m candle against anti-trap wick filters, AVWAP velocity slope, and 50% retracement:
```bash
python3 breakout_scanner.py --test-vyuha-intraday
```

### C. Run 2-Year Vyuha Backtester
Executes a simulated backtest across 50 liquid stocks and displays win rate, profit factor, and max drawdown:
```bash
python3 backtest_vyuha.py
```

### D. Single Scan Cycle (Process releases immediately)
```bash
python3 breakout_scanner.py
```

---

## 4. Launching the Web Dashboard

The project includes an interactive web dashboard with real-time charts, watchlist management, and active P&L tracking, integrated with the continuous scanner engine.

### Quick Launch Scripts:

* **On Mobile / Linux / Termux (Recommended):**
  ```bash
  # Run directly with bash (avoids permission denied errors):
  bash start_dashboard.sh

  # Custom port (e.g. 9000):
  bash start_dashboard.sh 9000
  ```

* **On Windows (Double-click or run from CMD):**
  ```cmd
  # Interactive prompt (defaults to 8080):
  start_dashboard.bat

  # Direct custom port:
  start_dashboard.bat 9000
  ```

### Direct Python Command:
```bash
python3 dashboard_server.py --port 8080 --host 0.0.0.0
```

### Banner Output:
When the server starts, it automatically detects your phone's network IP and displays:
```text
======================================================================
🌐  TRADER PILOT WEB DASHBOARD & SCANNER ENGINE
======================================================================
📱 Local Phone URL:    http://localhost:8080  (or http://127.0.0.1:8080)
💻 Laptop / Wi-Fi URL: http://192.168.1.15:8080
   (Ensure your phone and laptop are connected to the same Wi-Fi/Hotspot)
======================================================================
```

---

## 5. Viewing from Laptop / Tablet on the Same Network

You can monitor and manage your trading scanner from your laptop while your phone handles the computation.

### Step-by-Step:
1. **Connect to Same Wi-Fi:** Ensure your laptop and Android phone are connected to the same home or office Wi-Fi network.
2. **Start the Dashboard on Mobile:**
   ```bash
   bash start_dashboard.sh
   ```
3. **Note the Laptop URL:** Look at the terminal output on your phone for the `Laptop / Wi-Fi URL` (e.g. `http://192.168.1.15:8080`).
4. **Open Browser on Laptop:** Open Chrome, Edge, or Firefox on your laptop and navigate to:
   ```
   http://192.168.1.15:8080
   ```
*(Replace `192.168.1.15` with your phone's actual IP address shown in the banner).*

---

## 6. Mobile Hotspot Mode (No Wi-Fi Router Needed)

If you are travelling or don't have a home Wi-Fi router:

1. **Turn on Mobile Hotspot** on your Android phone.
2. **Connect your Laptop** to your phone's Wi-Fi Hotspot.
3. On your phone, start the server:
   ```bash
   bash start_dashboard.sh
   ```
4. In Android Hotspot mode, the phone's gateway IP address is typically:
   ```
   http://192.168.43.1:8080
   ```
5. Enter `http://192.168.43.1:8080` into your laptop browser.

---

## 7. Keeping Termux Alive in the Background (24/7)

Android aggressively kills background processes to save battery. To ensure your scanner and dashboard continue running even when the phone screen is off:

1. **Acquire Termux Wake-Lock:**
   Inside Termux (outside Ubuntu or in a new session), run:
   ```bash
   termux-wake-lock
   ```
   *(A notification will appear saying "Termux wake lock held").*

2. **Disable Android Battery Optimization:**
   - Go to your Phone's **Settings** -> **Apps** -> **Termux**.
   - Select **Battery** -> Change to **Unrestricted** (or turn off "Battery Optimization").

3. **Run as Continuous Daemon:**
   If you want the scanner to poll continuously during market hours in the background:
   ```bash
   python3 breakout_scanner.py --daemon
   ```

---

## 8. Termux Navigation & Keyboard Shortcuts

Termux provides an extra key bar directly above your on-screen keyboard:

| Shortcut | Action |
| :--- | :--- |
| **`CTRL` + `c`** | Stop / Kill the currently running script and return to prompt |
| **`CTRL` + `z`** | Pause current script |
| **`CTRL` + `l`** | Clear screen |
| **`Volume Down` + `c`** | Hardware shortcut for `Ctrl + C` |
| **Swipe from Left Edge** | Open Termux session drawer to create **New Session** or switch windows |
| **`killall -9 python python3`** | Emergency kill all Python processes |

---

## 9. Troubleshooting & Common Errors

### A. `Permission denied: ./start_dashboard.sh`
* **Cause:** The script does not have execute permissions (`+x`) on Android's filesystem.
* **Fix:** Run with `bash` directly (works 100% of the time):
  ```bash
  bash start_dashboard.sh
  ```
  Or grant execute permission:
  ```bash
  chmod +x start_dashboard.sh
  ```

### B. `Permission denied` on Port Binding
* **Cause:** Ports below 1024 (e.g. port 80 or 443) require root privileges in Linux/Android.
* **Fix:** Use standard user ports **1024 or higher** (e.g. `8080`, `9000`, `5000`):
  ```bash
  bash start_dashboard.sh 8080
  ```

### C. Laptop Cannot Open URL (`Connection Refused` or `Timeout`)
* **Check 1:** Ensure both your phone and laptop are connected to the **exact same Wi-Fi router** or phone's **Mobile Hotspot**.
* **Check 2:** Check if your router has "AP Isolation" or "Client Isolation" turned on (if so, turn on your phone's Mobile Hotspot instead).
* **Check 3:** Verify you typed `http://` and not `https://` (e.g. `http://192.168.1.15:8080`).

### D. `Market is CLOSED (Weekend). Process released.`
* The engine automatically stops outside trading hours (9:15 AM - 3:30 PM, Mon-Fri).
* To force-run a scan cycle immediately regardless of day/time, use:
  ```bash
  python3 breakout_scanner.py --force
  ```
