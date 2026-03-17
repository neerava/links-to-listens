"""Configuration loader and validator."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config.yaml"

# Approximate chars per token (used for content truncation)
CHARS_PER_TOKEN = 4


class ConfigError(ValueError):
    """Raised when configuration is invalid or missing required resources."""


@dataclass
class Settings:
    ollama_model: str = "llama3"
    ollama_url: str = "http://localhost:11434"
    ollama_prompt: str = (
        "You are a podcast host. Convert the following article into a natural, conversational "
        "audio script suitable for text-to-speech. "
        "Rules: "
        "- Write as if speaking directly to a listener. Use short paragraphs (2–4 sentences). "
        "- Preserve the article’s main points, key facts, and any important quotes. "
        "- No bullet points, headers, or markdown. No generic intros (“Welcome to the show”) or "
        "sign-offs. "
        "- Begin with the content immediately, do not include response like \"Here's the script\". "
        "Output only the script text, nothing else."
    )
    tts_engine: str = "vibevoice"
    tts_voice: str = "default"
    tts_voice_sample: str = ""  # path to a reference WAV for voice cloning (24kHz mono)
    tts_ddpm_steps: int = 15  # diffusion steps; more = higher fidelity, slower (default 15; max ~20)
    tts_cfg_scale: float = 1.3  # classifier-free guidance; 1.2–1.5 typical; higher = stronger voice match
    tts_mp3_bitrate: int = 192  # MP3 bitrate in kbps; 256 or 320 for higher fidelity
    tts_use_float32: bool = False  # if True, use float32 on MPS/CUDA (better quality, ~2x memory)
    scrape_timeout_sec: int = 15
    output_dir: str = "./output"
    web_port: int = 8080
    script_api_port: int = 8081
    audio_api_port: int = 8082
    poll_interval_sec: int = 5
    max_input_tokens: int = 4096
    tts_chunk_sentences: int = 10  # sentences per TTS inference call; lower = less memory
    intermediate_retention_days: int = 3  # days to keep script.txt / tts_input.txt before auto-delete
    # Derived at validation time
    output_path: Path = field(default=None, init=False)  # type: ignore[assignment]
    pipeline_path: Path = field(default=None, init=False)  # type: ignore[assignment]

    @property
    def max_input_chars(self) -> int:
        return self.max_input_tokens * CHARS_PER_TOKEN


def _load_yaml(path: Path) -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        logger.warning("config.yaml not found at %s — using defaults", path)
        return {}


def _apply_env_overrides(data: dict) -> dict:
    """Allow PODCAST_<KEY> env vars to override config.yaml values."""
    prefix = "PODCAST_"
    for key in list(data.keys()):
        env_key = prefix + key.upper()
        if env_key in os.environ:
            value = os.environ[env_key]
            # Preserve int types
            if isinstance(data[key], int):
                value = int(value)
            data[key] = value
    # Also pick up env vars for keys not yet in data
    for env_key, value in os.environ.items():
        if env_key.startswith(prefix):
            key = env_key[len(prefix):].lower()
            if key not in data:
                data[key] = value
    return data


def _validate(settings: Settings) -> None:
    """Validate settings and fail fast with a clear message."""
    # Validate Ollama URL
    parsed = urlparse(settings.ollama_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(
            f"ollama_url must be a valid HTTP/HTTPS URL, got: {settings.ollama_url!r}"
        )

    # Validate ports
    for port_name in ("web_port", "script_api_port", "audio_api_port"):
        port_val = getattr(settings, port_name)
        if not (1024 <= port_val <= 65535):
            raise ConfigError(
                f"{port_name} must be between 1024 and 65535, got: {port_val}"
            )

    # Validate poll interval
    if settings.poll_interval_sec <= 0:
        raise ConfigError(
            f"poll_interval_sec must be > 0, got: {settings.poll_interval_sec}"
        )

    if settings.tts_chunk_sentences <= 0:
        raise ConfigError(
            f"tts_chunk_sentences must be > 0, got: {settings.tts_chunk_sentences}"
        )

    # Coerce numeric TTS settings (e.g. from env overrides)
    if isinstance(settings.tts_ddpm_steps, str):
        settings.tts_ddpm_steps = int(settings.tts_ddpm_steps)
    if isinstance(settings.tts_cfg_scale, str):
        settings.tts_cfg_scale = float(settings.tts_cfg_scale)
    if isinstance(settings.tts_mp3_bitrate, str):
        settings.tts_mp3_bitrate = int(settings.tts_mp3_bitrate)

    if not (1 <= settings.tts_ddpm_steps <= 50):
        raise ConfigError(
            f"tts_ddpm_steps must be between 1 and 50, got: {settings.tts_ddpm_steps}"
        )
    if not (1.0 <= settings.tts_cfg_scale <= 2.0):
        raise ConfigError(
            f"tts_cfg_scale must be between 1.0 and 2.0, got: {settings.tts_cfg_scale}"
        )
    if settings.tts_mp3_bitrate not in (128, 192, 256, 320):
        raise ConfigError(
            f"tts_mp3_bitrate must be 128, 192, 256, or 320, got: {settings.tts_mp3_bitrate}"
        )

    # Validate voice sample path (if provided)
    if settings.tts_voice_sample:
        vsp = Path(settings.tts_voice_sample).resolve()
        if not vsp.exists():
            raise ConfigError(
                f"tts_voice_sample file not found: {vsp}"
            )
        if vsp.suffix.lower() not in (".wav", ".wave"):
            raise ConfigError(
                f"tts_voice_sample must be a .wav file, got: {vsp.suffix}"
            )

    # Ensure output_dir exists and is writable
    output_path = Path(settings.output_dir).resolve()
    try:
        output_path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(
            f"Cannot create output_dir {output_path}: {exc}"
        ) from exc

    test_file = output_path / ".write_test"
    try:
        test_file.touch()
        test_file.unlink()
    except OSError as exc:
        raise ConfigError(
            f"output_dir {output_path} is not writable: {exc}"
        ) from exc

    settings.output_path = output_path

    # Validate intermediate_retention_days
    if isinstance(settings.intermediate_retention_days, str):
        settings.intermediate_retention_days = int(settings.intermediate_retention_days)
    if settings.intermediate_retention_days < 1:
        raise ConfigError(
            f"intermediate_retention_days must be >= 1, got: {settings.intermediate_retention_days}"
        )

    # Derive pipeline_path (output/pipeline/) — created on demand, not checked for writability
    settings.pipeline_path = output_path / "pipeline"


def load_settings(path: Path = CONFIG_PATH) -> Settings:
    """Load, merge env overrides, validate, and return Settings."""
    data = _load_yaml(path)
    data = _apply_env_overrides(data)

    settings = Settings()
    for key, value in data.items():
        if hasattr(settings, key):
            setattr(settings, key, value)
        else:
            logger.debug("Unknown config key ignored: %s", key)

    _validate(settings)
    logger.debug("Settings loaded: %s", settings)
    return settings


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


_configure_logging()
