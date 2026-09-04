from pathlib import Path
import sys


root = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/sysdvr/sysmodule")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"P2.4 {label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, label: str) -> str:
    begin = text.find(start)
    if begin < 0:
        raise SystemExit(f"P2.4 {label}: start anchor missing")
    finish = text.find(end, begin + len(start))
    if finish < 0:
        raise SystemExit(f"P2.4 {label}: end anchor missing")
    return text[:begin] + replacement + text[finish:]


p = root / "source/ncm/ncm_device.c"
s = p.read_text(encoding="utf-8")

s = replace_once(
    s,
    "#define A7P221_RETX_BURST_SEGMENTS 8u\n#define A7P22_RETX_MAX_RETRIES 8u",
    "#define A7P221_RETX_BURST_SEGMENTS 8u\n"
    "#define A7P24_AUDIO_MAX_INFLIGHT (6u * A7P1_TCP_MSS)\n"
    "#define A7P24_AUDIO_RETX_AFTER_LOOPS 120\n"
    "#define A7P22_RETX_MAX_RETRIES 8u",
    "audio flow-control constants",
)

s = replace_once(
    s,
    "static Mutex g_a7p2TxMutex;",
    "static Mutex g_a7p2TxMutex;\nstatic Mutex g_a7p24RxMutex;",
    "RX serialization mutex",
)

audio_retx = r'''static bool _a7p24AudioRetransmitOldest(void)
{
    _a7p22AudioDiscardAcked();
    if (!g_a7p22AudioCount) return true;
    A7P22UnackedSegment* seg = &g_a7p22AudioRing[g_a7p22AudioHead];
    if (seg->retries >= A7P22_RETX_MAX_RETRIES) return false;
    Result rc = _audioTcpSendSegment(seg->seq, g_audioTcpClientNextSeq,
                                     TCP_FLAG_ACK | TCP_FLAG_PSH,
                                     seg->payload, seg->len);
    if (R_FAILED(rc)) return false;
    seg->retries++;
    g_a7p22AudioRetransmits++;
    if (!_a7p21FlushQueuedTx()) return false;
    LOG("A7-P2.4 audio RTO oldest seq=%u retry=%u total=%u\n",
        seg->seq, seg->retries, g_a7p22AudioRetransmits);
    return true;
}

'''
s = replace_between(
    s,
    "static bool _a7p221AudioRetransmitBurst(void)\n",
    "static void _audioTcpReset(const char* why)\n",
    audio_retx,
    "audio oldest-only retransmission",
)

audio_start = s.find("bool NcmDeviceSendAudioPacket(const void* data, u32 len)\n")
audio_end = s.find("void NcmDeviceAbortVideoSession(void)\n", audio_start)
if audio_start < 0 or audio_end < 0:
    raise SystemExit("P2.4 audio sender function anchors missing")
audio = s[audio_start:audio_end]
audio = replace_once(
    audio,
    "    while (remaining) {\n        int loops = 0;",
    "    while (remaining) {\n        NcmDeviceProcessRequests();\n        int loops = 0;",
    "audio ACK pump before send",
)
audio = replace_once(
    audio,
    "                A7P1_TCP_MAX_INFLIGHT) break;\n            if (!NcmDeviceAudioSessionReady()) return false;\n            loops++;\n            if ((loops % A7P22_RETX_AFTER_LOOPS) == 0) {\n                if (!_a7p221AudioRetransmitBurst()) {",
    "                A7P24_AUDIO_MAX_INFLIGHT) break;\n"
    "            NcmDeviceProcessRequests();\n"
    "            if (!NcmDeviceAudioSessionReady()) return false;\n"
    "            _a7p22AudioDiscardAcked();\n"
    "            loops++;\n"
    "            if ((loops % A7P24_AUDIO_RETX_AFTER_LOOPS) == 0) {\n"
    "                if (!_a7p24AudioRetransmitOldest()) {",
    "audio low-latency ACK wait",
)
s = s[:audio_start] + audio + s[audio_end:]

rx_parser = r'''static void _handleNcmEthernetFrame(const u8* eth, u16 ethLen)
{
    if (!eth || ethLen < 14) return;
    const u16 etherType = _be16(eth + 12);
    LOG_V("A7-P1 Ethernet frame: len=%u type=0x%04x src=%02x:%02x:%02x:%02x:%02x:%02x dst=%02x:%02x:%02x:%02x:%02x:%02x\n",
        ethLen, etherType,
        eth[6], eth[7], eth[8], eth[9], eth[10], eth[11],
        eth[0], eth[1], eth[2], eth[3], eth[4], eth[5]);

    if (etherType == 0x0806) {
        _handleArp(eth, ethLen);
        return;
    }
    if (etherType != 0x0800 || ethLen < 14 + 20) return;

    const u8* ip = eth + 14;
    const u8 ihl = (ip[0] & 0x0F) * 4;
    if ((ip[0] >> 4) != 4 || ihl < 20 || ethLen < (u16)(14 + ihl)) return;
    LOG_V("A7-P1 IPv4 proto=%u src=%u.%u.%u.%u dst=%u.%u.%u.%u\n",
        ip[9], ip[12], ip[13], ip[14], ip[15], ip[16], ip[17], ip[18], ip[19]);
    if (ip[9] == 17 && ethLen >= (u16)(14 + ihl + 8)) {
        const u8* udp = ip + ihl;
        LOG_V("A7-P1 IPv4 UDP: %u -> %u\n", _be16(udp), _be16(udp + 2));
        (void)udp;
        _handleDhcp(eth, ethLen, ip, ihl);
    } else if (ip[9] == 1) {
        _handleIcmp(eth, ethLen, ip, ihl);
    } else if (ip[9] == 6) {
        _handleTcp(eth, ethLen, ip, ihl);
    }
}

static void _logNcmRx(const u8* p, u32 len)
{
    if (len < sizeof(Nth16)) {
        LOG("A7-P2.4 NCM RX short: %u bytes\n", len);
        return;
    }
    const Nth16* nth = (const Nth16*)p;
    LOG_V("A7-P2.4 NCM RX: bytes=%u sig=0x%08x hdr=%u seq=%u block=%u ndp=%u\n",
        len, nth->signature, nth->headerLength, nth->sequence, nth->blockLength, nth->ndpIndex);

    if (nth->signature != NTH16_SIGNATURE || nth->headerLength < sizeof(Nth16) ||
        nth->blockLength < nth->headerLength || nth->blockLength > len) return;

    const u32 blockLen = nth->blockLength;
    u16 ndpIndex = nth->ndpIndex;
    u32 datagrams = 0;
    u32 ndpCount = 0;

    while (ndpIndex && ndpCount < 4u && datagrams < NCM_MAX_DATAGRAMS) {
        if ((u32)ndpIndex + sizeof(Ndp16) + sizeof(Ndp16Entry) > blockLen) break;
        const Ndp16* ndp = (const Ndp16*)(p + ndpIndex);
        if (ndp->signature != NDP16_SIGNATURE_0 && ndp->signature != NDP16_SIGNATURE_1) break;
        if (ndp->length < sizeof(Ndp16) + sizeof(Ndp16Entry) ||
            (u32)ndpIndex + ndp->length > blockLen) break;

        const u32 entryCount = (ndp->length - sizeof(Ndp16)) / sizeof(Ndp16Entry);
        const Ndp16Entry* entries = (const Ndp16Entry*)((const u8*)ndp + sizeof(Ndp16));
        for (u32 i = 0; i < entryCount && datagrams < NCM_MAX_DATAGRAMS; i++) {
            const u16 frameOffset = entries[i].datagramIndex;
            const u16 frameLength = entries[i].datagramLength;
            if (!frameOffset && !frameLength) break;
            if (!frameOffset || frameLength < 14 ||
                (u32)frameOffset + frameLength > blockLen) continue;
            _handleNcmEthernetFrame(p + frameOffset, frameLength);
            datagrams++;
        }

        ndpCount++;
        if (!ndp->nextNdpIndex || ndp->nextNdpIndex == ndpIndex) break;
        ndpIndex = ndp->nextNdpIndex;
    }

    static u64 rxNtbs = 0;
    static u64 rxDatagrams = 0;
    static u64 rxMultiNtbs = 0;
    rxNtbs++;
    rxDatagrams += datagrams;
    if (datagrams > 1) rxMultiNtbs++;
    if ((rxNtbs % 512u) == 0)
        LOG("A7-P2.4 RX stats ntb=%lu datagrams=%lu multi=%lu last=%u\n",
            rxNtbs, rxDatagrams, rxMultiNtbs, datagrams);
}

'''
s = replace_between(
    s,
    "static void _logNcmRx(const u8* p, u32 len)\n",
    "static void _pollOut(void)\n",
    rx_parser,
    "multi-datagram NCM RX parser",
)

s = replace_once(
    s,
    "void NcmDeviceProcessRequests(void)\n{",
    "static void _ncmDeviceProcessRequestsUnlocked(void)\n{",
    "request processor split",
)
s = replace_once(
    s,
    "void NcmDeviceExit(void)\n{",
    "void NcmDeviceProcessRequests(void)\n"
    "{\n"
    "    mutexLock(&g_a7p24RxMutex);\n"
    "    _ncmDeviceProcessRequestsUnlocked();\n"
    "    mutexUnlock(&g_a7p24RxMutex);\n"
    "}\n\n"
    "void NcmDeviceExit(void)\n{",
    "serialized request processor wrapper",
)

p.write_text(s, encoding="utf-8")

p = root / "source/core.c"
s = p.read_text(encoding="utf-8")
s = replace_once(
    s,
    "A7-P2.3.1 raw SD logfile initialized BUILD=P231",
    "A7-P2.4 raw SD logfile initialized BUILD=P24",
    "core runtime marker",
)
p.write_text(s, encoding="utf-8")

p = root / "source/ncm/NCMmode.c"
s = p.read_text(encoding="utf-8")
if s.count("A7-P2.3.1") != 10:
    raise SystemExit(f"P2.4 NCM markers: expected 10, found {s.count('A7-P2.3.1')}")
s = s.replace("A7-P2.3.1", "A7-P2.4")
s = s.replace("BUILD=P231", "BUILD=P24")
s = replace_once(
    s,
    "NTB=16384 DATAGRAMS=8 RING=32 RTO_LOOPS=25 BURST=8",
    "NTB=16384 DATAGRAMS=8 RX_MULTI=1 AUDIO_INFLIGHT=6 AUDIO_RTO=120 AUDIO_BURST=1",
    "startup tuning marker",
)
p.write_text(s, encoding="utf-8")

print("A7-P2.4 multi-datagram RX and low-latency audio TCP patch applied")

