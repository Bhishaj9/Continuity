from google import genai
import os
try:
    client = genai.Client(api_key="TEST")
    print("models methods:", dir(client.models))
except Exception as e:
    print(e)
