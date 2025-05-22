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
        await super().setup_hook()

# 봇 설정
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True
bot = DMZBot(command_prefix='/', intents=intents)

# 동적 사용자 리스트와 기록
SELECTED = []
last_chat = {}       # {name: datetime}
last_leave = {}      # {name: datetime}
join_times = {}      # {name: datetime}
total_voice = {}     # {name: timedelta}
status_msg = None
paginator_view = None

# Firestore 헬퍼 함수
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

# 시간 가공 헬퍼 함수
def humanize_delta(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    if secs < 60:
        return f"{secs}초"
    mins = secs // 60
    hours = mins // 60
    days = delta.days
    if days > 0:
        return f"{days}일 {hours % 24}시간"
    if hours > 0:
        return f"{hours}시간 {mins % 60}분"
    return f"{mins}분"

def humanize_duration(delta: timedelta) -> str:
    secs = int(delta.total_seconds())
    hours = secs // 3600
    mins = (secs % 3600) // 60
    if hours > 0:
        return f"{hours}시간 {mins}분"
    if mins > 0:
        return f"{mins}분"
    return f"{secs}초"

# /반가워 명령어
@bot.command(name="반가워")
async def greet(ctx):
    await ctx.send("안녕하세요!")

# 페이지네이션 뷰 클래스
class PaginatorView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.current_page = 0

    @button(label='🔄 새로고침', style=discord.ButtonStyle.secondary)
    async def refresh(self, interaction, button):
        await interaction.response.edit_message(embed=make_embed(self.current_page), view=self)

    @button(label='◀ 이전', style=discord.ButtonStyle.blurple)
    async def previous(self, interaction, button):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(embed=make_embed(self.current_page), view=self)
        else:
            await interaction.response.defer()

    @button(label='다음 ▶', style=discord.ButtonStyle.blurple)
    async def next(self, interaction, button):
        max_page = (len(SELECTED) - 1) // PAGE_SIZE
        if self.current_page < max_page:
            self.current_page += 1
            await interaction.response.edit_message(embed=make_embed(self.current_page), view=self)
        else:
            await interaction.response.defer()

# 봇 준비 이벤트
@bot.event
async def on_ready():
    global status_msg, paginator_view, SELECTED, join_times
    # 데이터 로드
    load_data()
    guild = bot.get_guild(GUILD_ID)
    # 감시할 사용자 리스트
    SELECTED = [m.display_name for m in guild.members if not m.bot]
    # 현재 음성방에 있는 사용자 초기화하여 재시작 후에도 누적 기록 유지
    now = datetime.now(timezone.utc)
    for vc in guild.voice_channels:
        for member in vc.members:
            if not member.bot and member.display_name in SELECTED:
                # 이미 접속 중이던 시간 기록 시작
                join_times[member.display_name] = now
    # 상태 메시지 초기화
    channel = bot.get_channel(STATUS_CHANNEL)
    paginator_view = PaginatorView()
    status_msg = await channel.send(embed=make_embed(0), view=paginator_view)
    update_status.start()
    print(f"Logged in as {bot.user} on {guild.name}, members={len(SELECTED)}")
    guild = bot.get_guild(GUILD_ID)
    SELECTED = [m.display_name for m in guild.members if not m.bot]
    channel = bot.get_channel(STATUS_CHANNEL)
    paginator_view = PaginatorView()
    status_msg = await channel.send(embed=make_embed(0), view=paginator_view)
    update_status.start()
    print(f"Logged in as {bot.user} on {guild.name}, members={len(SELECTED)}")

# 메시지 감지 (모든 채널)
@bot.event
async def on_message(msg):
    await bot.process_commands(msg)
    if msg.author.bot:
        return
    name = msg.author.display_name
    now = datetime.now(timezone.utc)
    last_chat[name] = now
    save_chat_time(name, now)

# 음성 상태 업데이트 이벤트
@bot.event
async def on_voice_state_update(member, before, after):
    if member.guild.id != GUILD_ID:
        return
    name = member.display_name
    now = datetime.now(timezone.utc)
    if after.channel and name in SELECTED:
        join_times[name] = now
    if before.channel and not after.channel and name in SELECTED:
        start = join_times.pop(name, None)
        if start:
            duration = now - start
            total = total_voice.get(name, timedelta()) + duration
            total_voice[name] = total
            save_total_voice(name, total.total_seconds())
            last_leave[name] = now
            save_leave_time(name, now)

# Embed 생성 함수
def make_embed(page: int) -> discord.Embed:
    now = datetime.now(timezone.utc)
    sorted_users = sorted(
        SELECTED,
        key=lambda n: max(
            last_chat.get(n, datetime.min.replace(tzinfo=timezone.utc)),
            last_leave.get(n, datetime.min.replace(tzinfo=timezone.utc))
        ), reverse=True
    )
    start, end = page*PAGE_SIZE, (page+1)*PAGE_SIZE
    slice_users = sorted_users[start:end]
    total_pages = (len(sorted_users)-1)//PAGE_SIZE + 1

    e = discord.Embed(
        title="DMZ 봇 실시간 현황",
        description=f"페이지 {page+1}/{total_pages}",
        timestamp=now
    )
    for name in slice_users:
        chat_display = humanize_delta(now - last_chat.get(name, now)) + "전" if name in last_chat else "–"
        cum = total_voice.get(name, timedelta())
        if name in join_times:
            cum += now - join_times[name]
        dur_str = humanize_duration(cum)
        leave_display = humanize_delta(now - last_leave.get(name, now)) + "전" if name in last_leave else "–"
        together = "✅" if (name in join_times and now - join_times[name] > timedelta(minutes=10)) else "❌"
        e.add_field(name=name, value=f"🗣 채팅: {chat_display} | 🔊 통화: {dur_str}/{leave_display} | ⏱ 10분 같이 통화: {together}", inline=False)
    return e

# 주기적 업데이트
@tasks.loop(seconds=30)
async def update_status():
    await status_msg.edit(embed=make_embed(paginator_view.current_page), view=paginator_view)

# 봇 실행
bot.run(TOKEN)
