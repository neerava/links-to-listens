"""Shared pytest fixtures."""
import os

import pytest

# Run TTS synthesis in-process during tests so that mocks patched onto tts.*
# remain visible.  Without this, synthesize() would spawn a fresh subprocess
# that re-imports tts.py from scratch, bypassing all parent-process patches.
os.environ.setdefault("PODCAST_TTS_IN_PROCESS", "1")
