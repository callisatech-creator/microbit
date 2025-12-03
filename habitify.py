import sys
import time
import threading
import queue
from datetime import datetime
from typing import Optional
import platform
import subprocess
import csv

import serial          # pip install pyserial
import requests        # pip install requests
import tkinter as tk
import matplotlib.pyplot as plt   # pip install matplotlib

# ====== CONFIG ======
# On Mac, your micro:bit port will look like /dev/cu.usbmodemXXX.
# Adjust this if it changes.
PORT = "/dev/cu.usbmodem102"
BAUDRATE = 115200
FOCUS_MINUTES = 25             # 25-minute focus session
# FOCUS_MINUTES = 1            # uncomment for quick tests

event_queue = queue.Queue()
IS_MAC = (platform.system() == "Darwin")

# ----- Habitify API config -----
HABITIFY_BASE_URL = "https://api.habitify.me"
HABITIFY_API_KEY = "5e18019ff199aafbc6ccf1f3faa93607f6823ba5c3858a580e05eb3fc0b98c95af74008a9b630a7ad7f915065e2a7eeb"
HABITIFY_HABIT_ID = "BE861428-3B40-4142-98A0-8914951844FE"  # Study Focus Session
# For a habit that is "5 times per day", Habitify uses unit_type = "rep"
HABITIFY_UNIT_TYPE = "rep"


def habitify_headers():
    return {
        "Authorization": HABITIFY_API_KEY,
        "Content-Type": "application/json"
    }


# ---------- Habitify: Actions ----------
def habitify_create_action() -> Optional[str]:
    """
    Create a Habitify action for the configured habit.
    Returns the action_id (string) if present, or None if Habitify
    does not return one. In both cases a 200/201 means success.
    """
    url = f"{HABITIFY_BASE_URL}/actions/{HABITIFY_HABIT_ID}"

    # Habitify wants: YYYY-MM-DDThh:mm:ss±hh:mm  (with timezone offset)
    now_local = datetime.now().astimezone()
    remind_at = now_local.replace(microsecond=0).isoformat()

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


# ---------- Habitify: Logs (record reps for the session) ----------
def habitify_add_log(value: float, end_time: Optional[datetime] = None):
    """
    Add a log to the Study Focus Session habit in Habitify.
    - value: number of reps (we just log 1 rep per finished session)
    - end_time: when the session ended (datetime with timezone)
    """
    if end_time is None:
        end_time = datetime.now().astimezone()

    # Habitify expects full ISO with offset: YYYY-MM-DDThh:mm:ss±hh:mm
    target_date = end_time.replace(microsecond=0).isoformat()
    print("[HABITIFY] Using target_date:", target_date)
    print("[HABITIFY] Logging value:", value, "unit_type:", HABITIFY_UNIT_TYPE)

    url = f"{HABITIFY_BASE_URL}/logs/{HABITIFY_HABIT_ID}"
    headers = habitify_headers()

    payload = {
        "value": value,                  # e.g., 1
        "unit_type": HABITIFY_UNIT_TYPE, # "rep" for times-per-day habits
        "target_date": target_date
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=10)
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
    print("[INTERPRET]", repr(s))

    # START_FOCUS variants
    if "FOCUS" in s and ("START" in s or "STRT" in s or "SART" in s or "STAT" in s or "TART" in s):
        return "START_FOCUS"

    # END_FOCUS variants
    if "FOCUS" in s and ("END" in s or "STOP" in s or "FINISH" in s):
        return "END_FOCUS"

    # Sudden movement variants
    if "MOVE" in s or "MOTION" in s or "SHAKE" in s:
        return "SUDDEN_MOVE"

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
        self.root.geometry("380x270")  # slightly taller to fit movement + button

        # Start hidden, show on first focus
        self.root.withdraw()

        self.focus_active = False
        self.duration = FOCUS_MINUTES * 60
        self.remaining = 0

        # For Habitify actions
        self.current_action_id: Optional[str] = None

        # For accurate session timing
        self.session_start_time: Optional[datetime] = None

        # Movement tracking
        self.move_count: int = 0

        # Focus mode enforcement
        self.focus_enforcer_thread: Optional[threading.Thread] = None
        self.focus_enforcer_running: bool = False

        # ---------- UI widgets ----------
        self.time_label = tk.Label(
            root,
            text="--:--",
            font=("Helvetica", 40, "bold"),
            fg="#ffffff",
            bg="#111111"
        )
        self.time_label.pack(pady=(20, 5))

        self.status_label = tk.Label(
            root,
            text="FOCUS OFF",
            font=("Helvetica", 14),
            fg="#e74c3c",
            bg="#111111"
        )
        self.status_label.pack(pady=(0, 5))

        # Movement label
        self.movement_label = tk.Label(
            root,
            text="Moves: 0",
            font=("Helvetica", 11),
            fg="#ffffff",
            bg="#111111"
        )
        self.movement_label.pack(pady=(0, 5))

        self.warning_label = tk.Label(
            root,
            text="",
            font=("Helvetica", 11),
            fg="#f1c40f",
            bg="#111111"
        )
        self.warning_label.pack(pady=(0, 5))

        hint = tk.Label(
            root,
            text="Use micro:bit\nA = Start focus, B = End focus",
            font=("Helvetica", 10),
            fg="#aaaaaa",
            bg="#111111"
        )
        hint.pack(pady=(0, 5))

        # 🔵 Button inside the timer GUI to view the movement graph
        history_btn = tk.Button(
            root,
            text="View History (Moves per Session)",
            command=self.show_history,
            bg="#333333",
            fg="#ffffff",
            activebackground="#444444",
            activeforeground="#ffffff"
        )
        history_btn.pack(pady=(5, 10))

        # Start polling for events
        self.root.after(200, self.poll_events)

    # ---------- Desktop focus / app hiding ----------
    def enter_desktop_focus(self):
        if not IS_MAC:
            print("[FOCUS] Desktop focus mode not supported on this OS.")
            return

        print("[FOCUS] Entering macOS focus mode…")

        # Optional: run your macOS Shortcut to toggle a Focus mode
        try:
            subprocess.run(
                ["shortcuts", "run", "Microbit Focus On"],
                check=False
            )
            print("[FOCUS] Running macOS Shortcut: 'Microbit Focus On'")
        except FileNotFoundError:
            print("[FOCUS] 'shortcuts' CLI not found; skipping Shortcut run.")

        # Start enforcement thread to hide non-allowed apps
        if not self.focus_enforcer_running:
            self.focus_enforcer_running = True
            self.focus_enforcer_thread = threading.Thread(
                target=self.focus_enforcer_loop,
                daemon=True
            )
            self.focus_enforcer_thread.start()

    def leave_desktop_focus(self):
        if not IS_MAC:
            return

        print("[FOCUS] Leaving macOS focus mode…")
        try:
            subprocess.run(
                ["shortcuts", "run", "Microbit Focus Off"],
                check=False
            )
            print("[FOCUS] Running macOS Shortcut: 'Microbit Focus Off'")
        except FileNotFoundError:
            print("[FOCUS] 'shortcuts' CLI not found; skipping Shortcut run.")

        # Stop enforcement loop
        self.focus_enforcer_running = False

    def focus_enforcer_loop(self):
        """
        Periodically hide all non-allowed apps using AppleScript with
        'set visible of process ... to false' instead of 'hide'.
        """
        # IMPORTANT: update allowedApps if you want more apps allowed.
        applescript = r'''
tell application "System Events"
    set allowedApps to {"Google Chrome", "Microsoft Word", "Terminal", "Python", "python3"}
    set frontApps to every application process whose background only is false
    repeat with proc in frontApps
        set appName to name of proc
        if allowedApps does not contain appName then
            try
                set visible of proc to false
            end try
        end if
    end repeat
end tell
'''.strip("\n")

        print("[FOCUS] Enforcer loop started.")
        while self.focus_enforcer_running:
            try:
                subprocess.run(
                    ["osascript", "-e", applescript],
                    check=False
                )
            except Exception as e:
                print("[FOCUS] Error running AppleScript:", e)
            time.sleep(5)
        print("[FOCUS] Enforcer loop stopped.")

    # ---------- Session logging (CSV) ----------
    def log_session_to_csv(self, end_time: datetime, elapsed_seconds: float):
        """
        Append a row to session_logs.csv with:
        - end_time (ISO)
        - duration_minutes (float)
        - move_count (int)
        """
        duration_minutes = round(elapsed_seconds / 60.0, 3)
        iso_end = end_time.replace(microsecond=0).isoformat()

        print(f"[LOG] Saving session: end={iso_end}, minutes={duration_minutes}, moves={self.move_count}")
        try:
            with open("session_logs.csv", "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([iso_end, duration_minutes, self.move_count])
        except Exception as e:
            print("[LOG] Error writing CSV:", e)

    # ---------- History visualization ----------
        # ---------- History visualization ----------
    def show_history(self):
        """
        Read session_logs.csv and show:
        - Bar chart of movement counts per session
        - Line plot of session duration (minutes) per session
        """
        try:
            rows = []
            with open("session_logs.csv", "r", newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    if len(row) >= 3:
                        rows.append(row)
        except FileNotFoundError:
            self.warning_label.config(text="No history yet. Complete a session first.")
            self.root.after(4000, lambda: self.warning_label.config(text=""))
            return

        if not rows:
            self.warning_label.config(text="No data in history yet.")
            self.root.after(4000, lambda: self.warning_label.config(text=""))
            return

        # rows: [iso_end, duration_minutes, move_count]
        sessions = list(range(1, len(rows) + 1))
        move_counts = []
        durations = []

        for r in rows:
            # duration_minutes (index 1)
            try:
                durations.append(float(r[1]))
            except ValueError:
                durations.append(0.0)

            # move_count (index 2)
            try:
                move_counts.append(int(float(r[2])))
            except ValueError:
                move_counts.append(0)

        # Create combined chart: bars for movements, line for duration
        fig, ax1 = plt.subplots()

        # Bar chart for movement counts
        ax1.bar(sessions, move_counts)
        ax1.set_xlabel("Session")
        ax1.set_ylabel("Movements")

        # Line plot for session duration (minutes) using a second y-axis
        ax2 = ax1.twinx()
        ax2.plot(sessions, durations, marker="o")
        ax2.set_ylabel("Session duration (minutes)")

        plt.title("Movements & Duration per Focus Session")
        plt.xticks(sessions)
        fig.tight_layout()
        plt.show()


    # ---------- UI helpers ----------
    def popup_window(self):
        """Always show and bring the window to front."""
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(1500, lambda: self.root.attributes("-topmost", False))

    # ---------- Focus session control ----------
    def start_focus(self):
        print("[GUI] start_focus() called")

        if self.focus_active:
            return

        self.focus_active = True
        self.remaining = self.duration

        # Reset movement count for this new session
        self.move_count = 0
        self.movement_label.config(text="Moves: 0")

        # Mark real start time
        self.session_start_time = datetime.now().astimezone()

        self.popup_window()
        self.status_label.config(text="FOCUS ON", fg="#2ecc71")
        self.warning_label.config(text="")

        # Create a Habitify action when session starts
        self.current_action_id = habitify_create_action()

        # Enter macOS focus mode + hide other apps (on Mac)
        self.enter_desktop_focus()

        self.update_timer()

    def end_focus(self):
        print("[GUI] end_focus() called")
        if not self.focus_active:
            return

        self.focus_active = False
        self.status_label.config(text="FOCUS STOPPED", fg="#e74c3c")

        end_time = datetime.now().astimezone()

        # For Habitify "5 times per day" style habit, we just log 1 rep per session.
        if self.session_start_time is not None:
            elapsed_seconds = (end_time - self.session_start_time).total_seconds()
            minutes_value = elapsed_seconds / 60.0
            print(f"[SESSION] Elapsed seconds: {elapsed_seconds}, minutes: {minutes_value}")
            print(f"[SESSION] Move count: {self.move_count}")

            # Log 1 rep to Habitify
            habitify_add_log(1.0, end_time=end_time)

            # Log details to CSV
            self.log_session_to_csv(end_time, elapsed_seconds)
        else:
            print("[SESSION] No session_start_time recorded; skipping log reps.")

        # Mark Habitify action as done (if we have one)
        if self.current_action_id:
            habitify_complete_action(self.current_action_id)
            self.current_action_id = None

        # Leave macOS focus mode
        self.leave_desktop_focus()

        # Reset for next session
        self.session_start_time = None

    def sudden_move(self):
        print("[GUI] sudden_move() called")
        # Increment movement counter
        self.move_count += 1
        self.movement_label.config(text=f"Moves: {self.move_count}")

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

            end_time = datetime.now().astimezone()

            if self.session_start_time is not None:
                elapsed_seconds = (end_time - self.session_start_time).total_seconds()
                minutes_value = elapsed_seconds / 60.0
                print(f"[SESSION] Auto-complete. Elapsed seconds: {elapsed_seconds}, minutes: {minutes_value}")
                print(f"[SESSION] Move count: {self.move_count}")

                # Log 1 rep for full session
                habitify_add_log(1.0, end_time=end_time)
                # Log CSV
                self.log_session_to_csv(end_time, elapsed_seconds)
            else:
                print("[SESSION] Auto-complete reached but no session_start_time.")

            if self.current_action_id:
                habitify_complete_action(self.current_action_id)
                self.current_action_id = None

            self.leave_desktop_focus()
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
