import asyncio
import aiosqlite
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from passlib.context import CryptContext
from jose import JWTError, jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, List

# --- Настройки ---
SECRET_KEY = "vibegram_super_secret_key_change_in_prod_123"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 43200 # 30 дней

app = FastAPI(title="Vibegram API")

# Разрешаем запросы с любого фронтенда (для локальной разработки и GitHub Pages)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# --- Модели Pydantic ---
class UserRegister(BaseModel):
    nick: str
    password: str

class UserLogin(BaseModel):
    nick: str
    password: str

class ChatCreate(BaseModel):
    target_nick: str

# --- База данных SQLite ---
DB_NAME = "vibegram.db"

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nick TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                online INTEGER DEFAULT 0
            )
        """)
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
        await db.commit()

@app.on_event("startup")
async def startup():
    await init_db()

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(token: str):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# --- API Роуты ---

@app.post("/api/register")
async def register(user: UserRegister):
    async with aiosqlite.connect(DB_NAME) as db:
        # Проверка уникальности ника
        existing = await db.execute("SELECT id FROM users WHERE nick = ?", (user.nick,))
        if await existing.fetchone():
            raise HTTPException(status_code=400, detail="Ник уже занят")
        
        hash_password = pwd_context.hash(user.password)
        await db.execute("INSERT INTO users (nick, password_hash) VALUES (?, ?)", (user.nick, hash_password))
        await db.commit()
    return {"message": "Успешная регистрация"}

@app.post("/api/login")
async def login(user: UserLogin):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM users WHERE nick = ?", (user.nick,))
        db_user = await cur.fetchone()
        
        if not db_user or not pwd_context.verify(user.password, db_user["password_hash"]):
            raise HTTPException(status_code=400, detail="Неверный ник или пароль")
        
        token = create_access_token({"user_id": db_user["id"]})
    return {"access_token": token, "token_type": "bearer", "nick": db_user["nick"]}

@app.post("/api/chats/create")
async def create_chat(data: ChatCreate, token: str = Depends(get_current_user_from_header)):
    my_id = token
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM users WHERE nick = ?", (data.target_nick,))
        target_user = await cur.fetchone()
        
        if not target_user:
            raise HTTPException(status_code=404, detail="Пользователь не найден")
        if target_user["id"] == my_id:
            raise HTTPException(status_code=400, detail="Нельзя создать чат с собой")

        # Проверяем, есть ли уже чат (сортируем ID чтобы не было дубликатов)
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

# Зависимость для получения юзера из заголовка (используется в обычных POST запросах)
async def get_current_user_from_header(token: str = Depends(get_current_user)):
    return token

@app.get("/api/chats")
async def get_chats(user_id: int = Depends(get_current_user_from_header)):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        # Получаем все чаты, где участвует юзер, подтягиваем ник собеседника и последнее сообщение
        query = """
            SELECT c.id as chat_id, u.nick, u.online, m.text as last_message, m.timestamp
            FROM chats c
            JOIN users u ON (u.id = CASE WHEN c.user1_id = ? THEN c.user2_id ELSE c.user1_id END)
            LEFT JOIN messages m ON m.id = (SELECT id FROM messages WHERE chat_id = c.id ORDER BY id DESC LIMIT 1)
            WHERE c.user1_id = ? OR c.user2_id = ?
            ORDER BY m.timestamp DESC
        """
        cur = await db.execute(query, (user_id, user_id, user_id))
        chats = await cur.fetchall()
        return [dict(chat) for chat in chats]

@app.get("/api/messages/{chat_id}")
async def get_messages(chat_id: int, user_id: int = Depends(get_current_user_from_header)):
    async with aiosqlite.connect(DB_NAME) as db:
        db.row_factory = aiosqlite.Row
        # Проверка, что юзер состоит в этом чате
        cur = await db.execute("SELECT id FROM chats WHERE id = ? AND (user1_id = ? OR user2_id = ?)", (chat_id, user_id, user_id))
        if not await cur.fetchone():
            raise HTTPException(status_code=403, detail="Доступ запрещен")
            
        cur = await db.execute("SELECT sender_id, text, timestamp FROM messages WHERE chat_id = ? ORDER BY timestamp ASC", (chat_id,))
        messages = await cur.fetchall()
        return [dict(msg) for msg in messages]

# --- WebSocket ---
class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, WebSocket] = {} # user_id: websocket

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        # Статус "В сети"
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET online = 1 WHERE id = ?", (user_id,))
            await db.commit()

    def disconnect(self, user_id: int):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
        # Статус "Не в сети" (выполняется синхронно, для простоты)

    async def set_offline(self, user_id: int):
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("UPDATE users SET online = 0 WHERE id = ?", (user_id,))
            await db.commit()

manager = ConnectionManager()

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = None):
    if not token:
        await websocket.close(code=1008)
        return
        
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
            
            if not chat_id or not text: continue

            # Сохраняем в БД
            async with aiosqlite.connect(DB_NAME) as db:
                db.row_factory = aiosqlite.Row
                # Проверка доступа к чату
                cur = await db.execute("SELECT user1_id, user2_id FROM chats WHERE id = ?", (chat_id,))
                chat = await cur.fetchone()
                if not chat or (chat["user1_id"] != user_id and chat["user2_id"] != user_id):
                    await websocket.send_json({"error": "Нет доступа к чату"})
                    continue
                
                await db.execute("INSERT INTO messages (chat_id, sender_id, text) VALUES (?, ?, ?)", (chat_id, user_id, text))
                await db.commit()
                
                cur = await db.execute("SELECT * FROM messages WHERE chat_id = ? ORDER BY id DESC LIMIT 1", (chat_id,))
                msg = dict(await cur.fetchone())

            # Определяем получателя
            target_id = chat["user2_id"] if chat["user1_id"] == user_id else chat["user1_id"]
            
            # Формируем payload
            payload = {
                "type": "new_message",
                "chat_id": chat_id,
                "message": msg
            }
            
            # Отправляем себе (подтверждение)
            await manager.active_connections[user_id].send_json(payload)
            
            # Отправляем собеседнику, если он онлайн
            if target_id in manager.active_connections:
                await manager.active_connections[target_id].send_json(payload)

    except WebSocketDisconnect:
        manager.disconnect(user_id)
        await manager.set_offline(user_id)
