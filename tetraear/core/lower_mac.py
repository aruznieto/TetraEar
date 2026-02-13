"""
Pure-Python lower MAC / FEC helpers ported from osmo-tetra.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np

SCRAMB_INIT = 3
TETRA_CRC_OK = 0x1D0F


class BlockType(Enum):
    SB1 = "SB1"
    SB2 = "SB2"
    NDB = "NDB"
    SCH_HU = "SCH_HU"
    SCH_F = "SCH_F"
    BBK = "BBK"


@dataclass
class BlockParams:
    type345_bits: int
    type2_bits: int
    type1_bits: int
    interleave_a: int
    have_crc16: bool


BLOCK_PARAMS: dict[BlockType, BlockParams] = {
    BlockType.SB1: BlockParams(type345_bits=120, type2_bits=80, type1_bits=60, interleave_a=11, have_crc16=True),
    BlockType.SB2: BlockParams(type345_bits=216, type2_bits=144, type1_bits=124, interleave_a=101, have_crc16=True),
    BlockType.NDB: BlockParams(type345_bits=216, type2_bits=144, type1_bits=124, interleave_a=101, have_crc16=True),
    BlockType.SCH_HU: BlockParams(type345_bits=168, type2_bits=112, type1_bits=92, interleave_a=13, have_crc16=True),
    BlockType.SCH_F: BlockParams(type345_bits=432, type2_bits=288, type1_bits=268, interleave_a=103, have_crc16=True),
    BlockType.BBK: BlockParams(type345_bits=30, type2_bits=30, type1_bits=14, interleave_a=0, have_crc16=False),
}


@dataclass
class LowerMacBlock:
    block_type: BlockType
    blk_num: int
    type1_bits: Optional[np.ndarray]
    crc_ok: bool
    type4_bits: np.ndarray
    scrambling_code: int
    is_traffic: bool = False
    codec_input: Optional[bytes] = None
    extra: Optional[dict] = None


# Burst layout constants (bits)
DQPSK4_BITS_PER_SYM = 2

SB_BLK1_OFFSET = (6 + 1 + 40) * DQPSK4_BITS_PER_SYM
SB_BBK_OFFSET = (6 + 1 + 40 + 60 + 19) * DQPSK4_BITS_PER_SYM
SB_BLK2_OFFSET = (6 + 1 + 40 + 60 + 19 + 15) * DQPSK4_BITS_PER_SYM

SB_BLK1_BITS = 60 * DQPSK4_BITS_PER_SYM
SB_BBK_BITS = 15 * DQPSK4_BITS_PER_SYM
SB_BLK2_BITS = 108 * DQPSK4_BITS_PER_SYM

NDB_BLK1_OFFSET = (5 + 1 + 1) * DQPSK4_BITS_PER_SYM
NDB_BBK1_OFFSET = (5 + 1 + 1 + 108) * DQPSK4_BITS_PER_SYM
NDB_BBK2_OFFSET = (5 + 1 + 1 + 108 + 7 + 11) * DQPSK4_BITS_PER_SYM
NDB_BLK2_OFFSET = (5 + 1 + 1 + 108 + 7 + 11 + 8) * DQPSK4_BITS_PER_SYM

NDB_BBK1_BITS = 7 * DQPSK4_BITS_PER_SYM
NDB_BBK2_BITS = 8 * DQPSK4_BITS_PER_SYM
NDB_BLK_BITS = 108 * DQPSK4_BITS_PER_SYM
NDB_BBK_BITS = SB_BBK_BITS


# Training sequences (from osmo-tetra tetra_burst.c)
N_BITS = np.array(
    [1, 1, 0, 1, 0, 0, 0, 0, 1, 1, 1, 0, 1, 0, 0, 1, 1, 1, 0, 1, 0, 0],
    dtype=np.uint8,
)
P_BITS = np.array(
    [0, 1, 1, 1, 1, 0, 1, 0, 0, 1, 0, 0, 0, 0, 1, 1, 0, 1, 1, 1, 1, 0],
    dtype=np.uint8,
)
Q_BITS = np.array(
    [1, 0, 1, 1, 0, 1, 1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 1, 0, 1, 1, 0, 1],
    dtype=np.uint8,
)
X_BITS = np.array(
    [1, 0, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0, 1, 1, 1, 0, 1, 0, 0, 1, 1, 1, 0, 1, 0, 0, 0, 0, 1, 1],
    dtype=np.uint8,
)
Y_BITS = np.array(
    [1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 1, 1, 0, 1, 0, 0, 1, 1, 1, 0, 0, 0, 0, 0, 1, 1, 0, 0, 1, 1, 1],
    dtype=np.uint8,
)


class TrainSeq(Enum):
    SYNC = "SYNC"
    NORM_1 = "NORM_1"
    NORM_2 = "NORM_2"
    NORM_3 = "NORM_3"
    EXT = "EXT"
    UNKNOWN = "UNKNOWN"


def bits_to_uint(bits: np.ndarray, start: int, length: int) -> int:
    val = 0
    end = start + length
    for bit in bits[start:end]:
        val = (val << 1) | (int(bit) & 1)
    return val


def crc16_ccitt_bits(bits: np.ndarray) -> int:
    crc = 0xFFFF
    for bit in bits:
        crc ^= (int(bit) & 1) << 15
        if crc & 0x8000:
            crc = (crc << 1) ^ 0x1021
        else:
            crc <<= 1
        crc &= 0xFFFF
    return crc


def _next_lfsr_bit(lfsr: int) -> tuple[int, int]:
    bit = (
        ((lfsr >> 0) ^ (lfsr >> 6) ^ (lfsr >> 9) ^ (lfsr >> 10) ^
         (lfsr >> 16) ^ (lfsr >> 20) ^ (lfsr >> 21) ^ (lfsr >> 22) ^
         (lfsr >> 24) ^ (lfsr >> 25) ^ (lfsr >> 27) ^ (lfsr >> 28) ^
         (lfsr >> 30) ^ (lfsr >> 31)) & 1
    )
    lfsr = (lfsr >> 1) | (bit << 31)
    return bit & 0xFF, lfsr


def tetra_scramb_get_init(mcc: int, mnc: int, colour: int) -> int:
    mcc &= 0x3FF
    mnc &= 0x3FFF
    colour &= 0x3F
    scramb_init = colour | (mnc << 6) | (mcc << 20)
    scramb_init = (scramb_init << 2) | SCRAMB_INIT
    return scramb_init


def tetra_scramb_bits(lfsr_init: int, bits: np.ndarray) -> np.ndarray:
    lfsr = lfsr_init & 0xFFFFFFFF
    out = np.array(bits, dtype=np.uint8, copy=True)
    for idx in range(len(out)):
        bit, lfsr = _next_lfsr_bit(lfsr)
        out[idx] ^= bit
    return out


def tetra_scramb_soft(lfsr_init: int, bits: np.ndarray) -> np.ndarray:
    lfsr = lfsr_init & 0xFFFFFFFF
    out = np.array(bits, dtype=np.int16, copy=True)
    for idx in range(len(out)):
        bit, lfsr = _next_lfsr_bit(lfsr)
        if bit:
            out[idx] = -out[idx]
    return out


def block_deinterleave(k: int, a: int, data: np.ndarray) -> np.ndarray:
    out = np.zeros(k, dtype=data.dtype)
    for i in range(1, k + 1):
        idx = 1 + (a * i) % k
        out[i - 1] = data[idx - 1]
    return out


class _Puncturer:
    def __init__(self, p: list[int], t: int, period: int, i_func):
        self.p = p
        self.t = t
        self.period = period
        self.i_func = i_func


def _i_func_equals(j: int) -> int:
    return j


def _i_func_292(j: int) -> int:
    return j + ((j - 1) // 65)


def _i_func_148(j: int) -> int:
    return j + ((j - 1) // 35)


P_RATE2_3 = [0, 1, 2, 5]
P_RATE1_3 = [0, 1, 2, 3, 5, 6, 7]
P_RATE8_12 = [0, 1, 2, 4]
P_RATE8_18 = [0, 1, 2, 3, 4, 5, 7, 8, 10, 11]
P_RATE8_17 = [0, 1, 2, 3, 4, 5, 7, 8, 10, 11, 13, 14, 16, 17, 19, 20, 22, 23]

PUNCTURERS = {
    "2_3": _Puncturer(P_RATE2_3, t=3, period=8, i_func=_i_func_equals),
    "1_3": _Puncturer(P_RATE1_3, t=6, period=8, i_func=_i_func_equals),
    "292_432": _Puncturer(P_RATE2_3, t=3, period=8, i_func=_i_func_292),
    "148_432": _Puncturer(P_RATE1_3, t=6, period=8, i_func=_i_func_148),
    "112_168": _Puncturer(P_RATE8_12, t=3, period=6, i_func=_i_func_equals),
    "72_162": _Puncturer(P_RATE8_18, t=9, period=12, i_func=_i_func_equals),
    "38_80": _Puncturer(P_RATE8_17, t=17, period=24, i_func=_i_func_equals),
}


def tetra_rcpc_depunct(punct: str, data: np.ndarray, out_len: int) -> np.ndarray:
    puncturer = PUNCTURERS[punct]
    out = np.full(out_len, 0xFF, dtype=np.uint8)
    t = puncturer.t
    p = puncturer.p
    for j in range(1, len(data) + 1):
        i = puncturer.i_func(j)
        k = puncturer.period * ((i - 1) // t) + p[i - t * ((i - 1) // t)]
        if 0 < k <= out_len:
            out[k - 1] = data[j - 1]
    return out


def tetra_rcpc_depunct_soft(punct: str, data: np.ndarray, out_len: int) -> np.ndarray:
    puncturer = PUNCTURERS[punct]
    out = np.zeros(out_len, dtype=np.int16)
    t = puncturer.t
    p = puncturer.p
    for j in range(1, len(data) + 1):
        i = puncturer.i_func(j)
        k = puncturer.period * ((i - 1) // t) + p[i - t * ((i - 1) // t)]
        if 0 < k <= out_len:
            out[k - 1] = int(data[j - 1])
    return out


CONV_CCH_NEXT_OUTPUT = np.array([
    [0, 15], [11, 4], [6, 9], [13, 2],
    [5, 10], [14, 1], [3, 12], [8, 7],
    [15, 0], [4, 11], [9, 6], [2, 13],
    [10, 5], [1, 14], [12, 3], [7, 8],
], dtype=np.uint8)

CONV_CCH_NEXT_STATE = np.array([
    [0, 1], [2, 3], [4, 5], [6, 7],
    [8, 9], [10, 11], [12, 13], [14, 15],
    [0, 1], [2, 3], [4, 5], [6, 7],
    [8, 9], [10, 11], [12, 13], [14, 15],
], dtype=np.uint8)

CONV_TCH_NEXT_OUTPUT = np.array([
    [0, 7], [6, 1], [5, 2], [3, 4],
    [6, 1], [0, 7], [3, 4], [5, 2],
    [7, 0], [1, 6], [2, 5], [4, 3],
    [1, 6], [7, 0], [4, 3], [2, 5],
], dtype=np.uint8)

CONV_TCH_NEXT_STATE = np.array([
    [0, 1], [2, 3], [4, 5], [6, 7],
    [8, 9], [10, 11], [12, 13], [14, 15],
    [0, 1], [2, 3], [4, 5], [6, 7],
    [8, 9], [10, 11], [12, 13], [14, 15],
], dtype=np.uint8)


def _viterbi_decode(
    next_state: np.ndarray,
    next_output: np.ndarray,
    soft: np.ndarray,
    n_bits: int,
    output_order: list[int],
) -> np.ndarray:
    n_states = next_state.shape[0]
    metrics = np.full(n_states, -1e9, dtype=np.float64)
    metrics[0] = 0.0
    decisions_state = np.zeros((n_bits, n_states), dtype=np.int16)
    decisions_bit = np.zeros((n_bits, n_states), dtype=np.uint8)
    num_out = len(output_order)

    for t in range(n_bits):
        new_metrics = np.full(n_states, -1e9, dtype=np.float64)
        soft_slice = soft[t * num_out : t * num_out + num_out]
        for state in range(n_states):
            prev_metric = metrics[state]
            if prev_metric <= -1e8:
                continue
            for bit in (0, 1):
                ns = int(next_state[state, bit])
                out_sym = int(next_output[state, bit])
                metric = 0.0
                for k, out_idx in enumerate(output_order):
                    s = int(soft_slice[k])
                    if s == 0:
                        continue
                    expected = (out_sym >> out_idx) & 1
                    metric += s if expected == 0 else -s
                metric += prev_metric
                if metric > new_metrics[ns]:
                    new_metrics[ns] = metric
                    decisions_state[t, ns] = state
                    decisions_bit[t, ns] = bit
        metrics = new_metrics

    state = int(np.argmax(metrics))
    out_bits = np.zeros(n_bits, dtype=np.uint8)
    for t in range(n_bits - 1, -1, -1):
        bit = decisions_bit[t, state]
        out_bits[t] = bit
        state = int(decisions_state[t, state])
    return out_bits


def viterbi_decode_cch(type3dp: np.ndarray, sym_count: int) -> np.ndarray:
    if type3dp.dtype != np.uint8 or np.any((type3dp != 0) & (type3dp != 1) & (type3dp != 0xFF)):
        soft = type3dp.astype(np.int16, copy=False)
    else:
        soft = np.zeros(sym_count * 4, dtype=np.int16)
        for i in range(sym_count * 4):
            val = int(type3dp[i])
            if val == 0xFF:
                soft[i] = 0
            elif val == 0:
                soft[i] = 127
            else:
                soft[i] = -127
    return _viterbi_decode(CONV_CCH_NEXT_STATE, CONV_CCH_NEXT_OUTPUT, soft, sym_count, [3, 2, 1, 0])


def viterbi_decode_tch(type3dp: np.ndarray, sym_count: int) -> np.ndarray:
    soft = np.zeros(sym_count * 3, dtype=np.int16)
    for i in range(sym_count * 3):
        val = int(type3dp[i])
        if val == 0xFF:
            soft[i] = 0
        elif val == 0:
            soft[i] = 127
        else:
            soft[i] = -127
    return _viterbi_decode(CONV_TCH_NEXT_STATE, CONV_TCH_NEXT_OUTPUT, soft, sym_count, [2, 1, 0])


def detect_training_sequence(bits: np.ndarray, start: int = 0) -> tuple[TrainSeq, float]:
    """Detect training sequence type in a burst."""
    if len(bits) < start + 510:
        return TrainSeq.UNKNOWN, 0.0
    sync_window = bits[start + 214 : start + 214 + len(Y_BITS)]
    sync_corr = float(np.mean(sync_window == Y_BITS)) if len(sync_window) == len(Y_BITS) else 0.0
    norm_window = bits[start + 244 : start + 244 + len(N_BITS)]
    if len(norm_window) != len(N_BITS):
        return TrainSeq.UNKNOWN, 0.0

    n_corr = float(np.mean(norm_window == N_BITS))
    p_corr = float(np.mean(norm_window == P_BITS))
    q_corr = float(np.mean(norm_window == Q_BITS))
    best_norm = max(n_corr, p_corr, q_corr)

    if sync_corr >= 0.75 and sync_corr >= best_norm:
        return TrainSeq.SYNC, sync_corr
    if best_norm >= 0.8:
        if n_corr >= p_corr and n_corr >= q_corr:
            return TrainSeq.NORM_1, n_corr
        if p_corr >= q_corr:
            return TrainSeq.NORM_2, p_corr
        return TrainSeq.NORM_3, q_corr
    return TrainSeq.UNKNOWN, max(sync_corr, best_norm)


def build_cdecoder_block(type4_bits: np.ndarray) -> Optional[bytes]:
    """Build ETSI TCH cdecoder input block (690 shorts) from 432 type-4 bits."""
    if type4_bits is None or len(type4_bits) < 432:
        return None
    soft = np.any((type4_bits != 0) & (type4_bits != 1))
    block = np.zeros(690, dtype=np.int16)
    for i in range(6):
        block[115 * i] = 0x6B21 + i
    for i in range(114):
        bit0 = type4_bits[i]
        bit1 = type4_bits[114 + i]
        bit2 = type4_bits[228 + i]
        block[1 + i] = -127 if (bit0 < 0 if soft else bit0) else 127
        block[116 + i] = -127 if (bit1 < 0 if soft else bit1) else 127
        block[231 + i] = -127 if (bit2 < 0 if soft else bit2) else 127
    for i in range(90):
        bit3 = type4_bits[342 + i]
        block[346 + i] = -127 if (bit3 < 0 if soft else bit3) else 127
    return block.tobytes()


def decode_access_assign(bits: np.ndarray) -> Optional[int]:
    """Decode AACH Access-Assign to get DL usage marker."""
    if bits is None or len(bits) < 14:
        return None
    if np.any((bits != 0) & (bits != 1)):
        bits = np.array(bits < 0, dtype=np.uint8)
    hdr = bits_to_uint(bits, 0, 2)
    field1 = bits_to_uint(bits, 2, 6)
    field2 = bits_to_uint(bits, 8, 6)
    if hdr == 0:
        return None
    if hdr == 1:
        return field1
    if hdr in (2, 3):
        return field1
    return None


class LowerMacDecoder:
    def __init__(self) -> None:
        self.mcc: Optional[int] = None
        self.mnc: Optional[int] = None
        self.colour_code: Optional[int] = None
        self.scramb_init: Optional[int] = None

    def _decode_block(self, block_type: BlockType, bits: np.ndarray, *, force_scramb: Optional[int] = None) -> LowerMacBlock:
        params = BLOCK_PARAMS[block_type]
        type5 = np.array(bits[: params.type345_bits], copy=True)
        is_soft = np.any((type5 != 0) & (type5 != 1))
        if force_scramb is not None:
            scramb_init = force_scramb
        elif block_type == BlockType.SB1:
            scramb_init = SCRAMB_INIT
        else:
            scramb_init = self.scramb_init

        if scramb_init is None:
            return LowerMacBlock(block_type, 0, None, False, type5, 0)

        if is_soft:
            type4 = tetra_scramb_soft(scramb_init, type5.astype(np.int16))
        else:
            type4 = tetra_scramb_bits(scramb_init, type5.astype(np.uint8))

        if params.interleave_a:
            type3 = block_deinterleave(params.type345_bits, params.interleave_a, type4)
            if is_soft:
                type3dp = tetra_rcpc_depunct_soft("2_3", type3, params.type2_bits * 4)
            else:
                type3dp = tetra_rcpc_depunct("2_3", type3.astype(np.uint8), params.type2_bits * 4)
            type2 = viterbi_decode_cch(type3dp, params.type2_bits)
        else:
            type2 = type4[: params.type2_bits]

        if params.have_crc16:
            crc = crc16_ccitt_bits(type2[: params.type1_bits + 16])
            crc_ok = crc == TETRA_CRC_OK
        else:
            crc_ok = True

        type1 = type2[: params.type1_bits]
        return LowerMacBlock(block_type, 0, type1, crc_ok, type4, scramb_init)

    def decode_burst(self, bits: np.ndarray, train_seq: TrainSeq) -> list[LowerMacBlock]:
        blocks: list[LowerMacBlock] = []
        bits = np.array(bits, copy=False)
        if np.any((bits != 0) & (bits != 1)):
            bits = bits.astype(np.int16, copy=False)
        else:
            bits = bits.astype(np.uint8, copy=False)

        if train_seq == TrainSeq.SYNC:
            sb1 = self._decode_block(BlockType.SB1, bits[SB_BLK1_OFFSET : SB_BLK1_OFFSET + SB_BLK1_BITS])
            blocks.append(sb1)
            if sb1.type1_bits is not None and sb1.crc_ok:
                colour = bits_to_uint(sb1.type1_bits, 4, 6)
                mcc = bits_to_uint(sb1.type1_bits, 31, 10)
                mnc = bits_to_uint(sb1.type1_bits, 41, 14)
                self.colour_code = colour
                self.mcc = mcc
                self.mnc = mnc
                self.scramb_init = tetra_scramb_get_init(mcc, mnc, colour)
                sb1.extra = {
                    "colour_code": colour,
                    "mcc": mcc,
                    "mnc": mnc,
                }

            bbk_bits = bits[SB_BBK_OFFSET : SB_BBK_OFFSET + SB_BBK_BITS]
            bbk = self._decode_block(BlockType.BBK, bbk_bits)
            blocks.append(bbk)

            sb2 = self._decode_block(BlockType.SB2, bits[SB_BLK2_OFFSET : SB_BLK2_OFFSET + SB_BLK2_BITS])
            blocks.append(sb2)
            return blocks

        if train_seq in (TrainSeq.NORM_1, TrainSeq.NORM_2):
            bbk_bits = np.concatenate(
                [
                    bits[NDB_BBK1_OFFSET : NDB_BBK1_OFFSET + NDB_BBK1_BITS],
                    bits[NDB_BBK2_OFFSET : NDB_BBK2_OFFSET + NDB_BBK2_BITS],
                ]
            )
            bbk = self._decode_block(BlockType.BBK, bbk_bits)
            dl_usage = decode_access_assign(bbk.type1_bits if bbk.type1_bits is not None else bbk.type4_bits)
            is_traffic = bool(dl_usage is not None and dl_usage > 3)
            bbk.is_traffic = is_traffic
            bbk.extra = {"dl_usage": dl_usage} if dl_usage is not None else None
            blocks.append(bbk)

            if train_seq == TrainSeq.NORM_2:
                blk1_bits = bits[NDB_BLK1_OFFSET : NDB_BLK1_OFFSET + NDB_BLK_BITS]
                blk2_bits = bits[NDB_BLK2_OFFSET : NDB_BLK2_OFFSET + NDB_BLK_BITS]
                blk1 = self._decode_block(BlockType.NDB, blk1_bits)
                blk1.blk_num = 1
                blk1.is_traffic = is_traffic
                blk2 = self._decode_block(BlockType.NDB, blk2_bits)
                blk2.blk_num = 2
                blk2.is_traffic = is_traffic
                blocks.extend([blk1, blk2])
                return blocks

            # Train seq NORM_1 -> SCH/F combined blocks
            combined = np.concatenate(
                [
                    bits[NDB_BLK1_OFFSET : NDB_BLK1_OFFSET + NDB_BLK_BITS],
                    bits[NDB_BLK2_OFFSET : NDB_BLK2_OFFSET + NDB_BLK_BITS],
                ]
            )
            schf = self._decode_block(BlockType.SCH_F, combined)
            schf.is_traffic = is_traffic
            if is_traffic:
                schf.codec_input = build_cdecoder_block(schf.type4_bits)
            blocks.append(schf)
            return blocks

        return blocks
