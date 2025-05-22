import os
import json
import discord
from discord.ext import tasks, commands
from discord.ext.commands import Bot
from discord.ui import View, button
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from aiohttp import web

# Firebase Admin SDK 초기화
import firebase_admin
from firebase_admin import credentials, firestore

# 환경 변수 로드
load_dotenv()

# Firebase 서비스 계정 키 처리: 경로 또는 JSON 문자열
sa = os.getenv("FIREBASE_SERVICE_ACCOUNT_JSON")
if sa and os.path.isfile(sa):
    cred = credentials.Certificate(sa)
else:
    cred_dict = json.loads(sa or "{}")
    cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)

# Firestore 클라이언트
db = firestore.client()

# 간단 HTTP 서버 (Render WebService 포트 바인딩용)
async def handle(request):
    return web.Response(text="OK")

async def start_webserver():
    app = web.Application()
    app.add_routes([web.get("/", handle)])
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# 환경변수 키 읽기
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
STATUS_CHANNEL = int(os.getenv("STATUS_CHANNEL", "0"))
PAGE_SIZE = 8

# 봇 클래스 정의 (setup_hook으로 웹서버 시작)
class DMZBot(Bot):
    async def setup_hook(self):
        # 웹서버 시작
        self.loop.create_task(start_webserver())
        # 뷰 등록 (optional)
        return await super().setup_hook()

# 봇 설정
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True
bot = DMZBot(command_prefix='/', intents=intents)

# 동적 사용자 리스트와 기록
SELECTED = []
last_chat = {}
last_leave = {}
join_times = {}
total_voice = {}
status_msg = None
paginator_view = None

# Firestore 헬퍼 함수
# ... (기존 헬퍼 함수 그대로 유지) ...

def save_chat_time(name: str, t: datetime):
    db.collection('last_chat').document(name).set({'time': t.isoformat()})
def get_all_chat_times():
    return {doc.id: datetime.fromisoformat(doc.to_dict()['time'])
            for doc in db.collection('last_chat').stream()}

def save_leave_time(name: str, t: datetime):
    db.collection('last_leave').document(name).set({'time': t.isoformat()})
def get_all_leave_times():
    return {doc.id: datetime.fromisoformat(doc.to_dict()['time'])
            for doc in db.collection('last_leave').stream()}

def save_total_voice(name: str, secs: float):
    db.collection('total_voice').document(name).set({'seconds': secs})
def get_all_total_voice():
    return {doc.id: doc.to_dict().get('seconds', 0)
            for doc in db.collection('total_voice').stream()}

# 데이터 로드
def load_data():
    global last_chat, last_leave, total_voice
    last_chat = get_all_chat_times()
    last_leave = get_all_leave_times()
    raw = get_all_total_voice()
    total_voice = {k: timedelta(seconds=v) for k, v in raw.items()}

# /반가워 명령어, 페이지네이션 뷰, on_ready, on_message, on_voice_state_update,
# make_embed, update_status 등 기존 이벤트 핸들러 그대로 이어서 작성...

# 봇 실행
bot.run(TOKEN)
