from google import genai
try:
    print("Client methods:", dir(genai.Client))
except Exception as e:
    print(e)
