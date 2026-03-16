"""TTS engine wrapper: converts a text script to an MP3 file via VibeVoice.

VibeVoice is a HuggingFace-style TTS model.  This module handles model
loading (once, then cached), inference, WAV rendering, and ffmpeg conversion
to MP3.
"""
from __future__ import annotations

import os
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import torch

from config import Settings, load_settings

logger = logging.getLogger(__name__)

MODEL_ID = "microsoft/VibeVoice-1.5b"

# Module-level singletons — loaded once per process.
_model = None
_processor = None


class TTSError(Exception):
    """Raised when TTS synthesis fails."""


# ---------------------------------------------------------------------------
# Model loading
# ---------------------------------------------------------------------------

def _get_device_and_dtype() -> tuple[str, torch.dtype, str]:
    """Pick the best available device."""
    if torch.cuda.is_available():
        return "cuda", torch.bfloat16, "flash_attention_2"
    if torch.backends.mps.is_available():
        return "mps", torch.float16, "sdpa"
    return "cpu", torch.float32, "sdpa"


def _ensure_model():
    """Lazy-load the VibeVoice model + processor once."""
    global _model, _processor

    if _model is not None and _processor is not None:
        return _model, _processor

    try:
        from vibevoice.modular.modeling_vibevoice_inference import (
            VibeVoiceForConditionalGenerationInference,
        )
        from vibevoice.processor.vibevoice_processor import VibeVoiceProcessor
    except ImportError as exc:
        raise TTSError(
            "VibeVoice is not installed.  Install with: pip install vibevoice"
        ) from exc

    device, dtype, attn_impl = _get_device_and_dtype()
    logger.info("Loading VibeVoice model %s on %s (%s)…", MODEL_ID, device, dtype)

    t0 = time.monotonic()

    # VibeVoice loads its tokenizer as a subclass of Qwen2Tokenizer. HuggingFace
    # logs a benign "tokenizer class mismatch" warning because the Qwen checkpoint's
    # tokenizer_config.json names "Qwen2Tokenizer" but VibeVoice loads it through
    # "VibeVoiceTextTokenizerFast". The subclass is behaviorally identical; this is
    # a missing registration in VibeVoice's tokenizer_config.json (upstream bug).
    import transformers as _transformers
    _prev_verbosity = _transformers.logging.get_verbosity()
    _transformers.logging.set_verbosity_error()
    _processor = VibeVoiceProcessor.from_pretrained(MODEL_ID)
    _transformers.logging.set_verbosity(_prev_verbosity)

    load_kwargs: dict = dict(
        torch_dtype=dtype,
        attn_implementation=attn_impl,
    )
    if device != "mps":
        load_kwargs["device_map"] = device

    _model = VibeVoiceForConditionalGenerationInference.from_pretrained(
        MODEL_ID, **load_kwargs
    )
    if device == "mps":
        _model = _model.to("mps")

    _model.eval()
    _model.set_ddpm_inference_steps(num_steps=10)
    logger.info("VibeVoice model loaded in %.1fs", time.monotonic() - t0)
    return _model, _processor


def _get_voice_sample(settings: Settings) -> str:
    """Return path to the voice sample WAV used for voice cloning.

    If ``settings.tts_voice_sample`` points to a real file, use it.
    Otherwise fall back to a generated 3-second silent WAV (lower quality).
    """
    if settings.tts_voice_sample:
        voice_path = Path(settings.tts_voice_sample).resolve()
        if voice_path.exists():
            logger.info("Using voice sample: %s", voice_path)
            return str(voice_path)
        logger.warning("Voice sample not found at %s — falling back to silence", voice_path)

    return _get_silent_fallback()


def _get_silent_fallback() -> str:
    """Generate a 3-second silent 24 kHz mono WAV as a last-resort voice reference."""
    import numpy as np

    voice_dir = Path(tempfile.gettempdir()) / "url-to-podcast-voices"
    voice_dir.mkdir(exist_ok=True)
    voice_path = voice_dir / "default_voice.wav"

    if voice_path.exists():
        return str(voice_path)

    import soundfile as sf

    sr = 24_000
    duration_sec = 3
    samples = np.zeros(sr * duration_sec, dtype=np.float32)
    sf.write(str(voice_path), samples, samplerate=sr)
    logger.warning(
        "No voice sample configured — using silent fallback at %s. "
        "Set tts_voice_sample in config.yaml for better quality.",
        voice_path,
    )
    return str(voice_path)


# ---------------------------------------------------------------------------
# Audio generation
# ---------------------------------------------------------------------------

def _format_script(script: str) -> str:
    """Format a plain-text script for VibeVoice.

    VibeVoice's processor expects each line to start with ``Speaker N:``.
    We split the script into sentences and label every sentence as Speaker 0.
    """
    import re

    # Split on sentence-ending punctuation, keeping the delimiter attached.
    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    lines = [f"Speaker 0: {s.strip()}" for s in sentences if s.strip()]
    return "\n".join(lines)


def _flush_device_cache() -> None:
    """Synchronize the accelerator and release cached memory.

    Synchronization ensures all async device operations from the previous chunk
    complete before the next chunk starts, preventing state bleed-through.
    """
    import gc
    gc.collect()
    if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
        torch.mps.synchronize()
        torch.mps.empty_cache()
    elif torch.cuda.is_available():
        torch.cuda.synchronize()
        torch.cuda.empty_cache()


def _generate_chunk_wav(
    text: str,
    wav_path: Path,
    model,
    processor,
    device,
    voice_sample: str,
) -> None:
    """Run VibeVoice inference on a single text chunk and save as WAV.

    All tensors are deleted and device cache flushed before returning so
    that memory is reclaimed before the next chunk is processed.
    """
    inputs = processor(
        text=[text],
        voice_samples=[[voice_sample]],
        padding=True,
        return_tensors="pt",
        return_attention_mask=True,
    )
    inputs = {
        k: v.to(device) if torch.is_tensor(v) else v
        for k, v in inputs.items()
        if v is not None
    }

    with torch.no_grad():
        outputs = model.generate(
            **inputs,
            cfg_scale=1.3,
            tokenizer=processor.tokenizer,
            generation_config={"do_sample": False},
            return_speech=True,
            verbose=False,
            show_progress_bar=False,
        )

    if not outputs.speech_outputs:
        raise TTSError("VibeVoice generated no audio output for chunk")

    audio_tensor = outputs.speech_outputs[0]

    if hasattr(processor, "save_audio"):
        processor.save_audio(
            audio_tensor,
            output_path=str(wav_path),
            sampling_rate=24_000,
            normalize=False,
        )
    else:
        import soundfile as sf

        sf.write(str(wav_path), audio_tensor.cpu().float().numpy(), samplerate=24_000)

    del inputs, outputs, audio_tensor
    _flush_device_cache()


def _concat_wavs(chunk_paths: list[Path], output_path: Path) -> None:
    """Concatenate WAV files into *output_path* using ffmpeg concat demuxer.

    Uses stream copy (no re-decode), so memory overhead is minimal.
    """
    if len(chunk_paths) == 1:
        shutil.copy2(chunk_paths[0], output_path)
        return

    list_file = output_path.with_suffix(".concat_list.txt")
    try:
        list_file.write_text("\n".join(f"file '{p}'" for p in chunk_paths))
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0",
                "-i", str(list_file),
                "-c", "copy",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise TTSError(f"WAV concat failed: {result.stderr[:300]}")
    finally:
        list_file.unlink(missing_ok=True)


def _generate_wav(script: str, wav_path: Path, settings: Settings) -> None:
    """Run VibeVoice inference in sentence-level chunks to bound peak memory.

    Each chunk is synthesised independently and saved to a temporary WAV file.
    After all chunks are produced they are concatenated into *wav_path* via
    ffmpeg and the temporary files are removed.
    """
    model, processor = _ensure_model()
    device = next(model.parameters()).device
    voice_sample = _get_voice_sample(settings)

    lines = _format_script(script).split("\n")
    chunk_size = settings.tts_chunk_sentences
    chunks = [lines[i : i + chunk_size] for i in range(0, len(lines), chunk_size)]
    logger.info("TTS: %d sentence(s) split into %d chunk(s)", len(lines), len(chunks))

    tmp_dir = wav_path.parent / (wav_path.stem + "_chunks")
    tmp_dir.mkdir(exist_ok=True)
    chunk_paths: list[Path] = []
    try:
        for idx, chunk_lines in enumerate(chunks):
            chunk_text = "\n".join(chunk_lines)
            chunk_wav = tmp_dir / f"chunk_{idx:04d}.wav"
            logger.info("TTS chunk %d/%d (%d sentences)…", idx + 1, len(chunks), len(chunk_lines))
            _generate_chunk_wav(chunk_text, chunk_wav, model, processor, device, voice_sample)
            chunk_paths.append(chunk_wav)

        _concat_wavs(chunk_paths, wav_path)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    """Convert a WAV file to MP3 using ffmpeg."""
    if not shutil.which("ffmpeg"):
        raise TTSError(
            "ffmpeg is required for WAV→MP3 conversion but was not found on PATH. "
            "Install it with: brew install ffmpeg  (macOS) or apt install ffmpeg  (Linux)"
        )

    result = subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(wav_path),
            "-codec:a", "libmp3lame",
            "-b:a", "192k",
            str(mp3_path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise TTSError(f"ffmpeg conversion failed: {result.stderr[:300]}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def synthesize(script: str, output_path: Path, settings: Settings | None = None) -> Path:
    """Convert *script* to an MP3 at *output_path* using VibeVoice.

    Returns the resolved output path.

    Raises:
        TTSError: if synthesis, conversion, or any dependency check fails.
    """
    if settings is None:
        settings = load_settings()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Synthesizing audio → %s", output_path)
    t0 = time.monotonic()

    # 1. Generate WAV via VibeVoice
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = Path(tmp.name)

    try:
        _generate_wav(script, wav_path, settings)

        if not wav_path.exists() or wav_path.stat().st_size == 0:
            raise TTSError("VibeVoice produced an empty or missing WAV file")

        # 2. Convert WAV → MP3
        _wav_to_mp3(wav_path, output_path)
    finally:
        wav_path.unlink(missing_ok=True)

    elapsed = time.monotonic() - t0

    if not output_path.exists():
        raise TTSError(f"TTS completed but output file not found: {output_path}")

    size_kb = output_path.stat().st_size / 1024
    logger.info("Audio saved to %s (%.1f KB) in %.1fs", output_path, size_kb, elapsed)
    return output_path
