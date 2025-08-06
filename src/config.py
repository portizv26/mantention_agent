from pathlib import Path
import sqlite3
from dotenv import load_dotenv
from openai import OpenAI

assistant_id = 'asst_feuOGXexFv0jk8EVDs4kR50E'

OPENAI_MODEL_CHAT = "gpt-4o-mini"
OPENAI_MODEL_STRUCT = "o3-mini"
MAX_SQL_RETRIES = 3
HEAD_ROWS = 5
CHAT_DOCS_DIR = Path("chat_docs")

load_dotenv()

client = OpenAI()
