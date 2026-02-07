class StitchError(Exception):
    """Base error for continuity-stitch failures."""


class MissingFFmpegError(StitchError):
    """Raised when ffmpeg/ffprobe binaries are unavailable."""


class ValidationError(StitchError):
    """Raised when input validation fails."""


class ResolutionMismatchError(ValidationError):
    """Raised when input videos have different resolutions."""


class CodecMismatchError(ValidationError):
    """Raised when input videos have different codecs."""
