from pathlib import Path
import sys

root = Path(sys.argv[1] if len(sys.argv) > 1 else '/work/a7p01')
p = root / 'source/usbnet/ncm_device.c'
s = p.read_text()

# usb:ds/libnx natively models alternate setting 0. Keep the standards-visible
# NCM alt1 for iPadOS, but duplicate the same bulk pipes under alt0 so the
# Switch device controller can activate/register them.
old = '''static struct usb_interface_descriptor g_dataInterfaceDesc0 = {
    .bLength = USB_DT_INTERFACE_SIZE,
    .bDescriptorType = USB_DT_INTERFACE,
    .bInterfaceNumber = 1,
    .bAlternateSetting = 0,
    .bNumEndpoints = 0,
'''
new = '''static struct usb_interface_descriptor g_dataInterfaceDesc0 = {
    .bLength = USB_DT_INTERFACE_SIZE,
    .bDescriptorType = USB_DT_INTERFACE,
    .bInterfaceNumber = 1,
    .bAlternateSetting = 0,
    .bNumEndpoints = 2,
'''
if old not in s:
    raise SystemExit('alt0 descriptor anchor missing')
s = s.replace(old, new, 1)

if '#define NCM_NTB_MAX_SIZE    16384u' not in s:
    raise SystemExit('NTB size anchor missing')
s = s.replace('#define NCM_NTB_MAX_SIZE    16384u', '#define NCM_NTB_MAX_SIZE    4096u', 1)

# Insert a duplicate HS bulk pair immediately under alt0. The original bulk pair
# remains after alt1 and is therefore still standards-visible to the host.
old = '''    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS data-alt0", &g_dataInterfaceDesc0, sizeof(g_dataInterfaceDesc0), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS data-alt1", &g_dataInterfaceDesc1, sizeof(g_dataInterfaceDesc1), &cum);
'''
new = '''    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS data-alt0", &g_dataInterfaceDesc0, sizeof(g_dataInterfaceDesc0), &cum);
    if (R_FAILED(rc)) return rc;
    g_dataInEndpointDesc.wMaxPacketSize = 512;
    g_dataOutEndpointDesc.wMaxPacketSize = 512;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS alt0-data-in", &g_dataInEndpointDesc, sizeof(g_dataInEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS alt0-data-out", &g_dataOutEndpointDesc, sizeof(g_dataOutEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_High, "HS data-alt1", &g_dataInterfaceDesc1, sizeof(g_dataInterfaceDesc1), &cum);
'''
if old not in s:
    raise SystemExit('HS alt0 insertion anchor missing')
s = s.replace(old, new, 1)

# Same workaround for SuperSpeed, including companion descriptors.
old = '''    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS data-alt0", &g_dataInterfaceDesc0, sizeof(g_dataInterfaceDesc0), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS data-alt1", &g_dataInterfaceDesc1, sizeof(g_dataInterfaceDesc1), &cum);
'''
new = '''    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS data-alt0", &g_dataInterfaceDesc0, sizeof(g_dataInterfaceDesc0), &cum);
    if (R_FAILED(rc)) return rc;
    g_dataInEndpointDesc.wMaxPacketSize = 1024;
    g_dataOutEndpointDesc.wMaxPacketSize = 1024;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS alt0-data-in", &g_dataInEndpointDesc, sizeof(g_dataInEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS alt0-data-in-comp", &g_ssBulkCompanion, sizeof(g_ssBulkCompanion), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS alt0-data-out", &g_dataOutEndpointDesc, sizeof(g_dataOutEndpointDesc), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS alt0-data-out-comp", &g_ssBulkCompanion, sizeof(g_ssBulkCompanion), &cum);
    if (R_FAILED(rc)) return rc;
    rc = _append(g_ctx.dataInterface, UsbDeviceSpeed_Super, "SS data-alt1", &g_dataInterfaceDesc1, sizeof(g_dataInterfaceDesc1), &cum);
'''
if old not in s:
    raise SystemExit('SS alt0 insertion anchor missing')
s = s.replace(old, new, 1)

# Replace _postOut by function boundaries instead of a fragile full-text anchor.
start = s.find('static void _postOut(void)')
end = s.find('static void _pollOut(void)', start)
if start < 0 or end < 0:
    raise SystemExit('_postOut function boundaries missing')
new_post = r'''static void _postOut(void)
{
    static Result lastArmRc = 0;
    static bool haveLastArmRc = false;
    if (!g_ctx.configured || g_ctx.dataAlt != 1 || g_ctx.outPending || !g_ctx.dataEndpointOut) return;

    Result readyRc = usbDsWaitReady(500000000ULL);
    if (R_FAILED(readyRc)) {
        if (!haveLastArmRc || lastArmRc != readyRc) {
            LOG("A7-P0.1 usbDsWaitReady before NCM OUT failed: 0x%x\n", readyRc);
            lastArmRc = readyRc;
            haveLastArmRc = true;
        }
        return;
    }

    memset(g_dataOutBuffer, 0, sizeof(g_dataOutBuffer));
    eventClear(&g_ctx.dataEndpointOut->CompletionEvent);
    Result rc = usbDsEndpoint_PostBufferAsync(g_ctx.dataEndpointOut,
        g_dataOutBuffer, sizeof(g_dataOutBuffer), &g_ctx.outUrbId);
    if (R_SUCCEEDED(rc)) {
        g_ctx.outPending = true;
        haveLastArmRc = false;
        LOG("A7-P0.1 NCM OUT armed (%u bytes)\n", (unsigned)sizeof(g_dataOutBuffer));
    } else if (!haveLastArmRc || lastArmRc != rc) {
        LOG("A7-P0.1 NCM OUT arm failed: 0x%x\n", rc);
        lastArmRc = rc;
        haveLastArmRc = true;
    }
}

'''
s = s[:start] + new_post + s[end:]

# Arm receive before link-up notification so the first host packet cannot race us.
old = '''                else {
                    _sendLinkNotifications();
                    _postOut();
                }
'''
new = '''                else {
                    _postOut();
                    _sendLinkNotifications();
                }
'''
if old not in s:
    raise SystemExit('SET_INTERFACE order anchor missing')
s = s.replace(old, new, 1)

old = '''    _sendLinkNotifications();
    _postOut();
    _pollOut();
'''
new = '''    _postOut();
    _sendLinkNotifications();
    _pollOut();
'''
if old not in s:
    raise SystemExit('process-loop order anchor missing')
s = s.replace(old, new, 1)

# Version base P0 markers, without touching P0.1 strings inserted above.
s = s.replace('A7-P0 ', 'A7-P0.1 ')
p.write_text(s)

# Runtime banner must prove the exact build without double-versioning.
p = root / 'source/sysmodule/main.c'
ms = p.read_text().replace('A7-P0', 'A7-P0.1')
ms = ms.replace('A7-P0.1 SysDVR USB-NCM binding diagnostic',
                'A7-P0.1 SysDVR USB-NCM RX-fix diagnostic')
p.write_text(ms)

print('A7-P0.1 NCM RX fix patch applied')
