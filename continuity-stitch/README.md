# continuity-stitch

## Project Name & Goal

**continuity-stitch** is a lightweight Python utility for frame-accurate video normalization
(1080p/24fps) using FFmpeg. It powers the video normalization engine for Project Continuity
with a focus on **zero configuration** and **reliability**.

## Installation

```bash
pip install continuity-stitch
```

## Key Technical Features

- **Normalization**: Automatically scales and converts videos to 1080p at 24fps for consistent
  AI generation.
- **Resource Safety**: Managed temporary directories with automatic cleanup of intermediate
  segments.
- **Concurrency Ready**: Designed for use in background workers (Celery/Redis) with high
  reliability.

## Quick Start Example

```python
from continuity_stitch import VideoStitcher

stitcher = VideoStitcher(
    input_paths=["intro.mp4", "main.mp4"],
    output_path="normalized.mp4",
)

normalized_path = stitcher.stitch()
print(normalized_path)
```

## Dependencies

- **FFmpeg** must be installed and available on your system PATH.

## License

MIT
