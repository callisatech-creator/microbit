import sys
import time
import threading
import queue
import datetime
from typing import Optional

import serial          # pip install pyserial
import requests        # pip install requests
import tkinter as tk

# ====== CONFIG ======
PORT = "COM3"          # <-- CHANGE THIS if your micro:bit is on a different COM port
BAUDRATE = 115200
FOCUS_MINUTES = 25     # 25-minute focus session
# FOCUS_MINUTES = 1    # uncomment for quick tests

event_queue = queue.Queue()

# ----- Habitify API config -----
HABITIFY_BASE_URL = "https://api.habitify.me"
HABITIFY_API_KEY = "5e18019ff199aafbc6ccf1f3faa93607f6823ba5c3858a580e05eb3fc0b98c95af74008a9b630a7ad7f915065e2a7eeb"
HABITIFY_HABIT_ID = "D3F6A59D-80F6-4E6D-B28A-89E52E8DE5B1"  # Study Focus Session
HABITIFY_UNIT_TYPE = "min"   # assuming the habit goal is in minutes


def habitify_headers():
    return {
        "Authorization": HABITIFY_API_KEY,
        "Content-Type": "application/json"
    }


# ---------- Habitify: Actions (works like your second script) ----------
def habitify_create_action() -> Optional[str]:
    """
    Create a Habitify action for the configured habit.
    Returns the action_id (string) if present, or None if Habitify
    does not return one. In both cases a 200/201 means success.
    """
    url = f"{HABITIFY_BASE_URL}/actions/{HABITIFY_HABIT_ID}"

    # Habitify wants: YYYY-MM-DDThh:mm:ss±hh:mm  (with timezone offset)
    now_local = datetime.datetime.now().astimezone()  # local time w/ tzinfo
    remind_at = now_local.replace(microsecond=0).isoformat()  # e.g. 2025-11-30T14:12:03-05:00

    print("[HABITIFY] Using remind_at:", remind_at)

    payload = {
        "title": "Focus session (micro:bit)",
        "remind_at": remind_at
    }

    try:
        resp = requests.post(url, json=payload, headers=habitify_headers(), timeout=10)
        print("[HABITIFY] Create action status:", resp.status_code)

        if resp.status_code not in (200, 201):
            print("[HABITIFY] Error creating action:", resp.text)
            return None

        # Try to parse an action id if Habitify returns one
        try:
            body = resp.json()
        except Exception:
            body = {}

        data = body.get("data")

        if isinstance(data, dict) and "id" in data:
            action_id = data["id"]
            print("[HABITIFY] Created action with id:", action_id)
            return action_id
        else:
            # Your previous behavior: "data": null → still success
            print("[HABITIFY] Action created (no id returned, that's OK).")
            return None

    except Exception as e:
        print("[HABITIFY] Exception while creating action:", e)
        return None


def habitify_complete_action(action_id: Optional[str]):
    """
    Mark an existing Habitify action as Done (status = 1).
    """
    if not action_id:
        return

    url = f"{HABITIFY_BASE_URL}/actions/{HABITIFY_HABIT_ID}/{action_id}"
    payload = {
        "status": 1  # 0 = Not Done Yet, 1 = Done
    }

    try:
        resp = requests.put(url, json=payload, headers=habitify_headers(), timeout=10)
        print("[HABITIFY] Complete action status:", resp.status_code)
        if resp.status_code not in (200, 201):
            print("[HABITIFY] Error completing action:", resp.text)
        else:
            print("[HABITIFY] Action marked as Done.")
    except Exception as e:
        print("[HABITIFY] Exception while completing action:", e)


# ---------- Habitify: Logs (optional, to record minutes) ----------
def habitify_add_log(minutes_value: float, end_time: Optional[datetime.datetime] = None):
    """
    Add a log to the Study Focus Session habit in Habitify.
    - minutes_value: how long this focus session lasted (in minutes)
    - end_time: when the session ended (datetime with timezone)
    """
    if end_time is None:
        end_time = datetime.datetime.now().astimezone()

    # Habitify expects full ISO with offset: YYYY-MM-DDThh:mm:ss±hh:mm
    target_date = end_time.replace(microsecond=0).isoformat()
    print("[HABITIFY] Using target_date:", target_date)
    print("[HABITIFY] Logging minutes:", minutes_value)

    url = f"{HABITIFY_BASE_URL}/logs/{HABITIFY_HABIT_ID}"
    payload = {
        "unit_type": HABITIFY_UNIT_TYPE,     # "min"
        "value": minutes_value,              # e.g., 23.5
        "target_date": target_date
    }

    try:
        resp = requests.post(url, json=payload, headers=habitify_headers(), timeout=10)
        print("[HABITIFY] Add log status:", resp.status_code)
        print("[HABITIFY] Response:", resp.text)
    except Exception as e:
        print("[HABITIFY] Error while adding log:", e)


# ---------- Helper: interpret noisy serial lines ----------
def interpret_event(line: str):
    """
    Take a raw line from the micro:bit and try to map it
    to one of: START_FOCUS, END_FOCUS, SUDDEN_MOVE, or None.
    This makes things robust to small typos like STAT_FOCUS, SART_FOCUS, etc.
    """
    s = line.strip().upper()
    # Debug:
    print("[INTERPRET]", repr(s))

    # START_FOCUS variants: look for both START-ish and FOCUS
    if "FOCUS" in s and ("START" in s or "STRT" in s or "SART" in s or "STAT" in s or "TART" in s):
        return "START_FOCUS"

    # END_FOCUS variants: look for END + FOCUS or STOP + FOCUS or FINISH + FOCUS
    if "FOCUS" in s and ("END" in s or "STOP" in s or "FINISH" in s):
        return "END_FOCUS"

    # Sudden movement variants
    if "MOVE" in s or "MOTION" in s or "SHAKE" in s:
        return "SUDDEN_MOVE"

    # If we can't confidently interpret it, ignore
    return None


# ====== SERIAL LISTENER THREAD ======
def serial_listener():
    try:
        ser = serial.Serial(PORT, BAUDRATE, timeout=1)
    except serial.SerialException as e:
        print("Could not open serial port:", e)
        sys.exit(1)

    print(f"[SERIAL] Listening on {PORT} ...")
    time.sleep(2)

    while True:
        try:
            line = ser.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            print("[MICROBIT RAW]", repr(line))

            event = interpret_event(line)
            if event is not None:
                print("[MICROBIT EVENT]", event)
                event_queue.put(event)

        except Exception as e:
            print("[SERIAL ERROR]", e)
            time.sleep(1)


# ====== TKINTER APP ======
class FocusApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Focus Session")
        self.root.configure(bg="#111111")
        self.root.geometry("340x220")

        # Start hidden, show on first focus
        self.root.withdraw()

        self.focus_active = False
        self.duration = FOCUS_MINUTES * 60
        self.remaining = 0

        # For Habitify actions
        self.current_action_id: Optional[str] = None

        # For accurate session timing
        self.session_start_time: Optional[datetime.datetime] = None

        # UI widgets
        self.time_label = tk.Label(
            root,
            text="--:--",
            font=("Helvetica", 40, "bold"),
            fg="#ffffff",
            bg="#111111"
        )
        self.time_label.pack(pady=(20, 10))

        self.status_label = tk.Label(
            root,
            text="FOCUS OFF",
            font=("Helvetica", 14),
            fg="#e74c3c",
            bg="#111111"
        )
        self.status_label.pack(pady=(0, 10))

        self.warning_label = tk.Label(
            root,
            text="",
            font=("Helvetica", 11),
            fg="#f1c40f",
            bg="#111111"
        )
        self.warning_label.pack(pady=(0, 10))

        hint = tk.Label(
            root,
            text="Use micro:bit\nA = Start focus, B = End focus",
            font=("Helvetica", 10),
            fg="#aaaaaa",
            bg="#111111"
        )
        hint.pack(pady=(0, 10))

        # Start polling for events
        self.root.after(200, self.poll_events)

    def popup_window(self):
        """Always show and bring the window to front."""
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(1500, lambda: self.root.attributes("-topmost", False))

    def start_focus(self):
        print("[GUI] start_focus() called")

        # If already active, ignore extra STARTs to avoid overlapping sessions
        if self.focus_active:
            return

        self.focus_active = True
        self.remaining = self.duration

        # Mark real start time
        self.session_start_time = datetime.datetime.now().astimezone()

        self.popup_window()
        self.status_label.config(text="FOCUS ON", fg="#2ecc71")
        self.warning_label.config(text="")

        # Create a Habitify action when session starts (like your working code)
        self.current_action_id = habitify_create_action()

        self.update_timer()

    def end_focus(self):
        print("[GUI] end_focus() called")
        if not self.focus_active:
            return

        self.focus_active = False
        self.status_label.config(text="FOCUS STOPPED", fg="#e74c3c")

        end_time = datetime.datetime.now().astimezone()

        # Calculate elapsed time for logging
        if self.session_start_time is not None:
            elapsed_seconds = (end_time - self.session_start_time).total_seconds()
            minutes_value = max(elapsed_seconds / 60.0, 0.1)  # avoid zero
            print(f"[SESSION] Elapsed seconds: {elapsed_seconds}, minutes: {minutes_value}")
            habitify_add_log(minutes_value, end_time=end_time)
        else:
            print("[SESSION] No session_start_time recorded; skipping log minutes.")

        # Mark Habitify action as done (if we have one)
        if self.current_action_id:
            habitify_complete_action(self.current_action_id)
            self.current_action_id = None

        # Reset for next session
        self.session_start_time = None

    def sudden_move(self):
        print("[GUI] sudden_move() called")
        self.warning_label.config(text="⚠️ Sudden movement detected – refocus?")
        self.root.after(3000, lambda: self.warning_label.config(text=""))

    def update_timer(self):
        if not self.focus_active:
            return

        mins = self.remaining // 60
        secs = self.remaining % 60
        self.time_label.config(text=f"{mins:02d}:{secs:02d}")

        if self.remaining <= 0:
            self.focus_active = False
            self.status_label.config(text="SESSION COMPLETE", fg="#f1c40f")
            self.warning_label.config(text="")

            end_time = datetime.datetime.now().astimezone()

            # Compute elapsed based on session_start_time
            if self.session_start_time is not None:
                elapsed_seconds = (end_time - self.session_start_time).total_seconds()
                minutes_value = max(elapsed_seconds / 60.0, 0.1)
                print(f"[SESSION] Auto-complete. Elapsed seconds: {elapsed_seconds}, minutes: {minutes_value}")
                habitify_add_log(minutes_value, end_time=end_time)
            else:
                print("[SESSION] Auto-complete reached but no session_start_time.")

            # Complete Habitify action if present
            if self.current_action_id:
                habitify_complete_action(self.current_action_id)
                self.current_action_id = None

            self.session_start_time = None
            return

        self.remaining -= 1
        self.root.after(1000, self.update_timer)

    def poll_events(self):
        while not event_queue.empty():
            msg = event_queue.get()
            print("[GUI] Got event:", msg)

            if msg == "START_FOCUS":
                self.start_focus()
            elif msg == "END_FOCUS":
                self.end_focus()
            elif msg == "SUDDEN_MOVE":
                self.sudden_move()

        self.root.after(200, self.poll_events)


def main():
    t = threading.Thread(target=serial_listener, daemon=True)
    t.start()

    root = tk.Tk()
    app = FocusApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

