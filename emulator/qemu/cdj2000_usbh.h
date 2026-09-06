/*
 * Pioneer CDJ-2000 -- the SH7764's USB 2.0 host/function module, as a host.
 *
 * The type-A socket (a memory stick, and the .UPD files a firmware update is
 * read from) is not the chip at 0x01000000 that cdj2000_usb.c models; that one
 * is the type-B function controller.  The stick hangs off the SoC's own USB
 * module, whose registers start at P4 0xfe400000 (SH7764 hardware manual,
 * section 21), and the evidence that this is the port the updater uses is in
 * the loader image rather than inferred: `usbh_load` (0x0400b2c8) hands
 * 0xfe400000 to the Cente USBH driver's port-open call (0x0400c598), the
 * driver's init (0x0400c608) then sets SYSCFG's USBE, DCFM (host function),
 * HSE, DRPD and SCKE bits in that order, and the updater itself only ever
 * starts when `usbh_msc_attach_drive` (0x0400f05c) has given the mounted stick
 * a drive letter.  Nothing else writes that letter; the SD card cannot start
 * an update.
 *
 * The module is exposed to QEMU's USB core as a host controller with one root
 * port, so `-device usb-storage,drive=...` plugs a real mass-storage device
 * into it and the descriptors, the bulk-only transport and the SCSI commands
 * are QEMU's, not ours.  What this file models is the register-level contract
 * the Cente driver relies on -- pipes, FIFO ports, the DCP's setup/data/status
 * stages, the transaction counter, and the BRDY/NRDY/BEMP/BCHG/SACK/SIGN
 * interrupts -- plus the DMAC side door: bulk transfers of a maximum packet or
 * more go through DMAC channel 0 reading the D0FIFO burst port at 0xfe400180
 * (driver 0x0400b3a0 programs SAR/DAR/TCR/CHCR, TCR in 32-byte units), and
 * the board's DMAC hands those to cdj_usbh_dma_start() below.
 *
 * Copyright (C) 2026 LycheeAPPF
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef CDJ2000_USBH_H
#define CDJ2000_USBH_H

#include "exec/hwaddr.h"
#include "system/memory.h"
#include "hw/core/irq.h"

/* Physical (area 7) address of P4 0xfe400000; the module is mapped at both. */
#define CDJ_USBH_BASE           0x1e400000
#define CDJ_USBH_SIZE           0x200
/* The FIFO burst windows the DMAC is pointed at (manual table 21.2). */
#define CDJ_USBH_D0FIFO_BURST   0x180
#define CDJ_USBH_D1FIFO_BURST   0x1c0

/*
 * Called when a DMA the DMAC handed over has moved every byte: the board
 * completes the channel and raises the channel's own vector.
 */
typedef void (*CdjUsbhDmaDone)(void *opaque, unsigned channel,
                               uint32_t nr_bytes);

void cdj_usbh_init(MemoryRegion *system, qemu_irq irq,
                   CdjUsbhDmaDone dma_done, void *dma_opaque);

/*
 * A DMAC channel started with one end on a FIFO burst window.  `offset` is the
 * window's offset within the module (0x180 or 0x1c0), `to_fifo` says which end
 * is memory, `memory` is that end's physical address.  Returns false if the
 * module is not present.  The transfer proceeds as packets move; the done
 * callback fires when nr_bytes have been moved.
 */
bool cdj_usbh_dma_start(hwaddr offset, bool to_fifo, hwaddr memory,
                        uint32_t nr_bytes, unsigned channel);

#endif
