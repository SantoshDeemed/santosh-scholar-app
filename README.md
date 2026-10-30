# Santosh Deemed to be University - PhD / JRF Scholar App

Mobile-first web app (installable on a phone via "Add to Home Screen") with an admin panel.
Backend: FastAPI + SQLite. Frontend: one HTML file, no build step.

## Features
- Login with ID **or phone number** + password. Admin registers a scholar; ID and password are generated and sent by SMS.
- Geo-camera attendance: live selfie + GPS, checked **on the server** against the campus circle. Outside campus = rejected. One IN and one OUT per day. No gallery uploads.
- DRC / RAC meetings: schedule, members, minutes, approve / reject.
- Research reports with supervisor feedback.
- Class updates and notifications (in app, plus SMS hook).
- Admin: dashboard, scholar management (reset password, delete), attendance with photo + map link + CSV export, campus location settings.
- Roles: admin (full control), supervisor (view, feedback, minutes), student.

## Run locally
```bash
python -m venv venv && source venv/bin/activate     # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # then edit the admin phone and password
export $(cat .env | xargs)
uvicorn main:app --reload
```
Open http://localhost:8000 and log in with the admin phone and password from `.env` (ID: `ADMIN`).

**First steps:** go to **Campus**, stand on campus, tap "Use my current location", set a radius, Save. Then add scholars.

## Keep data safe (free): Neon Postgres
Free hosts (Render free) erase local files on every update. Use a free cloud database so students, attendance and selfies are never lost.
1. Sign up at neon.tech, create a project, copy the **connection string** (starts with `postgresql://`).
2. On Render: your service > **Environment** > add `DATABASE_URL` = that string. Save. It redeploys.
3. Done. Tables are created automatically. Selfies are stored in the database too (about 30 KB each).
If `DATABASE_URL` is empty, the app uses a local SQLite file (fine for testing on your PC).

## Deploy
The camera and GPS only work over **HTTPS** (localhost is fine for testing). Use Render, Railway, Fly.io or any VPS with the included `Dockerfile`.

## Before real use
- Change the admin password. Do not commit `.env`.
- SMS: edit `send_sms()` in `main.py` and call Twilio, MSG91 or Fast2SMS. Until then messages are only printed in the server log.
- Browser GPS can be faked by a determined user. For strict control, wrap this in a native app with device integrity checks.
- Selfies have random names but open to anyone with the exact link. Add login checks for production.
- Web push (notifications when the app is closed) is not included. It needs a service worker plus VAPID keys.
- File upload for DRC/RAC/research documents is not included. Reports are text only.
