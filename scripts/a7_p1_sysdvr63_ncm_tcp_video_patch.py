from pathlib import Path
import shutil
import sys

if len(sys.argv) < 3:
    raise SystemExit('usage: patch.py <sysdvr-sysmodule-root> <ncm-reference-root>')

root = Path(sys.argv[1])
ref = Path(sys.argv[2])

# -----------------------------------------------------------------------------
# Bring the already iPad-validated CDC-NCM implementation (A7-P0.2) into the
# official SysDVR 6.3 sysmodule tree. The P0/P0.1/P0.2 scripts are applied to
# the reference clone by the workflow before this patch runs.
# -----------------------------------------------------------------------------
ncm_dir = root / 'source/ncm'
ncm_dir.mkdir(parents=True, exist_ok=True)
shutil.copy2(ref / 'source/usbnet/ncm_device.c', ncm_dir / 'ncm_device.c')
shutil.copy2(ref / 'source/usbnet/ncm_device.h', ncm_dir / 'ncm_device.h')

p = ncm_dir / 'ncm_device.c'
s = p.read_text()

# Rebrand the diagnostics and hook the official SysDVR protocol implementation.
s = s.replace('A7-P0.2', 'A7-P1')
anchor = '#include "../core.h"\n'
if anchor not in s:
    raise SystemExit('ncm core include anchor missing')
s = s.replace(anchor, anchor + '#include "../capture.h"\n#include "../modes/proto.h"\n', 1)

# Per-frame NCM diagnostics are too expensive once TCP video starts. Keep them
# available under VERBOSE_LOGGING, while retaining DHCP/ARP/TCP state messages.
s = s.replace('LOG("A7-P1 NCM RX:', 'LOG_V("A7-P1 NCM RX:')
s = s.replace('LOG("A7-P1 Ethernet frame:', 'LOG_V("A7-P1 Ethernet frame:')
s = s.replace('LOG("A7-P1 IPv4 proto=', 'LOG_V("A7-P1 IPv4 proto=')
s = s.replace('LOG("A7-P1 IPv4 UDP:', 'LOG_V("A7-P1 IPv4 UDP:')

# -----------------------------------------------------------------------------
# Add a compact single-client TCP/IPv4 server for SysDVR video port 9911.
# This is intentionally a point-to-point USB stack, not a replacement for the
# official BSD TCP mode. The official TCP/RTSP/original-USB modes remain intact
# and are used whenever /config/sysdvr/ncm is absent.
# -----------------------------------------------------------------------------
insert_at = s.find('static void _logNcmRx(const u8* p, u32 len)')
if insert_at < 0:
    raise SystemExit('NCM RX handler anchor missing')

tcp_code = r'''
#define A7P1_TCP_VIDEO_PORT 9911
#define A7P1_TCP_MSS 1460u
#define A7P1_TCP_MAX_INFLIGHT (24u * A7P1_TCP_MSS)
#define A7P1_TCP_ACK_WAIT_LOOPS 2000

#define TCP_FLAG_FIN 0x01
#define TCP_FLAG_SYN 0x02
#define TCP_FLAG_RST 0x04
#define TCP_FLAG_PSH 0x08
#define TCP_FLAG_ACK 0x10

typedef enum {
    A7Tcp_Closed = 0,
    A7Tcp_SynReceived,
    A7Tcp_Established,
} A7TcpState;

static A7TcpState g_tcpState = A7Tcp_Closed;
static u8 g_tcpClientMac[6];
static u8 g_tcpClientIp[4];
static u16 g_tcpClientPort = 0;
static u32 g_tcpClientNextSeq = 0;
static u32 g_tcpServerNextSeq = 0;
static u32 g_tcpServerAckedSeq = 0;
static bool g_tcpHelloSent = false;
static bool g_tcpProtoReady = false;
static u8 g_tcpHandshake[PROTO_HANDSHAKE_SIZE];
static u32 g_tcpHandshakeUsed = 0;

static u32 _be32(const u8* p)
{
    return ((u32)p[0] << 24) | ((u32)p[1] << 16) | ((u32)p[2] << 8) | p[3];
}

static bool _seqGe(u32 a, u32 b)
{
    return (s32)(a - b) >= 0;
}

static u32 _checksumAdd(const u8* data, size_t len, u32 sum)
{
    while (len >= 2) {
        sum += ((u16)data[0] << 8) | data[1];
        data += 2;
        len -= 2;
    }
    if (len) sum += (u16)data[0] << 8;
    return sum;
}

static u16 _checksumFinish(u32 sum)
{
    while (sum >> 16) sum = (sum & 0xFFFFu) + (sum >> 16);
    return (u16)(~sum);
}

static u16 _tcpChecksum(const u8* ip, const u8* tcp, size_t tcpLen)
{
    u32 sum = 0;
    sum = _checksumAdd(ip + 12, 8, sum);
    sum += 6; // protocol
    sum += (u16)tcpLen;
    sum = _checksumAdd(tcp, tcpLen, sum);
    return _checksumFinish(sum);
}

// Quiet variant of the validated P0.2 NTB sender. Streaming thousands of TCP
// segments through the normal diagnostic sender would make FILE_LOGGING the
// bottleneck and distort throughput results.
static Result _sendNcmEthernetFrameQuiet(const u8* frame, u16 frameLen)
{
    const u16 ndpIndex = (u16)sizeof(Nth16);
    const u16 dataIndex = 28;
    const u32 blockLength = (u32)dataIndex + frameLen;
    if (!g_ctx.configured || g_ctx.dataAlt != 1 || !g_ctx.dataEndpointIn)
        return MAKERESULT(Module_Libnx, LibnxError_NotInitialized);
    if (frameLen < 14 || blockLength > sizeof(g_dataInBuffer))
        return MAKERESULT(Module_Libnx, LibnxError_BadInput);

    memset(g_dataInBuffer, 0, blockLength);
    Nth16* nth = (Nth16*)g_dataInBuffer;
    nth->signature = NTH16_SIGNATURE;
    nth->headerLength = sizeof(Nth16);
    nth->sequence = g_p02TxSequence++;
    nth->blockLength = (u16)blockLength;
    nth->ndpIndex = ndpIndex;

    Ndp16* ndp = (Ndp16*)(g_dataInBuffer + ndpIndex);
    ndp->signature = NDP16_SIGNATURE_0;
    ndp->length = sizeof(Ndp16) + 2 * sizeof(Ndp16Entry);
    ndp->nextNdpIndex = 0;
    Ndp16Entry* entries = (Ndp16Entry*)((u8*)ndp + sizeof(Ndp16));
    entries[0].datagramIndex = dataIndex;
    entries[0].datagramLength = frameLen;
    entries[1].datagramIndex = 0;
    entries[1].datagramLength = 0;
    memcpy(g_dataInBuffer + dataIndex, frame, frameLen);

    Result rc = usbDsWaitReady(500000000ULL);
    if (R_FAILED(rc)) return rc;
    eventClear(&g_ctx.dataEndpointIn->CompletionEvent);
    u32 urbId = 0;
    rc = usbDsEndpoint_PostBufferAsync(g_ctx.dataEndpointIn, g_dataInBuffer, blockLength, &urbId);
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
    return rc;
}

static Result _tcpSendSegment(u32 seq, u32 ack, u8 flags, const void* payload, u16 payloadLen)
{
    if (g_tcpState == A7Tcp_Closed || !g_tcpClientPort) return MAKERESULT(Module_Libnx, LibnxError_NotInitialized);
    if (payloadLen > A7P1_TCP_MSS) return MAKERESULT(Module_Libnx, LibnxError_BadInput);

    const u16 tcpLen = 20 + payloadLen;
    const u16 ipLen = 20 + tcpLen;
    const u16 frameLen = 14 + ipLen;
    u8* f = g_p02Frame;
    memset(f, 0, frameLen);

    memcpy(f, g_tcpClientMac, 6);
    memcpy(f + 6, g_p02ServerMac, 6);
    _putBe16(f + 12, 0x0800);

    u8* ip = f + 14;
    ip[0] = 0x45;
    _putBe16(ip + 2, ipLen);
    _putBe16(ip + 4, g_p02IpId++);
    ip[8] = 64;
    ip[9] = 6;
    memcpy(ip + 12, g_p02ServerIp, 4);
    memcpy(ip + 16, g_tcpClientIp, 4);
    _putBe16(ip + 10, _checksum16(ip, 20));

    u8* tcp = ip + 20;
    _putBe16(tcp + 0, A7P1_TCP_VIDEO_PORT);
    _putBe16(tcp + 2, g_tcpClientPort);
    _putBe32(tcp + 4, seq);
    _putBe32(tcp + 8, ack);
    tcp[12] = 5u << 4;
    tcp[13] = flags;
    _putBe16(tcp + 14, 65535);
    if (payloadLen) memcpy(tcp + 20, payload, payloadLen);
    _putBe16(tcp + 16, _tcpChecksum(ip, tcp, tcpLen));
    return _sendNcmEthernetFrameQuiet(f, frameLen);
}

static void _tcpReset(const char* why)
{
    if (g_tcpProtoReady) ProtoClientGlobalStateDisconnected();
    if (g_tcpState != A7Tcp_Closed) LOG("A7-P1 TCP reset: %s\n", why ? why : "unknown");
    g_tcpState = A7Tcp_Closed;
    memset(g_tcpClientMac, 0, sizeof(g_tcpClientMac));
    memset(g_tcpClientIp, 0, sizeof(g_tcpClientIp));
    g_tcpClientPort = 0;
    g_tcpClientNextSeq = 0;
    g_tcpServerNextSeq = 0;
    g_tcpServerAckedSeq = 0;
    g_tcpHelloSent = false;
    g_tcpProtoReady = false;
    g_tcpHandshakeUsed = 0;
}

static bool _tcpSendControlPayload(const void* data, u16 len)
{
    Result rc = _tcpSendSegment(g_tcpServerNextSeq, g_tcpClientNextSeq,
                                TCP_FLAG_ACK | TCP_FLAG_PSH, data, len);
    if (R_FAILED(rc)) return false;
    g_tcpServerNextSeq += len;
    return true;
}

static void _tcpMaybeSendHello(void)
{
    if (g_tcpState != A7Tcp_Established || g_tcpHelloSent) return;
    static const char hello[] = PROTO_HANDSHAKE_HELLO;
    if (_tcpSendControlPayload(hello, sizeof(hello))) {
        g_tcpHelloSent = true;
        LOG("A7-P1 TCP 9911 connected; sent SysDVR protocol hello %s\n", SYSDVR_PROTOCOL_VERSION);
    }
}

static void _tcpConsumeHandshake(const u8* payload, u16 len)
{
    if (g_tcpProtoReady || !g_tcpHelloSent || !len) return;
    u32 room = PROTO_HANDSHAKE_SIZE - g_tcpHandshakeUsed;
    u32 take = len < room ? len : room;
    memcpy(g_tcpHandshake + g_tcpHandshakeUsed, payload, take);
    g_tcpHandshakeUsed += take;
    if (g_tcpHandshakeUsed != PROTO_HANDSHAKE_SIZE) return;

    ProtoParsedHandshake parsed = ProtoHandshake(ProtoHandshakeAccept_Video,
                                                  g_tcpHandshake, PROTO_HANDSHAKE_SIZE);
    if (!_tcpSendControlPayload(&parsed.Result, sizeof(parsed.Result))) {
        _tcpReset("handshake response TX failed");
        return;
    }
    if (parsed.Result.Code == Handshake_Ok) {
        g_tcpProtoReady = true;
        LOG("A7-P1 SysDVR protocol 03 video handshake accepted\n");
    } else {
        LOG("A7-P1 SysDVR handshake rejected code=%u\n", parsed.Result.Code);
    }
}

static void _handleTcp(const u8* eth, u16 ethLen, const u8* ip, u8 ihl)
{
    const u16 totalLen = _be16(ip + 2);
    if (!_ipEq(ip + 16, g_p02ServerIp) || totalLen < ihl + 20 || (u32)14 + totalLen > ethLen) return;
    const u8* tcp = ip + ihl;
    const u16 srcPort = _be16(tcp + 0);
    const u16 dstPort = _be16(tcp + 2);
    if (dstPort != A7P1_TCP_VIDEO_PORT) return;
    const u8 tcpHdrLen = (tcp[12] >> 4) * 4;
    if (tcpHdrLen < 20 || totalLen < ihl + tcpHdrLen) return;
    const u16 payloadLen = totalLen - ihl - tcpHdrLen;
    const u8* payload = tcp + tcpHdrLen;
    const u8 flags = tcp[13];
    const u32 seq = _be32(tcp + 4);
    const u32 ack = _be32(tcp + 8);

    if ((flags & TCP_FLAG_RST) != 0) {
        _tcpReset("peer RST");
        return;
    }

    if ((flags & TCP_FLAG_SYN) && !(flags & TCP_FLAG_ACK)) {
        _tcpReset("new SYN");
        memcpy(g_tcpClientMac, eth + 6, 6);
        memcpy(g_tcpClientIp, ip + 12, 4);
        g_tcpClientPort = srcPort;
        g_tcpClientNextSeq = seq + 1;
        g_tcpServerNextSeq = 0x53445652u; // 'SDVR'
        g_tcpServerAckedSeq = g_tcpServerNextSeq;
        g_tcpState = A7Tcp_SynReceived;
        Result rc = _tcpSendSegment(g_tcpServerNextSeq, g_tcpClientNextSeq,
                                    TCP_FLAG_SYN | TCP_FLAG_ACK, NULL, 0);
        if (R_FAILED(rc)) {
            _tcpReset("SYN-ACK TX failed");
            return;
        }
        g_tcpServerNextSeq += 1;
        LOG("A7-P1 TCP SYN 192.168.55.2:%u -> 9911; SYN-ACK sent\n", srcPort);
        return;
    }

    if (g_tcpState == A7Tcp_Closed || srcPort != g_tcpClientPort ||
        memcmp(ip + 12, g_tcpClientIp, 4) != 0) return;

    if (flags & TCP_FLAG_ACK) {
        if (_seqGe(ack, g_tcpServerAckedSeq) && _seqGe(g_tcpServerNextSeq, ack))
            g_tcpServerAckedSeq = ack;
        if (g_tcpState == A7Tcp_SynReceived && ack == g_tcpServerNextSeq) {
            g_tcpState = A7Tcp_Established;
            LOG("A7-P1 TCP 9911 established\n");
            _tcpMaybeSendHello();
        }
    }

    if (g_tcpState != A7Tcp_Established) return;

    if (payloadLen) {
        if (seq == g_tcpClientNextSeq) {
            g_tcpClientNextSeq += payloadLen;
            _tcpSendSegment(g_tcpServerNextSeq, g_tcpClientNextSeq, TCP_FLAG_ACK, NULL, 0);
            _tcpConsumeHandshake(payload, payloadLen);
        } else {
            // Duplicate/out-of-order segment: advertise the byte we still expect.
            _tcpSendSegment(g_tcpServerNextSeq, g_tcpClientNextSeq, TCP_FLAG_ACK, NULL, 0);
        }
    }

    if (flags & TCP_FLAG_FIN) {
        if (seq + payloadLen == g_tcpClientNextSeq) g_tcpClientNextSeq++;
        _tcpSendSegment(g_tcpServerNextSeq, g_tcpClientNextSeq, TCP_FLAG_ACK, NULL, 0);
        _tcpReset("peer FIN");
    }
}

bool NcmDeviceVideoSessionReady(void)
{
    return g_tcpState == A7Tcp_Established && g_tcpProtoReady;
}

void NcmDeviceAbortVideoSession(void)
{
    _tcpReset("local abort");
}

bool NcmDeviceSendVideoPacket(const void* data, u32 len)
{
    if (!NcmDeviceVideoSessionReady() || !data || !len) return false;
    const u8* p = (const u8*)data;
    u32 remaining = len;

    while (remaining) {
        int loops = 0;
        while ((u32)(g_tcpServerNextSeq - g_tcpServerAckedSeq) >= A7P1_TCP_MAX_INFLIGHT) {
            NcmDeviceProcessRequests();
            if (!NcmDeviceVideoSessionReady()) return false;
            if (++loops >= A7P1_TCP_ACK_WAIT_LOOPS) {
                LOG("A7-P1 TCP ACK timeout inflight=%u\n",
                    (unsigned)(g_tcpServerNextSeq - g_tcpServerAckedSeq));
                _tcpReset("ACK timeout");
                return false;
            }
            svcSleepThread(1000000ULL); // 1 ms
        }

        u16 chunk = remaining > A7P1_TCP_MSS ? A7P1_TCP_MSS : (u16)remaining;
        Result rc = _tcpSendSegment(g_tcpServerNextSeq, g_tcpClientNextSeq,
                                    TCP_FLAG_ACK | TCP_FLAG_PSH, p, chunk);
        if (R_FAILED(rc)) {
            LOG("A7-P1 TCP video segment TX failed rc=0x%x\n", rc);
            _tcpReset("video TX failed");
            return false;
        }
        g_tcpServerNextSeq += chunk;
        p += chunk;
        remaining -= chunk;
        // Drain immediately available ACKs without waiting.
        NcmDeviceProcessRequests();
    }
    return true;
}

'''
s = s[:insert_at] + tcp_code + s[insert_at:]

# Dispatch IPv4/TCP to the new server.
old = '''    if (ip[9] == 17 && ethLen >= (u16)(14 + ihl + 8)) {
        const u8* udp = ip + ihl;
        LOG_V("A7-P1 IPv4 UDP: %u -> %u\\n", _be16(udp), _be16(udp + 2));
        _handleDhcp(eth, ethLen, ip, ihl);
    } else if (ip[9] == 1) {
        _handleIcmp(eth, ethLen, ip, ihl);
    }
'''
new = '''    if (ip[9] == 17 && ethLen >= (u16)(14 + ihl + 8)) {
        const u8* udp = ip + ihl;
        LOG_V("A7-P1 IPv4 UDP: %u -> %u\\n", _be16(udp), _be16(udp + 2));
        _handleDhcp(eth, ethLen, ip, ihl);
    } else if (ip[9] == 1) {
        _handleIcmp(eth, ethLen, ip, ihl);
    } else if (ip[9] == 6) {
        _handleTcp(eth, ethLen, ip, ihl);
    }
'''
if old not in s:
    raise SystemExit('IPv4 dispatch anchor missing')
s = s.replace(old, new, 1)

# Reset the software TCP endpoint when USB disappears.
old = '''            g_ctx.dataAlt = 0;
            g_ctx.notificationsSent = false;
            _cancelOut();
'''
new = '''            g_ctx.dataAlt = 0;
            g_ctx.notificationsSent = false;
            _cancelOut();
            _tcpReset("USB disconnected");
'''
if old not in s:
    raise SystemExit('disconnect reset anchor missing')
s = s.replace(old, new, 1)

old = '''    if (!g_ctx.initialized) return;
    _cancelOut();
'''
new = '''    if (!g_ctx.initialized) return;
    _tcpReset("NCM exit");
    _cancelOut();
'''
if old not in s:
    raise SystemExit('exit reset anchor missing')
s = s.replace(old, new, 1)
p.write_text(s)

# Public API used by the NCM mode entrypoint.
(ncm_dir / 'ncm_device.h').write_text(r'''#pragma once
#include <switch.h>

typedef struct {
    u16 vendorId;
    u16 productId;
    const char* manufacturer;
    const char* product;
    const char* serialNumber;
} NcmDeviceConfig;

Result NcmDeviceInitialize(const NcmDeviceConfig* config);
void NcmDeviceProcessRequests(void);
void NcmDeviceExit(void);
bool NcmDeviceVideoSessionReady(void);
bool NcmDeviceSendVideoPacket(const void* data, u32 len);
void NcmDeviceAbortVideoSession(void);
''')

# -----------------------------------------------------------------------------
# Add a dedicated NCM entrypoint. It reuses the official SysDVR 6.3 capture
# packet format and ProtoHandshake, so the iPad client sees the exact same
# protocol it already uses over Wi-Fi.
# -----------------------------------------------------------------------------
(ncm_dir / 'NCMmode.c').write_text(r'''#include <switch.h>
#include "../core.h"
#include "../capture.h"
#include "ncm_device.h"

void NcmEntrypoint(void)
{
    LOG("A7-P1 SysDVR 6.3 + USB-NCM video mode starting\n");
    NcmDeviceConfig cfg = {
        .vendorId = 0x057E,
        .productId = 0x3001,
        .manufacturer = "Nintendo Switch",
        .product = "SysDVR USB NCM",
        .serialNumber = "SysDVR63-NCM",
    };

    Result rc = NcmDeviceInitialize(&cfg);
    if (R_FAILED(rc)) {
        LOG("A7-P1 NcmDeviceInitialize failed: 0x%x\n", rc);
        fatalThrow(rc);
    }

    bool wasReady = false;
    u32 frames = 0;
    LOG("A7-P1 NCM ready: DHCP server=192.168.55.1 client=192.168.55.2 TCP video=9911 protocol=03\n");

    for (;;) {
        NcmDeviceProcessRequests();
        bool ready = NcmDeviceVideoSessionReady();
        if (!ready) {
            wasReady = false;
            svcSleepThread(1000000ULL);
            continue;
        }

        if (!wasReady) {
            CaptureVideoConnected();
            wasReady = true;
            frames = 0;
            LOG("A7-P1 video session active; starting grc:d capture\n");
            svcSleepThread(100000000ULL);
        }

        // Official SysDVR capture function fills VPkt with the exact 18-byte
        // PacketHeader + H.264 payload expected by protocol 03 clients.
        CaptureReadVideo();
        if (!NcmDeviceVideoSessionReady()) continue;
        const u32 bytes = (u32)sizeof(PacketHeader) + VPkt.Header.DataSize;
        if (!NcmDeviceSendVideoPacket(&VPkt, bytes)) {
            LOG("A7-P1 video packet send failed; waiting for reconnect\n");
            NcmDeviceAbortVideoSession();
            wasReady = false;
            continue;
        }
        frames++;
        if ((frames % 120u) == 0)
            LOG("A7-P1 streamed %u video packets, last=%u bytes\n", frames, bytes);
    }
}
''')

# Add the new source directory to the official Makefile; do not remove any
# original mode directories.
p = root / 'Makefile'
ms = p.read_text()
old = 'SOURCES\t\t:=\tsource source/sysmodule source/rtsp source/modes source/USB source/ipc source/third_party source/net'
new = old + ' source/ncm'
if old not in ms:
    raise SystemExit('SysDVR Makefile SOURCES anchor missing')
ms = ms.replace(old, new, 1)
p.write_text(ms)

# Preserve original SysDVR behavior by default. The new mode is opt-in through
# /config/sysdvr/ncm; deleting that single flag returns to stock 6.3 mode logic.
p = root / 'source/sysmodule/main.c'
ms = p.read_text()
inc = '#include "../capture.h"\n'
if inc not in ms:
    raise SystemExit('main include anchor missing')
ms = ms.replace(inc, inc + '\nvoid NcmEntrypoint(void);\n', 1)
anchor = '''\tif (FileExists("/config/sysdvr/no_adv"))
\t\tg_tcpEnableBroadcast = false;

'''
insert = anchor + '''\t// A7-P1 opt-in mode. With this flag absent the code below is the stock
\t// SysDVR 6.3 TCP/RTSP/original-USB selection path.
\tif (FileExists("/config/sysdvr/ncm")) {
\t\tNcmEntrypoint();
\t\treturn 0;
\t}

'''
if anchor not in ms:
    raise SystemExit('main mode-selection anchor missing')
ms = ms.replace(anchor, insert, 1)
p.write_text(ms)

# Keep the familiar logfile name used throughout the A7 diagnostics.
p = root / 'source/core.c'
cs = p.read_text().replace('freopen("/sysdvr_log.txt", "w", stdout);',
                           'freopen("/logfile.txt", "w", stdout);')
p.write_text(cs)

print('A7-P1 SysDVR 6.3 dual-compatible NCM TCP video patch applied')
