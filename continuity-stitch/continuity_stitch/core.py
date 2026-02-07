import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable, List, Optional

from .exceptions import MissingFFmpegError, StitchError
from .validator import VideoValidator


class VideoStitcher:
    """Stitch multiple video clips into a single output file."""

    def __init__(
        self,
        input_paths: Iterable[str],
        output_path: str,
        *,
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        work_dir: Optional[str] = None,
    ) -> None:
        self.input_paths = [str(path) for path in input_paths]
        self.output_path = str(output_path)
        self.ffmpeg_path = ffmpeg_path
        self.validator = VideoValidator(ffprobe_path=ffprobe_path)
        self.work_dir = Path(work_dir) if work_dir else None

    def stitch(self) -> str:
        if not self.input_paths:
            raise StitchError("No input videos provided")
        self._ensure_ffmpeg()
        self.validator.validate(self.input_paths)
        normalized_paths: List[str] = []
        list_file: Optional[str] = None
        try:
            normalized_paths = [
                self._normalize_video(path) for path in self.input_paths
            ]
            list_file = self._write_concat_list(normalized_paths)
            self._concat(list_file, self.output_path)
        finally:
            self._cleanup_files(normalized_paths, list_file)
        return self.output_path

    def _ensure_ffmpeg(self) -> None:
        if not shutil.which(self.ffmpeg_path):
            raise MissingFFmpegError("ffmpeg not available")

    def _normalize_video(self, input_path: str) -> str:
        output_path = self._temp_path(Path(input_path).stem + "_norm.mp4")
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-i",
            input_path,
            "-vf",
            "scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,"
            "format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "28",
            "-an",
            output_path,
        ]
        self._run(cmd)
        return output_path

    def _write_concat_list(self, normalized_paths: Iterable[str]) -> str:
        list_path = self._temp_path("concat_list.txt")
        with open(list_path, "w", encoding="utf-8") as handle:
            for path in normalized_paths:
                handle.write(f"file '{path}'\n")
        return list_path

    def _concat(self, list_file: str, output_path: str) -> None:
        output_dir = Path(output_path).parent
        if output_dir:
            output_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            list_file,
            "-c",
            "copy",
            output_path,
        ]
        self._run(cmd)

    def _run(self, cmd: List[str]) -> None:
        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as exc:
            raise StitchError(
                f"Command failed: {' '.join(cmd)}\n{exc.stderr.strip()}"
            ) from exc

    def _temp_path(self, filename: str) -> str:
        if self.work_dir:
            self.work_dir.mkdir(parents=True, exist_ok=True)
            return str(self.work_dir / filename)
        temp_dir = tempfile.mkdtemp(prefix="continuity_stitch_")
        return str(Path(temp_dir) / filename)

    @staticmethod
    def _cleanup_files(normalized_paths: Iterable[str], list_file: Optional[str]) -> None:
        for path in normalized_paths:
            if path and os.path.exists(path):
                os.remove(path)
                parent = Path(path).parent
                if parent.exists() and parent.name.startswith("continuity_stitch_"):
                    try:
                        parent.rmdir()
                    except OSError:
                        pass
        if list_file and os.path.exists(list_file):
            os.remove(list_file)
            parent = Path(list_file).parent
            if parent.exists() and parent.name.startswith("continuity_stitch_"):
                try:
                    parent.rmdir()
                except OSError:
                    pass
