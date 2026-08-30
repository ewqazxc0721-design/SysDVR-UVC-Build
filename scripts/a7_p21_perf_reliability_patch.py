from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/sysdvr/sysmodule')
p = root / 'source/ncm/ncm_device.c'
s = p.read_text()

# A7-P2.1 targets the two bottlenecks proven by the P2 runtime log:
# 1) every host ACK re-armed Bulk OUT and synchronously flushed a log line to SD;
# 2) every <=1460-byte TCP segment was wrapped in its own NTB + USB IN URB.
# Restore the 16 KiB NCM buffer used by the original P0 design and advertise
# multiple datagrams per NTB so iPadOS can receive aggregated Ethernet frames.
if '#define NCM_NTB_MAX_SIZE    4096u' not in s:
    raise SystemExit('A7-P2.1 NTB size anchor missing')
s = s.replace('#define NCM_NTB_MAX_SIZE    4096u', '#define NCM_NTB_MAX_SIZE    16384u', 1)
if '#define NCM_MAX_DATAGRAMS   1u' not in s:
    raise SystemExit('A7-P2.1 datagram-count anchor missing')
s = s.replace('#define NCM_MAX_DATAGRAMS   1u', '#define NCM_MAX_DATAGRAMS   8u', 1)

# Remove the dominant hot-path log. The P2 test produced >13k of these lines,
# each going through the raw SD logger with Flush. Keep error logs intact.
s = s.replace('        LOG("A7-P1 NCM OUT armed (%u bytes)\\n", (unsigned)sizeof(g_dataOutBuffer));\n',
              '        /* A7-P2.1: hot-path success log intentionally suppressed. */\n', 1)

# The normal DHCP/ARP/ICMP sender shares g_dataInBuffer with the streaming
# aggregator. Flush queued streaming frames before emitting a control NTB.
old = '''static Result _sendNcmEthernetFrame(const u8* frame, u16 frameLen, const char* label)
{
    mutexLock(&g_a7p2TxMutex);
    Result rc = _sendNcmEthernetFrameUnlocked(frame, frameLen, label);
    mutexUnlock(&g_a7p2TxMutex);
    return rc;
}
'''
new = '''static Result _a7p21FlushAggUnlocked(void);

static Result _sendNcmEthernetFrame(const u8* frame, u16 frameLen, const char* label)
{
    mutexLock(&g_a7p2TxMutex);
    Result rc = _a7p21FlushAggUnlocked();
    if (R_SUCCEEDED(rc))
        rc = _sendNcmEthernetFrameUnlocked(frame, frameLen, label);
    mutexUnlock(&g_a7p2TxMutex);
    return rc;
}
'''
if old not in s:
    raise SystemExit('A7-P2.1 control sender wrapper anchor missing')
s = s.replace(old, new, 1)

# Replace the one-frame/one-URB quiet sender with an 8-frame NCM aggregator.
# We reserve 128 bytes for NTH16+NDP16+entries and 4-byte align datagrams.
start = s.find('static Result _sendNcmEthernetFrameQuietUnlocked(const u8* frame, u16 frameLen)\n')
end = s.find('static Result _sendNcmEthernetFrameQuiet(const u8* frame, u16 frameLen)\n', start)
if start < 0 or end < 0:
    raise SystemExit('A7-P2.1 quiet sender boundaries missing')

agg = r'''#define A7P21_AGG_MAX_FRAMES 8u
#define A7P21_AGG_DATA_BASE  128u

static u16 g_a7p21AggOffsets[A7P21_AGG_MAX_FRAMES];
static u16 g_a7p21AggLengths[A7P21_AGG_MAX_FRAMES];
static u16 g_a7p21AggCount = 0;
static u16 g_a7p21AggEnd = A7P21_AGG_DATA_BASE;
static u64 g_a7p21TxNtbs = 0;
static u64 g_a7p21TxFrames = 0;
static u64 g_a7p21TxBytes = 0;

static u16 _a7p21Align4(u16 v)
{
    return (u16)((v + 3u) & ~3u);
}

static void _a7p21ResetAgg(void)
{
    g_a7p21AggCount = 0;
    g_a7p21AggEnd = A7P21_AGG_DATA_BASE;
}

static Result _a7p21SubmitCurrentNtbUnlocked(void)
{
    if (!g_a7p21AggCount) return 0;
    if (!g_ctx.configured || g_ctx.dataAlt != 1 || !g_ctx.dataEndpointIn)
        return MAKERESULT(Module_Libnx, LibnxError_NotInitialized);

    const u16 ndpIndex = (u16)sizeof(Nth16);
    Nth16* nth = (Nth16*)g_dataInBuffer;
    Ndp16* ndp = (Ndp16*)(g_dataInBuffer + ndpIndex);
    Ndp16Entry* entries = (Ndp16Entry*)((u8*)ndp + sizeof(Ndp16));

    memset(g_dataInBuffer, 0, A7P21_AGG_DATA_BASE);
    nth->signature = NTH16_SIGNATURE;
    nth->headerLength = sizeof(Nth16);
    nth->sequence = g_p02TxSequence++;
    nth->blockLength = g_a7p21AggEnd;
    nth->ndpIndex = ndpIndex;

    ndp->signature = NDP16_SIGNATURE_0;
    ndp->length = (u16)(sizeof(Ndp16) + (g_a7p21AggCount + 1u) * sizeof(Ndp16Entry));
    ndp->nextNdpIndex = 0;
    for (u16 i = 0; i < g_a7p21AggCount; i++) {
        entries[i].datagramIndex = g_a7p21AggOffsets[i];
        entries[i].datagramLength = g_a7p21AggLengths[i];
    }
    entries[g_a7p21AggCount].datagramIndex = 0;
    entries[g_a7p21AggCount].datagramLength = 0;

    Result rc = usbDsWaitReady(500000000ULL);
    if (R_FAILED(rc)) return rc;
    eventClear(&g_ctx.dataEndpointIn->CompletionEvent);
    u32 urbId = 0;
    rc = usbDsEndpoint_PostBufferAsync(g_ctx.dataEndpointIn, g_dataInBuffer,
                                       g_a7p21AggEnd, &urbId);
    if (R_FAILED(rc)) return rc;
    rc = eventWait(&g_ctx.dataEndpointIn->CompletionEvent, EP_TIMEOUT_NS);
    if (R_FAILED(rc)) {
        usbDsEndpoint_Cancel(g_ctx.dataEndpointIn);
        return rc;
    }
    eventClear(&g_ctx.dataEndpointIn->CompletionEvent);
    UsbDsReportData rpt;
    rc = usbDsEndpoint_GetReportData(g_ctx.dataEndpointIn, &rpt);
    u32 transferred = 0;
    if (R_SUCCEEDED(rc)) rc = usbDsParseReportData(&rpt, urbId, NULL, &transferred);
    if (R_SUCCEEDED(rc)) {
        g_a7p21TxNtbs++;
        g_a7p21TxFrames += g_a7p21AggCount;
        g_a7p21TxBytes += transferred;
    }
    _a7p21ResetAgg();
    return rc;
}

static Result _a7p21FlushAggUnlocked(void)
{
    return _a7p21SubmitCurrentNtbUnlocked();
}

static Result _sendNcmEthernetFrameQuietUnlocked(const u8* frame, u16 frameLen)
{
    if (!g_ctx.configured || g_ctx.dataAlt != 1 || !g_ctx.dataEndpointIn)
        return MAKERESULT(Module_Libnx, LibnxError_NotInitialized);
    if (!frame || frameLen < 14 || frameLen > 1600)
        return MAKERESULT(Module_Libnx, LibnxError_BadInput);

    u16 off = _a7p21Align4(g_a7p21AggEnd);
    if (g_a7p21AggCount >= A7P21_AGG_MAX_FRAMES ||
        (u32)off + frameLen > sizeof(g_dataInBuffer)) {
        Result rc = _a7p21SubmitCurrentNtbUnlocked();
        if (R_FAILED(rc)) return rc;
        off = _a7p21Align4(g_a7p21AggEnd);
    }

    memcpy(g_dataInBuffer + off, frame, frameLen);
    g_a7p21AggOffsets[g_a7p21AggCount] = off;
    g_a7p21AggLengths[g_a7p21AggCount] = frameLen;
    g_a7p21AggCount++;
    g_a7p21AggEnd = (u16)(off + frameLen);

    if (g_a7p21AggCount >= A7P21_AGG_MAX_FRAMES)
        return _a7p21SubmitCurrentNtbUnlocked();
    return 0;
}

'''
s = s[:start] + agg + s[end:]

# Add a packet-boundary flush helper. Video/audio still retain independent TCP
# sequence/ACK state, but a SysDVR packet is emitted in a small number of larger
# NTBs instead of dozens of 1-frame NTBs.
anchor = '''static Result _sendNcmEthernetFrameQuiet(const u8* frame, u16 frameLen)
{
    mutexLock(&g_a7p2TxMutex);
    Result rc = _sendNcmEthernetFrameQuietUnlocked(frame, frameLen);
    mutexUnlock(&g_a7p2TxMutex);
    return rc;
}

'''
insert = anchor + '''static bool _a7p21FlushQueuedTx(void)
{
    mutexLock(&g_a7p2TxMutex);
    Result rc = _a7p21FlushAggUnlocked();
    mutexUnlock(&g_a7p2TxMutex);
    return R_SUCCEEDED(rc);
}

'''
if anchor not in s:
    raise SystemExit('A7-P2.1 quiet wrapper anchor missing')
s = s.replace(anchor, insert, 1)

# Flush once per complete audio packet instead of once per TCP segment.
def patch_send_function(text: str, signature: str, marker: str) -> str:
    st = text.find(signature)
    if st < 0:
        raise SystemExit(f'A7-P2.1 {marker} function missing')
    en = text.find('\n}\n', st)
    if en < 0:
        raise SystemExit(f'A7-P2.1 {marker} function end missing')
    en += 3
    body = text[st:en]
    if '    return true;\n' not in body:
        raise SystemExit(f'A7-P2.1 {marker} return anchor missing')
    body = body.replace('    return true;\n', '    return _a7p21FlushQueuedTx();\n', 1)
    return text[:st] + body + text[en:]

s = patch_send_function(s,
    'bool NcmDeviceSendAudioPacket(const void* data, u32 len)\n', 'audio-send')
s = patch_send_function(s,
    'bool NcmDeviceSendVideoPacket(const void* data, u32 len)\n', 'video-send')

# The P2 log showed the audio ACK window hitting its limit while the video
# thread was busy. Do not spin for two seconds without giving the main NCM
# request loop a chance to run: shorten the audio stall sleep and make timeout
# diagnostics explicit. We intentionally avoid concurrent RX polling here;
# NcmDeviceProcessRequests remains single-threaded on the video/NCM worker.
s = s.replace('''            svcSleepThread(1000000ULL);
        }

        u16 chunk = remaining > A7P1_TCP_MSS ? A7P1_TCP_MSS : (u16)remaining;
''', '''            svcSleepThread(250000ULL);
        }

        u16 chunk = remaining > A7P1_TCP_MSS ? A7P1_TCP_MSS : (u16)remaining;
''', 1)

# Reset queued NTB state whenever the transport drops so stale frames can never
# leak into a subsequent TCP session.
s = s.replace('_tcpReset("USB disconnected");\n            _audioTcpReset("USB disconnected");',
              '_tcpReset("USB disconnected");\n            _audioTcpReset("USB disconnected");\n            _a7p21ResetAgg();', 1)
s = s.replace('_tcpReset("NCM exit");\n    _audioTcpReset("NCM exit");',
              '_tcpReset("NCM exit");\n    _audioTcpReset("NCM exit");\n    _a7p21ResetAgg();', 1)

# Version markers used in the new runtime diagnostics.
s = s.replace('A7-P2 NCM ready:', 'A7-P2.1 NCM ready:')
p.write_text(s)

# Version the mode banner and reduce periodic packet logs from every 120 packets
# to every 600 packets. This preserves useful proof without making SD I/O part
# of the real-time data path.
p = root / 'source/ncm/NCMmode.c'
s = p.read_text()
s = s.replace('A7-P2 SysDVR 6.3 + USB-NCM A/V mode starting',
              'A7-P2.1 SysDVR 6.3 + USB-NCM aggregated A/V mode starting')
s = s.replace('(packets % 120u) == 0', '(packets % 600u) == 0')
s = s.replace('(frames % 120u) == 0', '(frames % 600u) == 0')
p.write_text(s)

print('A7-P2.1 performance/reliability patch applied')
