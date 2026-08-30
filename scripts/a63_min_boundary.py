from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/a63')
p = root / 'source/uvc/uvc_device.c'
s = p.read_text()

# A6.3-MIN: keep native H.264, intentionally omit the 6-byte Color Matching
# descriptor, and append the endpoint immediately after the H.264 frame.
# This isolates the suspected usb:ds per-interface configuration-data limit:
# 123 bytes before endpoint, 130 bytes after endpoint.
old_total = '        .wTotalLength = sizeof(g_vsDescriptors),\n'
new_total = '        .wTotalLength = sizeof(g_vsDescriptors) - sizeof(g_vsDescriptors.colorMatching),\n'
if old_total not in s:
    raise SystemExit('A6.3 wTotalLength anchor missing')
s = s.replace(old_total, new_total, 1)

start_marker = '    // Append VS descriptors (single alt 0: interface → VS class-specific → bulk EP)\n'
end_marker = '    // Register the video endpoint on VS\n'
start = s.index(start_marker)
end = s.index(end_marker, start)

block = r'''    // Append VS descriptors (single alt 0: interface → VS class-specific → bulk EP)
    // A6.3-MIN boundary test: omit Color Matching and test endpoint at cumulative 130 bytes.
#define A63_APPEND(label, iface, speed, ptr, sz, cumulative) do { \
        LOG("A6.3 append %-18s speed=%u size=%u cumulative=%u -> ", label, (unsigned)(speed), (unsigned)(sz), (unsigned)(cumulative)); \
        rc = usbDsInterface_AppendConfigurationData((iface), (speed), (ptr), (sz)); \
        LOG("rc=0x%x\n", rc); \
        if (R_FAILED(rc)) return rc; \
    } while (0)

    // High Speed VS: 9 + 14 + 52 + 48 = 123 bytes before endpoint.
    A63_APPEND("HS interface", g_uvcCtx.streamingInterface, UsbDeviceSpeed_High,
        &g_vsInterfaceDesc0, USB_DT_INTERFACE_SIZE, 9);
    A63_APPEND("HS input-header", g_uvcCtx.streamingInterface, UsbDeviceSpeed_High,
        &g_vsDescriptors.inputHeader, sizeof(g_vsDescriptors.inputHeader), 23);
    A63_APPEND("HS H264-format", g_uvcCtx.streamingInterface, UsbDeviceSpeed_High,
        &g_vsDescriptors.h264Format, sizeof(g_vsDescriptors.h264Format), 75);
    A63_APPEND("HS H264-frame", g_uvcCtx.streamingInterface, UsbDeviceSpeed_High,
        &g_vsDescriptors.h264Frame, sizeof(g_vsDescriptors.h264Frame), 123);
    LOG("A6.3 OMIT HS color        size=%u cumulative stays=123\n", (unsigned)sizeof(g_vsDescriptors.colorMatching));
    g_videoEndpointDesc.wMaxPacketSize = 512;
    A63_APPEND("HS endpoint", g_uvcCtx.streamingInterface, UsbDeviceSpeed_High,
        &g_videoEndpointDesc, USB_DT_ENDPOINT_SIZE, 130);

    // Super Speed VS, reached only if the HS endpoint unexpectedly succeeds.
    A63_APPEND("SS interface", g_uvcCtx.streamingInterface, UsbDeviceSpeed_Super,
        &g_vsInterfaceDesc0, USB_DT_INTERFACE_SIZE, 9);
    A63_APPEND("SS input-header", g_uvcCtx.streamingInterface, UsbDeviceSpeed_Super,
        &g_vsDescriptors.inputHeader, sizeof(g_vsDescriptors.inputHeader), 23);
    A63_APPEND("SS H264-format", g_uvcCtx.streamingInterface, UsbDeviceSpeed_Super,
        &g_vsDescriptors.h264Format, sizeof(g_vsDescriptors.h264Format), 75);
    A63_APPEND("SS H264-frame", g_uvcCtx.streamingInterface, UsbDeviceSpeed_Super,
        &g_vsDescriptors.h264Frame, sizeof(g_vsDescriptors.h264Frame), 123);
    LOG("A6.3 OMIT SS color        size=%u cumulative stays=123\n", (unsigned)sizeof(g_vsDescriptors.colorMatching));
    g_videoEndpointDesc.wMaxPacketSize = 1024;
    A63_APPEND("SS endpoint", g_uvcCtx.streamingInterface, UsbDeviceSpeed_Super,
        &g_videoEndpointDesc, USB_DT_ENDPOINT_SIZE, 130);
    A63_APPEND("SS companion", g_uvcCtx.streamingInterface, UsbDeviceSpeed_Super,
        &g_ssCompanionDesc, sizeof(g_ssCompanionDesc), 136);

#undef A63_APPEND

'''
s = s[:start] + block + s[end:]

old_sizes = '    LOG("A6.1 VS sizes: header=%u format=%u frame=%u color=%u total=%u\\n", (unsigned)sizeof(g_vsDescriptors.inputHeader), (unsigned)sizeof(g_vsDescriptors.h264Format), (unsigned)sizeof(g_vsDescriptors.h264Frame), (unsigned)sizeof(g_vsDescriptors.colorMatching), (unsigned)sizeof(g_vsDescriptors));\n'
new_sizes = '    LOG("A6.3 VS sizes: header=%u format=%u frame=%u color=%u structTotal=%u advertisedCSNoColor=%u\\n", (unsigned)sizeof(g_vsDescriptors.inputHeader), (unsigned)sizeof(g_vsDescriptors.h264Format), (unsigned)sizeof(g_vsDescriptors.h264Frame), (unsigned)sizeof(g_vsDescriptors.colorMatching), (unsigned)sizeof(g_vsDescriptors), (unsigned)(sizeof(g_vsDescriptors) - sizeof(g_vsDescriptors.colorMatching)));\n'
if old_sizes not in s:
    raise SystemExit('A6.3 size-log anchor missing')
s = s.replace(old_sizes, new_sizes, 1)

p.write_text(s)

# Rename A6.1 monitor/banner so the runtime log cannot be confused with older builds.
p = root / 'source/sysmodule/main.c'
s = p.read_text()
s = s.replace('A61UsbStateName', 'A63UsbStateName')
s = s.replace('A6.1-NATIVE-H264-SPLIT-DIAG', 'A6.3-MIN-NATIVE-H264-BOUNDARY-DIAG')
s = s.replace('A6.1 USB state', 'A6.3 USB state')
s = s.replace('A6.1 heartbeat', 'A6.3 heartbeat')
p.write_text(s)

print('A6.3-MIN boundary patch applied')
