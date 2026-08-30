from pathlib import Path
import sys


root = Path(sys.argv[1] if len(sys.argv) > 1 else "/work/sysdvr/sysmodule")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"P2.1.2 {label}: expected one anchor, found {text.count(old)}")
    return text.replace(old, new, 1)


# P2.1 introduced a shared eight-frame NTB aggregator.  That is correct for
# stream data, but the original patch also queued SYN/SYN-ACK, pure ACK, the
# SysDVR hello and the handshake result.  A short control exchange can never
# fill eight slots, so peers retransmit SYN and eventually close/reset before
# streaming begins.  Keep aggregation for media payloads, but flush every TCP
# control segment and every protocol-control payload immediately.
p = root / "source/ncm/ncm_device.c"
s = p.read_text()

quiet_wrapper = '''static Result _sendNcmEthernetFrameQuiet(const u8* frame, u16 frameLen)
{
    mutexLock(&g_a7p2TxMutex);
    Result rc = _sendNcmEthernetFrameQuietUnlocked(frame, frameLen);
    mutexUnlock(&g_a7p2TxMutex);
    return rc;
}

'''
quiet_wrapper_new = quiet_wrapper + '''static Result _sendNcmEthernetFrameQuietFlushed(const u8* frame, u16 frameLen)
{
    mutexLock(&g_a7p2TxMutex);
    Result rc = _sendNcmEthernetFrameQuietUnlocked(frame, frameLen);
    if (R_SUCCEEDED(rc)) rc = _a7p21FlushAggUnlocked();
    mutexUnlock(&g_a7p2TxMutex);
    return rc;
}

'''
s = replace_once(s, quiet_wrapper, quiet_wrapper_new, "flushed sender insertion")

builder_return = "    return _sendNcmEthernetFrameQuiet(f, frameLen);\n"
builder_return_new = '''    if (payloadLen == 0)
        return _sendNcmEthernetFrameQuietFlushed(f, frameLen);
    return _sendNcmEthernetFrameQuiet(f, frameLen);
'''
if s.count(builder_return) != 2:
    raise SystemExit(
        f"P2.1.2 TCP builder return: expected two anchors, found {s.count(builder_return)}"
    )
s = s.replace(builder_return, builder_return_new, 2)

video_control = '''    Result rc = _tcpSendSegment(g_tcpServerNextSeq, g_tcpClientNextSeq,
                                TCP_FLAG_ACK | TCP_FLAG_PSH, data, len);
    if (R_FAILED(rc)) return false;
    g_tcpServerNextSeq += len;
    return true;
'''
video_control_new = '''    Result rc = _tcpSendSegment(g_tcpServerNextSeq, g_tcpClientNextSeq,
                                TCP_FLAG_ACK | TCP_FLAG_PSH, data, len);
    if (R_FAILED(rc) || !_a7p21FlushQueuedTx()) return false;
    g_tcpServerNextSeq += len;
    return true;
'''
s = replace_once(s, video_control, video_control_new, "video protocol-control flush")

audio_control = '''    Result rc = _audioTcpSendSegment(g_audioTcpServerNextSeq, g_audioTcpClientNextSeq,
                                     TCP_FLAG_ACK | TCP_FLAG_PSH, data, len);
    if (R_FAILED(rc)) return false;
    g_audioTcpServerNextSeq += len;
    return true;
'''
audio_control_new = '''    Result rc = _audioTcpSendSegment(g_audioTcpServerNextSeq, g_audioTcpClientNextSeq,
                                     TCP_FLAG_ACK | TCP_FLAG_PSH, data, len);
    if (R_FAILED(rc) || !_a7p21FlushQueuedTx()) return false;
    g_audioTcpServerNextSeq += len;
    return true;
'''
s = replace_once(s, audio_control, audio_control_new, "audio protocol-control flush")

p.write_text(s)


p = root / "source/core.c"
s = p.read_text()
s = replace_once(
    s,
    "A7-P2.1.1 raw SD logfile initialized BUILD=P211",
    "A7-P2.1.2 raw SD logfile initialized BUILD=P212",
    "core build marker",
)
p.write_text(s)


p = root / "source/ncm/NCMmode.c"
s = p.read_text()
s = replace_once(
    s,
    "A7-P2.1.1 SysDVR 6.3 + USB-NCM aggregated A/V mode starting BUILD=P211 NTB=16384 DATAGRAMS=8",
    "A7-P2.1.2 SysDVR 6.3 + USB-NCM control-flush A/V mode starting BUILD=P212 NTB=16384 DATAGRAMS=8",
    "mode build marker",
)
s = replace_once(
    s,
    "A7-P2 NCM ready: DHCP",
    "A7-P2.1.2 NCM ready BUILD=P212: DHCP",
    "ready build marker",
)
# The generated P2 NCM mode used a literal "\\n" sequence in several raw
# patch strings.  Make the two P2.1.2 proof lines real, separately terminated
# records so deployment checks remain readable even when threads log together.
s = replace_once(
    s,
    'DATAGRAMS=8\\\\n");',
    'DATAGRAMS=8\\n");',
    "mode marker newline",
)
s = replace_once(
    s,
    'protocol=03\\\\n");',
    'protocol=03\\n");',
    "ready marker newline",
)
p.write_text(s)

print("A7-P2.1.2 immediate TCP control flush patch applied")
