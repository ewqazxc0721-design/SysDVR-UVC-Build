from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/sysdvr/sysmodule')

# -----------------------------------------------------------------------------
# A7-P2: add audio TCP/9922, keep IPC alive during NCM mode, and replace the
# stdio logfile path with the raw-FS logger already proven by the A7 UVC builds.
# This patch expects A7-P1 to have been applied first.
# -----------------------------------------------------------------------------

# ===== core.h: raw-FS FILE_LOGGING hook =======================================
p = root / 'source/core.h'
s = p.read_text()
old = '''\t#if FILE_LOGGING
\t\t#include <stdio.h>
\t\t#define LogFunctionImpl(...) do { printf(__VA_ARGS__); fflush(stdout); } while (0)
\t#else
\t\tvoid LogFunctionImpl(const char* fmt, ...);
\t#endif
'''
new = '''\t#if FILE_LOGGING
\t\tvoid LogFunctionImpl(const char* fmt, ...);
\t#else
\t\tvoid LogFunctionImpl(const char* fmt, ...);
\t#endif
'''
if old not in s:
    raise SystemExit('A7-P2 core.h FILE_LOGGING anchor missing')
s = s.replace(old, new, 1)
p.write_text(s)

# ===== core.c: raw-FS /logfile.txt logger + report NCM as running TCP =========
p = root / 'source/core.c'
s = p.read_text()
if '#include <stdarg.h>' not in s:
    s = s.replace('#include <string.h>\n', '#include <string.h>\n#include <stdarg.h>\n#include <stdio.h>\n', 1)

anchor = '// Statically allocate all needed buffers\nStaticBuffers Buffers;\n'
logger = r'''
#if FILE_LOGGING
static FsFileSystem g_a7LogFs;
static bool g_a7LogReady = false;

static void A7InitFileLogging(void)
{
    if (g_a7LogReady) return;
    Result rc = fsOpenSdCardFileSystem(&g_a7LogFs);
    if (R_FAILED(rc)) return;
    g_a7LogReady = true;
    fsFsDeleteFile(&g_a7LogFs, "/logfile.txt");
    fsFsCreateFile(&g_a7LogFs, "/logfile.txt", 0, 0);
}

void LogFunctionImpl(const char* fmt, ...)
{
    if (!g_a7LogReady) return;
    char buf[512];
    va_list args;
    va_start(args, fmt);
    int len = vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    if (len <= 0) return;
    if (len > (int)sizeof(buf)) len = sizeof(buf);

    FsFile file;
    Result rc = fsFsOpenFile(&g_a7LogFs, "/logfile.txt",
                             FsOpenMode_Write | FsOpenMode_Append, &file);
    if (R_FAILED(rc)) return;
    s64 offset = 0;
    fsFileGetSize(&file, &offset);
    fsFileWrite(&file, offset, buf, (u64)len, FsWriteOption_Flush);
    fsFileClose(&file);
}
#endif

'''
if anchor not in s:
    raise SystemExit('A7-P2 core.c buffer anchor missing')
s = s.replace(anchor, anchor + logger, 1)

old = 'Result CoreInit()\n{\n\tResult rc = setsysInitialize();'
new = '''Result CoreInit()
{
#if FILE_LOGGING
\tA7InitFileLogging();
\tLOG("A7-P2 raw SD logfile initialized\\n");
#endif
\tResult rc = setsysInitialize();'''
if old not in s:
    raise SystemExit('A7-P2 CoreInit anchor missing')
s = s.replace(old, new, 1)

s = s.replace('''#ifdef FILE_LOGGING
\tfreopen("/logfile.txt", "w", stdout);
#endif

''', '')

old = '''u32 GetCurrentMode()
{
\tconst StreamMode* mode = GetUserVisibleMode();
'''
new = '''u32 GetCurrentMode()
{
\textern bool NcmModeIsActive(void);
\tif (NcmModeIsActive())
\t\treturn TYPE_MODE_TCP; // Config app sees the running NCM service as a network mode.
\tconst StreamMode* mode = GetUserVisibleMode();
'''
if old not in s:
    raise SystemExit('A7-P2 GetCurrentMode anchor missing')
s = s.replace(old, new, 1)
p.write_text(s)

# ===== NCM device: serialize USB IN and add independent audio TCP/9922 =========
p = root / 'source/ncm/ncm_device.c'
s = p.read_text()

needle = 'static Result _sendNcmEthernetFrame(const u8* frame, u16 frameLen, const char* label)\n'
if needle not in s:
    raise SystemExit('A7-P2 regular NCM sender anchor missing')
s = s.replace(needle, 'static Mutex g_a7p2TxMutex;\n\nstatic Result _sendNcmEthernetFrameUnlocked(const u8* frame, u16 frameLen, const char* label)\n', 1)

anchor = 'static int _findDhcpMessageType(const u8* dhcp, size_t len)\n'
wrapper = r'''static Result _sendNcmEthernetFrame(const u8* frame, u16 frameLen, const char* label)
{
    mutexLock(&g_a7p2TxMutex);
    Result rc = _sendNcmEthernetFrameUnlocked(frame, frameLen, label);
    mutexUnlock(&g_a7p2TxMutex);
    return rc;
}

'''
if anchor not in s:
    raise SystemExit('A7-P2 DHCP anchor missing')
s = s.replace(anchor, wrapper + anchor, 1)

needle = 'static Result _sendNcmEthernetFrameQuiet(const u8* frame, u16 frameLen)\n'
if needle not in s:
    raise SystemExit('A7-P2 quiet NCM sender anchor missing')
s = s.replace(needle, 'static Result _sendNcmEthernetFrameQuietUnlocked(const u8* frame, u16 frameLen)\n', 1)
anchor = 'static Result _tcpSendSegment(u32 seq, u32 ack, u8 flags, const void* payload, u16 payloadLen)\n'
wrapper = r'''static Result _sendNcmEthernetFrameQuiet(const u8* frame, u16 frameLen)
{
    mutexLock(&g_a7p2TxMutex);
    Result rc = _sendNcmEthernetFrameQuietUnlocked(frame, frameLen);
    mutexUnlock(&g_a7p2TxMutex);
    return rc;
}

'''
if anchor not in s:
    raise SystemExit('A7-P2 video TCP sender anchor missing')
s = s.replace(anchor, wrapper + anchor, 1)

anchor = 'static void _handleTcp(const u8* eth, u16 ethLen, const u8* ip, u8 ihl)\n'
if anchor not in s:
    raise SystemExit('A7-P2 TCP handler anchor missing')
audio_code = r'''
#define A7P2_TCP_AUDIO_PORT 9922

static A7TcpState g_audioTcpState = A7Tcp_Closed;
static u8 g_audioTcpClientMac[6];
static u8 g_audioTcpClientIp[4];
static u16 g_audioTcpClientPort = 0;
static u32 g_audioTcpClientNextSeq = 0;
static u32 g_audioTcpServerNextSeq = 0;
static u32 g_audioTcpServerAckedSeq = 0;
static bool g_audioTcpHelloSent = false;
static bool g_audioTcpProtoReady = false;
static u8 g_audioTcpHandshake[PROTO_HANDSHAKE_SIZE];
static u32 g_audioTcpHandshakeUsed = 0;

static Result _audioTcpSendSegment(u32 seq, u32 ack, u8 flags, const void* payload, u16 payloadLen)
{
    if (g_audioTcpState == A7Tcp_Closed || !g_audioTcpClientPort)
        return MAKERESULT(Module_Libnx, LibnxError_NotInitialized);
    if (payloadLen > A7P1_TCP_MSS)
        return MAKERESULT(Module_Libnx, LibnxError_BadInput);

    const u16 tcpLen = 20 + payloadLen;
    const u16 ipLen = 20 + tcpLen;
    const u16 frameLen = 14 + ipLen;
    u8* f = g_p02Frame;
    memset(f, 0, frameLen);

    memcpy(f, g_audioTcpClientMac, 6);
    memcpy(f + 6, g_p02ServerMac, 6);
    _putBe16(f + 12, 0x0800);

    u8* ip = f + 14;
    ip[0] = 0x45;
    _putBe16(ip + 2, ipLen);
    _putBe16(ip + 4, g_p02IpId++);
    ip[8] = 64;
    ip[9] = 6;
    memcpy(ip + 12, g_p02ServerIp, 4);
    memcpy(ip + 16, g_audioTcpClientIp, 4);
    _putBe16(ip + 10, _checksum16(ip, 20));

    u8* tcp = ip + 20;
    _putBe16(tcp + 0, A7P2_TCP_AUDIO_PORT);
    _putBe16(tcp + 2, g_audioTcpClientPort);
    _putBe32(tcp + 4, seq);
    _putBe32(tcp + 8, ack);
    tcp[12] = 5u << 4;
    tcp[13] = flags;
    _putBe16(tcp + 14, 65535);
    if (payloadLen) memcpy(tcp + 20, payload, payloadLen);
    _putBe16(tcp + 16, _tcpChecksum(ip, tcp, tcpLen));
    return _sendNcmEthernetFrameQuiet(f, frameLen);
}

static void _audioTcpReset(const char* why)
{
    if (g_audioTcpProtoReady)
        ProtoClientGlobalStateDisconnected();
    if (g_audioTcpState != A7Tcp_Closed)
        LOG("A7-P2 audio TCP reset: %s\\n", why ? why : "unknown");
    g_audioTcpState = A7Tcp_Closed;
    memset(g_audioTcpClientMac, 0, sizeof(g_audioTcpClientMac));
    memset(g_audioTcpClientIp, 0, sizeof(g_audioTcpClientIp));
    g_audioTcpClientPort = 0;
    g_audioTcpClientNextSeq = 0;
    g_audioTcpServerNextSeq = 0;
    g_audioTcpServerAckedSeq = 0;
    g_audioTcpHelloSent = false;
    g_audioTcpProtoReady = false;
    g_audioTcpHandshakeUsed = 0;
}

static bool _audioTcpSendControlPayload(const void* data, u16 len)
{
    Result rc = _audioTcpSendSegment(g_audioTcpServerNextSeq, g_audioTcpClientNextSeq,
                                     TCP_FLAG_ACK | TCP_FLAG_PSH, data, len);
    if (R_FAILED(rc)) return false;
    g_audioTcpServerNextSeq += len;
    return true;
}

static void _audioTcpMaybeSendHello(void)
{
    if (g_audioTcpState != A7Tcp_Established || g_audioTcpHelloSent) return;
    static const char hello[] = PROTO_HANDSHAKE_HELLO;
    if (_audioTcpSendControlPayload(hello, sizeof(hello))) {
        g_audioTcpHelloSent = true;
        LOG("A7-P2 TCP 9922 connected; sent SysDVR protocol hello %s\\n",
            SYSDVR_PROTOCOL_VERSION);
    }
}

static void _audioTcpConsumeHandshake(const u8* payload, u16 len)
{
    if (g_audioTcpProtoReady || !g_audioTcpHelloSent || !len) return;
    u32 room = PROTO_HANDSHAKE_SIZE - g_audioTcpHandshakeUsed;
    u32 take = len < room ? len : room;
    memcpy(g_audioTcpHandshake + g_audioTcpHandshakeUsed, payload, take);
    g_audioTcpHandshakeUsed += take;
    if (g_audioTcpHandshakeUsed != PROTO_HANDSHAKE_SIZE) return;

    ProtoParsedHandshake parsed = ProtoHandshake(ProtoHandshakeAccept_Audio,
                                                  g_audioTcpHandshake,
                                                  PROTO_HANDSHAKE_SIZE);
    if (!_audioTcpSendControlPayload(&parsed.Result, sizeof(parsed.Result))) {
        _audioTcpReset("handshake response TX failed");
        return;
    }
    if (parsed.Result.Code == Handshake_Ok) {
        g_audioTcpProtoReady = true;
        LOG("A7-P2 SysDVR protocol 03 audio handshake accepted\\n");
    } else {
        LOG("A7-P2 SysDVR audio handshake rejected code=%u\\n", parsed.Result.Code);
    }
}

static void _handleAudioTcp(const u8* eth, u16 ethLen, const u8* ip, u8 ihl)
{
    const u16 totalLen = _be16(ip + 2);
    if (!_ipEq(ip + 16, g_p02ServerIp) || totalLen < ihl + 20 ||
        (u32)14 + totalLen > ethLen) return;

    const u8* tcp = ip + ihl;
    const u16 srcPort = _be16(tcp + 0);
    const u16 dstPort = _be16(tcp + 2);
    if (dstPort != A7P2_TCP_AUDIO_PORT) return;

    const u8 tcpHdrLen = (tcp[12] >> 4) * 4;
    if (tcpHdrLen < 20 || totalLen < ihl + tcpHdrLen) return;
    const u16 payloadLen = totalLen - ihl - tcpHdrLen;
    const u8* payload = tcp + tcpHdrLen;
    const u8 flags = tcp[13];
    const u32 seq = _be32(tcp + 4);
    const u32 ack = _be32(tcp + 8);

    if (flags & TCP_FLAG_RST) {
        _audioTcpReset("peer RST");
        return;
    }

    if ((flags & TCP_FLAG_SYN) && !(flags & TCP_FLAG_ACK)) {
        _audioTcpReset("new SYN");
        memcpy(g_audioTcpClientMac, eth + 6, 6);
        memcpy(g_audioTcpClientIp, ip + 12, 4);
        g_audioTcpClientPort = srcPort;
        g_audioTcpClientNextSeq = seq + 1;
        g_audioTcpServerNextSeq = 0x41554430u;
        g_audioTcpServerAckedSeq = g_audioTcpServerNextSeq;
        g_audioTcpState = A7Tcp_SynReceived;
        Result rc = _audioTcpSendSegment(g_audioTcpServerNextSeq,
                                         g_audioTcpClientNextSeq,
                                         TCP_FLAG_SYN | TCP_FLAG_ACK,
                                         NULL, 0);
        if (R_FAILED(rc)) {
            _audioTcpReset("SYN-ACK TX failed");
            return;
        }
        g_audioTcpServerNextSeq++;
        LOG("A7-P2 TCP SYN 192.168.55.2:%u -> 9922; SYN-ACK sent\\n", srcPort);
        return;
    }

    if (g_audioTcpState == A7Tcp_Closed ||
        srcPort != g_audioTcpClientPort ||
        memcmp(ip + 12, g_audioTcpClientIp, 4) != 0) return;

    if (flags & TCP_FLAG_ACK) {
        if (_seqGe(ack, g_audioTcpServerAckedSeq) &&
            _seqGe(g_audioTcpServerNextSeq, ack))
            g_audioTcpServerAckedSeq = ack;
        if (g_audioTcpState == A7Tcp_SynReceived &&
            ack == g_audioTcpServerNextSeq) {
            g_audioTcpState = A7Tcp_Established;
            LOG("A7-P2 TCP 9922 established\\n");
            _audioTcpMaybeSendHello();
        }
    }

    if (g_audioTcpState != A7Tcp_Established) return;

    if (payloadLen) {
        if (seq == g_audioTcpClientNextSeq) {
            g_audioTcpClientNextSeq += payloadLen;
            _audioTcpSendSegment(g_audioTcpServerNextSeq,
                                 g_audioTcpClientNextSeq,
                                 TCP_FLAG_ACK, NULL, 0);
            _audioTcpConsumeHandshake(payload, payloadLen);
        } else {
            _audioTcpSendSegment(g_audioTcpServerNextSeq,
                                 g_audioTcpClientNextSeq,
                                 TCP_FLAG_ACK, NULL, 0);
        }
    }

    if (flags & TCP_FLAG_FIN) {
        if (seq + payloadLen == g_audioTcpClientNextSeq)
            g_audioTcpClientNextSeq++;
        _audioTcpSendSegment(g_audioTcpServerNextSeq,
                             g_audioTcpClientNextSeq,
                             TCP_FLAG_ACK, NULL, 0);
        _audioTcpReset("peer FIN");
    }
}

'''
s = s.replace(anchor, audio_code + anchor, 1)

old = '''    const u16 srcPort = _be16(tcp + 0);
    const u16 dstPort = _be16(tcp + 2);
    if (dstPort != A7P1_TCP_VIDEO_PORT) return;
'''
new = '''    const u16 srcPort = _be16(tcp + 0);
    const u16 dstPort = _be16(tcp + 2);
    if (dstPort == A7P2_TCP_AUDIO_PORT) {
        _handleAudioTcp(eth, ethLen, ip, ihl);
        return;
    }
    if (dstPort != A7P1_TCP_VIDEO_PORT) return;
'''
if old not in s:
    raise SystemExit('A7-P2 TCP port dispatch anchor missing')
s = s.replace(old, new, 1)

anchor = '''bool NcmDeviceVideoSessionReady(void)
{
    return g_tcpState == A7Tcp_Established && g_tcpProtoReady;
}
'''
insert = r'''bool NcmDeviceAudioSessionReady(void)
{
    return g_audioTcpState == A7Tcp_Established && g_audioTcpProtoReady;
}

'''
if anchor not in s:
    raise SystemExit('A7-P2 video ready anchor missing')
s = s.replace(anchor, insert + anchor, 1)

anchor = 'void NcmDeviceAbortVideoSession(void)\n'
audio_send = r'''void NcmDeviceAbortAudioSession(void)
{
    _audioTcpReset("local abort");
}

bool NcmDeviceSendAudioPacket(const void* data, u32 len)
{
    if (!NcmDeviceAudioSessionReady() || !data || !len) return false;
    const u8* p = (const u8*)data;
    u32 remaining = len;

    while (remaining) {
        int loops = 0;
        while ((u32)(g_audioTcpServerNextSeq - g_audioTcpServerAckedSeq) >=
               A7P1_TCP_MAX_INFLIGHT) {
            if (!NcmDeviceAudioSessionReady()) return false;
            if (++loops >= A7P1_TCP_ACK_WAIT_LOOPS) {
                LOG("A7-P2 audio TCP ACK timeout inflight=%u\\n",
                    (unsigned)(g_audioTcpServerNextSeq - g_audioTcpServerAckedSeq));
                _audioTcpReset("ACK timeout");
                return false;
            }
            svcSleepThread(1000000ULL);
        }

        u16 chunk = remaining > A7P1_TCP_MSS ? A7P1_TCP_MSS : (u16)remaining;
        Result rc = _audioTcpSendSegment(g_audioTcpServerNextSeq,
                                         g_audioTcpClientNextSeq,
                                         TCP_FLAG_ACK | TCP_FLAG_PSH,
                                         p, chunk);
        if (R_FAILED(rc)) {
            LOG("A7-P2 audio TCP segment TX failed rc=0x%x\\n", rc);
            _audioTcpReset("audio TX failed");
            return false;
        }
        g_audioTcpServerNextSeq += chunk;
        p += chunk;
        remaining -= chunk;
    }
    return true;
}

'''
if anchor not in s:
    raise SystemExit('A7-P2 abort-video anchor missing')
s = s.replace(anchor, audio_send + anchor, 1)

s = s.replace('_tcpReset("USB disconnected");',
              '_tcpReset("USB disconnected");\n            _audioTcpReset("USB disconnected");')
s = s.replace('_tcpReset("NCM exit");',
              '_tcpReset("NCM exit");\n    _audioTcpReset("NCM exit");')
p.write_text(s)

p = root / 'source/ncm/ncm_device.h'
s = p.read_text()
old = '''bool NcmDeviceVideoSessionReady(void);
bool NcmDeviceSendVideoPacket(const void* data, u32 len);
void NcmDeviceAbortVideoSession(void);
'''
new = '''bool NcmDeviceVideoSessionReady(void);
bool NcmDeviceSendVideoPacket(const void* data, u32 len);
void NcmDeviceAbortVideoSession(void);
bool NcmDeviceAudioSessionReady(void);
bool NcmDeviceSendAudioPacket(const void* data, u32 len);
void NcmDeviceAbortAudioSession(void);
'''
if old not in s:
    raise SystemExit('A7-P2 ncm_device.h API anchor missing')
s = s.replace(old, new, 1)
p.write_text(s)

# ===== NCM mode: video + audio thread, expose active state =====================
p = root / 'source/ncm/NCMmode.c'
p.write_text(r'''#include <switch.h>
#include <stdatomic.h>
#include "../core.h"
#include "../capture.h"
#include "ncm_device.h"

static atomic_bool g_ncmModeActive = false;
static Thread g_audioThread;
static u8 alignas(0x1000) g_audioStack[0x3000 + LOGGING_STACK_BOOST];

bool NcmModeIsActive(void)
{
    return atomic_load(&g_ncmModeActive);
}

static void A7P2AudioThread(void* arg)
{
    (void)arg;
    bool wasReady = false;
    u32 packets = 0;

    for (;;) {
        if (!NcmDeviceAudioSessionReady()) {
            wasReady = false;
            svcSleepThread(1000000ULL);
            continue;
        }

        if (!wasReady) {
            CaptureAudioConnected();
            wasReady = true;
            packets = 0;
            LOG("A7-P2 audio session active; starting grc:d capture\\n");
            svcSleepThread(100000000ULL);
        }

        CaptureReadAudio();
        if (!NcmDeviceAudioSessionReady()) continue;
        const u32 bytes = (u32)sizeof(PacketHeader) + APkt.Header.DataSize;
        if (!NcmDeviceSendAudioPacket(&APkt, bytes)) {
            LOG("A7-P2 audio packet send failed; waiting for reconnect\\n");
            NcmDeviceAbortAudioSession();
            wasReady = false;
            continue;
        }
        packets++;
        if ((packets % 120u) == 0)
            LOG("A7-P2 streamed %u audio packets, last=%u bytes\\n",
                packets, bytes);
    }
}

void NcmEntrypoint(void)
{
    atomic_store(&g_ncmModeActive, true);
    LOG("A7-P2 SysDVR 6.3 + USB-NCM A/V mode starting\\n");

    NcmDeviceConfig cfg = {
        .vendorId = 0x057E,
        .productId = 0x3001,
        .manufacturer = "Nintendo Switch",
        .product = "SysDVR USB NCM",
        .serialNumber = "SysDVR63-NCM",
    };

    Result rc = NcmDeviceInitialize(&cfg);
    if (R_FAILED(rc)) {
        LOG("A7-P2 NcmDeviceInitialize failed: 0x%x\\n", rc);
        atomic_store(&g_ncmModeActive, false);
        fatalThrow(rc);
    }

    memset(g_audioStack, 0, sizeof(g_audioStack));
    LaunchThread(&g_audioThread, A7P2AudioThread, NULL,
                 g_audioStack, sizeof(g_audioStack), 0x2C);

    bool wasReady = false;
    u32 frames = 0;
    LOG("A7-P2 NCM ready: DHCP 192.168.55.1/55.2 TCP video=9911 audio=9922 protocol=03\\n");

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
            LOG("A7-P2 video session active; starting grc:d capture\\n");
            svcSleepThread(100000000ULL);
        }

        CaptureReadVideo();
        if (!NcmDeviceVideoSessionReady()) continue;
        const u32 bytes = (u32)sizeof(PacketHeader) + VPkt.Header.DataSize;
        if (!NcmDeviceSendVideoPacket(&VPkt, bytes)) {
            LOG("A7-P2 video packet send failed; waiting for reconnect\\n");
            NcmDeviceAbortVideoSession();
            wasReady = false;
            continue;
        }
        frames++;
        if ((frames % 120u) == 0)
            LOG("A7-P2 streamed %u video packets, last=%u bytes\\n",
                frames, bytes);
    }
}
''')

# ===== main.c: run NCM in worker thread, leave official IPC service alive ======
p = root / 'source/sysmodule/main.c'
s = p.read_text()
anchor = '// from TCPMode.c\nextern int g_tcpEnableBroadcast;\n'
extra = r'''
static Thread g_a7p2NcmThread;
static u8 alignas(0x1000) g_a7p2NcmStack[0x4000 + LOGGING_STACK_BOOST];

static void A7P2NcmThreadMain(void* arg)
{
    (void)arg;
    NcmEntrypoint();
}

'''
if anchor not in s:
    raise SystemExit('A7-P2 main TCP extern anchor missing')
s = s.replace(anchor, anchor + extra, 1)

old = '''\tif (FileExists("/config/sysdvr/ncm")) {
\t\tNcmEntrypoint();
\t\treturn 0;
\t}
'''
new = '''\tif (FileExists("/config/sysdvr/ncm")) {
\t\tLOG("A7-P2 NCM flag present; launching NCM worker + IPC server\\n");
\t\tmemset(g_a7p2NcmStack, 0, sizeof(g_a7p2NcmStack));
\t\tLaunchThread(&g_a7p2NcmThread, A7P2NcmThreadMain, NULL,
\t\t             g_a7p2NcmStack, sizeof(g_a7p2NcmStack), 0x2C);
\t\tIpcThread();
\t\treturn 0;
\t}
'''
if old not in s:
    raise SystemExit('A7-P2 NCM mode-selection anchor missing')
s = s.replace(old, new, 1)
p.write_text(s)

# ===== IPC: keep status query, block runtime mode-switch commands in NCM =======
p = root / 'source/ipc/ipc.c'
s = p.read_text()
anchor = '#include "../util.h"\n'
if anchor not in s:
    raise SystemExit('A7-P2 ipc include anchor missing')
s = s.replace(anchor, anchor + '\nextern bool NcmModeIsActive(void);\n', 1)

old = '''\t\tcase CMD_SET_USB:
\t\tcase CMD_SET_TCP:
\t\tcase CMD_SET_RTSP:
\t\tcase CMD_SET_OFF:
\t\t\t// This relies nn the following conditions, otherwise it needs custom conversion code
'''
new = '''\t\tcase CMD_SET_USB:
\t\tcase CMD_SET_TCP:
\t\tcase CMD_SET_RTSP:
\t\tcase CMD_SET_OFF:
\t\t\tif (NcmModeIsActive()) {
\t\t\t\tWriteResponseToTLS(ERR_MAIN_SWITCHING);
\t\t\t\treturn false;
\t\t\t}
\t\t\t// This relies nn the following conditions, otherwise it needs custom conversion code
'''
if old not in s:
    raise SystemExit('A7-P2 IPC mode command anchor missing')
s = s.replace(old, new, 1)
p.write_text(s)

print('A7-P2 audio 9922 + IPC + raw logfile patch applied')
