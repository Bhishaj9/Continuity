# continuity-stitch

A lightweight Python library for stitching multiple video clips into a single output file.

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
)

stitcher.stitch()
```

## Validation

`VideoStitcher` validates that all clips share the same codec and resolution before stitching.
If you need to run validation separately, use `VideoValidator` directly:

```python
from continuity_stitch import VideoValidator

validator = VideoValidator()
validator.validate(["clip_a.mp4", "clip_b.mp4"])
```

## Requirements

- `ffmpeg` and `ffprobe` available on your PATH.

## License

MIT
