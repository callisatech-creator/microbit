import sys
import time
import threading
import queue

import serial  # pip install pyserial
import tkinter as tk

# ====== CONFIG ======
PORT = "COM3"          # <-- CHANGE THIS to your micro:bit serial port
BAUDRATE = 115200
FOCUS_MINUTES = 25     # 25-minute focus session
# FOCUS_MINUTES = 1    # uncomment for quick tests

event_queue = queue.Queue()


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
    if "FOCUS" in s and ("START" in s or "STRT" in s or "SART" in s or "STAT" in s):
        return "START_FOCUS"

    # END_FOCUS variants: look for END + FOCUS
    if "FOCUS" in s and ("END" in s or "STOP" in s):
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
        #if timer was not running AND remaining time > 0 
        if not self.focus_active and self.remaining > 0:
            pass
        else:
            self.remaining = self.duration
        self.focus_active = True
        self.popup_window()
        self.status_label.config(text="FOCUS ON", fg="#2ecc71")
        self.warning_label.config(text="")
        self.update_timer()

    def end_focus(self):
        print("[GUI] end_focus() called")
        if not self.focus_active:
            return
        self.focus_active = False
        self.status_label.config(text="FOCUS STOPPED", fg="#e74c3c")

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
