/*
 * Pioneer CDJ-2000 USB controller — the bus surface.
 *
 * MAIN reports `E-7020: USB-B DEVICE ERROR` because the USB function task
 * cannot bring its controller up, and it cannot bring it up because the chip is
 * not on the bus.  cdj2000_usb.h has the whole derivation: the device-status
 * array at 0x04c0875c, the switch in 0x24fd20 that turns device 3 into caution
 * 61, and the nine-instruction enable-and-poll at 0x2399c8 that fails.
 *
 * This file is only the window.  The controller is a 16-bit register file at
 * physical 0x01000000 — the firmware builds that address four separate times
 * with the same `#-95 << 8 << 16`, so it is not inferred — and every access is
 * 16 bits wide.  What the registers answer is cdj2000_usb_model.c's business.
 *
 * Copyright (C) 2026 LycheeAPPF
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/log.h"
#include "qapi/error.h"
#include "system/memory.h"

#include "cdj2000_usb.h"

typedef struct CdjUsbState {
    MemoryRegion iomem;
    CdjUsbModel *model;
    bool trace;
    bool announced;             /* the "module came up" line is worth once */
} CdjUsbState;

static uint64_t cdj_usb_read(void *opaque, hwaddr offset, unsigned size)
{
    CdjUsbState *usb = opaque;
    uint16_t value = cdj_usb_model_read(usb->model, offset);

    if (usb->trace) {
        qemu_log("cdj2000-usb: read  %#04" HWADDR_PRIx " (%u) = %#06x\n",
                 offset, size, value);
    }
    return value;
}

static void cdj_usb_write(void *opaque, hwaddr offset, uint64_t value,
                          unsigned size)
{
    CdjUsbState *usb = opaque;

    cdj_usb_model_write(usb->model, offset, (uint16_t)value);

    if (usb->trace) {
        qemu_log("cdj2000-usb: write %#04" HWADDR_PRIx " (%u) = %#06x\n",
                 offset, size, (uint16_t)value);
    }
    if (!usb->announced && cdj_usb_model_enabled(usb->model)) {
        usb->announced = true;
        qemu_log("cdj2000-usb: the module is enabled; 0x2399c8 will see bit 0\n");
    }
}

/*
 * 16-bit only.  Every instruction in the image that touches this chip is a
 * `mov.w`, so a wider access would be a bug worth seeing rather than something
 * to accommodate silently.
 */
static const MemoryRegionOps cdj_usb_ops = {
    .read = cdj_usb_read,
    .write = cdj_usb_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 2,
    .valid.max_access_size = 2,
    .impl.min_access_size = 2,
    .impl.max_access_size = 2,
};

void cdj_usb_init(MemoryRegion *system)
{
    CdjUsbState *usb = g_new0(CdjUsbState, 1);
    bool absent = getenv("CDJ_USB_ABSENT") != NULL;
    bool vbus = getenv("CDJ_USB_VBUS") != NULL;

    usb->trace = getenv("CDJ_USB_TRACE") != NULL;
    usb->model = cdj_usb_model_new(absent, vbus);

    memory_region_init_io(&usb->iomem, NULL, &cdj_usb_ops, usb,
                          "cdj2000.usb", CDJ_USB_SIZE);
    memory_region_add_subregion(system, CDJ_USB_BASE, &usb->iomem);

    if (absent) {
        qemu_log("cdj2000-usb: CDJ_USB_ABSENT -- the window answers zero, "
                 "which is the machine before this device existed\n");
    }
}
