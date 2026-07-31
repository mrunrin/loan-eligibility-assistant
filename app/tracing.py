import os

from dotenv import load_dotenv
from langfuse import get_client

load_dotenv()

langfuse = get_client()

