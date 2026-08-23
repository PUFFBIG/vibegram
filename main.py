import os
import aiosqlite
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import Optional, Dict

# — Настройки —

SECRET_KEY = os.environ.get(“SECRET_KEY”, “vibegram_super_secret_key_change_in_prod_123”)
ALGORITHM = “HS256”
ACCESS_TOKEN_EXPIRE_MINUTES = 43200  # 30 дней

app = FastAPI(title=“Vibegram API”)

app.add_middleware(
CORSMiddleware,
allow_origins=[”*”],
allow_credentials=True,
allow_methods=[”*”],
allow_headers=[”*”],
)

pwd_context = CryptContext(schemes=[“bcrypt”], deprecated=“auto”)

# — Модели Pydantic —

class UserRegister(BaseModel):
nick: str
password: str

class UserLogin(BaseModel):
nick: str
password: str

class ChatCreate(BaseModel):
target_nick: str

class ProfileUpdate(BaseModel):
avatar_emoji: Optional[str] = None
avatar_color: Optional[str] = None
bio: Optional[str] = None

class ChannelCreate(BaseModel):
name: str
title: str
description: Optional[str] = “”
avatar_emoji: Optional[str] = “📢”

class ChannelMessageIn(BaseModel):
text: str

# — База данных SQLite —

DB_NAME = “vibegram.db”

async def init_db():
async with aiosqlite.connect(DB_NAME) as db:
await db.execute(”””
CREATE TABLE IF NOT EXISTS users (
id INTEGER PRIMARY KEY AUTOINCREMENT,
nick TEXT UNIQUE NOT NULL,
password_hash TEXT NOT NULL,
online INTEGER DEFAULT 0
)
“””)
# Миграция: добавляем новые колонки, если БД создана до этого обновления
for coldef in [
“avatar_emoji TEXT DEFAULT ‘😊’”,
“avatar_color TEXT DEFAULT ‘#7c3aed’”,
“bio TEXT DEFAULT ‘’”,
]:
try:
await db.execute(f”ALTER TABLE users ADD COLUMN {coldef}”)
except Exception:
pass

```
    await db.execute("""
        CREATE TABLE IF NOT EXISTS chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user1_id INTEGER NOT NULL,
            user2_id INTEGER NOT NULL,
            UNIQUE(user1_id, user2_id)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(chat_id) REFERENCES chats(id),
            FOREIGN KEY(sender_id) REFERENCES users(id)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            avatar_emoji TEXT DEFAULT '📢',
            owner_id INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS channel_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            UNIQUE(channel_id, user_id)
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS channel_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    await db.commit()
```

@app.on_event(“startup”)
async def startup():
await init_db()

def create_access_token(data: dict):
to_encode = data.copy()
expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
to_encode.update({“exp”: expire})
return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str):
try:
payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
user_id: int = payload.get(“user_id”)
if user_id is None:
raise HTTPException(status_code=401, detail=“Invalid token”)
return user_id
except JWTError:
raise HTTPException(status_code=401, detail=“Invalid token”)

# Определена ДО использования в других роутах — иначе NameError при старте

async def get_current_user_from_header(token: str = Depends(get_current_user)):
return token

# — Auth / Профиль —

@app.post(”/api/register”)
async def register(user: UserRegister):
nick = user.nick.strip()
if not nick or not user.password:
raise HTTPException(status_code=400, detail=“Заполните все поля”)

```
async with aiosqlite.connect(DB_NAME) as db:
    # Проверка уникальности ника без учёта регистра (Lol и lol считаются одинаковыми)
    existing = await db.execute("SELECT id FROM users WHERE LOWER(nick) = LOWER(?)", (nick,))
    if await existing.fetchone():
        raise HTTPException(status_code=400, detail="Такой никнейм уже занят")

    hash_password = pwd_context.hash(user.password)
    await db.execute("INSERT INTO users (nick, password_hash) VALUES (?, ?)", (nick, hash_password))
    await db.commit()
return {"message": "Успешная регистрация"}
```

@app.post(”/api/login”)
async def login(user: UserLogin):
async with aiosqlite.connect(DB_NAME) as db:
db.row_factory = aiosqlite.Row
cur = await db.execute(“SELECT * FROM users WHERE LOWER(nick) = LOWER(?)”, (user.nick.strip(),))
db_user = await cur.fetchone()

```
    if not db_user or not pwd_context.verify(user.password, db_user["password_hash"]):
        raise HTTPException(status_code=400, detail="Неверный ник или пароль")

    token = create_access_token({"user_id": db_user["id"]})
return {
    "access_token": token,
    "token_type": "bearer",
    "nick": db_user["nick"],
    "avatar_emoji": db_user["avatar_emoji"],
    "avatar_color": db_user["avatar_color"],
    "bio": db_user["bio"],
}
```

@app.get(”/api/me”)
async def get_me(user_id: int = Depends(get_current_user_from_header)):
async with aiosqlite.connect(DB_NAME) as db:
db.row_factory = aiosqlite.Row
cur = await db.execute(
“SELECT id, nick, avatar_emoji, avatar_color, bio FROM users WHERE id = ?”, (user_id,)
)
u = await cur.fetchone()
if not u:
raise HTTPException(status_code=404, detail=“Пользователь не найден”)
return dict(u)

@app.patch(”/api/profile”)
async def update_profile(data: ProfileUpdate, user_id: int = Depends(get_current_user_from_header)):
fields, values = [], []
if data.avatar_emoji is not None:
fields.append(“avatar_emoji = ?”)
values.append(data.avatar_emoji)
if data.avatar_color is not None:
fields.append(“avatar_color = ?”)
values.append(data.avatar_color)
if data.bio is not None:
fields.append(“bio = ?”)
values.append(data.bio[:200])
if not fields:
return {“message”: “Нечего обновлять”}

```
values.append(user_id)
async with aiosqlite.connect(DB_NAME) as db:
    await db.execute(f"UPDATE users SET {', '.join(fields)} WHERE id = ?", values)
    await db.commit()
return {"message": "Профиль обновлён"}
```

# — Чаты —

@app.post(”/api/chats/create”)
async def create_chat(data: ChatCreate, my_id: int = Depends(get_current_user_from_header)):
async with aiosqlite.connect(DB_NAME) as db:
db.row_factory = aiosqlite.Row
cur = await db.execute(“SELECT id FROM users WHERE LOWER(nick) = LOWER(?)”, (data.target_nick.strip(),))
target_user = await cur.fetchone()

```
    if not target_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")
    if target_user["id"] == my_id:
        raise HTTPException(status_code=400, detail="Нельзя создать чат с собой")

    u1, u2 = min(my_id, target_user["id"]), max(my_id, target_user["id"])
    cur = await db.execute("SELECT id FROM chats WHERE user1_id = ? AND user2_id = ?", (u1, u2))
    existing_chat = await cur.fetchone()
    if existing_chat:
        return {"chat_id": existing_chat["id"]}

    await db.execute("INSERT INTO chats (user1_id, user2_id) VALUES (?, ?)", (u1, u2))
    await db.commit()

    cur = await db.execute("SELECT id FROM chats WHERE user1_id = ? AND user2_id = ?", (u1, u2))
    new_chat = await cur.fetchone()
return {"chat_id": new_chat["id"]}
```

@app.get(”/api/chats”)
async def get_chats(user_id: int = Depends(get_current_user_from_header)):
async with aiosqlite.connect(DB_NAME) as db:
db.row_factory = aiosqlite.Row
query = “””
SELECT c.id as chat_id, u.nick, u.online, u.avatar_emoji, u.avatar_color,
m.text as last_message, m.timestamp
FROM chats c
JOIN users u ON (u.id = CASE WHEN c.user1_id = ? THEN c.user2_id ELSE c.user1_id END)
LEFT JOIN messages m ON m.id = (SELECT id FROM messages WHERE chat_id = c.id ORDER BY id DESC LIMIT 1)
WHERE c.user1_id = ? OR c.user2_id = ?
ORDER BY m.timestamp DESC
“””
cur = await db.execute(query, (user_id, user_id, user_id))
chats = await cur.fetchall()
return [dict(chat) for chat in chats]

@app.get(”/api/messages/{chat_id}”)
async def get_messages(chat_id: int, user_id: int = Depends(get_current_user_from_header)):
async with aiosqlite.connect(DB_NAME) as db:
db.row_factory = aiosqlite.Row
cur = await db.execute(
“SELECT id FROM chats WHERE id = ? AND (user1_id = ? OR user2_id = ?)”, (chat_id, user_id, user_id)
)
if not await cur.fetchone():
raise HTTPException(status_code=403, detail=“Доступ запрещен”)

```
    cur = await db.execute(
        "SELECT sender_id, text, timestamp FROM messages WHERE chat_id = ? ORDER BY timestamp ASC", (chat_id,)
    )
    messages = await cur.fetchall()
return [dict(msg) for msg in messages]
```

# — Каналы —

@app.post(”/api/channels/create”)
async def create_channel(data: ChannelCreate, owner_id: int = Depends(get_current_user_from_header)):
name = data.name.strip().lstrip(’@’)
title = data.title.strip()
if not name or not title:
raise HTTPException(status_code=400, detail=“Укажите имя и название канала”)

```
async with aiosqlite.connect(DB_NAME) as db:
    db.row_factory = aiosqlite.Row
    existing = await db.execute("SELECT id FROM channels WHERE LOWER(name) = LOWER(?)", (name,))
    if await existing.fetchone():
        raise HTTPException(status_code=400, detail="Канал с таким именем уже существует")

    cur = await db.execute(
        "INSERT INTO channels (name, title, description, avatar_emoji, owner_id) VALUES (?, ?, ?, ?, ?)",
        (name, title, data.description or "", data.avatar_emoji or "📢", owner_id),
    )
    await db.commit()
    channel_id = cur.lastrowid
    await db.execute("INSERT INTO channel_members (channel_id, user_id) VALUES (?, ?)", (channel_id, owner_id))
    await db.commit()
return {"channel_id": channel_id}
```

@app.get(”/api/channels/search”)
async def search_channels(q: str = “”, user_id: int = Depends(get_current_user_from_header)):
async with aiosqlite.connect(DB_NAME) as db:
db.row_factory = aiosqlite.Row
like = f”%{q.strip()}%”
cur = await db.execute(
“””
SELECT ch.id as channel_id, ch.name, ch.title, ch.description, ch.avatar_emoji, ch.owner_id,
(SELECT COUNT(*) FROM channel_members cm WHERE cm.channel_id = ch.id) as members_count,
EXISTS(SELECT 1 FROM channel_members cm2 WHERE cm2.channel_id = ch.id AND cm2.user_id = ?) as is_member
FROM channels ch
WHERE ch.name LIKE ? OR ch.title LIKE ?
LIMIT 30
“””,
(user_id, like, like),
)
rows = await cur.fetchall()
return [dict(r) for r in rows]

@app.get(”/api/channels/my”)
async def my_channels(user_id: int = Depends(get_current_user_from_header)):
async with aiosqlite.connect(DB_NAME) as db:
db.row_factory = aiosqlite.Row
cur = await db.execute(
“””
SELECT ch.id as channel_id, ch.name, ch.title, ch.avatar_emoji, ch.owner_id,
(ch.owner_id = ?) as is_owner,
m.text as last_message, m.timestamp
FROM channel_members cm
JOIN channels ch ON ch.id = cm.channel_id
LEFT JOIN channel_messages m ON m.id = (SELECT id FROM channel_messages WHERE channel_id = ch.id ORDER BY id DESC LIMIT 1)
WHERE cm.user_id = ?
ORDER BY m.timestamp DESC
“””,
(user_id, user_id),
)
rows = await cur.fetchall()
return [dict(r) for r in rows]

@app.post(”/api/channels/{channel_id}/join”)
async def join_channel(channel_id: int, user_id: int = Depends(get_current_user_from_header)):
async with aiosqlite.connect(DB_NAME) as db:
cur = await db.execute(“SELECT id FROM channels WHERE id = ?”, (channel_id,))
if not await cur.fetchone():
raise HTTPException(status_code=404, detail=“Канал не найден”)
try:
await db.execute(
“INSERT INTO channel_members (channel_id, user_id) VALUES (?, ?)”, (channel_id, user_id)
)
await db.commit()
except Exception:
pass
return {“message”: “Вы подписались”}

@app.post(”/api/channels/{channel_id}/leave”)
async def leave_channel(channel_id: int, user_id: int = Depends(get_current_user_from_header)):
async with aiosqlite.connect(DB_NAME) as db:
db.row_factory = aiosqlite.Row
cur = await db.execute(“SELECT owner_id FROM channels WHERE id = ?”, (channel_id,))
ch = await cur.fetchone()
if ch and ch[“owner_id”] == user_id:
raise HTTPException(status_code=400, detail=“Владелец не может покинуть свой канал”)
await db.execute(“DELETE FROM channel_members WHERE channel_id = ? AND user_id = ?”, (channel_id, user_id))
await db.commit()
return {“message”: “Вы отписались”}

@app.get(”/api/channels/{channel_id}/messages”)
async def get_channel_messages(channel_id: int, user_id: int = Depends(get_current_user_from_header)):
async with aiosqlite.connect(DB_NAME) as db:
db.row_factory = aiosqlite.Row
cur = await db.execute(
“SELECT id FROM channel_members WHERE channel_id = ? AND user_id = ?”, (channel_id, user_id)
)
if not await cur.fetchone():
raise HTTPException(status_code=403, detail=“Вы не подписаны на этот канал”)

```
    cur = await db.execute(
        "SELECT sender_id, text, timestamp FROM channel_messages WHERE channel_id = ? ORDER BY timestamp ASC",
        (channel_id,),
    )
    rows = await cur.fetchall()
return [dict(r) for r in rows]
```

@app.post(”/api/channels/{channel_id}/messages”)
async def post_channel_message(
channel_id: int, data: ChannelMessageIn, user_id: int = Depends(get_current_user_from_header)
):
async with aiosqlite.connect(DB_NAME) as db:
db.row_factory = aiosqlite.Row
cur = await db.execute(“SELECT owner_id FROM channels WHERE id = ?”, (channel_id,))
ch = await cur.fetchone()
if not ch:
raise HTTPException(status_code=404, detail=“Канал не найден”)
if ch[“owner_id”] != user_id:
raise HTTPException(status_code=403, detail=“Публиковать может только владелец канала”)

```
    await db.execute(
        "INSERT INTO channel_messages (channel_id, sender_id, text) VALUES (?, ?, ?)",
        (channel_id, user_id, data.text),
    )
    await db.commit()
    cur = await db.execute(
        "SELECT * FROM channel_messages WHERE channel_id = ? ORDER BY id DESC LIMIT 1", (channel_id,)
    )
    msg = dict(await cur.fetchone())

    cur = await db.execute("SELECT user_id FROM channel_members WHERE channel_id = ?", (channel_id,))
    member_ids = [r["user_id"] for r in await cur.fetchall()]

payload = {"type": "new_channel_message", "channel_id": channel_id, "message": msg}
for mid in member_ids:
    if mid in manager.active_connections:
        await manager.active_connections[mid].send_json(payload)

return msg
```

# — WebSocket —

class ConnectionManager:
def **init**(self):
self.active_connections: Dict[int, WebSocket] = {}

```
async def connect(self, websocket: WebSocket, user_id: int):
    await websocket.accept()
    self.active_connections[user_id] = websocket
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET online = 1 WHERE id = ?", (user_id,))
        await db.commit()

def disconnect(self, user_id: int):
    if user_id in self.active_connections:
        del self.active_connections[user_id]

async def set_offline(self, user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET online = 0 WHERE id = ?", (user_id,))
        await db.commit()
```

manager = ConnectionManager()

@app.websocket(”/ws”)
async def websocket_endpoint(websocket: WebSocket, token: str = None):
if not token:
await websocket.close(code=1008)
return

```
try:
    user_id = await get_current_user(token)
except HTTPException:
    await websocket.close(code=1008)
    return

await manager.connect(websocket, user_id)
try:
    while True:
        data = await websocket.receive_json()
        chat_id = data.get("chat_id")
        text = data.get("text")
        if not chat_id or not text:
            continue

        async with aiosqlite.connect(DB_NAME) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT user1_id, user2_id FROM chats WHERE id = ?", (chat_id,))
            chat = await cur.fetchone()

            if not chat or (chat["user1_id"] != user_id and chat["user2_id"] != user_id):
                await websocket.send_json({"error": "Нет доступа к чату"})
                continue

            await db.execute(
                "INSERT INTO messages (chat_id, sender_id, text) VALUES (?, ?, ?)", (chat_id, user_id, text)
            )
            await db.commit()

            cur = await db.execute(
                "SELECT * FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT 1", (chat_id,)
            )
            msg = dict(await cur.fetchone())

        target_id = chat["user2_id"] if chat["user1_id"] == user_id else chat["user1_id"]

        payload = {"type": "new_message", "chat_id": chat_id, "message": msg}

        await manager.active_connections[user_id].send_json(payload)
        if target_id in manager.active_connections:
            await manager.active_connections[target_id].send_json(payload)

except WebSocketDisconnect:
    manager.disconnect(user_id)
    await manager.set_offline(user_id)
```

if **name** == “**main**”:
import uvicorn
port = int(os.environ.get(“PORT”, 8000))
uvicorn.run(app, host=“0.0.0.0”, port=port)
