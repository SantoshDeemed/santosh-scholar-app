"""Santosh Deemed to be University - PhD/JRF Scholar App (FastAPI + SQLite)."""
import os, io, re, csv, math, secrets, hashlib, sqlite3, datetime as dt
from typing import Optional
from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Header
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

DB = os.getenv("DB_PATH", "data/app.db")
DBURL = os.getenv("DATABASE_URL", "")
PG = DBURL.startswith("postgres")   # DATABASE_URL set -> Postgres (data safe). Not set -> local SQLite file.
if PG:
    import psycopg2, psycopg2.extras
    DBERR = (sqlite3.IntegrityError, psycopg2.IntegrityError)
    BIN = psycopg2.Binary
else:
    DBERR = (sqlite3.IntegrityError,)
    BIN = lambda b: b
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
app = FastAPI(title="Santosh Scholar App")
from starlette.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=800)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id {ID}, uid TEXT UNIQUE, phone TEXT UNIQUE, name TEXT,
 role TEXT DEFAULT 'student', dept TEXT, category TEXT, supervisor TEXT, salt TEXT, pw TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY, user_id INTEGER);
CREATE TABLE IF NOT EXISTS settings(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS attendance(id {ID}, user_id INTEGER, day TEXT, ts TEXT, kind TEXT,
 lat {REAL}, lng {REAL}, accuracy {REAL}, dist {REAL}, status TEXT, photo TEXT);
CREATE TABLE IF NOT EXISTS photos(name TEXT PRIMARY KEY, data {BLOB});
CREATE TABLE IF NOT EXISTS meetings(id {ID}, kind TEXT, user_id INTEGER, title TEXT, date TEXT,
 members TEXT, status TEXT DEFAULT 'scheduled', minutes TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS reports(id {ID}, user_id INTEGER, title TEXT, milestone TEXT, status TEXT,
 publications TEXT, notes TEXT, created TEXT, feedback TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS updates(id {ID}, title TEXT, body TEXT, user_id INTEGER, created TEXT)
"""

_pool = None
NETERR = (psycopg2.OperationalError, psycopg2.InterfaceError) if PG else ()

def conn():
    global _pool
    if not PG:
        c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c
    if _pool is None:   # reuse connections: no new SSL handshake on every query = fast
        from psycopg2.pool import ThreadedConnectionPool
        _pool = ThreadedConnectionPool(1, 20, DBURL, cursor_factory=psycopg2.extras.RealDictCursor,
                                       keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3)
    return _pool.getconn()

def release(c, bad=False):
    if not PG: c.close(); return
    try:
        if not bad: c.rollback()
    except Exception: bad = True
    _pool.putconn(c, close=bad or bool(c.closed))

def fix(sql):
    if not PG: return sql
    if sql.startswith("INSERT OR IGNORE INTO"): sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO") + " ON CONFLICT DO NOTHING"
    elif sql.startswith("REPLACE INTO"): sql = sql.replace("REPLACE INTO", "INSERT INTO") + " ON CONFLICT (k) DO UPDATE SET v=EXCLUDED.v"
    sql = sql.replace("?", "%s")
    if re.match(r"INSERT INTO (users|attendance|meetings|reports|updates)\b", sql): sql += " RETURNING id"
    return sql

def q(sql, a=(), one=False, write=False):
    sql = fix(sql)
    for attempt in (0, 1):
        c = conn(); bad = False
        try:
            cur = c.cursor(); cur.execute(sql, a)
            if write:
                rid = cur.fetchone()["id"] if PG and sql.endswith("RETURNING id") else (None if PG else cur.lastrowid)
                c.commit(); return rid
            r = [dict(x) for x in cur.fetchall()]
            return (r[0] if r else None) if one else r
        except NETERR:      # stale connection (database slept): drop it and retry once
            bad = True
            if attempt: raise
        finally: release(c, bad)

def setup():
    sc = SCHEMA.replace("{ID}", "SERIAL PRIMARY KEY" if PG else "INTEGER PRIMARY KEY").replace("{REAL}", "DOUBLE PRECISION" if PG else "REAL").replace("{BLOB}", "BYTEA" if PG else "BLOB")
    c = conn(); cur = c.cursor()
    for st in sc.split(";"):
        if st.strip(): cur.execute(st)
    c.commit(); release(c)

def hp(pw, salt): return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex()
def now(): return dt.datetime.now().isoformat(timespec="seconds")

def make_user(name, phone, role="student", dept="", category="", supervisor="", pw=None):
    pw = pw or secrets.token_urlsafe(6); salt = secrets.token_hex(8)
    try:
        i = q("INSERT INTO users(uid,phone,name,role,dept,category,supervisor,salt,pw,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
              (secrets.token_hex(6), phone, name, role, dept, category, supervisor, salt, hp(pw, salt), now()), write=True)
    except DBERR: raise HTTPException(400, "This phone number is already registered")
    uid = "ADMIN" if role == "admin" else f"SDU{dt.date.today().year}{i:04d}"
    q("UPDATE users SET uid=? WHERE id=?", (uid, i), write=True)
    return {"id": i, "uid": uid, "password": pw}

def send_sms(phone, text):
    # Plug in Twilio / MSG91 / Fast2SMS here. For now the message is printed to the server log.
    print(f"[SMS to {phone}] {text}")

@app.on_event("startup")
def init():
    setup()
    for k, v in {"lat": "0", "lng": "0", "radius": "200"}.items():
        q("INSERT OR IGNORE INTO settings VALUES(?,?)", (k, v), write=True)
    if not q("SELECT 1 FROM users WHERE role='admin'", one=True):
        make_user("Administrator", os.getenv("ADMIN_PHONE", "9999999999"), "admin", pw=os.getenv("ADMIN_PASSWORD", "ChangeMe@123"))

def cfg(): return {r["k"]: float(r["v"]) for r in q("SELECT * FROM settings")}

def user(authorization: str = Header(None)):
    t = (authorization or "").replace("Bearer ", "")
    u = q("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (t,), one=True)
    if not u: raise HTTPException(401, "Please log in")
    return u
def staff(u=Depends(user)):
    if u["role"] not in ("admin", "supervisor"): raise HTTPException(403, "Staff only")
    return u
def admin(u=Depends(user)):
    if u["role"] != "admin": raise HTTPException(403, "Admin only")
    return u

def pub(u): return {k: u[k] for k in ("id", "uid", "phone", "name", "role", "dept", "category", "supervisor", "created")}

class LoginIn(BaseModel): uid: str; password: str
class PwIn(BaseModel): old: str; new: str
class StudentIn(BaseModel): name: str; phone: str; dept: str = ""; category: str = "PhD"; supervisor: str = ""; role: str = "student"
class SettingsIn(BaseModel): lat: float; lng: float; radius: float
class MeetingIn(BaseModel): kind: str; user_id: int; title: str; date: str; members: str = ""
class MeetingUp(BaseModel): status: str; minutes: str = ""
class ReportIn(BaseModel): title: str; milestone: str = ""; status: str = "In progress"; publications: str = ""; notes: str = ""
class FeedbackIn(BaseModel): feedback: str
class UpdateIn(BaseModel): title: str; body: str; user_id: Optional[int] = None

@app.get("/api/health")
def health(): q("SELECT 1 AS ok"); return {"ok": True}   # ping this every 5 min (UptimeRobot) to keep app + database awake

@app.post("/api/login")
def login(d: LoginIn):
    u = q("SELECT * FROM users WHERE uid=? OR phone=?", (d.uid.strip(), d.uid.strip()), one=True)
    if not u or not secrets.compare_digest(hp(d.password, u["salt"]), u["pw"]): raise HTTPException(401, "Wrong ID/phone or password")
    t = secrets.token_hex(24); q("INSERT INTO sessions VALUES(?,?)", (t, u["id"]), write=True)
    return {"token": t, "user": pub(u)}

@app.get("/api/me")
def me(u=Depends(user)): return pub(u)

@app.post("/api/password")
def chpw(d: PwIn, u=Depends(user)):
    if hp(d.old, u["salt"]) != u["pw"]: raise HTTPException(400, "Old password is wrong")
    if len(d.new) < 6: raise HTTPException(400, "Use at least 6 characters")
    s = secrets.token_hex(8); q("UPDATE users SET salt=?,pw=? WHERE id=?", (s, hp(d.new, s), u["id"]), write=True); return {"ok": True}

# ---- students (admin) ----
@app.post("/api/students")
def add_student(d: StudentIn, _=Depends(admin)):
    r = make_user(d.name, d.phone, d.role if d.role in ("student", "supervisor") else "student", d.dept, d.category, d.supervisor)
    send_sms(d.phone, f"Santosh University: your ID is {r['uid']} and password is {r['password']}")
    return r
@app.get("/api/students")
def students(_=Depends(staff)): return q("SELECT id,uid,phone,name,role,dept,category,supervisor FROM users WHERE role!='admin' ORDER BY id DESC")
@app.post("/api/students/{i}/reset")
def reset(i: int, _=Depends(admin)):
    u = q("SELECT * FROM users WHERE id=?", (i,), one=True)
    if not u: raise HTTPException(404, "Not found")
    pw = secrets.token_urlsafe(6); s = secrets.token_hex(8)
    q("UPDATE users SET salt=?,pw=? WHERE id=?", (s, hp(pw, s), i), write=True)
    send_sms(u["phone"], f"Santosh University: your new password is {pw}")
    return {"uid": u["uid"], "password": pw}
@app.delete("/api/students/{i}")
def del_student(i: int, _=Depends(admin)): q("DELETE FROM users WHERE id=? AND role!='admin'", (i,), write=True); return {"ok": True}

# ---- settings / geofence ----
@app.get("/api/settings")
def get_settings(_=Depends(user)): return cfg()
@app.put("/api/settings")
def put_settings(d: SettingsIn, _=Depends(admin)):
    for k, v in d.dict().items(): q("REPLACE INTO settings VALUES(?,?)", (k, str(v)), write=True)
    return cfg()

# ---- attendance with geofence ----
def dist_m(a, b, c, d):
    R = 6371000; p1, p2 = math.radians(a), math.radians(c)
    x = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(d - b) / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))

@app.post("/api/attendance")
async def mark(kind: str = Form(...), lat: float = Form(...), lng: float = Form(...), accuracy: float = Form(0),
               photo: UploadFile = File(...), u=Depends(user)):
    if kind not in ("in", "out"): raise HTTPException(400, "kind must be in/out")
    if not (photo.content_type or "").startswith("image/"): raise HTTPException(400, "A camera photo is required")
    data = await photo.read()
    if len(data) > 6_000_000: raise HTTPException(400, "Photo too large")
    s = cfg()
    if s["lat"] == 0 and s["lng"] == 0: raise HTTPException(503, "Campus location is not set yet. Ask the admin.")
    day = dt.date.today().isoformat()
    if q("SELECT 1 FROM attendance WHERE user_id=? AND day=? AND kind=? AND status='present'", (u["id"], day, kind), one=True):
        raise HTTPException(400, f"You already marked {kind.upper()} today")
    d = dist_m(lat, lng, s["lat"], s["lng"]); ok = d <= s["radius"]
    fn = secrets.token_hex(16) + ".jpg"
    q("INSERT INTO photos(name,data) VALUES(?,?)", (fn, BIN(data)), write=True)
    q("INSERT INTO attendance(user_id,day,ts,kind,lat,lng,accuracy,dist,status,photo) VALUES(?,?,?,?,?,?,?,?,?,?)",
      (u["id"], day, now(), kind, lat, lng, accuracy, d, "present" if ok else "rejected", fn), write=True)
    if not ok: raise HTTPException(403, f"You are {int(d)} m from campus (limit {int(s['radius'])} m). Attendance not marked.")
    return {"ok": True, "distance": round(d)}

def att_rows(u, day=None, uid=None):
    w, a = ["1=1"], []
    if u["role"] == "student": w.append("a.user_id=?"); a.append(u["id"])
    elif uid: w.append("a.user_id=?"); a.append(uid)
    if day: w.append("a.day=?"); a.append(day)
    return q(f"SELECT a.*,u.name,u.uid,u.dept FROM attendance a JOIN users u ON u.id=a.user_id WHERE {' AND '.join(w)} ORDER BY a.id DESC LIMIT 500", a)

@app.get("/api/attendance")
def att(day: str = None, user_id: int = None, u=Depends(user)): return att_rows(u, day, user_id)
@app.get("/api/attendance.csv")
def att_csv(day: str = None, u=Depends(staff)):
    o = io.StringIO(); w = csv.writer(o); w.writerow(["Student", "ID", "Dept", "Time", "Type", "Status", "Distance_m", "Lat", "Lng"])
    for r in att_rows(u, day): w.writerow([r["name"], r["uid"], r["dept"], r["ts"], r["kind"], r["status"], round(r["dist"]), r["lat"], r["lng"]])
    return Response(o.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=attendance.csv"})

# ---- DRC / RAC meetings ----
@app.post("/api/meetings")
def add_meeting(d: MeetingIn, _=Depends(admin)):
    if d.kind not in ("DRC", "RAC"): raise HTTPException(400, "kind must be DRC or RAC")
    i = q("INSERT INTO meetings(kind,user_id,title,date,members) VALUES(?,?,?,?,?)", (d.kind, d.user_id, d.title, d.date, d.members), write=True)
    s = q("SELECT phone FROM users WHERE id=?", (d.user_id,), one=True)
    if s: send_sms(s["phone"], f"{d.kind} meeting scheduled: {d.title} on {d.date}")
    q("INSERT INTO updates(title,body,user_id,created) VALUES(?,?,?,?)", (f"{d.kind} meeting scheduled", f"{d.title} - {d.date}", d.user_id, now()), write=True)
    return {"id": i}
@app.get("/api/meetings")
def meetings(u=Depends(user)):
    w = "WHERE m.user_id=?" if u["role"] == "student" else ""
    return q(f"SELECT m.*,u.name,u.uid FROM meetings m JOIN users u ON u.id=m.user_id {w} ORDER BY m.date DESC", (u["id"],) if w else ())
@app.put("/api/meetings/{i}")
def up_meeting(i: int, d: MeetingUp, _=Depends(staff)):
    q("UPDATE meetings SET status=?,minutes=? WHERE id=?", (d.status, d.minutes, i), write=True); return {"ok": True}

# ---- research reports ----
@app.post("/api/reports")
def add_report(d: ReportIn, u=Depends(user)):
    return {"id": q("INSERT INTO reports(user_id,title,milestone,status,publications,notes,created) VALUES(?,?,?,?,?,?,?)",
                    (u["id"], d.title, d.milestone, d.status, d.publications, d.notes, now()), write=True)}
@app.get("/api/reports")
def reports(u=Depends(user)):
    w = "WHERE r.user_id=?" if u["role"] == "student" else ""
    return q(f"SELECT r.*,u.name,u.uid FROM reports r JOIN users u ON u.id=r.user_id {w} ORDER BY r.id DESC", (u["id"],) if w else ())
@app.put("/api/reports/{i}/feedback")
def fb(i: int, d: FeedbackIn, _=Depends(staff)): q("UPDATE reports SET feedback=? WHERE id=?", (d.feedback, i), write=True); return {"ok": True}

# ---- class updates / notifications ----
@app.post("/api/updates")
def add_update(d: UpdateIn, _=Depends(admin)):
    i = q("INSERT INTO updates(title,body,user_id,created) VALUES(?,?,?,?)", (d.title, d.body, d.user_id, now()), write=True)
    for r in (q("SELECT phone FROM users WHERE id=?", (d.user_id,)) if d.user_id else q("SELECT phone FROM users WHERE role='student'")):
        send_sms(r["phone"], f"{d.title}: {d.body}")
    return {"id": i}
@app.get("/api/updates")
def updates(u=Depends(user)):
    if u["role"] == "student": return q("SELECT * FROM updates WHERE user_id IS NULL OR user_id=? ORDER BY id DESC LIMIT 100", (u["id"],))
    return q("SELECT * FROM updates ORDER BY id DESC LIMIT 100")

@app.get("/api/dashboard")
def dash(_=Depends(staff)):
    day = dt.date.today().isoformat()
    n = lambda s, a=(): q(s, a, one=True)["n"]
    return {"students": n("SELECT COUNT(*) n FROM users WHERE role='student'"),
            "present": n("SELECT COUNT(DISTINCT user_id) n FROM attendance WHERE day=? AND status='present'", (day,)),
            "pending": n("SELECT COUNT(*) n FROM meetings WHERE status='scheduled'"),
            "reports": n("SELECT COUNT(*) n FROM reports WHERE feedback=''")}

@app.get("/uploads/{name}")
def photo(name: str):
    r = q("SELECT data FROM photos WHERE name=?", (name,), one=True)
    if not r: raise HTTPException(404, "No photo")
    return Response(bytes(r["data"]), media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})

app.mount("/", StaticFiles(directory="static", html=True), name="static")
