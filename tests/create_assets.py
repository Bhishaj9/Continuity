import cv2
import numpy as np
import os

def make_video(filename, color):
    height, width = 480, 640
    # Use 'mp4v' for .mp4 containers
    fourcc = cv2.VideoWriter_fourcc(*'mp4v')
    out = cv2.VideoWriter(filename, fourcc, 30.0, (width, height))
    
    # Create a frame with the solid color
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = color # BGR format
    
    # Write 30 frames (1 second)
    for _ in range(30):
        out.write(frame)
    out.release()
    print(f"Created {filename}")

if __name__ == "__main__":
    make_video('tests/scene_a.mp4', (255, 0, 0)) # Blue
    make_video('tests/scene_c.mp4', (0, 0, 255)) # Red
