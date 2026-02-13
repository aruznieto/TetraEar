"""
Traffic Channel (TCH) helpers for extracting codec input from TETRA bursts.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from tetraear.core.lower_mac import build_cdecoder_block

SYMBOLS_PER_SLOT = 255
DATA_SYMBOLS_PER_BLOCK = 108
TRAINING_SYMBOLS = 11
BITS_PER_SYMBOL = 2


def _dqpsk_symbols_from_complex(samples: np.ndarray) -> np.ndarray:
    """Convert complex symbols to hard-decision π/4-DQPSK symbols (0-3)."""
    if samples is None or len(samples) < 2:
        return np.array([], dtype=np.uint8)

    symbols = []
    prev_sample = samples[0]
    for sample in samples[1:]:
        diff = sample * np.conj(prev_sample)
        phase_diff = np.arctan2(np.imag(diff), np.real(diff))

        if phase_diff < -5 * np.pi / 8:
            symbol = 3
        elif phase_diff < -3 * np.pi / 8:
            symbol = 2
        elif phase_diff < 3 * np.pi / 8:
            symbol = 0
        elif phase_diff < 5 * np.pi / 8:
            symbol = 1
        else:
            symbol = 3

        symbols.append(symbol)
        prev_sample = sample

    if not symbols:
        return np.array([], dtype=np.uint8)

    # Pad to preserve length; prepend last-known symbol for the first sample.
    symbols.insert(0, symbols[0])
    return np.array(symbols, dtype=np.uint8)


def extract_tch_soft_bits(symbols: np.ndarray, start_bit: int) -> Optional[list[int]]:
    """Extract 432 type-4 bits from a single TCH burst."""
    if symbols is None or start_bit is None:
        return None

    if np.iscomplexobj(symbols):
        symbols = _dqpsk_symbols_from_complex(symbols)

    if symbols is None or len(symbols) < SYMBOLS_PER_SLOT:
        return None

    symbol_pos = int(start_bit) // BITS_PER_SYMBOL
    if symbol_pos < 0 or symbol_pos + SYMBOLS_PER_SLOT > len(symbols):
        return None

    slot_symbols = np.asarray(symbols[symbol_pos : symbol_pos + SYMBOLS_PER_SLOT], dtype=np.int16)

    soft_bits: list[int] = []

    for i in range(DATA_SYMBOLS_PER_BLOCK):
        sym = int(slot_symbols[i]) & 0x3
        soft_bits.append((sym >> 1) & 1)
        soft_bits.append(sym & 1)

    second_start = DATA_SYMBOLS_PER_BLOCK + TRAINING_SYMBOLS
    for i in range(second_start, second_start + DATA_SYMBOLS_PER_BLOCK):
        if i >= len(slot_symbols):
            break
        sym = int(slot_symbols[i]) & 0x3
        soft_bits.append((sym >> 1) & 1)
        soft_bits.append(sym & 1)

    if len(soft_bits) < 432:
        return None
    return soft_bits


def pack_codec_input(soft_bits: list[int]) -> Optional[bytes]:
    """Pack type-4 bits into ETSI codec input format."""
    if soft_bits is None or len(soft_bits) < 432:
        return None
    bits = np.asarray(soft_bits[:432], dtype=np.uint8)
    return build_cdecoder_block(bits)


def extract_tch_codec_input(symbols: np.ndarray, start_bit: int) -> Optional[bytes]:
    """Extract a single-burst codec input when enough soft bits are available."""
    soft_bits = extract_tch_soft_bits(symbols, start_bit)
    if not soft_bits:
        return None
    return pack_codec_input(soft_bits)


class TchFrameAssembler:
    """Assemble bursts into codec frames."""

    def __init__(self, required_bits: int = 432):
        self.required_bits = required_bits
        self._buffer: list[int] = []

    def add_burst(self, symbols: np.ndarray, start_bit: int) -> Optional[bytes]:
        soft_bits = extract_tch_soft_bits(symbols, start_bit)
        if not soft_bits:
            return None
        self._buffer.extend(soft_bits)
        if len(self._buffer) < self.required_bits:
            return None
        frame_bits = self._buffer[: self.required_bits]
        self._buffer = self._buffer[self.required_bits :]
        return pack_codec_input(frame_bits)
