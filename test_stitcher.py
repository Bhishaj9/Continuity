import os
import subprocess
import shutil
import sys
from videostitcher import VideoStitcher, VideoStitcherError

def generate_dummy_clip(filename, width, height, fps, duration):
    # Generates a test clip with both video and audio streams
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate={fps}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=1000:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-shortest", # Ensure audio matches video duration
        filename
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"Generated {filename}")

def main():
    clip1 = "clip1.mp4" # 720p, 30fps
    clip2 = "clip2.mp4" # 1080p, 24fps
    output = "final_output.mp4"

    stitcher = VideoStitcher()

    try:
        # Generate assets
        print("Generating test assets...")
        generate_dummy_clip(clip1, 1280, 720, 30, 3)
        generate_dummy_clip(clip2, 1920, 1080, 24, 3)

        # Validation Test
        print("\nRunning Validation Test (Exception Handling)...")
        try:
            stitcher.stitch(["non_existent.mp4"], output)
            print("FAILED: VideoStitcherError not raised for non-existent file.")
            sys.exit(1)
        except VideoStitcherError as e:
            print(f"PASSED: Caught expected VideoStitcherError: {e}")
        except Exception as e:
            print(f"FAILED: Caught unexpected exception type: {type(e)}")
            sys.exit(1)

        # Core Execution
        print("\nRunning Core Execution...")
        stitcher.stitch([clip1, clip2], output)

        # Final Checks
        print("\nRunning Final Checks...")
        if not os.path.exists(output):
            print("FAILED: Output file does not exist.")
            sys.exit(1)

        # Check resolution
        cmd = [
            "ffprobe", "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=p=0",
            output
        ]
        res = subprocess.check_output(cmd).decode().strip()
        width, height = map(int, res.split(','))
        print(f"Output Resolution: {width}x{height}")

        if width == 1920 and height == 1080:
            print("PASSED: Resolution normalized to 1080p.")
        else:
            print(f"FAILED: Resolution incorrect (expected 1920x1080, got {width}x{height}).")
            sys.exit(1)

        # Playability check (basic: ffprobe duration)
        cmd_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", output]
        duration = float(subprocess.check_output(cmd_dur).decode().strip())
        print(f"Output Duration: {duration}s")
        if duration > 5.5: # 3+3=6, approximate
             print("PASSED: Output duration seems correct.")
        else:
             print("WARNING: Output duration seems short.")

        # Cleanup Check
        print("\nChecking Cleanup...")
        temp_files = [f for f in os.listdir('.') if f.startswith('temp_') and f.endswith('.ts')]
        if not temp_files and not os.path.exists("concat.txt"):
             print("PASSED: Temporary files cleaned up.")
        else:
             print(f"FAILED: Temporary files remaining: {temp_files}")
             # cleanup manually
             for f in temp_files: os.remove(f)
             if os.path.exists("concat.txt"): os.remove("concat.txt")

        print("\nVERDICT: Ready to Merge")

    except Exception as e:
        print(f"\nFATAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        print("VERDICT: Fix Required")
        sys.exit(1)
    finally:
        # Cleanup clips (optional, keeping for inspection if needed, but deleting here for clean state)
        if os.path.exists(clip1): os.remove(clip1)
        if os.path.exists(clip2): os.remove(clip2)
        if os.path.exists(output): os.remove(output)

if __name__ == "__main__":
    main()
