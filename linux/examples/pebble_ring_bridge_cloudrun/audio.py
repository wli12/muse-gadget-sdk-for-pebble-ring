"""Bounded, local-only conversion of Pebble M4A/WAV to Muse's WAV format."""
from __future__ import annotations

import io
import os
from pathlib import Path
import subprocess
import tempfile
import wave

MAX_AUDIO_BYTES = 8 * 1024 * 1024
SAMPLE_RATE = 16000
MAX_SECONDS = 60
MAX_PCM_BYTES = SAMPLE_RATE * 2 * MAX_SECONDS
DECODE_OUTPUT_LIMIT = MAX_PCM_BYTES + 65536
M4A_TYPES = frozenset({'audio/mp4', 'audio/m4a', 'audio/x-m4a'})
WAV_TYPES = frozenset({'audio/wav', 'audio/wave', 'audio/x-wav'})


class AudioError(ValueError):
    """A fixed diagnostic code, never decoder output or recording metadata."""


def normalize_audio(raw: bytes, content_type: str) -> bytes:
    """Return canonical PCM16LE, 16 kHz, mono WAV; reject audio over 60 s.

    ffmpeg reads a seekable owner-only file because M4A metadata may follow the
    audio. Only the selected mov/wav demuxer and local file/pipe protocols are
    admitted. Raw PCM output has a byte limit above 60 seconds; any result over
    60 seconds is rejected instead of sending a truncated instruction. This
    avoids making the duration decision from untrusted container timestamps.
    """
    if not isinstance(raw, bytes) or not raw:
        raise AudioError('audio_required')
    if len(raw) > MAX_AUDIO_BYTES:
        raise AudioError('audio_too_large')
    mime = content_type.split(';', 1)[0].strip().lower()
    if mime in M4A_TYPES and len(raw) >= 12 and raw[4:8] == b'ftyp':
        demuxer, suffix = 'mov', '.m4a'
    elif mime in WAV_TYPES and len(raw) >= 12 and raw[:4] == b'RIFF' and raw[8:12] == b'WAVE':
        demuxer, suffix = 'wav', '.wav'
    else:
        raise AudioError('unsupported_audio_format')

    with tempfile.TemporaryDirectory(prefix='index-audio-') as temporary:
        directory = Path(temporary)
        os.chmod(directory, 0o700)
        source = directory / ('recording' + suffix)
        fd = os.open(source, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
        argv = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin',
                '-threads', '1', '-protocol_whitelist', 'file,pipe', '-f', demuxer]
        if demuxer == 'mov':
            argv += ['-enable_drefs', '0', '-use_absolute_path', '0']
        argv += ['-i', str(source), '-map', '0:a:0', '-vn', '-sn', '-dn',
                 '-ac', '1', '-ar', str(SAMPLE_RATE), '-c:a', 'pcm_s16le',
                 '-threads', '1', '-filter_threads', '1',
                 '-fs', str(DECODE_OUTPUT_LIMIT), '-f', 's16le', 'pipe:1']
        try:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=15, check=False)
        except subprocess.TimeoutExpired:
            raise AudioError('audio_decode_timeout') from None
        except OSError:
            raise AudioError('audio_decoder_unavailable') from None
    if result.returncode != 0:
        raise AudioError('audio_decode_failed')
    pcm = result.stdout
    if not pcm or len(pcm) % 2:
        raise AudioError('invalid_decoded_audio')
    if len(pcm) > MAX_PCM_BYTES:
        raise AudioError('audio_too_long')
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return output.getvalue()
