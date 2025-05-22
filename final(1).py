import os
import discord
from discord.ext import tasks, commands
from discord.ext.commands import Bot
from discord.ui import View, button
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

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

# .env에서 환경변수 로드
load_dotenv()

# 환경변수 키 읽기
TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", 0))
CHAT_CHANNEL = int(os.getenv("CHANNEL_ID", 0))
STATUS_CHANNEL = int(os.getenv("STATUS_CHANNEL", 0))

# 페이지네이션 설정
PAGE_SIZE = 8

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
intents.members = True
bot = Bot(command_prefix="/", intents=intents)

# 동적 사용자 리스트
SELECTED = []

# 기록 저장용
last_chat = {}          # {'닉네임': datetime of last chat}
last_leave = {}         # {'닉네임': datetime of last leave}
join_times = {}         # {'닉네임': datetime of current join}
total_voice = {}        # {'닉네임': timedelta total voice duration}
status_msg = None
paginator_view = None

# 간단 명령어 추가: /반가워 → 안녕하세요!
@bot.command(name="반가워")
async def greet(ctx):
    await ctx.send("안녕하세요!")

class PaginatorView(View):
    def __init__(self):
        super().__init__(timeout=None)
        self.current_page = 0

    @button(label='🔄 새로고침', style=discord.ButtonStyle.secondary, custom_id='refresh')
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(embed=make_embed(self.current_page), view=self)

    @button(label='◀ 이전', style=discord.ButtonStyle.blurple, custom_id='prev')
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.current_page > 0:
            self.current_page -= 1
            await interaction.response.edit_message(embed=make_embed(self.current_page), view=self)
        else:
            await interaction.response.defer()

    @button(label='다음 ▶', style=discord.ButtonStyle.blurple, custom_id='next')
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        max_page = (len(SELECTED) - 1) // PAGE_SIZE
        if self.current_page < max_page:
            self.current_page += 1
            await interaction.response.edit_message(embed=make_embed(self.current_page), view=self)
        else:
            await interaction.response.defer()

@bot.event
async def on_ready():
    global status_msg, paginator_view, SELECTED
    guild = bot.get_guild(GUILD_ID)
    SELECTED = [member.display_name for member in guild.members if not member.bot]
    channel = bot.get_channel(STATUS_CHANNEL)
    paginator_view = PaginatorView()
    status_msg = await channel.send(embed=make_embed(paginator_view.current_page), view=paginator_view)
    update_status.start()
    print(f"Logged in as {bot.user} on guild {guild.name}, loaded {len(SELECTED)} members")

@bot.event
async def on_message(msg):
    # 챗팅 인식 (모든 채널)
    if msg.author.bot:
        await bot.process_commands(msg)
        return
    name = msg.author.display_name
    last_chat[name] = datetime.now(timezone.utc)
    await bot.process_commands(msg)

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
            total_voice[name] = total_voice.get(name, timedelta(0)) + duration
            last_leave[name] = now


def make_embed(page: int) -> discord.Embed:
    now = datetime.now(timezone.utc)
    def last_activity(n):
        chat_t = last_chat.get(n, datetime.min.replace(tzinfo=timezone.utc))
        leave_t = last_leave.get(n, datetime.min.replace(tzinfo=timezone.utc))
        return max(chat_t, leave_t)
    sorted_users = sorted(SELECTED, key=last_activity, reverse=True)

    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    slice_users = sorted_users[start:end]
    total_pages = (len(sorted_users) - 1) // PAGE_SIZE + 1

    e = discord.Embed(
        title="DMZ 봇 실시간 현황",
        description=f"페이지 {page+1}/{total_pages}",
        timestamp=now
    )
    for name in slice_users:
        chat_time = last_chat.get(name)
        chat_display = humanize_delta(now - chat_time) + "전" if chat_time else "–"
        cum = total_voice.get(name, timedelta(0))
        if name in join_times:
            cum += now - join_times[name]
        dur_str = humanize_duration(cum)
        leave_time = last_leave.get(name)
        leave_display = humanize_delta(now - leave_time) + "전" if leave_time else "–"
        together = ("✅" if (name in join_times and now - join_times[name] > timedelta(minutes=10)) else "❌")
        field_val = (
            f"🗣 채팅: {chat_display} | "
            f"🔊 통화: {dur_str}/{leave_display} | "
            f"⏱ 10분 같이 통화: {together}"
        )
        e.add_field(name=name, value=field_val, inline=False)
    return e

@tasks.loop(seconds=30)
async def update_status():
    await status_msg.edit(embed=make_embed(paginator_view.current_page), view=paginator_view)

bot.run(TOKEN)
