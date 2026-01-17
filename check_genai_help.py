from google import genai
import os
try:
    client = genai.Client(api_key="TEST")
    print(help(client.models.generate_videos))
except Exception as e:
    print(e)
