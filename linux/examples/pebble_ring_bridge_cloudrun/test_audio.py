"""Synthetic tests for the bounded media decoder; no credentials or network."""
import io
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
import wave

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import audio


def synthetic_wav(pcm=b'\x00\x01' * 16):
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(pcm)
    return stream.getvalue()


M4A_HEADER = b'\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00M4A isom'


def assert_canonical_wav(result, pcm):
    assert result[:4] == b'RIFF' and result[8:12] == b'WAVE'
    with wave.open(io.BytesIO(result), 'rb') as decoded:
        assert decoded.getnchannels() == 1
        assert decoded.getsampwidth() == 2
        assert decoded.getframerate() == 16000
        assert decoded.getcomptype() == 'NONE'
        assert decoded.readframes(decoded.getnframes()) == pcm
    assert len(result) <= 2 * 1024 * 1024


@pytest.mark.parametrize(('raw', 'mime', 'demuxer'), [
    (M4A_HEADER, 'audio/mp4', 'mov'),
    (M4A_HEADER, 'audio/m4a', 'mov'),
    (M4A_HEADER, 'Audio/X-M4A; codecs=mp4a.40.2', 'mov'),
    (synthetic_wav(), 'audio/wav', 'wav'),
    (synthetic_wav(), 'audio/x-wav', 'wav'),
    (synthetic_wav(), 'audio/wave', 'wav'),
])
def test_conversion_has_private_seekable_input_and_bounded_local_decoder(monkeypatch, raw, mime, demuxer):
    pcm = b'\x01\x02' * 160
    paths = []

    def decoder(argv, **kwargs):
        assert isinstance(argv, list) and argv[0] == 'ffmpeg'
        assert not kwargs.get('shell')
        assert kwargs['stdin'] == subprocess.DEVNULL
        assert kwargs['stdout'] == subprocess.PIPE
        assert kwargs['stderr'] == subprocess.DEVNULL
        assert kwargs['timeout'] == 15 and kwargs['check'] is False
        assert '-nostdin' in argv
        index = argv.index('-i')
        assert argv[:index].count('-f') == 1
        assert argv[argv.index('-f') + 1] == demuxer
        assert argv[argv.index('-protocol_whitelist') + 1] == 'file,pipe'
        if demuxer == 'mov':
            assert argv.index('-enable_drefs') < index
            assert argv[argv.index('-enable_drefs') + 1] == '0'
            assert argv[argv.index('-use_absolute_path') + 1] == '0'
        source = Path(argv[index + 1])
        paths.append(source)
        assert source.read_bytes() == raw
        assert stat.S_IMODE(source.stat().st_mode) == 0o600
        assert stat.S_IMODE(source.parent.stat().st_mode) == 0o700
        assert argv[argv.index('-map') + 1] == '0:a:0'
        assert argv[argv.index('-c:a') + 1] == 'pcm_s16le'
        assert argv[argv.index('-ac') + 1] == '1'
        assert argv[argv.index('-ar') + 1] == '16000'
        assert argv[-2:] == ['s16le', 'pipe:1']
        limit = int(argv[argv.index('-fs') + 1])
        assert audio.MAX_PCM_BYTES < limit <= audio.MAX_PCM_BYTES + 128 * 1024
        # A container timestamp cutoff could silently discard later speech.
        assert '-t' not in argv and '-to' not in argv
        return SimpleNamespace(returncode=0, stdout=pcm)

    monkeypatch.setattr(audio.subprocess, 'run', decoder)
    assert_canonical_wav(audio.normalize_audio(raw, mime), pcm)
    assert paths and all(not path.parent.exists() for path in paths)


@pytest.mark.parametrize(('raw', 'mime', 'code'), [
    (b'', 'audio/mp4', 'audio_required'),
    (None, 'audio/mp4', 'audio_required'),
    (bytearray(M4A_HEADER), 'audio/mp4', 'audio_required'),
    (b'not a recording', 'audio/mp4', 'unsupported_audio_format'),
    (M4A_HEADER, 'audio/wav', 'unsupported_audio_format'),
    (synthetic_wav(), 'audio/mp4', 'unsupported_audio_format'),
    (M4A_HEADER, 'text/plain', 'unsupported_audio_format'),
    (b'#EXTM3U\nhttps://example.invalid/audio', 'audio/mp4', 'unsupported_audio_format'),
])
def test_invalid_input_never_starts_decoder(monkeypatch, raw, mime, code):
    def unexpected(*args, **kwargs):
        pytest.fail('Decoder must not start for invalid input')
    monkeypatch.setattr(audio.subprocess, 'run', unexpected)
    with pytest.raises(audio.AudioError) as failure:
        audio.normalize_audio(raw, mime)
    assert str(failure.value) == code


def test_compressed_input_size_is_checked_before_decoding(monkeypatch):
    monkeypatch.setattr(audio.subprocess, 'run', lambda *a, **k: pytest.fail('Unexpected decoder'))
    raw = M4A_HEADER + b'\0' * (audio.MAX_AUDIO_BYTES - len(M4A_HEADER) + 1)
    with pytest.raises(audio.AudioError, match='^audio_too_large$'):
        audio.normalize_audio(raw, 'audio/mp4')


@pytest.mark.parametrize(('exception', 'code'), [
    (subprocess.TimeoutExpired(['synthetic-decoder'], 15, stderr=b'private synthetic metadata'), 'audio_decode_timeout'),
    (FileNotFoundError('synthetic missing decoder'), 'audio_decoder_unavailable'),
    (PermissionError('synthetic permission failure'), 'audio_decoder_unavailable'),
])
def test_decoder_failures_have_fixed_codes_and_remove_temporary_input(monkeypatch, exception, code):
    paths = []
    def failure(argv, **kwargs):
        paths.append(Path(argv[argv.index('-i') + 1]))
        raise exception
    monkeypatch.setattr(audio.subprocess, 'run', failure)
    with pytest.raises(audio.AudioError) as error:
        audio.normalize_audio(M4A_HEADER, 'audio/mp4')
    assert str(error.value) == code
    assert paths and not paths[0].parent.exists()


@pytest.mark.parametrize(('returncode', 'pcm', 'code'), [
    (1, b'private synthetic decoder output', 'audio_decode_failed'),
    (0, b'', 'invalid_decoded_audio'),
    (0, b'\x00', 'invalid_decoded_audio'),
    (0, b'\x00' * (audio.MAX_PCM_BYTES + 2), 'audio_too_long'),
])
def test_bad_or_overlong_decoded_audio_is_not_returned(monkeypatch, returncode, pcm, code):
    monkeypatch.setattr(audio.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=returncode, stdout=pcm))
    with pytest.raises(audio.AudioError) as error:
        audio.normalize_audio(M4A_HEADER, 'audio/mp4')
    assert str(error.value) == code


@pytest.mark.parametrize('size', [2, audio.MAX_PCM_BYTES - 2, audio.MAX_PCM_BYTES])
def test_decoded_sample_limit_and_wav_header_are_exact(monkeypatch, size):
    pcm = b'\0' * size
    monkeypatch.setattr(audio.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=0, stdout=pcm))
    assert_canonical_wav(audio.normalize_audio(M4A_HEADER, 'audio/mp4'), pcm)
