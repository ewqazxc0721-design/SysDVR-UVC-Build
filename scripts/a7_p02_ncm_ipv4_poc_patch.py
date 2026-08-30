from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/a7p02')
p = root / 'source/usbnet/ncm_device.c'
s = p.read_text()

# Add a dedicated aligned USB IN NTB buffer plus an Ethernet-frame scratch area.
old = '''static u8 alignas(0x1000) g_ctrlBuffer[0x1000];
static u8 alignas(0x1000) g_notifyBuffer[0x1000];
static u8 alignas(0x1000) g_dataOutBuffer[NCM_NTB_MAX_SIZE];
'''
new = '''static u8 alignas(0x1000) g_ctrlBuffer[0x1000];
static u8 alignas(0x1000) g_notifyBuffer[0x1000];
static u8 alignas(0x1000) g_dataOutBuffer[NCM_NTB_MAX_SIZE];
static u8 alignas(0x1000) g_dataInBuffer[NCM_NTB_MAX_SIZE];
static u8 g_p02Frame[1600];
'''
if old not in s:
    raise SystemExit('A7 P0.2 global buffer anchor missing')
s = s.replace(old, new, 1)

# Replace the passive RX logger with a tiny point-to-point IPv4 service:
# - device/server 192.168.55.1, MAC 02:00:00:00:00:02
# - iPad lease      192.168.55.2
# - DHCP OFFER/ACK, ARP reply and ICMP echo reply
# This intentionally does not advertise a default router or DNS server so the
# USB link remains local-only and should not steal the iPad's Internet route.
start = s.find('static void _logNcmRx(const u8* p, u32 len)')
end = s.find('static void _pollOut(void)', start)
if start < 0 or end < 0:
    raise SystemExit('A7 P0.2 RX logger boundaries missing')

replacement = r'''static const u8 g_p02ServerMac[6] = { 0x02, 0x00, 0x00, 0x00, 0x00, 0x02 };
static const u8 g_p02ServerIp[4]  = { 192, 168, 55, 1 };
static const u8 g_p02ClientIp[4]  = { 192, 168, 55, 2 };
static u16 g_p02TxSequence = 0;
static u16 g_p02IpId = 1;

static void _putBe16(u8* p, u16 v)
{
    p[0] = (u8)(v >> 8);
    p[1] = (u8)v;
}

static void _putBe32(u8* p, u32 v)
{
    p[0] = (u8)(v >> 24);
    p[1] = (u8)(v >> 16);
    p[2] = (u8)(v >> 8);
    p[3] = (u8)v;
}

static bool _ipEq(const u8* a, const u8* b)
{
    return memcmp(a, b, 4) == 0;
}

static u16 _checksum16(const u8* data, size_t len)
{
    u32 sum = 0;
    while (len >= 2) {
        sum += ((u16)data[0] << 8) | data[1];
        data += 2;
        len -= 2;
    }
    if (len) sum += (u16)data[0] << 8;
    while (sum >> 16) sum = (sum & 0xFFFFu) + (sum >> 16);
    return (u16)(~sum);
}

static Result _sendNcmEthernetFrame(const u8* frame, u16 frameLen, const char* label)
{
    const u16 ndpIndex = (u16)sizeof(Nth16);          // 12
    const u16 dataIndex = 28;                         // NTH12 + NDP16
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
    if (R_FAILED(rc)) {
        LOG("A7-P0.2 %s TX wait-ready failed: 0x%x\n", label, rc);
        return rc;
    }

    eventClear(&g_ctx.dataEndpointIn->CompletionEvent);
    u32 urbId = 0;
    rc = usbDsEndpoint_PostBufferAsync(g_ctx.dataEndpointIn, g_dataInBuffer, blockLength, &urbId);
    if (R_FAILED(rc)) {
        LOG("A7-P0.2 %s TX post failed: 0x%x\n", label, rc);
        return rc;
    }
    rc = eventWait(&g_ctx.dataEndpointIn->CompletionEvent, EP_TIMEOUT_NS);
    if (R_FAILED(rc)) {
        usbDsEndpoint_Cancel(g_ctx.dataEndpointIn);
        LOG("A7-P0.2 %s TX timeout: 0x%x\n", label, rc);
        return rc;
    }
    eventClear(&g_ctx.dataEndpointIn->CompletionEvent);

    UsbDsReportData rpt;
    rc = usbDsEndpoint_GetReportData(g_ctx.dataEndpointIn, &rpt);
    u32 transferred = 0;
    if (R_SUCCEEDED(rc)) rc = usbDsParseReportData(&rpt, urbId, NULL, &transferred);
    LOG("A7-P0.2 %s TX complete rc=0x%x ntb=%u frame=%u seq=%u\n",
        label, rc, transferred, frameLen, (u16)(g_p02TxSequence - 1));
    return rc;
}

static int _findDhcpMessageType(const u8* dhcp, size_t len)
{
    if (len < 240) return -1;
    if (dhcp[236] != 99 || dhcp[237] != 130 || dhcp[238] != 83 || dhcp[239] != 99)
        return -1;
    size_t i = 240;
    while (i < len) {
        u8 code = dhcp[i++];
        if (code == 0) continue;
        if (code == 255) break;
        if (i >= len) break;
        u8 optLen = dhcp[i++];
        if (i + optLen > len) break;
        if (code == 53 && optLen >= 1) return dhcp[i];
        i += optLen;
    }
    return -1;
}

static void _sendDhcpReply(const u8* requestDhcp, size_t requestLen, u8 replyType)
{
    if (requestLen < 240) return;

    // 300-byte BOOTP/DHCP payload keeps the response conservative and mirrors
    // the host's observed 342-byte Ethernet DHCP frames.
    const u16 dhcpLen = 300;
    const u16 udpLen = 8 + dhcpLen;
    const u16 ipLen = 20 + udpLen;
    const u16 frameLen = 14 + ipLen;
    u8* f = g_p02Frame;
    memset(f, 0, frameLen);

    memset(f, 0xFF, 6);                       // Ethernet broadcast
    memcpy(f + 6, g_p02ServerMac, 6);
    _putBe16(f + 12, 0x0800);

    u8* ip = f + 14;
    ip[0] = 0x45;
    ip[1] = 0;
    _putBe16(ip + 2, ipLen);
    _putBe16(ip + 4, g_p02IpId++);
    _putBe16(ip + 6, 0);
    ip[8] = 64;
    ip[9] = 17;
    memcpy(ip + 12, g_p02ServerIp, 4);
    ip[16] = 255; ip[17] = 255; ip[18] = 255; ip[19] = 255;
    _putBe16(ip + 10, _checksum16(ip, 20));

    u8* udp = ip + 20;
    _putBe16(udp + 0, 67);
    _putBe16(udp + 2, 68);
    _putBe16(udp + 4, udpLen);
    _putBe16(udp + 6, 0);                     // IPv4 UDP checksum may be zero

    u8* d = udp + 8;
    d[0] = 2;                                 // BOOTREPLY
    d[1] = requestDhcp[1];                    // htype
    d[2] = requestDhcp[2];                    // hlen
    d[3] = 0;
    memcpy(d + 4, requestDhcp + 4, 4);        // xid
    memcpy(d + 10, requestDhcp + 10, 2);      // flags
    memcpy(d + 16, g_p02ClientIp, 4);         // yiaddr
    memcpy(d + 20, g_p02ServerIp, 4);         // siaddr
    memcpy(d + 28, requestDhcp + 28, 16);     // chaddr
    d[236] = 99; d[237] = 130; d[238] = 83; d[239] = 99;

    size_t o = 240;
    d[o++] = 53; d[o++] = 1; d[o++] = replyType;
    d[o++] = 54; d[o++] = 4; memcpy(d + o, g_p02ServerIp, 4); o += 4;
    d[o++] = 1;  d[o++] = 4; d[o++] = 255; d[o++] = 255; d[o++] = 255; d[o++] = 0;
    d[o++] = 51; d[o++] = 4; _putBe32(d + o, 86400); o += 4;
    d[o++] = 58; d[o++] = 4; _putBe32(d + o, 43200); o += 4;
    d[o++] = 59; d[o++] = 4; _putBe32(d + o, 75600); o += 4;
    d[o++] = 28; d[o++] = 4; d[o++] = 192; d[o++] = 168; d[o++] = 55; d[o++] = 255;
    d[o++] = 255;

    const char* label = replyType == 2 ? "DHCP-OFFER" : "DHCP-ACK";
    LOG("A7-P0.2 %s 192.168.55.2 from 192.168.55.1 (no router/DNS option)\n", label);
    _sendNcmEthernetFrame(f, frameLen, label);
}

static void _handleDhcp(const u8* eth, u16 ethLen, const u8* ip, u8 ihl)
{
    if (ethLen < (u16)(14 + ihl + 8 + 240)) return;
    const u8* udp = ip + ihl;
    if (_be16(udp) != 68 || _be16(udp + 2) != 67) return;
    const u16 udpLen = _be16(udp + 4);
    if (udpLen < 8 + 240 || (u32)(14 + ihl + udpLen) > ethLen) return;
    const u8* dhcp = udp + 8;
    const size_t dhcpLen = udpLen - 8;
    int msgType = _findDhcpMessageType(dhcp, dhcpLen);
    if (msgType == 1) {
        LOG("A7-P0.2 DHCP DISCOVER received\n");
        _sendDhcpReply(dhcp, dhcpLen, 2);
    } else if (msgType == 3) {
        LOG("A7-P0.2 DHCP REQUEST received\n");
        _sendDhcpReply(dhcp, dhcpLen, 5);
    } else if (msgType >= 0) {
        LOG("A7-P0.2 DHCP message type=%d observed\n", msgType);
    }
}

static void _handleArp(const u8* eth, u16 ethLen)
{
    if (ethLen < 42) return;
    const u8* a = eth + 14;
    const u16 op = _be16(a + 6);
    LOG("A7-P0.2 ARP op=%u spa=%u.%u.%u.%u tpa=%u.%u.%u.%u\n",
        op, a[14], a[15], a[16], a[17], a[24], a[25], a[26], a[27]);
    if (op != 1 || !_ipEq(a + 24, g_p02ServerIp)) return;

    u8* f = g_p02Frame;
    memset(f, 0, 42);
    memcpy(f, eth + 6, 6);                    // requester Ethernet MAC
    memcpy(f + 6, g_p02ServerMac, 6);
    _putBe16(f + 12, 0x0806);
    u8* r = f + 14;
    _putBe16(r + 0, 1);                       // Ethernet
    _putBe16(r + 2, 0x0800);                  // IPv4
    r[4] = 6; r[5] = 4;
    _putBe16(r + 6, 2);                       // ARP reply
    memcpy(r + 8, g_p02ServerMac, 6);
    memcpy(r + 14, g_p02ServerIp, 4);
    memcpy(r + 18, a + 8, 6);                 // target HW = requester SHA
    memcpy(r + 24, a + 14, 4);                // target IP = requester SPA
    LOG("A7-P0.2 ARP reply: 192.168.55.1 is 02:00:00:00:00:02\n");
    _sendNcmEthernetFrame(f, 42, "ARP-REPLY");
}

static void _handleIcmp(const u8* eth, u16 ethLen, const u8* ip, u8 ihl)
{
    if (ethLen > sizeof(g_p02Frame) || ethLen < (u16)(14 + ihl + 8)) return;
    const u16 totalLen = _be16(ip + 2);
    if (totalLen < ihl + 8 || (u32)14 + totalLen > ethLen) return;
    if (!_ipEq(ip + 16, g_p02ServerIp)) return;
    const u8* icmp = ip + ihl;
    if (icmp[0] != 8 || icmp[1] != 0) return;  // Echo Request

    memcpy(g_p02Frame, eth, ethLen);
    u8* f = g_p02Frame;
    memcpy(f, eth + 6, 6);
    memcpy(f + 6, g_p02ServerMac, 6);
    u8* rip = f + 14;
    memcpy(rip + 16, ip + 12, 4);
    memcpy(rip + 12, g_p02ServerIp, 4);
    rip[8] = 64;
    _putBe16(rip + 10, 0);
    _putBe16(rip + 10, _checksum16(rip, ihl));

    u8* ricmp = rip + ihl;
    const u16 icmpLen = totalLen - ihl;
    ricmp[0] = 0;                             // Echo Reply
    ricmp[1] = 0;
    _putBe16(ricmp + 2, 0);
    _putBe16(ricmp + 2, _checksum16(ricmp, icmpLen));
    LOG("A7-P0.2 ICMP echo request -> reply\n");
    _sendNcmEthernetFrame(f, ethLen, "ICMP-ECHO-REPLY");
}

static void _logNcmRx(const u8* p, u32 len)
{
    if (len < sizeof(Nth16)) {
        LOG("A7-P0.2 NCM RX short: %u bytes\n", len);
        return;
    }
    const Nth16* nth = (const Nth16*)p;
    LOG("A7-P0.2 NCM RX: bytes=%u sig=0x%08x hdr=%u seq=%u block=%u ndp=%u\n",
        len, nth->signature, nth->headerLength, nth->sequence, nth->blockLength, nth->ndpIndex);

    if (nth->signature != NTH16_SIGNATURE || nth->ndpIndex + sizeof(Ndp16) + 2 * sizeof(Ndp16Entry) > len)
        return;
    const Ndp16* ndp = (const Ndp16*)(p + nth->ndpIndex);
    if (ndp->signature != NDP16_SIGNATURE_0 && ndp->signature != NDP16_SIGNATURE_1)
        return;
    const Ndp16Entry* e = (const Ndp16Entry*)((const u8*)ndp + sizeof(Ndp16));
    if (!e->datagramIndex || e->datagramLength < 14 ||
        (u32)e->datagramIndex + e->datagramLength > len)
        return;

    const u8* eth = p + e->datagramIndex;
    const u16 ethLen = e->datagramLength;
    const u16 etherType = _be16(eth + 12);
    LOG("A7-P0.2 Ethernet frame: off=%u len=%u type=0x%04x src=%02x:%02x:%02x:%02x:%02x:%02x dst=%02x:%02x:%02x:%02x:%02x:%02x\n",
        e->datagramIndex, ethLen, etherType,
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
    LOG("A7-P0.2 IPv4 proto=%u src=%u.%u.%u.%u dst=%u.%u.%u.%u\n",
        ip[9], ip[12], ip[13], ip[14], ip[15], ip[16], ip[17], ip[18], ip[19]);
    if (ip[9] == 17 && ethLen >= (u16)(14 + ihl + 8)) {
        const u8* udp = ip + ihl;
        LOG("A7-P0.2 IPv4 UDP: %u -> %u\n", _be16(udp), _be16(udp + 2));
        _handleDhcp(eth, ethLen, ip, ihl);
    } else if (ip[9] == 1) {
        _handleIcmp(eth, ethLen, ip, ihl);
    }
}

'''

s = s[:start] + replacement + s[end:]

# Promote the whole inherited diagnostic to P0.2 markers.
s = s.replace('A7-P0.1 ', 'A7-P0.2 ')
p.write_text(s)

# Runtime banner: make it unambiguous that this build includes active IPv4 TX.
p = root / 'source/sysmodule/main.c'
ms = p.read_text().replace('A7-P0.1', 'A7-P0.2')
ms = ms.replace('A7-P0.2 SysDVR USB-NCM RX-fix diagnostic',
                'A7-P0.2 SysDVR USB-NCM IPv4 POC diagnostic')
ms = ms.replace('A7-P0.2 running. Connect iPad through the proven USB hub topology.',
                'A7-P0.2 running: DHCP 192.168.55.2, server 192.168.55.1; connect iPad through proven hub topology.')
p.write_text(ms)

print('A7-P0.2 NCM IPv4 DHCP/ARP/ICMP POC patch applied')
