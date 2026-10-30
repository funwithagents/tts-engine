# Findings — `20261029-1124_pi4-1500-nodaemon`

The reading of this result: what stood out, which decisions it fed, what remains. The numbers are in [`summary.md`](summary.md) (one row per entry) and [`results.csv`](results.csv) (every statistic per mode × text); the device, the cooling and what the result covers are in [`description.md`](description.md). The Mac numbers quoted for comparison come from [`20261027-1453_mac-m5-pro`](../20261027-1453_mac-m5-pro/summary.md).

## Time to first audio is the wrong headline here

On the Mac every local model generates many times faster than real time, so the first chunk is the start of continuous speech. On the Pi most models run near or below real time. A model can send its first chunk quickly and then fall behind, so playback started at that chunk stutters. The useful figure is the **time to continuous audio**: the earliest playback start that never runs out of audio afterwards. It is not a summary column yet; it is derived from the per-chunk timelines in `raw.jsonl.gz`:

```python
def time_to_continuous_audio(record):
    """Earliest playback start (ms after the call) that never stalls."""
    queued_ms, start_ms = 0.0, 0.0
    for arrival_ms, n_bytes, _peak in record["chunk_log"]:
        start_ms = max(start_ms, arrival_ms - queued_ms)
        queued_ms += n_bytes / 2 / record["sample_rate"] * 1000  # 16-bit mono
    return start_ms
```

## Headline

Medians over the 10 measured runs. TTFA and continuous audio are on `say()` with the short text, then on the three-sentence text as a token stream. "Real time ×" is the audio duration over the whole call's wall time, on that stream: below 1 the listener waits. CPU peak is in percent of one core (400 = the whole board).

| entry | TTFA ms | continuous audio, short | continuous audio, three sentences | real time × | CPU peak | peak MB |
|---|---:|---:|---:|---:|---:|---:|
| pocket/english_2m_2026-10+quantize | 192 | 192 | 194 | 1.06 | 196 | 548 |
| pocket/english_2m_2026-10 | 198 | 198 | 203 | 1.02 | 232 | 617 |
| piper/en_US-lessac-low | 797 | 797 | 945 | 4.02 | 418 | 368 |
| piper/en_US-lessac-low+t2 | 1137 | 1137 | 1367 | 2.75 | 210 | 360 |
| piper/en_US-lessac-medium | 943 | 943 | 1263 | 3.10 | 420 | 413 |
| piper/en_US-ljspeech-medium (default) | 1201 | 1201 | 1281 | 3.14 | 419 | 408 |
| piper/en_US-ljspeech-medium+t2 | 1784 | 1784 | 1914 | 2.08 | 214 | 402 |
| phonon/31m-en@cpu+q4k | 128 | 1211 | 1223 | 1.33 | 408 | 391 |
| phonon/31m-en@cpu+q8_0 | 124 | 1287 | 1264 | 1.31 | 408 | 397 |
| phonon/31m-en@cpu+q4k+t2 | 188 | 1300 | 1326 | 1.14 | 312 | 312 |
| phonon/40m-multilingual@cpu+q4k | 185 | 1336 | 1386 | 1.24 | 408 | 404 |
| phonon/31m-en@cpu | 144 | 1703 | 1852 | 1.04 | 408 | 456 |
| phonon/31m-en@cpu+q4k+t1 | 326 | 2016 | 4418 | 0.81 | 209 | 289 |
| pocket/english_7m_2026-10+quantize | 311 | 788 | 2205 | 0.86 | 254 | 608 |
| pocket/english_18m_2026-10+quantize | 371 | 1272 | 4480 | 0.74 | 245 | 684 |
| pocket/english_2026-10 (100M, the pocket default) | 1315 | 19840 | 74415 | 0.14 | 307 | 1275 |
| kokoro/v1.0-int8 | 15149 | 15149 | 51981 | 0.23 | 422 | 540 |

## What stood out

- **pocket 2m is the only model that speaks within about 200 ms and keeps going.** Its margin is thin: 1.02× real time, 1.06× with `quantize`. On this board `quantize` helps every pocket size, which it never did on the Mac.
- **piper is the robust choice.** It starts at 0.8–1.2 s and then never stalls: 3–4× real time on four cores, 2–2.75× on two. Its time to first audio is its time to continuous audio, because each sentence arrives whole. One thread costs 2.7× the latency, and `ljspeech-medium+t1` stalls about 0.5 s over three sentences.
- **phonon's 125 ms time to first audio hides a slow start.** It sends two 80 ms chunks, then generates below real time for about two seconds. Continuous speech starts at 1.2–1.3 s for the quantized 31m models, no sooner than piper, with less margin and all four cores busy. Unquantized `cpu` and `auto` need 1.6–1.9 s, and every `+t1` row 4–5 s on three sentences.
- **phonon needs every core here.** On four cores the uncapped pool is the four-thread case: `+t4` matches every uncapped row. Two threads cost 0.1–0.2× of real-time margin and one thread drops every phonon below real time. The Mac's finding that every cap beats the default came from oversubscribing 15 cores and does not transfer.
- **q4k ties or beats q8_0 on the Pi**, where it was the slowest CPU variant on the Mac: 31m 1.33× against 1.31×, 40m-multilingual 1.24× against 1.18×. The smaller weights matter more on the A72's memory.
- **Out on this board:** pocket 7m, 18m and 100M, kokoro int8, and phonon 40m-multilingual unquantized all fall below real time over three sentences. kokoro int8 takes 15 s to first audio. pocket 100M takes 1.3 GB and runs at 0.14×.
- **pocket does not stay on one core.** Its library pins torch to one thread, and it read 143–194 % on the Mac, but 196–307 % here. What else spins is an open question.
- **The Pi is 8–20× slower than the M5 Pro on time to first audio** for most local entries: phonon 5–10× uncapped and up to 30× capped (its Mac numbers are a few milliseconds, so the ratio is coarse), piper 15–18×, the pocket tiny models 11–20×. The outliers are pocket 100M at 36× and kokoro int8 at 58×.
- **Memory is not the limit.** Every entry except pocket 100M stays at 290–690 MB.
- **No entry throttled.** The hottest was kokoro int8 at 71 °C, under the firmware's 80 °C limit.

## What this result changed

- **phonon gets no `threads` default.** The Mac result left the choice open, on the hypothesis that ptts oversubscribes. On the actual core count, any cap only slows phonon down, so the field stays unset unless a host needs cores for something else.

## Still to do

1. **A pass with the Reachy Mini daemon running**, once its setup is trusted. It decides between pocket 2m, whose 1.02× margin is unlikely to survive a shared CPU, and piper, whose margin should.
2. **Time to continuous audio as a summary column**, so that this reading does not need the script above.
3. **The expected-to-fail tier** at n=3: pocket 100M `quantize`, pocket 24-layer, kokoro fp32 and fp16, piper `high`, kokoro int8 `+t1`.
4. **Hosted providers over the Pi's Wi-Fi**, for the path the robot would use and the client-side CPU of an A72.
