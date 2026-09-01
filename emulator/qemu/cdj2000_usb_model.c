/*
 * Pioneer CDJ-2000 USB controller — what it says.
 *
 * The bus surface is cdj2000_usb.c; this file is the whole of the controller's
 * behaviour, and it is deliberately almost nothing.  Every access the firmware
 * makes to this chip during bring-up is a read-modify-write of bits it set
 * itself (see cdj2000_usb.h for the list, each with the address that proves it),
 * so a register file that remembers what was written answers all of them
 * correctly without a datasheet and without inventing a value.
 *
 * Two things are not the guest's to decide, and those are modelled here:
 *
 *   - the enable bit.  0x2399c8 writes 1 to W[0x00] and then polls bit 0 until
 *     it reads back, 255 attempts.  A register file satisfies that by itself,
 *     but the bit is named and watched here because it is the single condition
 *     that decides between caution 61 and a working port.
 *   - VBUS.  W[0x18] bit 7 is only ever read, never written, and the strings
 *     next to its reader say "USBF Init Vbus = %d" / "USBF Vbus ON" / "USBF Vbus
 *     OFF".  It is the one input that comes from outside the machine: whether a
 *     computer is plugged into the type-B socket.  It defaults to **off**,
 *     because a player switched on with nothing in that socket is the normal
 *     case and must not be an error.  CDJ_USB_VBUS=1 says a host is attached.
 *
 * Copyright (C) 2026 LycheeAPPF
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/log.h"

#include "cdj2000_usb.h"

struct CdjUsbModel {
    /*
     * The register file, 16 bits per entry.  Only offsets below 0x80 are ever
     * touched by the image; the rest of the mapped page reads as zero and is
     * reported by the bus surface instead of being stored.
     */
    uint16_t reg[0x40];
    bool absent;                /* CDJ_USB_ABSENT: answer like unmapped memory */
    bool vbus;                  /* CDJ_USB_VBUS: a host is plugged into USB-B */
    bool enabled;               /* the guest has set bit 0 of 0x00 */
};

#define CDJ_USB_REG_SPAN  (sizeof(((CdjUsbModel *)0)->reg) / sizeof(uint16_t) * 2)

CdjUsbModel *cdj_usb_model_new(bool absent, bool vbus)
{
    CdjUsbModel *model = g_new0(CdjUsbModel, 1);

    model->absent = absent;
    model->vbus = vbus;
    cdj_usb_model_reset(model);
    return model;
}

void cdj_usb_model_reset(CdjUsbModel *model)
{
    memset(model->reg, 0, sizeof(model->reg));
    model->enabled = false;
}

bool cdj_usb_model_enabled(const CdjUsbModel *model)
{
    return model->enabled;
}

uint16_t cdj_usb_model_read(CdjUsbModel *model, hwaddr offset)
{
    uint16_t value;

    /*
     * The control arm.  Unmapped memory on this board reads zero, so answering
     * zero here reproduces the machine before the device existed exactly --
     * including the 255 futile attempts of 0x2399c8 -- while still going through
     * the same code path, so a trace shows what the driver was asking for.
     */
    if (model->absent || offset >= CDJ_USB_REG_SPAN) {
        return 0;
    }

    value = model->reg[offset / 2];

    /*
     * VBUS is the model's to answer, not the register file's: nothing in the
     * image ever writes 0x18, so whatever is in the file there came from a
     * reset, and the bit means "a computer is on the other end of the cable".
     */
    if (offset == CDJ_USB_STATUS) {
        value = (value & (uint16_t)~CDJ_USB_VBUS_BIT)
              | (model->vbus ? CDJ_USB_VBUS_BIT : 0);
    }
    return value;
}

void cdj_usb_model_write(CdjUsbModel *model, hwaddr offset, uint16_t value)
{
    if (model->absent || offset >= CDJ_USB_REG_SPAN) {
        return;
    }

    model->reg[offset / 2] = value;

    if (offset == CDJ_USB_ENABLE) {
        /*
         * 0x2399bc turns the module off and 0x2399c8 turns it on; the poll that
         * follows the second is the one that decides whether MAIN reports the
         * port broken.  Tracking it costs nothing and makes the trace say
         * "the module came up" rather than "a word was written".
         */
        model->enabled = (value & CDJ_USB_ENABLE_BIT) != 0;
    }
}
