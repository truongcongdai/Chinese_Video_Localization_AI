#!/usr/bin/env python3
"""Isolated low-VRAM Stable Video Diffusion worker.

Keeping CUDA video inference outside the web process means a native driver or
PyTorch crash only fails one scene; the creator job can safely fall back to an
animated AI keyframe.
"""
from __future__ import annotations

import argparse
import gc
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--aspect-ratio", choices=("9:16", "16:9"), required=True)
    parser.add_argument("--duration", type=float, required=True)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    import torch
    from PIL import Image
    from diffusers import StableVideoDiffusionPipeline

    if not torch.cuda.is_available():
        raise RuntimeError("SVD worker requires an NVIDIA CUDA GPU")

    model_id = os.environ.get(
        "SVD_VIDEO_MODEL", "stabilityai/stable-video-diffusion-img2vid",
    ).strip()
    quality = os.environ.get("SVD_VIDEO_QUALITY", "high").lower()
    if quality not in ("balanced", "high"):
        raise RuntimeError("SVD_VIDEO_QUALITY must be balanced or high")
    if quality == "high":
        width, height = ((1024, 576) if args.aspect_ratio == "16:9" else (576, 1024))
    else:
        width, height = ((768, 432) if args.aspect_ratio == "16:9" else (432, 768))
    # The base SVD checkpoint is trained for 14 frames. Generate the full
    # native sequence, then use motion-compensated interpolation to span the
    # complete scene at 30 FPS instead of looping a one-second clip.
    frames = max(6, min(14, int(os.environ.get("SVD_VIDEO_FRAMES", "14"))))
    steps = max(4, min(30, int(os.environ.get("SVD_VIDEO_STEPS", "18"))))
    preview_fps = max(4, min(12, int(os.environ.get("SVD_VIDEO_FPS", "7"))))
    motion = max(1, min(255, int(os.environ.get("SVD_MOTION_BUCKET_ID", "80"))))

    gpu_name = torch.cuda.get_device_name(0)
    requested_precision = os.environ.get("SVD_VIDEO_PRECISION", "auto").lower()
    if requested_precision not in ("auto", "fp16", "fp32"):
        raise RuntimeError("SVD_VIDEO_PRECISION must be auto, fp16 or fp32")
    fp16_safe = not ("GTX 16" in gpu_name.upper())
    use_fp16 = requested_precision == "fp16" or (
        requested_precision == "auto" and fp16_safe
    )
    dtype = torch.float16 if use_fp16 else torch.float32
    pipe = StableVideoDiffusionPipeline.from_pretrained(
        model_id, torch_dtype=dtype, variant="fp16",
    )
    # GTX 16-series overflows in FP16 in both the temporal VAE and UNet.
    # Full FP32 plus sequential offload is slower, but only one layer resides
    # on the 6 GB GPU at a time and the web process remains isolated.
    pipe.unet.enable_forward_chunking()
    pipe.enable_sequential_cpu_offload()
    image = Image.open(args.input).convert("RGB").resize(
        (width, height), Image.Resampling.LANCZOS,
    )
    generator = torch.Generator(device="cpu").manual_seed(args.seed)
    generated = pipe(
        image,
        height=height,
        width=width,
        num_frames=frames,
        num_inference_steps=steps,
        fps=preview_fps,
        decode_chunk_size=1,
        motion_bucket_id=motion,
        # Lower augmentation preserves keyframe identity and fine detail.
        noise_aug_strength=0.02,
        generator=generator,
    ).frames[0]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    frame_dir = Path(tempfile.mkdtemp(prefix="svd_frames_", dir=str(args.output.parent)))
    try:
        for index, frame in enumerate(generated):
            frame.save(frame_dir / f"frame_{index:04d}.png")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise RuntimeError("Không tìm thấy FFmpeg trong PATH")
        duration = max(1.0, args.duration)
        source_fps = frames / duration
        encoded = subprocess.run(
            [
                ffmpeg, "-y", "-framerate", f"{source_fps:.6f}",
                "-i", str(frame_dir / "frame_%04d.png"),
                "-vf", (
                    f"tpad=stop_mode=clone:stop_duration={min(2.0, duration):.3f},"
                    "minterpolate=fps=30:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1,"
                    "unsharp=5:5:0.65:3:3:0.25"
                ),
                "-t", f"{duration:.3f}",
                "-c:v", "libx264", "-crf", "18", "-preset", "slow",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                str(args.output),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180,
        )
        if encoded.returncode != 0:
            raise RuntimeError((encoded.stderr or encoded.stdout)[-3000:])
    finally:
        shutil.rmtree(frame_dir, ignore_errors=True)
    if not args.output.exists() or args.output.stat().st_size < 4096:
        raise RuntimeError("SVD produced an invalid output file")
    print(
        f"SVD_OK output={args.output} bytes={args.output.stat().st_size} "
        f"ai_frames={frames} output_fps=30 duration={duration:.3f} "
        f"resolution={width}x{height} quality={quality} preview_fps={preview_fps}"
    )

    del generated, pipe
    gc.collect()
    torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
