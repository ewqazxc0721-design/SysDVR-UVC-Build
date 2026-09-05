from pathlib import Path
import sys


root = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/sysdvr/sysmodule")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"P2.5.2 {label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


# Keep P2.5.1's staged USB transition intact, while giving this release an
# unambiguous runtime marker.
core = root / "source/core.c"
s = core.read_text(encoding="utf-8")
if s.count("A7-P2.5.1") != 6 or s.count("BUILD=P251") != 1:
    raise SystemExit("P2.5.2 core markers do not match the P2.5.1 base")
s = s.replace("A7-P2.5.1", "A7-P2.5.2")
s = s.replace("BUILD=P251", "BUILD=P252")
core.write_text(s, encoding="utf-8")


ncm_mode = root / "source/ncm/NCMmode.c"
s = ncm_mode.read_text(encoding="utf-8")
if s.count("A7-P2.5.1") != 10 or s.count("BUILD=P251") != 3:
    raise SystemExit("P2.5.2 NCM mode markers do not match the P2.5.1 base")
s = s.replace("A7-P2.5.1", "A7-P2.5.2")
s = s.replace("BUILD=P251", "BUILD=P252")
s = replace_once(
    s,
    "RX_MULTI=1 AUDIO_INFLIGHT=6 AUDIO_RTO=40 AUDIO_BURST=3\\n",
    "RX_MULTI=1 AUDIO_INFLIGHT=6 AUDIO_RTO=40 AUDIO_BURST=3 "
    "DISCOVERY=UDP19999 AUDIO_PUMP=1\\n",
    "startup feature marker",
)
s = replace_once(
    s,
    "    while (atomic_load(&IsThreadRunning)) {\n"
    "        if (!NcmDeviceAudioSessionReady()) {",
    "    while (atomic_load(&IsThreadRunning)) {\n"
    "        // The video capture call may block before the audio socket has\n"
    "        // completed its handshake. Let the audio worker service USB/NCM\n"
    "        // too, so TCP 9922 cannot starve behind video capture.\n"
    "        NcmDeviceProcessRequests();\n\n"
    "        if (!NcmDeviceAudioSessionReady()) {",
    "audio pre-handshake request pump",
)
ncm_mode.write_text(s, encoding="utf-8")


ncm_device = root / "source/ncm/ncm_device.c"
s = ncm_device.read_text(encoding="utf-8")
if s.count("A7-P2.5.1") != 4:
    raise SystemExit("P2.5.2 NCM device markers do not match the P2.5.1 base")
s = s.replace("A7-P2.5.1", "A7-P2.5.2")

s = replace_once(
    s,
    "#define A7P2_TCP_AUDIO_PORT 9922\n\n"
    "static A7TcpState g_audioTcpState = A7Tcp_Closed;",
    "#define A7P2_TCP_AUDIO_PORT 9922\n"
    "#define A7P252_DISCOVERY_PORT 19999u\n"
    "#define A7P252_DISCOVERY_INTERVAL_NS 2000000000ULL\n\n"
    "static A7TcpState g_audioTcpState = A7Tcp_Closed;",
    "discovery constants",
)

s = replace_once(
    s,
    "static u32 g_audioTcpHandshakeUsed = 0;\n\n"
    "static Result _audioTcpSendSegment",
    "static u32 g_audioTcpHandshakeUsed = 0;\n"
    "static u64 g_a7p252DiscoveryLastTick = 0;\n"
    "static u32 g_a7p252DiscoverySent = 0;\n"
    "static u32 g_a7p252DiscoveryFailed = 0;\n\n"
    "static void _a7p252MaybeAdvertise(void)\n"
    "{\n"
    "    // Discovery is useful only before a stream connects. Stopping as soon\n"
    "    // as either TCP socket leaves Closed guarantees that the periodic UDP\n"
    "    // packet never competes with active audio/video traffic.\n"
    "    if (!g_ctx.configured || g_ctx.dataAlt != 1 || !g_ctx.dataEndpointIn ||\n"
    "        g_tcpState != A7Tcp_Closed || g_audioTcpState != A7Tcp_Closed)\n"
    "        return;\n\n"
    "    const u64 now = armGetSystemTick();\n"
    "    const u64 interval = armNsToTicks(A7P252_DISCOVERY_INTERVAL_NS);\n"
    "    if (g_a7p252DiscoveryLastTick &&\n"
    "        now - g_a7p252DiscoveryLastTick < interval)\n"
    "        return;\n"
    "    // Advance the deadline before TX. A detached or temporarily busy host\n"
    "    // must not turn this low-priority path into a tight retry loop.\n"
    "    g_a7p252DiscoveryLastTick = now;\n\n"
    "    if (SysDVRBeaconLen <= 0 ||\n"
    "        (u32)SysDVRBeaconLen > sizeof(g_p02Frame) - 42u)\n"
    "        return;\n\n"
    "    const u16 payloadLen = (u16)SysDVRBeaconLen;\n"
    "    const u16 udpLen = (u16)(8u + payloadLen);\n"
    "    const u16 ipLen = (u16)(20u + udpLen);\n"
    "    const u16 frameLen = (u16)(14u + ipLen);\n"
    "    u8* f = g_p02Frame;\n"
    "    memset(f, 0, frameLen);\n"
    "    memset(f, 0xFF, 6);\n"
    "    memcpy(f + 6, g_p02ServerMac, 6);\n"
    "    _putBe16(f + 12, 0x0800);\n\n"
    "    u8* ip = f + 14;\n"
    "    ip[0] = 0x45;\n"
    "    _putBe16(ip + 2, ipLen);\n"
    "    _putBe16(ip + 4, g_p02IpId++);\n"
    "    ip[8] = 64;\n"
    "    ip[9] = 17;\n"
    "    memcpy(ip + 12, g_p02ServerIp, 4);\n"
    "    ip[16] = 192; ip[17] = 168; ip[18] = 55; ip[19] = 255;\n"
    "    _putBe16(ip + 10, _checksum16(ip, 20));\n\n"
    "    u8* udp = ip + 20;\n"
    "    _putBe16(udp + 0, A7P252_DISCOVERY_PORT);\n"
    "    _putBe16(udp + 2, A7P252_DISCOVERY_PORT);\n"
    "    _putBe16(udp + 4, udpLen);\n"
    "    _putBe16(udp + 6, 0);\n"
    "    memcpy(udp + 8, SysDVRBeacon, payloadLen);\n\n"
    "    Result rc = _sendNcmEthernetFrameQuietFlushed(f, frameLen);\n"
    "    if (R_SUCCEEDED(rc)) {\n"
    "        g_a7p252DiscoverySent++;\n"
    "        if (g_a7p252DiscoverySent == 1 ||\n"
    "            (g_a7p252DiscoverySent % 30u) == 0)\n"
    "            LOG(\"A7-P2.5.2 UDP discovery sent port=19999 count=%u bytes=%u\\n\",\n"
    "                g_a7p252DiscoverySent, payloadLen);\n"
    "    } else {\n"
    "        g_a7p252DiscoveryFailed++;\n"
    "        if (g_a7p252DiscoveryFailed == 1 ||\n"
    "            (g_a7p252DiscoveryFailed % 30u) == 0)\n"
    "            LOG(\"A7-P2.5.2 UDP discovery deferred rc=0x%x count=%u\\n\",\n"
    "                rc, g_a7p252DiscoveryFailed);\n"
    "    }\n"
    "}\n\n"
    "static Result _audioTcpSendSegment",
    "discovery state and sender",
)

s = replace_once(
    s,
    "    memset(&g_ctx, 0, sizeof(g_ctx));\n"
    "    g_ctx.ntbInputSize = NCM_NTB_MAX_SIZE;",
    "    memset(&g_ctx, 0, sizeof(g_ctx));\n"
    "    g_a7p252DiscoveryLastTick = 0;\n"
    "    g_a7p252DiscoverySent = 0;\n"
    "    g_a7p252DiscoveryFailed = 0;\n"
    "    g_ctx.ntbInputSize = NCM_NTB_MAX_SIZE;",
    "initialize discovery scheduler",
)

s = replace_once(
    s,
    "            _audioTcpReset(\"USB disconnected\");\n"
    "            _a7p21ResetAgg();",
    "            _audioTcpReset(\"USB disconnected\");\n"
    "            _a7p21ResetAgg();\n"
    "            g_a7p252DiscoveryLastTick = 0;",
    "reset discovery on detach",
)

s = replace_once(
    s,
    "    _sendLinkNotifications();\n"
    "    _pollOut();\n"
    "}",
    "    _sendLinkNotifications();\n"
    "    _pollOut();\n"
    "    _a7p252MaybeAdvertise();\n"
    "}",
    "service discovery after RX",
)

ncm_device.write_text(s, encoding="utf-8")

print("A7-P2.5.2 audio request pump and idle-only UDP discovery patch applied")
