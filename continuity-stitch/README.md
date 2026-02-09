# continuity-stitch

A production-ready Python library for stitching multiple video clips into a single output file,
with automated 1080p/24fps normalization, FFmpeg-backed resilience, and clean management of
temporary working directories verified by Jules.

## Installation

```bash
pip install continuity-stitch
```

## Usage

```python
from continuity_stitch import VideoStitcher

stitcher = VideoStitcher(
    input_paths=["intro.mp4", "main.mp4", "outro.mp4"],
    output_path="stitched.mp4",
    work_dir="tmp/continuity_stitch",
)

stitcher.stitch()
```

## Features

- Normalizes every clip to 1080p/24fps with consistent H.264 output, so resolution or codec
  mismatches are handled automatically.
- Uses FFmpeg and FFprobe for validation, making the stitching workflow robust in production.
- Manages temporary working directories cleanly, whether you provide a `work_dir` or rely on
  isolated temp folders.

## Validation

`VideoStitcher` validates that all clips share the same codec and resolution before stitching.
If you need to run validation separately, use `VideoValidator` directly:

```python
from continuity_stitch import VideoValidator

validator = VideoValidator()
validator.validate(["clip_a.mp4", "clip_b.mp4"])
```

## Requirements

- `ffmpeg` and `ffprobe` must be installed and available on your system PATH.

## License

MIT. This standalone utility remains MIT-licensed even though the main SaaS platform is
proprietary.
