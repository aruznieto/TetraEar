#!/usr/bin/env python3
"""
Offline IQ analyzer for TETRA recordings.
"""

from __future__ import annotations

import argparse
import re
import wave
from collections import Counter
from pathlib import Path
from typing import Iterable

import numpy as np

from tetraear.audio.tch import TchFrameAssembler
from tetraear.audio.voice import VoiceProcessor
from tetraear.core.decoder import TetraDecoder
from tetraear.signal.processor import SignalProcessor


def _dtype_from_width(width: int) -> np.dtype:
    if width == 1:
        return np.uint8
    if width == 2:
        return np.int16
    if width == 4:
        return np.int32
    raise ValueError(f"Unsupported sample width: {width}")


def _scale_samples(samples: np.ndarray, width: int) -> np.ndarray:
    if width == 1:
        return (samples.astype(np.float32) - 128.0) / 128.0
    if width == 2:
        return samples.astype(np.float32) / 32768.0
    if width == 4:
        return samples.astype(np.float32) / 2147483648.0
    return samples.astype(np.float32)


def iter_iq_samples(path: Path, chunk_frames: int) -> Iterable[np.ndarray]:
    with wave.open(str(path), "rb") as wf:
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        dtype = _dtype_from_width(width)
        while True:
            raw = wf.readframes(chunk_frames)
            if not raw:
                break
            data = np.frombuffer(raw, dtype=dtype)
            if channels == 2:
                i = data[0::2]
                q = data[1::2]
            elif channels == 1:
                if len(data) % 2 != 0:
                    data = data[:-1]
                i = data[0::2]
                q = data[1::2]
            else:
                raise ValueError(f"Unsupported channel count: {channels}")
            i = _scale_samples(i, width)
            q = _scale_samples(q, width)
            yield i + 1j * q


def parse_reference_logs(paths: list[Path]) -> dict:
    mccs = set()
    mncs = set()
    groups = set()
    issi = set()

    mcc_re = re.compile(r"MCC:\s*(\d+)")
    mnc_re = re.compile(r"MNC:\s*(\d+)")
    group_re = re.compile(r"Group:\s*(\d+)")
    issi_re = re.compile(r"ISSI:\s*(\d+)")
    calling_re = re.compile(r"Calling ISSI:\s*(\d+)")

    for path in paths:
        for line in path.read_text(errors="ignore").splitlines():
            if m := mcc_re.search(line):
                mccs.add(int(m.group(1)))
            if m := mnc_re.search(line):
                mncs.add(int(m.group(1)))
            if m := group_re.search(line):
                groups.add(int(m.group(1)))
            if m := issi_re.search(line):
                issi.add(int(m.group(1)))
            if m := calling_re.search(line):
                issi.add(int(m.group(1)))

    return {
        "mcc": mccs,
        "mnc": mncs,
        "groups": groups,
        "issi": issi,
    }


def guess_center_freq(path: Path) -> float | None:
    match = re.search(r"(\d+)Hz", path.name)
    if match:
        return float(match.group(1))
    nums = re.findall(r"(\d{6,})", path.name)
    if nums:
        return float(nums[-1])
    return None


CARRIER_OFFSET_HZ = (0, 6250, -6250, 12500)


def tetra_dl_carrier_hz(band: int, carrier: int, offset: int) -> float:
    base = band * 100_000_000
    return float(base + carrier * 25_000 + CARRIER_OFFSET_HZ[offset & 3])


def estimate_freq_offset(samples: np.ndarray, sample_rate: int, search_hz: int = 100000) -> float:
    """Estimate carrier offset by finding the strongest spectral peak."""
    if samples is None or len(samples) == 0:
        return 0.0
    n = min(len(samples), 16384)
    spectrum = np.fft.fftshift(np.fft.fft(samples[:n]))
    power = np.abs(spectrum)
    freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1 / sample_rate))
    mask = (freqs >= -search_hz) & (freqs <= search_hz)
    if not np.any(mask):
        return 0.0
    idx = np.argmax(power[mask])
    return float(freqs[mask][idx])


def estimate_freq_candidates(samples: np.ndarray, sample_rate: int, search_hz: int = 150000, top_n: int = 5) -> list[float]:
    if samples is None or len(samples) == 0:
        return [0.0]
    n = min(len(samples), 16384)
    spectrum = np.fft.fftshift(np.fft.fft(samples[:n]))
    power = np.abs(spectrum)
    freqs = np.fft.fftshift(np.fft.fftfreq(n, d=1 / sample_rate))
    mask = (freqs >= -search_hz) & (freqs <= search_hz)
    freqs = freqs[mask]
    power = power[mask]
    if len(power) == 0:
        return [0.0]
    indices = np.argsort(power)[::-1]
    candidates: list[float] = []
    for idx in indices:
        freq = float(freqs[idx])
        if all(abs(freq - c) > 2000 for c in candidates):
            candidates.append(freq)
        if len(candidates) >= top_n:
            break
    return candidates or [0.0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze TETRA IQ recordings.")
    parser.add_argument("iq", type=Path, help="Path to IQ WAV file")
    parser.add_argument("--chunk-frames", type=int, default=256 * 1024, help="Frames per read")
    parser.add_argument("--max-seconds", type=float, default=None, help="Optional time limit")
    parser.add_argument("--log", action="append", type=Path, help="Reference log(s) for comparison")
    parser.add_argument("--save-audio", action="store_true", help="Write decoded voice segments")
    parser.add_argument("--audio-dir", type=Path, default=Path("records"), help="Directory for audio output")
    parser.add_argument("--center-freq", type=float, default=None, help="Center frequency in Hz (optional)")
    parser.add_argument("--band", type=int, default=None, help="TETRA band index (optional)")
    parser.add_argument("--multi-carrier", action="store_true", help="Enable multi-carrier decoding")
    parser.add_argument("--max-carriers", type=int, default=6, help="Maximum carriers to decode")
    args = parser.parse_args()

    iq_path = args.iq
    if not iq_path.exists():
        raise SystemExit(f"Missing IQ file: {iq_path}")

    ref = parse_reference_logs(args.log or []) if args.log else None

    with wave.open(str(iq_path), "rb") as wf:
        sample_rate = wf.getframerate()
        total_frames = wf.getnframes()

    processor = SignalProcessor(sample_rate=sample_rate)
    decoder = TetraDecoder(auto_decrypt=False)
    voice = VoiceProcessor()
    tch_assembler = TchFrameAssembler()

    mcc_counts: Counter[int] = Counter()
    mnc_counts: Counter[int] = Counter()
    group_counts: Counter[int] = Counter()
    issi_counts: Counter[int] = Counter()
    frames_seen = 0
    voice_segments = 0

    max_frames = None
    if args.max_seconds:
        max_frames = int(args.max_seconds * sample_rate)

    processed_frames = 0
    audio_dir = args.audio_dir
    if args.save_audio:
        audio_dir.mkdir(parents=True, exist_ok=True)

    center_freq = args.center_freq or guess_center_freq(iq_path)
    band_guess = args.band
    if band_guess is None and center_freq is not None:
        band_guess = int(center_freq // 100_000_000)
    multi_carrier = args.multi_carrier or center_freq is not None

    sample_iter = iter_iq_samples(iq_path, args.chunk_frames)
    first_samples = next(sample_iter, None)
    if first_samples is None:
        print("No samples read.")
        return 1

    # Build a longer chunk for frequency estimation (~1 second if possible).
    search_samples = [first_samples]
    target_search_len = int(sample_rate)
    while len(np.concatenate(search_samples)) < target_search_len:
        nxt = next(sample_iter, None)
        if nxt is None:
            break
        search_samples.append(nxt)
    search_block = np.concatenate(search_samples)

    # Find best frequency offset using the first chunk.
    coarse_candidates = estimate_freq_candidates(search_block, sample_rate)
    candidates = []
    for base in coarse_candidates:
        for delta in range(-3000, 3001, 500):
            candidates.append(base + delta)
    best_offset = 0.0
    best_score = -1
    for offset in candidates:
        demod = processor.process(search_block, freq_offset=offset)
        if demod is None or len(demod) < 255:
            continue
        frames = decoder.decode(demod, confidences=processor.symbol_confidence)
        crc_ok = sum(1 for f in frames if f.get("crc_ok"))
        score = crc_ok if crc_ok > 0 else len(frames)
        if score > best_score:
            best_score = score
            best_offset = offset

    print(f"[INFO] Using frequency offset: {best_offset:.1f} Hz (candidates: {candidates})")
    if center_freq is not None:
        print(f"[INFO] Center frequency: {center_freq:.0f} Hz (band guess {band_guess})")

    active_offsets = [best_offset]
    offset_set = {best_offset}

    def maybe_add_offset(extra: dict) -> None:
        nonlocal active_offsets
        if not multi_carrier or center_freq is None:
            return
        if not extra:
            return
        carrier = extra.get("carrier")
        if carrier is None:
            carrier = extra.get("channel")
        if carrier is None:
            return
        freq_band = extra.get("freq_band", band_guess)
        if freq_band is None:
            return
        freq_offset = extra.get("freq_offset", 0)
        dl_freq = tetra_dl_carrier_hz(int(freq_band), int(carrier), int(freq_offset))
        offset = dl_freq - center_freq
        if abs(offset) > (sample_rate / 2 - 12_500):
            return
        if offset in offset_set:
            return
        offset_set.add(offset)
        active_offsets.append(offset)
        active_offsets = sorted(active_offsets, key=lambda x: abs(x))[: max(1, args.max_carriers)]

    def process_chunk(samples: np.ndarray) -> None:
        nonlocal frames_seen, voice_segments

        offsets = list(active_offsets)
        for offset in offsets:
            demodulated = processor.process(samples, freq_offset=offset)
            if demodulated is None or len(demodulated) < 255:
                continue

            frames = decoder.decode(demodulated, confidences=processor.symbol_confidence)
            for frame in frames:
                frames_seen += 1
                info = frame.get("additional_info", {})
                if "mcc" in info:
                    mcc_counts[int(info["mcc"])] += 1
                if "mnc" in info:
                    mnc_counts[int(info["mnc"])] += 1

                meta = frame.get("call_metadata", {})
                if meta:
                    call_type = meta.get("call_type")
                    if (
                        meta.get("talkgroup_id")
                        and call_type == "Group"
                        and frame.get("mac_pdu", {}).get("type") == "MAC_RESOURCE"
                    ):
                        group_counts[int(meta["talkgroup_id"])] += 1
                    if meta.get("source_ssi"):
                        issi_counts[int(meta["source_ssi"])] += 1
                    if meta.get("dest_ssi"):
                        issi_counts[int(meta["dest_ssi"])] += 1
                    maybe_add_offset(meta)

                extra = frame.get("mac_pdu_extra") or {}
                if extra:
                    maybe_add_offset(extra)

                if voice.working:
                    codec_input = frame.get("codec_input")
                    if codec_input is None and frame.get("position") is not None:
                        codec_input = tch_assembler.add_burst(demodulated, frame.get("position"))
                    if codec_input:
                        audio = voice.decode_frame(codec_input)
                        if audio.size > 0 and float(np.max(np.abs(audio))) > 1e-4:
                            voice_segments += 1
                            if args.save_audio:
                                out_path = audio_dir / f"{iq_path.stem}_voice_{voice_segments:04d}.wav"
                                audio_i16 = np.clip(audio * 32767.0, -32768, 32767).astype(np.int16)
                                with wave.open(str(out_path), "wb") as out_wav:
                                    out_wav.setnchannels(1)
                                    out_wav.setsampwidth(2)
                                    out_wav.setframerate(8000)
                                    out_wav.writeframes(audio_i16.tobytes())

    processed_frames += len(first_samples)
    if max_frames is None or processed_frames <= max_frames:
        process_chunk(first_samples)
    for samples in sample_iter:
        processed_frames += len(samples)
        if max_frames is not None and processed_frames > max_frames:
            break
        process_chunk(samples)

    print(f"[SUMMARY] Frames decoded: {frames_seen}")
    if mcc_counts:
        print(f"[SUMMARY] MCC counts: {dict(mcc_counts.most_common(5))}")
    if mnc_counts:
        print(f"[SUMMARY] MNC counts: {dict(mnc_counts.most_common(5))}")
    if group_counts:
        print(f"[SUMMARY] GSSI groups: {dict(group_counts.most_common(5))}")
    if issi_counts:
        print(f"[SUMMARY] ISSI users: {dict(issi_counts.most_common(5))}")
    print(f"[SUMMARY] Voice segments: {voice_segments}")

    if ref:
        def _overlap(name: str, ref_set: set[int], counts: Counter[int]) -> None:
            if not ref_set:
                return
            decoded = set(counts.keys())
            overlap = ref_set & decoded
            print(f"[COMPARE] {name}: decoded={len(decoded)} ref={len(ref_set)} overlap={len(overlap)}")

        _overlap("MCC", ref["mcc"], mcc_counts)
        _overlap("MNC", ref["mnc"], mnc_counts)
        _overlap("Groups", ref["groups"], group_counts)
        _overlap("ISSI", ref["issi"], issi_counts)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
