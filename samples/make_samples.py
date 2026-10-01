"""Generate the untagged sample files in this folder (synthetic audio, CC0).

    python samples/make_samples.py [out_dir]

  song-128.mp3 / song-320.mp3 / song.flac   one "song" in three encodings
  other.mp3                                 a different song
  tone.ogg / tone.wav                       short tones for the other formats
  tone.opus.ogg                             Opus in an .ogg file
  silent.m4a                                a valid but silent AAC container (no encoder in Python)

Written with libsndfile (pip install soundfile numpy); no ffmpeg needed.
The tests copy these files and write the tags they need into the copies.
"""
from __future__ import annotations

import os
import struct
import sys

SAMPLE_RATE = 44100


def song(seed: int, seconds: float = 16.0):
    """A melody of random notes with a little noise, so Chromaprint has something to hold on to."""
    import numpy as np

    rng = np.random.default_rng(seed)
    n = int(SAMPLE_RATE * seconds)
    out = np.zeros(n, dtype="float64")
    step = int(SAMPLE_RATE * 0.25)
    for start in range(0, n, step):
        f = 220.0 * 2 ** (rng.integers(0, 24) / 12)
        t = np.arange(min(step, n - start)) / SAMPLE_RATE
        env = np.exp(-t * 6)
        out[start:start + len(t)] += env * (np.sin(2 * np.pi * f * t) + 0.4 * np.sin(4 * np.pi * f * t))
    out += rng.normal(0, 0.02, n)
    return (out / np.abs(out).max() * 0.5).astype("float32")


def tone(seconds: float, freq: float):
    import numpy as np

    t = np.arange(int(SAMPLE_RATE * seconds)) / SAMPLE_RATE
    return (np.sin(2 * np.pi * freq * t) * 0.25).astype("float32")


# ------------------------------------------------------------------ minimal m4a
def _atom(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", 8 + len(payload)) + kind + payload


def _full(kind: bytes, version: int, flags: int, payload: bytes) -> bytes:
    return _atom(kind, struct.pack(">B", version) + flags.to_bytes(3, "big") + payload)


def write_m4a(path: str, seconds: float = 6.0, frames_per_second: int = 43) -> str:
    """A valid MP4 with one AAC-LC audio track whose samples are empty frames."""
    timescale = SAMPLE_RATE
    sample_count = int(seconds * frames_per_second)
    sample_size = 8
    duration = sample_count * 1024
    mdat_payload = bytes([0x21, 0x10, 0x04, 0x60, 0x8C, 0x1C, 0x00, 0x00]) * sample_count

    esds = _full(b"esds", 0, 0, bytes([
        0x03, 0x19, 0x00, 0x01, 0x00,  # ES_Descriptor
        0x04, 0x11, 0x40, 0x15, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,  # DecoderConfig
        0x05, 0x02, 0x12, 0x10,  # AudioSpecificConfig: AAC-LC 44100 stereo
        0x06, 0x01, 0x02,
    ]))
    mp4a = _atom(b"mp4a", bytes(6) + struct.pack(">H", 1) + bytes(8) + struct.pack(">HHHH", 2, 16, 0, 0)
                 + struct.pack(">I", timescale << 16) + esds)
    stsd = _full(b"stsd", 0, 0, struct.pack(">I", 1) + mp4a)
    stts = _full(b"stts", 0, 0, struct.pack(">III", 1, sample_count, 1024))
    stsc = _full(b"stsc", 0, 0, struct.pack(">IIII", 1, 1, sample_count, 1))
    stsz = _full(b"stsz", 0, 0, struct.pack(">II", sample_size, sample_count))
    # chunk offset is patched after the moov size is known
    stco_placeholder = _full(b"stco", 0, 0, struct.pack(">II", 1, 0))
    stbl = _atom(b"stbl", stsd + stts + stsc + stsz + stco_placeholder)
    dref = _full(b"dref", 0, 0, struct.pack(">I", 1) + _full(b"url ", 0, 1, b""))
    dinf = _atom(b"dinf", dref)
    smhd = _full(b"smhd", 0, 0, bytes(4))
    minf = _atom(b"minf", smhd + dinf + stbl)
    hdlr = _full(b"hdlr", 0, 0, bytes(4) + b"soun" + bytes(12) + b"SoundHandler\0")
    mdhd = _full(b"mdhd", 0, 0, struct.pack(">IIII", 0, 0, timescale, duration) + struct.pack(">HH", 0x55C4, 0))
    mdia = _atom(b"mdia", mdhd + hdlr + minf)
    tkhd = _full(b"tkhd", 0, 7, struct.pack(">IIII", 0, 0, 1, 0) + struct.pack(">I", duration) + bytes(8)
                 + struct.pack(">HHHH", 0, 0, 0x0100, 0)
                 + struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000) + struct.pack(">II", 0, 0))
    trak = _atom(b"trak", tkhd + mdia)
    mvhd = _full(b"mvhd", 0, 0, struct.pack(">IIII", 0, 0, timescale, duration)
                 + struct.pack(">IH", 0x10000, 0x0100) + bytes(10)
                 + struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000) + bytes(24) + struct.pack(">I", 2))
    moov_body = mvhd + trak
    ftyp = _atom(b"ftyp", b"M4A " + struct.pack(">I", 0) + b"M4A mp42isom")
    free = _atom(b"free", bytes(1024))
    moov_size = 8 + len(moov_body)
    mdat_offset = len(ftyp) + moov_size + len(free) + 8
    stco = _full(b"stco", 0, 0, struct.pack(">II", 1, mdat_offset))
    moov_body = moov_body.replace(stco_placeholder, stco)
    moov = _atom(b"moov", moov_body)
    mdat = _atom(b"mdat", mdat_payload)
    with open(path, "wb") as f:
        f.write(ftyp + moov + free + mdat)
    return path


def main(argv: list[str]) -> int:
    import soundfile as sf

    out_dir = argv[0] if argv else os.path.dirname(os.path.abspath(__file__))
    os.makedirs(out_dir, exist_ok=True)
    a, b = song(1), song(2)
    mp3 = {"format": "MP3", "subtype": "MPEG_LAYER_III", "bitrate_mode": "CONSTANT"}
    files = [
        ("song-128.mp3", a, dict(mp3, compression_level=0.65)),
        ("song-320.mp3", a, dict(mp3, compression_level=0.0)),
        ("song.flac", a, {"format": "FLAC"}),
        ("other.mp3", b, dict(mp3, compression_level=0.65)),
        ("tone.ogg", tone(3.0, 523.25), {"format": "OGG", "subtype": "VORBIS"}),
        ("tone.wav", tone(3.0, 659.25), {"format": "WAV", "subtype": "PCM_16"}),
    ]
    for name, data, kwargs in files:
        path = os.path.join(out_dir, name)
        sf.write(path, data, SAMPLE_RATE, **kwargs)
        print(path)
    opus = os.path.join(out_dir, "tone.opus.ogg")
    sf.write(opus, tone(3.0, 440.0), 48000, format="OGG", subtype="OPUS")  # Opus only runs at 48 kHz
    print(opus)
    print(write_m4a(os.path.join(out_dir, "silent.m4a")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
