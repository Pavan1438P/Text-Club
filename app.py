import os
import re
import uuid
import time
import secrets
from flask import Flask, render_template, redirect, url_for, abort
from flask_socketio import SocketIO, join_room, leave_room, emit

# ── App setup ────────────────────────────────────────────────────────────────

app = Flask(__name__)

secret_key = os.environ.get("SECRET_KEY")
if not secret_key:
    secret_key = secrets.token_hex(32)
app.config["SECRET_KEY"] = secret_key

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# ── In-memory store ───────────────────────────────────────────────────────────

rooms = {}   # room_id → { "clips": [...] }

# ── Limits ────────────────────────────────────────────────────────────────────

ROOM_ID_RE      = re.compile(r"^[0-9a-f]{8}$")
CLIP_ID_RE      = re.compile(r"^[0-9a-f]{12}$")
MAX_LABEL_LEN   = 80
MAX_TEXT_BYTES  = 512 * 1024
MAX_CLIPS       = 100

# ── Helpers ───────────────────────────────────────────────────────────────────

def valid_room_id(room_id):
    return isinstance(room_id, str) and bool(ROOM_ID_RE.match(room_id))

def valid_clip_id(clip_id):
    return isinstance(clip_id, str) and bool(CLIP_ID_RE.match(clip_id))

def get_room(room_id):
    if room_id not in rooms:
        rooms[room_id] = {"clips": []}
    return rooms[room_id]

def sanitize_label(raw):
    label = str(raw).strip()[:MAX_LABEL_LEN]
    return label or "Untitled"

def sanitize_text(raw):
    text = str(raw)
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        encoded = text.encode("utf-8")[:MAX_TEXT_BYTES]
        text = encoded.decode("utf-8", errors="ignore")
    return text

# ── HTTP routes ───────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("home.html")

@app.route("/new")
def new_room():
    room_id = uuid.uuid4().hex[:8]
    rooms[room_id] = {"clips": []}
    return redirect(url_for("room", room_id=room_id))

@app.route("/room/<room_id>")
def room(room_id):
    if not valid_room_id(room_id):
        abort(404)
    get_room(room_id)
    return render_template("room.html", room_id=room_id)

@app.errorhandler(404)
def not_found(e):
    return render_template("home.html"), 404

# ── WebSocket events ──────────────────────────────────────────────────────────

@socketio.on("join")
def on_join(data):
    if not isinstance(data, dict): return
    room_id = data.get("room")
    if not valid_room_id(room_id): return
    join_room(room_id)
    room = get_room(room_id)
    emit("load_clips", {"clips": room["clips"]})

@socketio.on("add_clip")
def on_add_clip(data):
    if not isinstance(data, dict): return
    room_id = data.get("room")
    if not valid_room_id(room_id): return
    room = get_room(room_id)
    if len(room["clips"]) >= MAX_CLIPS:
        emit("error_msg", {"message": "Room is full (max 100 boxes). Delete some to add more."})
        return
    clip = {
        "id":         uuid.uuid4().hex[:12],
        "label":      sanitize_label(data.get("label", "")),
        "text":       sanitize_text(data.get("text", "")),
        "created_at": int(time.time()),
    }
    room["clips"].append(clip)
    emit("clips_update", {"clips": room["clips"]}, to=room_id)

@socketio.on("update_clip")
def on_update_clip(data):
    if not isinstance(data, dict): return
    room_id = data.get("room")
    clip_id = data.get("clip_id")
    if not valid_room_id(room_id) or not valid_clip_id(clip_id): return
    room = get_room(room_id)
    for clip in room["clips"]:
        if clip["id"] == clip_id:
            clip["label"] = sanitize_label(data.get("label", ""))
            clip["text"]  = sanitize_text(data.get("text", ""))
            break
    emit("clips_update", {"clips": room["clips"]}, to=room_id)

@socketio.on("delete_clip")
def on_delete_clip(data):
    if not isinstance(data, dict): return
    room_id = data.get("room")
    clip_id = data.get("clip_id")
    if not valid_room_id(room_id) or not valid_clip_id(clip_id): return
    room = get_room(room_id)
    room["clips"] = [c for c in room["clips"] if c["id"] != clip_id]
    emit("clips_update", {"clips": room["clips"]}, to=room_id)

@socketio.on("leave")
def on_leave(data):
    if not isinstance(data, dict): return
    room_id = data.get("room")
    if valid_room_id(room_id):
        leave_room(room_id)

# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host="0.0.0.0", port=port, debug=False, allow_unsafe_werkzeug=True)
