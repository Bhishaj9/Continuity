import os
import subprocess
import glob
from typing import List
from .exceptions import VideoStitcherError

class VideoStitcher:
    def _probe(self, filepath: str) -> dict:
        """Probe video file for metadata."""
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate",
            "-of", "csv=p=0",
            filepath
        ]
        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT).decode("utf-8").strip()
            width, height, fps_str = output.split(',')
            if '/' in fps_str:
                num, den = map(int, fps_str.split('/'))
                fps = num / den
            else:
                fps = float(fps_str)
            return {"width": int(width), "height": int(height), "fps": fps}
        except subprocess.CalledProcessError as e:
            raise VideoStitcherError(f"Failed to probe {filepath}: {e.output.decode()}")
        except Exception as e:
            raise VideoStitcherError(f"Error probing {filepath}: {str(e)}")

    def stitch(self, input_paths: List[str], output_path: str):
        """Stitch multiple video files into one."""
        if not input_paths:
            raise VideoStitcherError("No input files provided.")

        # Validate inputs
        for path in input_paths:
            if not os.path.exists(path):
                raise VideoStitcherError(f"File not found: {path}")

        try:
            # Determine target specs
            max_width = 0
            max_height = 0
            max_fps = 0.0

            for path in input_paths:
                meta = self._probe(path)
                max_width = max(max_width, meta['width'])
                max_height = max(max_height, meta['height'])
                max_fps = max(max_fps, meta['fps'])

            # Normalize to intermediate .ts files
            ts_files = []
            for i, path in enumerate(input_paths):
                ts_path = f"temp_{i}.ts"
                ts_files.append(ts_path)
                # Ensure output dimensions are divisible by 2 for libx264
                w = max_width if max_width % 2 == 0 else max_width - 1
                h = max_height if max_height % 2 == 0 else max_height - 1

                cmd = [
                    "ffmpeg", "-y",
                    "-i", path,
                    "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
                    "-r", str(max_fps),
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "128k",
                    "-f", "mpegts",
                    ts_path
                ]
                subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            # Concatenate
            concat_list = "concat.txt"
            with open(concat_list, "w") as f:
                for ts in ts_files:
                    f.write(f"file '{ts}'\n")

            cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", concat_list,
                "-c", "copy",
                "-bsf:a", "aac_adtstoasc",
                output_path
            ]
            subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        except subprocess.CalledProcessError as e:
             raise VideoStitcherError(f"FFmpeg error: {e}")
        finally:
            # Cleanup
            for ts in glob.glob("temp_*.ts"):
                try:
                    os.remove(ts)
                except OSError:
                    pass
            if os.path.exists("concat.txt"):
                try:
                    os.remove("concat.txt")
                except OSError:
                    pass
