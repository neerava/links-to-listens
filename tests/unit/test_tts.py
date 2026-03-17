"""Unit tests for tts.py."""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from config import Settings
from tts import TTSError, synthesize, _concat_wavs, _format_script, _wav_to_mp3


def _settings() -> Settings:
    s = Settings()
    s.tts_voice = "default"
    return s


def test_tts_error_when_vibevoice_not_installed(tmp_path):
    """If vibevoice cannot be imported, raise TTSError."""
    out = tmp_path / "out.mp3"
    with patch("tts._model", None), \
         patch("tts._processor", None), \
         patch.dict("sys.modules", {"vibevoice": None,
                                     "vibevoice.modular": None,
                                     "vibevoice.modular.modeling_vibevoice_inference": None,
                                     "vibevoice.processor": None,
                                     "vibevoice.processor.vibevoice_processor": None}):
        # Force ImportError on the vibevoice imports
        with patch("tts._ensure_model", side_effect=TTSError("VibeVoice is not installed")):
            with pytest.raises(TTSError, match="not installed"):
                synthesize("Hello world.", out, _settings())


def test_tts_error_when_output_missing(tmp_path):
    """If generation runs but no output file, raise TTSError."""
    out = tmp_path / "out.mp3"

    with patch("tts._generate_wav") as mock_gen, \
         patch("tts._wav_to_mp3") as mock_convert:
        # Neither creates the output file
        mock_gen.return_value = None
        mock_convert.return_value = None
        # Need a temp WAV that exists for the check
        with pytest.raises(TTSError):
            synthesize("Hello world.", out, _settings())


def test_tts_full_success(tmp_path):
    """If WAV generation and ffmpeg both succeed, return the MP3 path."""
    out = tmp_path / "out.mp3"

    def fake_generate_wav(script, wav_path, settings):
        wav_path.write_bytes(b"RIFF" + b"\x00" * 100)

    def fake_wav_to_mp3(wav_path, mp3_path):
        mp3_path.write_bytes(b"\xff\xfb\x90" + b"\x00" * 100)

    with patch("tts._generate_wav", side_effect=fake_generate_wav), \
         patch("tts._wav_to_mp3", side_effect=fake_wav_to_mp3):
        result = synthesize("Hello world.", out, _settings())
        assert result == out
        assert out.exists()


def test_tts_creates_parent_dirs(tmp_path):
    """synthesize() creates missing parent directories."""
    out = tmp_path / "nested" / "deep" / "out.mp3"

    def fake_generate_wav(script, wav_path, settings):
        wav_path.write_bytes(b"RIFF" + b"\x00" * 100)

    def fake_wav_to_mp3(wav_path, mp3_path):
        mp3_path.write_bytes(b"\xff\xfb" + b"\x00" * 100)

    with patch("tts._generate_wav", side_effect=fake_generate_wav), \
         patch("tts._wav_to_mp3", side_effect=fake_wav_to_mp3):
        result = synthesize("Hello", out, _settings())
        assert result.exists()


def test_tts_cleans_up_temp_wav(tmp_path):
    """Temp WAV file should be deleted after conversion."""
    out = tmp_path / "out.mp3"
    created_wavs = []

    def fake_generate_wav(script, wav_path, settings):
        wav_path.write_bytes(b"RIFF" + b"\x00" * 100)
        created_wavs.append(wav_path)

    def fake_wav_to_mp3(wav_path, mp3_path):
        mp3_path.write_bytes(b"\xff\xfb" + b"\x00" * 100)

    with patch("tts._generate_wav", side_effect=fake_generate_wav), \
         patch("tts._wav_to_mp3", side_effect=fake_wav_to_mp3):
        synthesize("Hello", out, _settings())

    assert len(created_wavs) == 1
    assert not created_wavs[0].exists()  # temp file was cleaned up


def test_tts_ffmpeg_missing_raises(tmp_path):
    """If ffmpeg is not on PATH, raise TTSError."""
    wav = tmp_path / "input.wav"
    wav.write_bytes(b"RIFF" + b"\x00" * 100)
    mp3 = tmp_path / "output.mp3"

    with patch("shutil.which", return_value=None):
        with pytest.raises(TTSError, match="ffmpeg"):
            _wav_to_mp3(wav, mp3)


def test_tts_concat_ffmpeg_missing_raises(tmp_path):
    """Chunked generation should fail cleanly when ffmpeg is missing."""
    wav1 = tmp_path / "part1.wav"
    wav2 = tmp_path / "part2.wav"
    wav1.write_bytes(b"RIFF" + b"\x00" * 100)
    wav2.write_bytes(b"RIFF" + b"\x00" * 100)
    out = tmp_path / "joined.wav"

    with patch("shutil.which", return_value=None):
        with pytest.raises(TTSError, match="ffmpeg"):
            _concat_wavs([wav1, wav2], out)


def test_synthesize_rejects_empty_script(tmp_path):
    """Blank scripts should be rejected before model inference starts."""
    out = tmp_path / "out.mp3"

    with pytest.raises(TTSError, match="empty"):
        synthesize("   ", out, _settings())


def test_flush_device_cache_skips_unavailable_mps():
    """Do not call MPS synchronization helpers when MPS is unavailable."""
    with patch("tts.torch.backends.mps.is_available", return_value=False), \
         patch("tts.torch.cuda.is_available", return_value=False), \
         patch("tts.torch.mps.synchronize", side_effect=AssertionError("should not be called")), \
         patch("tts.torch.mps.empty_cache", side_effect=AssertionError("should not be called")):
        from tts import _flush_device_cache
        _flush_device_cache()


def test_format_script():
    """Script should be wrapped with Speaker 0 label."""
    assert _format_script("Hello world.") == "Speaker 0: Hello world."


def test_format_script_flattens_embedded_newlines():
    """Embedded newlines should not leave raw lines for VibeVoice to parse."""
    script = 'First line.\n\nSo, you might\'ve noticed lately\nthat products say "AI-free".'
    assert _format_script(script) == (
        'Speaker 0: First line.\n'
        'Speaker 0: So, you might\'ve noticed lately that products say "AI-free".'
    )
