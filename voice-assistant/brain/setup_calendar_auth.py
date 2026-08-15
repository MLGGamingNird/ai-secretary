"""
Run this ONCE to connect VESPER to your Google Calendar.

What it does:
  1. Opens your browser and asks you to sign into Google and approve access.
  2. Saves the result to token.json in this folder, so main.py can use your
     calendar without ever asking you to sign in again.

You only need to re-run this if you ever delete token.json, or if you
revoke VESPER's access in your Google account settings.

Usage:
    python setup_calendar_auth.py
"""

from google_auth_oauthlib.flow import InstalledAppFlow

# "Read/write your calendars" -- the minimum scope needed for VESPER to
# check your schedule and add events.
SCOPES = ["https://www.googleapis.com/auth/calendar"]


def main():
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0)

    with open("token.json", "w") as f:
        f.write(creds.to_json())

    print("\nSuccess! token.json has been created.")
    print("VESPER can now access your Google Calendar. You don't need to run this again.")


if __name__ == "__main__":
    main()
