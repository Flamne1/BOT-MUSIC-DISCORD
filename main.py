
from asyncio.queues import Queue
from re import A
import discord
from discord import message
from discord import client
from discord import channel
from discord import player
from discord import embeds
from discord import voice_client
from discord.embeds import Embed
from discord.ext import commands
from datetime import datetime, timedelta
from discord.ext.commands.core import guild_only
from discord import FFmpegPCMAudio
from discord.gateway import VoiceKeepAliveHandler
from discord.utils import get
import youtube_dl
import asyncio
from async_timeout import timeout
from functools import partial
import itertools

# wrapper / dacorator

message_lastseen = datetime.now()
message2_lastseen = datetime.now()

bot = commands.Bot(command_prefix='!')

youtube_dl.utils.bug_reports_message = lambda: ''

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0' 
}

ffmpeg_options = {
    'options': '-vn',
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5" 
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, *, data, requester):
        super().__init__(source)
        self.requester = requester

        self.title = data.get('title')
        self.web_url = data.get('webpage_url')

    def __getitem__(self, item: str):
        """Allows us to access attributes similar to a dict.
        This is only useful when you are NOT downloading.
        """
        return self.__getattribute__(item)

    @classmethod
    async def create_source(cls, ctx, search: str, *, loop, download=False):
        loop = loop or asyncio.get_event_loop()

        to_run = partial(ytdl.extract_info, url=search, download=download)
        data = await loop.run_in_executor(None, to_run)

        if 'entries' in data:
            
            data = data['entries'][0]

        await ctx.send(f'```ini\n[Added {data["title"]} to the Queue.]\n```')

        if download:
            source = ytdl.prepare_filename(data)
        else:
            return {'webpage_url': data['webpage_url'], 'requester': ctx.author, 'title': data['title']}

        return cls(discord.FFmpegPCMAudio(source, **ffmpeg_options), data=data, requester=ctx.author)

    @classmethod
    async def regather_stream(cls, data, *, loop):
        """Used for preparing a stream, instead of downloading.
        Since Youtube Streaming links expire."""
        loop = loop or asyncio.get_event_loop()
        requester = data['requester']

        to_run = partial(ytdl.extract_info, url=data['webpage_url'], download=False)
        data = await loop.run_in_executor(None, to_run)

        return cls(discord.FFmpegPCMAudio(data['url'], **ffmpeg_options), data=data, requester=requester)

class MusicPlayer:

    __slots__ = ('bot', '_guild', '_channel', '_cog', 'queue', 'next', 'current', 'np', 'volume')

    def __init__(self, ctx):
        self.bot = ctx.bot
        self._guild = ctx.guild
        self._channel = ctx.channel
        self._cog = ctx.cog

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.np = None 
        self.volume = .5
        self.current = None

        ctx.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        """Our main player loop."""
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                
                async with timeout(250):  
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                del players[self._guild]
                return await self.destroy(self._guild)

            if not isinstance(source, YTDLSource):
               
                try:
                    source = await YTDLSource.regather_stream(source, loop=self.bot.loop)
                except Exception as e:
                    await self._channel.send(f'There was an error processing your song.\n'
                                             f'```css\n[{e}]\n```')
                    continue

            source.volume = self.volume
            self.current = source

            self._guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            self.np = await self._channel.send(f'**Now Playing:** `{source.title}` requested by '
                                               f'`{source.requester}`')
            await self.next.wait()
        
            source.cleanup()
            self.current = None

            try:
                
                await self.np.delete()
            except discord.HTTPException:
                pass

    async def destroy(self, guild):
        """Disconnect and cleanup the player."""
        await self._guild.voice_client.disconnect()
        return self.bot.loop.create_task(self._cog.cleanup(guild))


class songAPI:
    def __init__(self):
        self.players = {}

    async def play(self, ctx,search: str):
        self.bot = ctx.bot
        self._guild = ctx.guild
        channel = ctx.author.voice.channel
        voice_client = get(self.bot.voice_clients, guild=ctx.guild)
        
        if voice_client == None:
            await ctx.channel.send("Joined")
            await channel.connect()
            voice_client = get(self.bot.voice_clients, guild=ctx.guild)

        await ctx.trigger_typing()

        _player = self.get_player(ctx)
        source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop, download=False)

        await _player.queue.put(source)


    players = {}
    def get_player(self, ctx):
        try:
            player = self.players[ctx.guild.id]
        except:
            player = MusicPlayer(ctx)
            self.players[ctx.guild.id] = player
        
        return player
        

    async def stop(self, ctx):
        voice_client = get(self.bot.voice_clients, guild=ctx.guild)
        if voice_client == None:
            await ctx.channel.send("Bot is not connected to vc")
            return

        if voice_client.channel != ctx.author.voice.channel:
            await ctx.channel.send("The bot is currently connected to {0}".format(voice_client.channel))
            return

        voice_client.stop()

    async def pause(self, ctx):
        voice_client = get(self.bot.voice_clients, guild=ctx.guild)
        if voice_client == None:
            await ctx.channel.send("Bot is not connected to vc")
            return

        if voice_client.channel != ctx.author.voice.channel:
            await ctx.channel.send("The bot is currently connected to {0}".format(voice_client.channel))
            return

        voice_client.pause()

    async def resume(self, ctx):
        voice_client = get(self.bot.voice_clients, guild=ctx.guild)
        if voice_client == None:
            await ctx.channel.send("Bot is not connected to vc")
            return

        if voice_client.channel != ctx.author.voice.channel:
            await ctx.channel.send("The bot is currently connected to {0}".format(voice_client.channel))
            return

        voice_client.resume()

    async def leave(self, ctx):
        del self.players[ctx.guild.id]
        await ctx.voice_client.disconnect()

    async def queueList(self, ctx):
        voice_client = get(self.bot.voice_clients, guild=ctx.guild)

        if voice_client == None or not voice_client.is_connected():
            await ctx.channel.send("Bot is not connected to vc", delete_after=10)
            return
        
        player = self.get_player(ctx)
        if player.queue.empty():
            return await ctx.send('There are currently no more queued songs')
        
        # 1 2 3
        upcoming = list(itertools.islice(player.queue._queue,0,player.queue.qsize()))
        fmt = '\n'.join(f'**`{_["title"]}`**' for _ in upcoming)
        embed = discord.Embed(title=f'Upcoming - Next {len(upcoming)}', description=fmt)
        await ctx.send(embed=embed)

    async def skip(self, ctx):
        voice_client = get(self.bot.voice_clients, guild=ctx.guild)

        if voice_client == None or not voice_client.is_connected():
            await ctx.channel.send("Bot is not connected to vc", delete_after=10)
            return

        if voice_client.is_paused():
            pass
        elif not voice_client.is_playing():
            return

        voice_client.stop()
        await ctx.send(f'**`{ctx.author}`**: Skipped the song!')

@bot.command()
async def Help(ctx):
    emBed = discord.Embed(title="Help Command", description="All avaliable bot commands", color=0x42f5a7)
    emBed.add_field(name="Help,", value="Get Help Command", inline=False)
    emBed.add_field(name="Play,", value="Let's start listen to music", inline=False)
    emBed.add_field(name="Stop,", value="Stop music", inline=False)
    emBed.add_field(name="Resume,", value="Resume music", inline=False)
    emBed.add_field(name="Skip,", value="Press For Skip music", inline=False)
    emBed.add_field(name="Leave,", value="Leave BOT", inline=False)
    emBed.add_field(name="Join,", value="CMON", inline=False)
    emBed.add_field(name="QueueList,", value="Show queue songs(is in progress.)", inline=False)
    emBed.set_thumbnail(url='https://scontent.fbkk8-4.fna.fbcdn.net/v/t1.15752-9/258867305_445744790505763_7418211449054647093_n.png?_nc_cat=100&ccb=1-5&_nc_sid=ae9488&_nc_eui2=AeGFO8L843ws5LAb2irAWrZxePqEhLVUz9R4-oSEtVTP1AtA9DufNeDoxLF6ItMfPT9i07tlpfVrZJWrD19IQ8LS&_nc_ohc=VYTNYRKzUrUAX-T4LXW&_nc_ht=scontent.fbkk8-4.fna&oh=03_AVK0Pq0nuOJ4axYodVsQDJKyG7dEQRBB7GgK9QNdZSMPWQ&oe=61E9996D')
    await ctx.channel.send(embed=emBed)

@bot.command()
async def play(ctx,* ,search: str):
    channel = ctx.author.voice.channel
    voice_client = get(bot.voice_clients, guild=ctx.guild)

    if voice_client == None :
        await ctx.channel.send("Joined")
        await channel.connect()
        voice_client = get(bot.voice_clients, guild=ctx.guild)

    _player = get_player(ctx)
    source = await YTDLSource.create_source(ctx, search, loop=bot.loop, download=False)

    await _player.queue.put(source)

players = {}
def get_player(ctx):
    try:
        player = players[ctx.guild.id]
    except:
        player = MusicPlayer(ctx)
        players[ctx.guild.id] = player

    return player    


@bot.command()
async def stop(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None:
        await ctx.channel.send("Bot is not connected to vc")
        return

    if voice_client.channel != ctx.author.voice.channel:
        await ctx.channel.send("The bot is currently connected to {0}".format(voice_client.channel))

    voice_client.stop()

@bot.command()
async def pause(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None:
        await ctx.channel.send("Bot is not connected to vc")
        return

    if voice_client.channel != ctx.author.voice.channel:
        await ctx.channel.send("The bot is currently connected to {0}".format(voice_client.channel))

    voice_client.pause()

@bot.command()
async def resume(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None:
        await ctx.channel.send("Bot is not connected to vc")
        return

    if voice_client.channel != ctx.author.voice.channel:
        await ctx.channel.send("The bot is currently connected to {0}".format(voice_client.channel))

    voice_client.resume()

@bot.command()
async def leave(ctx):
    channel = ctx.author.voice.channel
    await ctx.voice_client.disconnect()
    await channel.disconnect()

@bot.command()
async def queuelist(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)
    if voice_client == None or not voice_client.is_connected():
        await ctx.channel.send("Bot is not connected to vc", delete_after=10)
        return

    player = get_player(ctx)
    if player.queue.empty():
        return await ctx.send("There are currently no more queued songs")

    upcoming = list(itertools.islice(player.queue,0,player.queue.qsize()))
    fmt = '\n'.join(f'**~{_["title"]}~**' for _ in upcoming)
    embed = discord.Embed(title=f'Upcoming - Next{len(upcoming)}', description=fmt)

    await ctx.send(embed)

@bot.command()
async def skip(ctx):
    voice_client = get(bot.voice_clients, guild=ctx.guild)

    if voice_client == None or not voice_client.is_connected():
        await ctx.channel.send("Bot is not connected to vc", delete_after=10)
        return

    if voice_client.is_paused():
        pass
    elif not voice_client.is_playing():
        return

    voice_client.stop()
    await ctx.send(f'**~{ctx.author}~**: Skipped the song!')
        
bot.run('OTIzMjI0NDU4NDQ4MjI4Mzkz.YcM5xw.IALgWqT2MhZGQDw5z_WIdmngimI')