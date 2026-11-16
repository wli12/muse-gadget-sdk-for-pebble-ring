"""Real ffmpeg smoke checks using only self-generated tones and silence.

Run inside the image during its build: python codec_smoke_test.py.
No network, credentials, microphone, recordings, or Muse APIs are used.
"""
from __future__ import annotations

import io
import math
from pathlib import Path
import struct
import subprocess
import tempfile
import wave

from audio import AudioError, normalize_audio


def make_wav(seconds, sample_rate=16000, channels=1, silence=False):
    output = io.BytesIO()
    pcm = bytearray()
    for index in range(round(seconds * sample_rate)):
        value = 0 if silence else round(6000 * math.sin(2 * math.pi * 440 * index / sample_rate))
        pcm.extend(struct.pack('<h', value) * channels)
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return output.getvalue()


def inspect_wav(raw, minimum_frames=1, maximum_frames=960000):
    with wave.open(io.BytesIO(raw), 'rb') as wav:
        assert wav.getnchannels() == 1, 'Expected mono output'
        assert wav.getsampwidth() == 2, 'Expected PCM16 output'
        assert wav.getframerate() == 16000, 'Expected 16kHz output'
        assert wav.getcomptype() == 'NONE', 'Expected uncompressed output'
        frames = wav.getnframes()
        assert minimum_frames <= frames <= maximum_frames, 'Decoded duration was truncated or overlong'
        assert len(wav.readframes(frames)) == frames * 2, 'WAV header does not match payload'
    assert len(raw) <= 2 * 1024 * 1024, 'WAV exceeds SDK upload cap'


def encode_m4a(source, target, audio_filter=None):
    argv = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y', '-i', str(source)]
    if audio_filter:
        argv += ['-af', audio_filter]
    argv += ['-c:a', 'aac', '-b:a', '32000', '-f', 'ipod', str(target)]
    subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, check=True, timeout=15)
    raw = target.read_bytes()
    assert raw[4:8] == b'ftyp', 'Fixture is not M4A'
    return raw


def expect_too_long(raw, mime):
    try:
        normalize_audio(raw, mime)
    except AudioError as error:
        assert str(error) == 'audio_too_long', 'Expected explicit overlength refusal'
    else:
        raise AssertionError('Overlong recording was accepted or silently truncated')


def main():
    short = make_wav(0.25, sample_rate=22050, channels=2)
    inspect_wav(normalize_audio(short, 'audio/wav'), 3900, 4100)
    with tempfile.TemporaryDirectory(prefix='synthetic-codec-') as temporary:
        directory = Path(temporary)
        source = directory / 'tone.wav'
        source.write_bytes(short)
        m4a = encode_m4a(source, directory / 'tone.m4a')
        # Match the seekable, metadata-at-end form emitted by common M4A encoders.
        assert m4a.find(b'moov') > m4a.find(b'mdat') > 0, 'Expected end-of-file M4A metadata'
        inspect_wav(normalize_audio(m4a, 'audio/mp4'), 3000, 5500)

        long_wav = make_wav(61.25, silence=True)
        expect_too_long(long_wav, 'audio/wav')
        source.write_bytes(long_wav)
        long_m4a = encode_m4a(source, directory / 'long.m4a')
        expect_too_long(long_m4a, 'audio/mp4')

        # A long timestamp gap must not cause a timestamp cutoff to discard the
        # second half of this one-second tone and accept the truncated command.
        source.write_bytes(make_wav(1.0))
        shifted = encode_m4a(
            source, directory / 'timestamp-gap.m4a',
            'asetpts=PTS+if(gte(N\\,8000)\\,75/TB\\,0)',
        )
        inspect_wav(normalize_audio(shifted, 'audio/mp4'), 14000, 19000)
    print('Codec smoke passed: WAV, AAC/M4A, explicit overlength refusal, timestamp-gap preservation.')


if __name__ == '__main__':
    main()
