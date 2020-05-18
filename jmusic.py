import asyncio
import os
import random
from dotenv import load_dotenv
import discord
import youtube_dl
from discord.ext import commands
#from functools import partial

load_dotenv()
token = os.getenv("TOKEN")

# Suppress noise about console usage from errors
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
    'source_address': '0.0.0.0' # bind to ipv4 since ipv6 addresses cause issues sometimes
}

ffmpeg_options = {
    'options': '-vn'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)


class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)

        self.data = data

        self.title = data.get('title')
        self.url = data.get('url')
        self.thumbnail = data.get('thumbnail')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=False):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))

        if 'entries' in data:
            # take first item from a playlist
            data = data['entries'][0]

        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **ffmpeg_options), data=data)



class JamendoMusic(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def about(self, ctx):
        """About Jamendo Music"""
        embed = discord.Embed(title="About", description="In the early years of the new millennium, there was a limited set of options for anyone who wanted to enjoy music online: downloading illegally from P2P file-sharing services, or spending money on digital downloads that you could only use on one specific device.\n\nIn the rise of more permissive models and movements such as *Open Source* and the *FreeCulture Movement*, new ideas on how to digitally share creative works came to life. *Creative Commons* brought an alternative to the automatic “all-rights reserved” copyright, eventually leading a small group of people in Luxembourg to found in 2004 the pioneering website Jamendo.com, the first platform to legally share music for free from any creator under Creative Commons licenses.\n\nMore info by [clicking here](https://www.jamendo.com/en/about).", color=0xff1e58)
        embed.set_thumbnail(url="https://i.imgur.com/G2l6t3X.png")
        embed.set_author(name="Jamendo Music", url="https://www.jamendo.com/en/", icon_url="https://i.imgur.com/G2l6t3X.png")

        await ctx.send(embed=embed)

    @commands.command()
    async def join(self, ctx, *, channel: discord.VoiceChannel):
        """Joins a voice channel"""

        if ctx.voice_client is not None:
            return await ctx.voice_client.move_to(channel)

        await channel.connect()

    @commands.command()
    async def play(self, ctx, *, url: str):
        """
        Plays a song from JamendoMusic (only Jamendo URLs supported).
        For example: https://www.jamendo.com/track/496520/jungle-of-groove
        """

        async with ctx.typing():
            player = await YTDLSource.from_url(url, loop=self.bot.loop)
            ctx.voice_client.play(player, after=lambda e: print('Player error: %s' % e) if e else None)

        embed = (discord.Embed(title="Now playing:", description="{}".format(player.title), color=0xff1e58).set_thumbnail(url=self.source.thumbnail))

        await ctx.send(embed=embed)

    @commands.command()
    async def volume(self, ctx, volume: int):
        """Changes the player's volume."""

        if ctx.voice_client is None:
            return await ctx.send("Not connected to a voice channel.")

        ctx.voice_client.source.volume = volume / 100
        await ctx.send("Changed volume to {}%".format(volume))

    @commands.command()
    async def leave(self, ctx):
        """Stops and disconnects the bot from voice"""

        await ctx.voice_client.disconnect()

    @play.before_invoke
    async def ensure_voice(self, ctx):
        if ctx.voice_client is None:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect()
            else:
                await ctx.send("You are not connected to a voice channel.")
                raise commands.CommandError("Author not connected to a voice channel.")
        elif ctx.voice_client.is_playing():
            ctx.voice_client.stop()

bot = commands.Bot(command_prefix="jm.")

@bot.event
async def on_ready():
    await bot.change_presence(status=discord.Status.online, activity=discord.Activity(name="Jamendo Music | jm.help", type=discord.ActivityType.listening))
    print('Logged in as {0}'.format(bot.user))

bot.add_cog(JamendoMusic(bot))
bot.run(token)
