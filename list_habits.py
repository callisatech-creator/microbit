import requests
import os

HABITIFY_API_KEY = "5e18019ff199aafbc6ccf1f3faa93607f6823ba5c3858a580e05eb3fc0b98c95af74008a9b630a7ad7f915065e2a7eeb"
HABITIFY_BASE_URL = "https://api.habitify.me"

headers = {
    "Authorization": HABITIFY_API_KEY,
    "Content-Type": "application/json",
}

print("Fetching habits...")

resp = requests.get(f"{HABITIFY_BASE_URL}/habits", headers=headers)

print("Status:", resp.status_code)
try:
    data = resp.json()
    print("\nYour Habits:\n")
    for h in data.get("data", []):
        print(f"Name: {h['name']}")
        print(f"ID:   {h['id']}\n")
except Exception:
    print("Raw response:", resp.text)
