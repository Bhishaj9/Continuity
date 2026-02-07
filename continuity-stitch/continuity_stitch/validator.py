import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List

from .exceptions import CodecMismatchError, MissingFFmpegError, ResolutionMismatchError


@dataclass(frozen=True)
class VideoMetadata:
    codec: str
    width: int
    height: int


class VideoValidator:
    """Validate videos prior to stitching."""

    def __init__(self, ffprobe_path: str = "ffprobe") -> None:
        self.ffprobe_path = ffprobe_path

    def validate(self, paths: Iterable[str]) -> List[VideoMetadata]:
        metadata = [self._probe(path) for path in paths]
        if not metadata:
            return metadata
        codecs = {item.codec for item in metadata}
        if len(codecs) > 1:
            raise CodecMismatchError(f"Input codecs differ: {sorted(codecs)}")
        resolutions = {(item.width, item.height) for item in metadata}
        if len(resolutions) > 1:
            raise ResolutionMismatchError(
                f"Input resolutions differ: {sorted(resolutions)}"
            )
        return metadata

    def _probe(self, path: str) -> VideoMetadata:
        if not Path(path).exists():
            raise FileNotFoundError(f"Video not found: {path}")
        cmd = [
            self.ffprobe_path,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name,width,height",
            "-of",
            "json",
            path,
        ]
        try:
            result = subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as exc:
            raise MissingFFmpegError("ffprobe not available") from exc
        except subprocess.CalledProcessError as exc:
            raise MissingFFmpegError(
                f"Unable to probe video metadata: {exc.stderr.strip()}"
            ) from exc
        payload = json.loads(result.stdout)
        streams = payload.get("streams", [])
        if not streams:
            raise MissingFFmpegError("No video stream metadata available")
        stream = streams[0]
        return VideoMetadata(
            codec=stream.get("codec_name", "unknown"),
            width=int(stream.get("width", 0)),
            height=int(stream.get("height", 0)),
        )
