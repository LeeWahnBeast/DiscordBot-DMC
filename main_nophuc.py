import asyncio
import re
import runpy
import unicodedata
from collections import defaultdict
from datetime import timedelta

import discord

PATTERN = re.compile(r"ph[uú]c", re.IGNORECASE)
strikes = defaultdict(int)

def bad(text):
    return bool(text) and bool(PATTERN.search(unicodedata.normalize("NFC", text)))

async def handle(msg):
    if msg.author.bot or not msg.guild or not bad(msg.content):
        return
    try:
        await msg.delete()
    except discord.HTTPException:
        return
    key = (msg.guild.id, msg.author.id)
    strikes[key] += 1
    if strikes[key] >= 3:
        strikes[key] = 0
        try:
            await msg.author.timeout(timedelta(minutes=1))
        except discord.HTTPException:
            pass

_dispatch = discord.Client.dispatch
def dispatch(self, event, *args, **kwargs):
    if event == "message":
        asyncio.create_task(handle(args[0]))
    elif event == "message_edit":
        asyncio.create_task(handle(args[1]))
    return _dispatch(self, event, *args, **kwargs)
discord.Client.dispatch = dispatch

runpy.run_path("main.py", run_name="__main__")