import os
import aiohttp
from os import getenv
from dotenv import load_dotenv
    
load_dotenv()
que = {}
admins = {}

API_ID = int(getenv("API_ID", ""))
API_HASH = getenv("API_HASH", "")
BOT_TOKEN = getenv("BOT_TOKEN", "")
SESSION_NAME = getenv("SESSION_NAME", "")
OWNER_ID = getenv("OWNER_ID", "")
aiohttpsession = aiohttp.ClientSession()
