import asyncio
import math
import os
import random
import functools
import itertools
from dotenv import load_dotenv
from async_timeout import timeout
import discord
import youtube_dl
from discord.ext import commands

load_dotenv()
token = os.getenv("TOKEN")

# Suppress noise about console usage from errors
youtube_dl.utils.bug_reports_message = lambda: ''


ytdl_format_options = {
    'format': 'mp32',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0' # bind to ipv4 since ipv6 addresses cause issues sometimes
}

ffmpeg_options = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class VoiceError(Exception):
    pass


class YTDLError(Exception):
    pass

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)

        self.data = data

        self.title = data.get('title')
        self.url = data.get('url')
        self.thumbnail = data.get('thumbnail')

    def __str__(self):
        return '**{0.title}**'.format(self)

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            # take first item from a playlist
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)

class Song:
    __slots__ = ('player')

    def __init__(self, player: YTDLSource):
        self.player = player

    def create_embed(self):
        embed = (discord.Embed(title="Now playing:", description="{0.player.title}".format(self), color=0xff1e58).set_thumbnail(url=self.player.thumbnail))
        return embed

class SongQueue(asyncio.Queue):
    def __getitem__(self, item):
        if isinstance(item, slice):
            return list(itertools.islice(self._queue, item.start, item.stop, item.step))
        else:
            return self._queue[item]

    def __iter__(self):
        return self._queue.__iter__()

    def __len__(self):
        return self.qsize()

    def clear(self):
        self._queue.clear()

    def shuffle(self):
        random.shuffle(self.queue)

    def remove(self, index: int):
        del self._queue[index]

class VoiceState:
    def __init__(self, bot: commands.Bot, ctx: commands.Context):
        self.bot = bot
        self._ctx = ctx

        self.current = None
        self.voice = None
        self.next = asyncio.Event()
        self.songs = SongQueue()

        self._loop = False
        self._volume = 0.5
        self.skip_votes = set()

        self.audio_player = bot.loop.create_task(self.audio_player_task())

    def __del__(self):
        self.audio_player.cancel()

    @property
    def volume(self):
        return self._volume

    @volume.setter
    def volume(self, value: float):
        self._volume = value

    @property
    def is_playing(self):
        return self.voice and self.current

    async def audio_player_task(self):
        while True:
            self.next.clear()

            if not self.loop:
                # Try to get the next song within 3 minutes.
                # If no song will be added to the queue in time,
                # the player will disconnect due to performance
                # reasons.
                try:
                    async with timeout(180):  # 3 minutes
                        self.current = await self.songs.get()
                except asyncio.TimeoutError:
                    self.bot.loop.create_task(self.stop())
                    return

            self.current.source.volume = self._volume
            self.voice.play(self.current.source, after=self.play_next_song)
            await self.current.source.channel.send(embed=self.current.create_embed())

            await self.next.wait()

    def play_next_song(self, error=None):
        if error:
            raise VoiceError(str(error))

        self.next.set()

    def skip(self):
        self.skip_votes.clear()

        if self.is_playing:
            self.voice.stop()

    async def stop(self):
        self.songs.clear()

        if self.voice:
            await self.voice.disconnect()
            self.voice = None

class JamendoMusic(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.voice_states = {}

    def get_voice_state(self, ctx):
        state = self.voice_states.get(ctx.guild.id)
        if not state:
            state = VoiceState(self.bot, ctx)
            self.voice_states[ctx.guild.id] = state

        return state

    async def cog_before_invoke(self, ctx):
        ctx.voice_state = self.get_voice_state(ctx)

    @commands.command()
    async def about(self, ctx):
        """About Jamendo Music"""
        embed = discord.Embed(title="About", description="In the early years of the new millennium, there was a limited set of options for anyone who wanted to enjoy music online: downloading illegally from P2P file-sharing services, or spending money on digital downloads that you could only use on one specific device.\n\nIn the rise of more permissive models and movements such as *Open Source* and the *FreeCulture Movement*, new ideas on how to digitally share creative works came to life. *Creative Commons* brought an alternative to the automatic “all-rights reserved” copyright, eventually leading a small group of people in Luxembourg to found in 2004 the pioneering website Jamendo.com, the first platform to legally share music for free from any creator under Creative Commons licenses.\n\nMore info by [clicking here](https://www.jamendo.com/en/about).", color=0xff1e58)
        embed.set_thumbnail(url="https://i.imgur.com/G2l6t3X.png")
        embed.set_author(name="Jamendo Music", url="https://www.jamendo.com/en/", icon_url="https://i.imgur.com/G2l6t3X.png")

        await ctx.send(embed=embed)

    @commands.command(name="join")
    async def _join(self, ctx):
        """Joins your voice channel."""

        destination = ctx.author.voice.channel
        if ctx.voice_state.voice:
            await ctx.voice_state.voice.move_to(destination)
            return

        ctx.voice_state.voice = await destination.connect()

    @commands.command(name="play")
    async def _play(self, ctx, *, url: str):
        """
        Plays a song from JamendoMusic (only Jamendo URLs supported).
        For example: jm.play https://www.jamendo.com/track/496520/jungle-of-groove
        """

        if not ctx.voice_state.voice:
            await ctx.invoke(self._join)

        async with ctx.typing():
            player = await YTDLSource.from_url(url, loop=self.bot.loop)
            song = Song(player)

            await ctx.voice_state.songs.put(song)
            await ctx.send('Enqueued {}'.format(str(player)))

    @commands.command(name="leave",aliases=['disconnect',])
    async def _leave(self, ctx):
        """Clears the queue and leaves the voice channel."""

        if not ctx.voice_state.voice:
            return await ctx.send('Not connected to any voice channel.')

        await ctx.voice_state.stop()
        del self.voice_states[ctx.guild.id]

    @commands.command(name="resume")
    async def _resume(self, ctx):
        """Resumes a currently paused song."""

        if not ctx.voice_state.is_playing and ctx.voice_state.voice.is_paused():
            ctx.voice_state.voice.resume()
            await ctx.message.add_reaction('⏯')

    @commands.command(name="pause")
    async def _pause(self, ctx):
        """Pauses the currently playing song."""

        if not ctx.voice_state.is_playing and ctx.voice_state.voice.is_playing():
            ctx.voice_state.voice.pause()
            await ctx.message.add_reaction('⏯')

    @commands.command(name="skip")
    async def _skip(self, ctx):
        """Skips to the next song in queue."""

        if not ctx.voice_state.is_playing:
            return await ctx.send('Not playing any music right now...')

        voter = ctx.message.author
        if voter == ctx.voice_state.current.requester:
            await ctx.message.add_reaction('⏭')
            ctx.voice_state.skip()

        elif voter.id not in ctx.voice_state.skip_votes:
            ctx.voice_state.skip_votes.add(voter.id)
            total_votes = len(ctx.voice_state.skip_votes)

            if total_votes >= 1:
                await ctx.message.add_reaction('⏭')
                ctx.voice_state.skip()
            else:
                await ctx.send('Skip vote added, currently at **{}/1**'.format(total_votes))

        else:
            await ctx.send('You have already voted to skip this song.')

    @commands.command(name="queue")
    async def _queue(self, ctx, *, page: int = 1):
        """Shows the player's queue.
        You can optionally specify the page to show. Each page contains 10 elements.
        """

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('Empty queue.')

        items_per_page = 10
        pages = math.ceil(len(ctx.voice_state.songs) / items_per_page)

        start = (page - 1) * items_per_page
        end = start + items_per_page

        queue = ''
        for i, song in enumerate(ctx.voice_state.songs[start:end], start=start):
            queue += '`{0}.` [**{1.source.title}**]({1.source.url})\n'.format(i + 1, song)

        embed = (discord.Embed(description='**{} tracks:**\n\n{}'.format(len(ctx.voice_state.songs), queue), color=0xff1e58)
                 .set_footer(text='Viewing page {}/{}'.format(page, pages)))
        await ctx.send(embed=embed)

    @commands.command(name="remove")
    async def _remove(self, ctx, index: int):
        """Removes a song from the queue at a given index."""

        if len(ctx.voice_state.songs) == 0:
            return await ctx.send('Empty queue.')

        ctx.voice_state.songs.remove(index - 1)
        await ctx.message.add_reaction('✅')

    @commands.command(name="stop")
    async def _stop(self, ctx):
        """Stops playing the song and clears the queue."""

        ctx.voice_state.songs.clear()

        if not ctx.voice_state.is_playing:
            ctx.voice_state.voice.stop()
            await ctx.message.add_reaction('⏹')

    @_join.before_invoke
    @_play.before_invoke
    async def ensure_voice_state(self, ctx):
        if not ctx.author.voice or not ctx.author.voice.channel:
            raise commands.CommandError('You are not connected to any voice channel.')

        if ctx.voice_client:
            if ctx.voice_client.channel != ctx.author.voice.channel:
                raise commands.CommandError('Bot is already in a voice channel.')

bot = commands.Bot(command_prefix="jm.")

@bot.event
async def on_ready():
    await bot.change_presence(status=discord.Status.online, activity=discord.Activity(name="Jamendo Music | jm.help", type=discord.ActivityType.listening))
    print('Logged in as {0}'.format(bot.user))

bot.add_cog(JamendoMusic(bot))
bot.run(token)
