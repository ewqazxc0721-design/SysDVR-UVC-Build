from pathlib import Path
import sys


root = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/sysdvr/sysmodule")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"P2.2 {label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def replace_function(text: str, signature: str, replacement: str, label: str) -> str:
    start = text.find(signature)
    if start < 0:
        raise SystemExit(f"P2.2 {label}: function missing")
    end = text.find("\n}\n", start)
    if end < 0:
        raise SystemExit(f"P2.2 {label}: function end missing")
    end += 3
    return text[:start] + replacement + text[end:]


p = root / "source/ncm/ncm_device.c"
s = p.read_text()

# P2.1.2 proves that control traffic now flushes correctly.  Its runtime log
# then shows both streams filling the 24*MSS window and timing out because the
# minimal TCP implementation discards transmitted payloads.  Retain a bounded
# copy of every unacknowledged media segment, retire entries with cumulative
# ACKs, and retransmit the oldest segment while a full window is stalled.
s = replace_once(
    s,
    "#define A7P1_TCP_ACK_WAIT_LOOPS 2000\n",
    "#define A7P1_TCP_ACK_WAIT_LOOPS 2000\n"
    "#define A7P22_RETX_RING_SEGMENTS 32u\n"
    "#define A7P22_RETX_AFTER_LOOPS 100\n"
    "#define A7P22_RETX_MAX_RETRIES 8u\n",
    "retransmit constants",
)

video_insert_anchor = "static void _tcpReset(const char* why)\n"
video_ring = r'''typedef struct {
    u32 seq;
    u16 len;
    u8 retries;
    u8 payload[A7P1_TCP_MSS];
} A7P22UnackedSegment;

static A7P22UnackedSegment g_a7p22VideoRing[A7P22_RETX_RING_SEGMENTS];
static u32 g_a7p22VideoHead = 0;
static u32 g_a7p22VideoCount = 0;
static u32 g_a7p22VideoRetransmits = 0;

static void _a7p22VideoRingReset(void)
{
    g_a7p22VideoHead = 0;
    g_a7p22VideoCount = 0;
    g_a7p22VideoRetransmits = 0;
}

static void _a7p22VideoDiscardAcked(void)
{
    while (g_a7p22VideoCount) {
        A7P22UnackedSegment* seg = &g_a7p22VideoRing[g_a7p22VideoHead];
        if (!_seqGe(g_tcpServerAckedSeq, seg->seq + seg->len)) break;
        g_a7p22VideoHead = (g_a7p22VideoHead + 1u) % A7P22_RETX_RING_SEGMENTS;
        g_a7p22VideoCount--;
    }
}

static bool _a7p22VideoRemember(u32 seq, const u8* payload, u16 len)
{
    _a7p22VideoDiscardAcked();
    if (!len || len > A7P1_TCP_MSS ||
        g_a7p22VideoCount >= A7P22_RETX_RING_SEGMENTS) return false;
    u32 index = (g_a7p22VideoHead + g_a7p22VideoCount) % A7P22_RETX_RING_SEGMENTS;
    A7P22UnackedSegment* seg = &g_a7p22VideoRing[index];
    seg->seq = seq;
    seg->len = len;
    seg->retries = 0;
    memcpy(seg->payload, payload, len);
    g_a7p22VideoCount++;
    return true;
}

static bool _a7p22VideoRetransmitOldest(void)
{
    _a7p22VideoDiscardAcked();
    if (!g_a7p22VideoCount) return true;
    A7P22UnackedSegment* seg = &g_a7p22VideoRing[g_a7p22VideoHead];
    if (seg->retries >= A7P22_RETX_MAX_RETRIES) return false;
    Result rc = _tcpSendSegment(seg->seq, g_tcpClientNextSeq,
                                TCP_FLAG_ACK | TCP_FLAG_PSH,
                                seg->payload, seg->len);
    if (R_FAILED(rc) || !_a7p21FlushQueuedTx()) return false;
    seg->retries++;
    g_a7p22VideoRetransmits++;
    LOG("A7-P2.2 video RTO retransmit seq=%u len=%u retry=%u total=%u\n",
        seg->seq, seg->len, seg->retries, g_a7p22VideoRetransmits);
    return true;
}

'''
if video_insert_anchor not in s:
    raise SystemExit("P2.2 video ring insertion anchor missing")
s = s.replace(video_insert_anchor, video_ring + video_insert_anchor, 1)

s = replace_once(
    s,
    "    g_tcpState = A7Tcp_Closed;\n",
    "    _a7p22VideoRingReset();\n"
    "    g_tcpState = A7Tcp_Closed;\n",
    "video reset",
)

video_send = r'''bool NcmDeviceSendVideoPacket(const void* data, u32 len)
{
    if (!NcmDeviceVideoSessionReady() || !data || !len) return false;
    const u8* p = (const u8*)data;
    u32 remaining = len;

    while (remaining) {
        int loops = 0;
        for (;;) {
            _a7p22VideoDiscardAcked();
            if ((u32)(g_tcpServerNextSeq - g_tcpServerAckedSeq) <
                A7P1_TCP_MAX_INFLIGHT) break;
            NcmDeviceProcessRequests();
            if (!NcmDeviceVideoSessionReady()) return false;
            _a7p22VideoDiscardAcked();
            loops++;
            if ((loops % A7P22_RETX_AFTER_LOOPS) == 0) {
                if (!_a7p22VideoRetransmitOldest()) {
                    LOG("A7-P2.2 video retransmit exhausted inflight=%u queued=%u\n",
                        (unsigned)(g_tcpServerNextSeq - g_tcpServerAckedSeq),
                        g_a7p22VideoCount);
                    _tcpReset("retransmit exhausted");
                    return false;
                }
            }
            if (loops >= A7P1_TCP_ACK_WAIT_LOOPS) {
                LOG("A7-P2.2 video TCP ACK timeout inflight=%u queued=%u retx=%u\n",
                    (unsigned)(g_tcpServerNextSeq - g_tcpServerAckedSeq),
                    g_a7p22VideoCount, g_a7p22VideoRetransmits);
                _tcpReset("ACK timeout");
                return false;
            }
            svcSleepThread(1000000ULL);
        }

        u16 chunk = remaining > A7P1_TCP_MSS ? A7P1_TCP_MSS : (u16)remaining;
        u32 seq = g_tcpServerNextSeq;
        Result rc = _tcpSendSegment(seq, g_tcpClientNextSeq,
                                    TCP_FLAG_ACK | TCP_FLAG_PSH, p, chunk);
        if (R_FAILED(rc)) {
            LOG("A7-P2.2 TCP video segment TX failed rc=0x%x\n", rc);
            _tcpReset("video TX failed");
            return false;
        }
        if (!_a7p22VideoRemember(seq, p, chunk)) {
            LOG("A7-P2.2 video retransmit ring full count=%u\n", g_a7p22VideoCount);
            _tcpReset("video retransmit ring full");
            return false;
        }
        g_tcpServerNextSeq += chunk;
        p += chunk;
        remaining -= chunk;
        NcmDeviceProcessRequests();
    }
    return _a7p21FlushQueuedTx();
}
'''
s = replace_function(
    s,
    "bool NcmDeviceSendVideoPacket(const void* data, u32 len)\n",
    video_send,
    "video send",
)

audio_insert_anchor = "static void _audioTcpReset(const char* why)\n"
audio_ring = r'''static A7P22UnackedSegment g_a7p22AudioRing[A7P22_RETX_RING_SEGMENTS];
static u32 g_a7p22AudioHead = 0;
static u32 g_a7p22AudioCount = 0;
static u32 g_a7p22AudioRetransmits = 0;

static void _a7p22AudioRingReset(void)
{
    g_a7p22AudioHead = 0;
    g_a7p22AudioCount = 0;
    g_a7p22AudioRetransmits = 0;
}

static void _a7p22AudioDiscardAcked(void)
{
    while (g_a7p22AudioCount) {
        A7P22UnackedSegment* seg = &g_a7p22AudioRing[g_a7p22AudioHead];
        if (!_seqGe(g_audioTcpServerAckedSeq, seg->seq + seg->len)) break;
        g_a7p22AudioHead = (g_a7p22AudioHead + 1u) % A7P22_RETX_RING_SEGMENTS;
        g_a7p22AudioCount--;
    }
}

static bool _a7p22AudioRemember(u32 seq, const u8* payload, u16 len)
{
    _a7p22AudioDiscardAcked();
    if (!len || len > A7P1_TCP_MSS ||
        g_a7p22AudioCount >= A7P22_RETX_RING_SEGMENTS) return false;
    u32 index = (g_a7p22AudioHead + g_a7p22AudioCount) % A7P22_RETX_RING_SEGMENTS;
    A7P22UnackedSegment* seg = &g_a7p22AudioRing[index];
    seg->seq = seq;
    seg->len = len;
    seg->retries = 0;
    memcpy(seg->payload, payload, len);
    g_a7p22AudioCount++;
    return true;
}

static bool _a7p22AudioRetransmitOldest(void)
{
    _a7p22AudioDiscardAcked();
    if (!g_a7p22AudioCount) return true;
    A7P22UnackedSegment* seg = &g_a7p22AudioRing[g_a7p22AudioHead];
    if (seg->retries >= A7P22_RETX_MAX_RETRIES) return false;
    Result rc = _audioTcpSendSegment(seg->seq, g_audioTcpClientNextSeq,
                                     TCP_FLAG_ACK | TCP_FLAG_PSH,
                                     seg->payload, seg->len);
    if (R_FAILED(rc) || !_a7p21FlushQueuedTx()) return false;
    seg->retries++;
    g_a7p22AudioRetransmits++;
    LOG("A7-P2.2 audio RTO retransmit seq=%u len=%u retry=%u total=%u\n",
        seg->seq, seg->len, seg->retries, g_a7p22AudioRetransmits);
    return true;
}

'''
if audio_insert_anchor not in s:
    raise SystemExit("P2.2 audio ring insertion anchor missing")
s = s.replace(audio_insert_anchor, audio_ring + audio_insert_anchor, 1)

s = replace_once(
    s,
    "    g_audioTcpState = A7Tcp_Closed;\n",
    "    _a7p22AudioRingReset();\n"
    "    g_audioTcpState = A7Tcp_Closed;\n",
    "audio reset",
)

audio_send = r'''bool NcmDeviceSendAudioPacket(const void* data, u32 len)
{
    if (!NcmDeviceAudioSessionReady() || !data || !len) return false;
    const u8* p = (const u8*)data;
    u32 remaining = len;

    while (remaining) {
        int loops = 0;
        for (;;) {
            _a7p22AudioDiscardAcked();
            if ((u32)(g_audioTcpServerNextSeq - g_audioTcpServerAckedSeq) <
                A7P1_TCP_MAX_INFLIGHT) break;
            if (!NcmDeviceAudioSessionReady()) return false;
            loops++;
            if ((loops % A7P22_RETX_AFTER_LOOPS) == 0) {
                if (!_a7p22AudioRetransmitOldest()) {
                    LOG("A7-P2.2 audio retransmit exhausted inflight=%u queued=%u\n",
                        (unsigned)(g_audioTcpServerNextSeq - g_audioTcpServerAckedSeq),
                        g_a7p22AudioCount);
                    _audioTcpReset("retransmit exhausted");
                    return false;
                }
            }
            if (loops >= A7P1_TCP_ACK_WAIT_LOOPS) {
                LOG("A7-P2.2 audio TCP ACK timeout inflight=%u queued=%u retx=%u\n",
                    (unsigned)(g_audioTcpServerNextSeq - g_audioTcpServerAckedSeq),
                    g_a7p22AudioCount, g_a7p22AudioRetransmits);
                _audioTcpReset("ACK timeout");
                return false;
            }
            svcSleepThread(1000000ULL);
        }

        u16 chunk = remaining > A7P1_TCP_MSS ? A7P1_TCP_MSS : (u16)remaining;
        u32 seq = g_audioTcpServerNextSeq;
        Result rc = _audioTcpSendSegment(seq, g_audioTcpClientNextSeq,
                                         TCP_FLAG_ACK | TCP_FLAG_PSH,
                                         p, chunk);
        if (R_FAILED(rc)) {
            LOG("A7-P2.2 audio TCP segment TX failed rc=0x%x\n", rc);
            _audioTcpReset("audio TX failed");
            return false;
        }
        if (!_a7p22AudioRemember(seq, p, chunk)) {
            LOG("A7-P2.2 audio retransmit ring full count=%u\n", g_a7p22AudioCount);
            _audioTcpReset("audio retransmit ring full");
            return false;
        }
        g_audioTcpServerNextSeq += chunk;
        p += chunk;
        remaining -= chunk;
    }
    return _a7p21FlushQueuedTx();
}
'''
s = replace_function(
    s,
    "bool NcmDeviceSendAudioPacket(const void* data, u32 len)\n",
    audio_send,
    "audio send",
)

p.write_text(s)


p = root / "source/core.c"
s = p.read_text()
s = replace_once(
    s,
    "A7-P2.1.2 raw SD logfile initialized BUILD=P212",
    "A7-P2.2 raw SD logfile initialized BUILD=P22",
    "core marker",
)
p.write_text(s)


p = root / "source/ncm/NCMmode.c"
s = p.read_text()
s = replace_once(
    s,
    "A7-P2.1.2 SysDVR 6.3 + USB-NCM control-flush A/V mode starting BUILD=P212 NTB=16384 DATAGRAMS=8",
    "A7-P2.2 SysDVR 6.3 + USB-NCM retransmit A/V mode starting BUILD=P22 NTB=16384 DATAGRAMS=8 RING=32 RTO_LOOPS=100",
    "mode marker",
)
s = replace_once(
    s,
    "A7-P2.1.2 NCM ready BUILD=P212: DHCP",
    "A7-P2.2 NCM ready BUILD=P22: DHCP",
    "ready marker",
)
p.write_text(s)

print("A7-P2.2 bounded TCP retransmit patch applied")

