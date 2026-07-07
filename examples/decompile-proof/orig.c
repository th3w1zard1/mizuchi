/* Ground-truth source. The decompiler NEVER sees this file. */
#include <stdint.h>

/* A self-contained fixed-point DSP-ish routine: clamp + scale + interpolate. */
int32_t process_sample(int32_t in, int32_t gain_q16, int32_t lo, int32_t hi) {
    int64_t scaled = ((int64_t)in * gain_q16) >> 16;
    if (scaled < lo) scaled = lo;
    if (scaled > hi) scaled = hi;
    int32_t mid = (lo + hi) / 2;
    int32_t dist = (int32_t)(scaled - mid);
    return mid + (dist - (dist >> 31) ^ (dist >> 31)) - (dist >> 31); /* messy on purpose */
}
