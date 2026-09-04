from pathlib import Path
import sys


root = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/sysdvr/sysmodule")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"P2.5 {label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    begin = text.find(start)
    if begin < 0:
        raise SystemExit(f"P2.5 {label}: start anchor missing")
    finish = text.find(end, begin + len(start))
    if finish < 0:
        raise SystemExit(f"P2.5 {label}: end anchor missing")
    return text[:begin] + replacement + text[finish:]


p = root / "source/ncm/ncm_device.c"
s = p.read_text(encoding="utf-8")

s = replace_once(
    s,
    "#define A7P24_AUDIO_RETX_AFTER_LOOPS 120\n#define A7P22_RETX_MAX_RETRIES 8u",
    "#define A7P24_AUDIO_RETX_AFTER_LOOPS 40\n"
    "#define A7P25_AUDIO_RECOVERY_SEGMENTS 3u\n"
    "#define A7P22_RETX_MAX_RETRIES 8u",
    "audio fast-recovery constants",
)

audio_recovery = r'''static bool _a7p25AudioFastRecovery(void)
{
    _a7p22AudioDiscardAcked();
    if (!g_a7p22AudioCount) return true;

    const u32 burst = g_a7p22AudioCount < A7P25_AUDIO_RECOVERY_SEGMENTS ?
                      g_a7p22AudioCount : A7P25_AUDIO_RECOVERY_SEGMENTS;
    const u32 firstSeq = g_a7p22AudioRing[g_a7p22AudioHead].seq;
    for (u32 i = 0; i < burst; i++) {
        const u32 index = (g_a7p22AudioHead + i) % A7P22_RETX_RING_SEGMENTS;
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
    LOG("A7-P2.5 audio fast recovery first=%u segments=%u total=%u\n",
        firstSeq, burst, g_a7p22AudioRetransmits);
    return true;
}

'''
s = replace_between(
    s,
    "static bool _a7p24AudioRetransmitOldest(void)\n",
    "static void _audioTcpReset(const char* why)\n",
    audio_recovery,
    "three-segment audio recovery",
)

s = replace_once(
    s,
    "if (!_a7p24AudioRetransmitOldest()) {",
    "if (!_a7p25AudioFastRecovery()) {",
    "audio recovery call",
)

if s.count("A7-P2.4 NCM RX") != 2 or s.count("A7-P2.4 RX stats") != 1:
    raise SystemExit("P2.5 RX diagnostic marker anchors changed")
s = s.replace("A7-P2.4 NCM RX", "A7-P2.5 NCM RX")
s = s.replace("A7-P2.4 RX stats", "A7-P2.5 RX stats")

p.write_text(s, encoding="utf-8")

p = root / "source/core.c"
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    "A7-P2.4 raw SD logfile initialized BUILD=P24",
    "A7-P2.5 raw SD logfile initialized BUILD=P25",
    "core runtime marker",
)
p.write_text(s, encoding="utf-8")

p = root / "source/ncm/NCMmode.c"
s = p.read_text(encoding="utf-8")
if s.count("A7-P2.4") != 10:
    raise SystemExit(f"P2.5 NCM markers: expected 10, found {s.count('A7-P2.4')}")
s = s.replace("A7-P2.4", "A7-P2.5")
s = s.replace("BUILD=P24", "BUILD=P25")
s = replace_once(
    s,
    "AUDIO_RTO=120 AUDIO_BURST=1",
    "AUDIO_RTO=40 AUDIO_BURST=3",
    "startup tuning marker",
)
p.write_text(s, encoding="utf-8")

print("A7-P2.5 40 ms three-segment audio fast recovery patch applied")

