import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio
from openai import OpenAI  # ✅ 최신 OpenAI SDK 사용 (Groq 호환)
from youtubesearchpython import VideosSearch
import yt_dlp

# 환경변수 불러오기
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

intents = discord.Intents.all()
bot = commands.Bot(command_prefix='/', intents=intents)

# 음악 큐
music_queue = []

# Groq API 클라이언트
client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url="https://api.groq.com/openai/v1"
)

# 유저별 대화 기록
chat_history = {}

# 애니메이션 메시지
async def animate_message(message, stop_event, prefix="DMZ 봇에게 물어보는 중"):
    dots = [".", "..", "...", ".", "..", "..."]
    i = 0
    while not stop_event.is_set():
        await asyncio.sleep(0.5)
        await message.edit(content=f"{prefix} {dots[i % len(dots)]}")
        i += 1

# 음성 채널 참가
async def ensure_voice(ctx):
    if ctx.author.voice:
        if not ctx.voice_client:
            await ctx.author.voice.channel.connect()
            await ctx.send("✅ 음성 채널에 들어왔어요!")
        return True
    else:
        await ctx.send("❌ 먼저 음성 채널에 들어가주세요.")
        return False

# DMZ 대화 명령어
@bot.command()
async def 대화(ctx, *, question):
    user_id = str(ctx.author.id)
    history = chat_history.get(user_id, [])

    loading_message = await ctx.send("DMZ 봇에게 물어보는 중")
    stop_event = asyncio.Event()
    animation_task = asyncio.create_task(animate_message(loading_message, stop_event))

    base_prompt = (
        "너는 Call of Duty: DMZ 모드의 봇이야.\n"
        "한국어로만 대화하고, 유저가 묻는 전략, 무기, 팀플레이에 대해 정확하고 친절하게 답변해.\n"
        "절대 한 클랜을 편들지 말고, 모든 클랜을 중립적으로 설명해.\n"
        "유저가 봇의 실수를 지적하면, 정중하게 인지하고 정확하게 수정해서 설명해.\n"
        "유저가 게임의 버그나 악용(예: 글리치)을 물어보면 정중히 거절하고 공식적인 플레이만 안내해.\n\n"
        "다음은 주요 클랜들에 대한 설명이야 (이 내용은 유저가 질문할 때만 참고해서 사용해):\n"
        "- NATO: 플스 패드 유저들이 주축이며, 주로 아쉬카 섬을 플레이함. 실력 있는 유저들이 많음.\n"
        "- 망치(MIU): 뉴비와 즐겜 유저들이 많고 친절한 분위기. 도움을 줄 때가 많음.\n"
        "- 악질: 실력 중심의 전투형 클랜. 대전 시 주의가 필요함.\n\n"
        "또한, 유저가 '떙떙떙 노래를 틀어줘' 또는 'OOO 노래 듣고 싶어'라고 말하면 해당 곡을 재생해."
    )

    dialogue = ""
    for pair in history[-5:]:
        dialogue += f"User: {pair['q']}\nAI: {pair['a']}\n"
    dialogue += f"User: {question}\nAI:"

    try:
        response = client.chat.completions.create(
            model="llama3-70b-8192",
            messages=[
                {"role": "system", "content": base_prompt},
                {"role": "user", "content": dialogue}
            ],
            temperature=0.7,
            max_tokens=600,
            top_p=0.9
        )
        final_response = response.choices[0].message.content.strip()

        # "노래 틀어줘" 감지
        if "노래를 틀어줘" in question or "노래 듣고 싶어" in question:
            import re
            song = re.findall(r'\"(.+?)\"', question) or re.findall(r'(?:노래를|노래) 듣고 싶어(?:요)?(?:\s*:\s*)?(.+)', question)
            if song:
                await ensure_voice(ctx)
                await ctx.invoke(bot.get_command("play"), search=song[0])

    except Exception as e:
        final_response = f"⚠️ 오류 발생: {str(e)}"

    stop_event.set()
    await animation_task
    await loading_message.edit(content=final_response)

    history.append({"q": question, "a": final_response})
    chat_history[user_id] = history

# 음악 기능
@bot.command()
async def join(ctx):
    await ensure_voice(ctx)

@bot.command()
async def play(ctx, *, search):
    loading_message = await ctx.send("🎵 곡을 찾는 중 .")
    stop_event = asyncio.Event()
    animation_task = asyncio.create_task(animate_message(loading_message, stop_event, prefix="🎵 곡을 찾는 중"))

    try:
        if search.startswith("https://") or search.startswith("www."):
            url = search
            title = "링크에서 재생 중..."
        else:
            video = VideosSearch(search, limit=1)
            results = video.result().get('result')
            if not results:
                stop_event.set()
                await animation_task
                await loading_message.edit(content="❌ 유튜브에서 결과를 찾지 못했어요.")
                return
            url = results[0]['link']
            title = results[0]['title']

        music_queue.append((url, title))

        if not ctx.voice_client or not ctx.voice_client.is_playing():
            await play_music(ctx, override_title=title, loading_message=loading_message, stop_event=stop_event, animation_task=animation_task)
        else:
            stop_event.set()
            await animation_task
            await loading_message.delete()
            await ctx.send(f"🎵 대기열에 추가됨: **{title}**")

    except Exception as e:
        stop_event.set()
        await animation_task
        await loading_message.edit(content=f"⚠️ 오류 발생: {e}")

async def play_music(ctx, override_title=None, loading_message=None, stop_event=None, animation_task=None):
    if len(music_queue) == 0:
        await ctx.send("⏹️ 더 이상 재생할 곡이 없어요.")
        return

    url, title = music_queue.pop(0)

    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'outtmpl': 'song.%(ext)s',
        'noplaylist': True,
        'default_search': 'ytsearch',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'retries': 3,
        'fragment_retries': 3,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            stream_url = info['url']

        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }

        source = discord.FFmpegPCMAudio(stream_url, **ffmpeg_options)

        ctx.voice_client.play(
            source,
            after=lambda e: print(f"🔴 재생 오류: {e}") or asyncio.run_coroutine_threadsafe(play_music(ctx), bot.loop)
        )

        final_title = override_title if override_title else title

        if stop_event and animation_task and loading_message:
            stop_event.set()
            await animation_task
            await loading_message.edit(content=f"🎶 Now playing: **{final_title}**")
        else:
            await ctx.send(f"🎶 Now playing: **{final_title}**")

    except Exception as e:
        if stop_event and animation_task and loading_message:
            stop_event.set()
            await animation_task
            await loading_message.edit(content=f"❌ 음악 재생 중 오류 발생: {e}")

@bot.command()
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ 다음 곡으로 넘어갈게요!")

@bot.command()
async def stop(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        music_queue.clear()
        await ctx.send("🛑 음악을 중지했어요.")

@bot.command()
async def left(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        music_queue.clear()
        await ctx.send("👋 음성 채널에서 나갈게요!")

@bot.event
async def on_ready():
    print(f"✅ 봇 로그인됨: {bot.user}")
    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Game("DMZ 하는 중")
    )

bot.run(DISCORD_TOKEN)