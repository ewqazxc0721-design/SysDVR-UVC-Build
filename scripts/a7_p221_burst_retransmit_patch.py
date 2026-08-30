from pathlib import Path
import sys


root = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/sysdvr/sysmodule")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"P2.2.1 {label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def replace_function(text: str, signature: str, replacement: str, label: str) -> str:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"P2.2.1 {label}: function missing")
    end = text.find("\n}\n", start)
    if end < 0:
        raise SystemExit(f"P2.2.1 {label}: function end missing")
    end += 3
    return text[:start] + replacement + text[end:]


p = root / "source/ncm/ncm_device.c"
s = p.read_text()

# The P2.2 runtime log proves recovery works, but a lost eight-frame NCM NTB
# causes three consecutive audio retransmits or up to eight video retransmits.
# Sending one segment per 100 ms makes that recovery audible.  Replay one NTB-
# sized burst per RTO and flush it immediately; duplicate TCP data is harmless
# and cumulative ACKs retire the recovered prefix on the next receive pass.
s = replace_once(
    s,
    "#define A7P22_RETX_AFTER_LOOPS 100\n",
    "#define A7P22_RETX_AFTER_LOOPS 25\n"
    "#define A7P221_RETX_BURST_SEGMENTS 8u\n",
    "RTO and burst constants",
)

video_burst = r'''static bool _a7p221VideoRetransmitBurst(void)
{
    _a7p22VideoDiscardAcked();
    if (!g_a7p22VideoCount) return true;
    u32 burst = g_a7p22VideoCount < A7P221_RETX_BURST_SEGMENTS ?
                g_a7p22VideoCount : A7P221_RETX_BURST_SEGMENTS;
    u32 firstSeq = g_a7p22VideoRing[g_a7p22VideoHead].seq;
    for (u32 i = 0; i < burst; i++) {
        u32 index = (g_a7p22VideoHead + i) % A7P22_RETX_RING_SEGMENTS;
        A7P22UnackedSegment* seg = &g_a7p22VideoRing[index];
        if (seg->retries >= A7P22_RETX_MAX_RETRIES) return false;
        Result rc = _tcpSendSegment(seg->seq, g_tcpClientNextSeq,
                                    TCP_FLAG_ACK | TCP_FLAG_PSH,
                                    seg->payload, seg->len);
        if (R_FAILED(rc)) return false;
        seg->retries++;
        g_a7p22VideoRetransmits++;
    }
    if (!_a7p21FlushQueuedTx()) return false;
    LOG("A7-P2.2.1 video RTO burst first=%u segments=%u total=%u\n",
        firstSeq, burst, g_a7p22VideoRetransmits);
    return true;
}
'''
s = replace_function(
    s,
    "static bool _a7p22VideoRetransmitOldest(void)\n",
    video_burst,
    "video burst retransmit",
)
s = s.replace("_a7p22VideoRetransmitOldest()", "_a7p221VideoRetransmitBurst()")
if "_a7p22VideoRetransmitOldest" in s:
    raise SystemExit("P2.2.1 stale video single-segment retransmit remains")

audio_burst = r'''static bool _a7p221AudioRetransmitBurst(void)
{
    _a7p22AudioDiscardAcked();
    if (!g_a7p22AudioCount) return true;
    u32 burst = g_a7p22AudioCount < A7P221_RETX_BURST_SEGMENTS ?
                g_a7p22AudioCount : A7P221_RETX_BURST_SEGMENTS;
    u32 firstSeq = g_a7p22AudioRing[g_a7p22AudioHead].seq;
    for (u32 i = 0; i < burst; i++) {
        u32 index = (g_a7p22AudioHead + i) % A7P22_RETX_RING_SEGMENTS;
        A7P22UnackedSegment* seg = &g_a7p22AudioRing[index];
        if (seg->retries >= A7P22_RETX_MAX_RETRIES) return false;
        Result rc = _audioTcpSendSegment(seg->seq, g_audioTcpClientNextSeq,
                                         TCP_FLAG_ACK | TCP_FLAG_PSH,
                                         seg->payload, seg->len);
        if (R_FAILED(rc)) return false;
        seg->retries++;
        g_a7p22AudioRetransmits++;
    }
    if (!_a7p21FlushQueuedTx()) return false;
    LOG("A7-P2.2.1 audio RTO burst first=%u segments=%u total=%u\n",
        firstSeq, burst, g_a7p22AudioRetransmits);
    return true;
}
'''
s = replace_function(
    s,
    "static bool _a7p22AudioRetransmitOldest(void)\n",
    audio_burst,
    "audio burst retransmit",
)
s = s.replace("_a7p22AudioRetransmitOldest()", "_a7p221AudioRetransmitBurst()")
if "_a7p22AudioRetransmitOldest" in s:
    raise SystemExit("P2.2.1 stale audio single-segment retransmit remains")

p.write_text(s)


p = root / "source/core.c"
s = p.read_text()
s = replace_once(
    s,
    "A7-P2.2 raw SD logfile initialized BUILD=P22",
    "A7-P2.2.1 raw SD logfile initialized BUILD=P221",
    "core marker",
)
p.write_text(s)


p = root / "source/ncm/NCMmode.c"
s = p.read_text()
s = replace_once(
    s,
    "A7-P2.2 SysDVR 6.3 + USB-NCM retransmit A/V mode starting BUILD=P22 NTB=16384 DATAGRAMS=8 RING=32 RTO_LOOPS=100",
    "A7-P2.2.1 SysDVR 6.3 + USB-NCM burst-retransmit A/V mode starting BUILD=P221 NTB=16384 DATAGRAMS=8 RING=32 RTO_LOOPS=25 BURST=8",
    "mode marker",
)
s = replace_once(
    s,
    "A7-P2.2 NCM ready BUILD=P22: DHCP",
    "A7-P2.2.1 NCM ready BUILD=P221: DHCP",
    "ready marker",
)
p.write_text(s)

print("A7-P2.2.1 NTB-sized burst retransmit patch applied")

