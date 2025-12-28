import requests
import os
import sys

def test_api():
    url = "http://127.0.0.1:8000/generate-transition"
    
    video_a_path = 'tests/scene_a.mp4'
    video_c_path = 'tests/scene_c.mp4'
    
    if not os.path.exists(video_a_path) or not os.path.exists(video_c_path):
        print("Error: Test videos not found. Run create_assets.py first.")
        sys.exit(1)

    files = {
        'video_a': ('scene_a.mp4', open(video_a_path, 'rb'), 'video/mp4'),
        'video_c': ('scene_c.mp4', open(video_c_path, 'rb'), 'video/mp4')
    }
    data = {'prompt': 'Test transition'}

    print(f"Sending POST request to {url}...")
    try:
        response = requests.post(url, files=files, data=data)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print(f"Success! Response: {response.json()}")
        else:
            print(f"Failed. Response: {response.text}")
    except Exception as e:
        print(f"Connection Failed: {e}")
        print("Make sure the server is running on port 8000.")

if __name__ == "__main__":
    test_api()
