/*
 * Pioneer CDJ-2000 audio DSP — the bus surface.
 *
 * MAIN reports `E-7010: DSP DEVICE ERROR` because it looks for an audio DSP and
 * finds nothing.  That is not a GUI fault and not a cosmetic one: MAIN's whole
 * transport layer sits behind the DSP, so without it the time fields, tempo,
 * cue and track loading stay dead.  This file gives the DSP a bus to live on;
 * cdj2000_dsp_model.c decides what it says.
 *
 * Everything below is read off the firmware, not a datasheet.
 *
 * The shared window
 * -----------------
 * 0x1c747a and 0x1c74c0 arm one DMAC channel:
 *
 *     [0xff608080] = SAR      [0xff608084] = DAR
 *     [0xff608088] = len >> 2         <- longwords, not the boot channel's
 *     [0xff60808c] = 0x5430 (to the DSP) or 0x1430 (from it)
 *     [0xff608060] |= 1  (DMAOR DME) ; CHCR |= 1 (DE)
 *     spin until CHCR bit 1 (TE)                        <- 0x1c74b6
 *
 * and 0x1c75e4 clamps every transfer with
 *
 *     room = 0x10000 - (destination - 0xAC0C0000)
 *
 * which fixes the window at 64 KiB starting at 0xAC0C0000, i.e. physical
 * 0x0C0C0000.  It is modelled as plain RAM: the DMA has to be able to copy into
 * it without a special case, and both sides must see the same bytes.
 *
 * The mailbox
 * -----------
 * The top of the window is control, not data.  Three four-line accessors sit
 * next to each other and give the whole doorbell protocol:
 *
 *     0x2b0d6c   return L[0xac0cfffc]     read the answer
 *     0x2b0d72   L[0xac0cffec] = 0        lower the request
 *     0x2b0d7a   L[0xac0cffec] = 1        raise the request
 *
 * Those words are overlaid with MMIO on top of the RAM, because a request the
 * guest writes straight through the CPU is invisible in plain memory and the
 * model would never be told to run.  Their values are mirrored back into the
 * RAM image so the model sees one coherent window.
 *
 * The firmware
 * ------------
 * The DSP has no image of its own; MAIN carries it.  The bring-up at 0x1c77dc
 * reads a 16-byte header at 0xa4001000 — destination window offset in W[+0x0c],
 * a constant 0xfff0 in W[+0x0e] — and downloads the payload from 0xa4001010.
 * There are two records: 0xd3b0 bytes to window+0x1da0 and a second to
 * window+0x7800, which is exactly where the addresses MAIN reads all over the
 * image (0xac0c7ba0, 0xac0c7ccc, 0xac0c8140 …) live.  See
 * tools/cdj_main/dsp_image.py.
 *
 * Copyright (C) 2026 LycheeAPPF
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/log.h"
#include "qemu/timer.h"
#include "qapi/error.h"
#include "system/address-spaces.h"
#include "system/memory.h"

#include "cdj2000_dsp.h"

/*
 * The window control register.  Bit 0 is the run bit: 0x1c75c8 writes
 * `[0xAC000000] = (value & 0xFFFFF000) | 1` at the end of the reset sequence.
 * Bit 2 is set by 0x1c745e — which is the *success* arm of the ready poll, not
 * its timeout, so it is a handshake acknowledgement and not an error flag.  A
 * trace of a real bring-up reads 0 -> 4 -> 1 -> 5 -> 1, which is that pair
 * alternating exactly as the two writers predict.
 *
 * The register is read-modify-written, so it has to behave like memory before
 * any of it can mean anything.
 */
#define DSP_CTL_BASE          0x0C000000
#define DSP_CTL_SIZE          0x10
#define DSP_CTL_RUN           0x0001
#define DSP_CTL_ACK           0x0004

/* The mailbox overlay: the last 32 bytes of the window. */
#define DSP_MAILBOX_OFFSET    0xffe0
#define DSP_MAILBOX_SIZE      0x20

/* How often the model is given a chance to advance its own state. */
#define DSP_TICK_NS           (10 * 1000 * 1000)

typedef struct {
    MemoryRegion window;
    MemoryRegion mailbox;
    MemoryRegion ctl;

    uint8_t *ram;                       /* the window's host memory */
    uint32_t ctl_value;

    QEMUTimer *tick;
    CdjDspModel *model;

    /* Reporting, so a run says what the DSP was asked for without a debugger. */
    bool trace;
    uint64_t transfers;
    uint64_t doorbells;
} CdjDspState;

/* One DSP per machine, and the DMAC has to reach it from the board file. */
static CdjDspState *cdj_dsp;

/*
 * The mailbox words are ordinary window memory that this device merely watches:
 * reads and writes go to the same RAM the model sees, so there is one copy of
 * the truth and the model can answer by writing into the window like anything
 * else.  The overlay exists only so that a store by the guest is *observed* —
 * in plain RAM a request would be invisible and the model would never run.
 */
static uint64_t cdj_dsp_mailbox_read(void *opaque, hwaddr offset, unsigned size)
{
    CdjDspState *dsp = opaque;
    const uint8_t *at = dsp->ram + DSP_MAILBOX_OFFSET + offset;
    uint64_t value;

    switch (size) {
    case 1:  value = *at;            break;
    case 2:  value = lduw_le_p(at);  break;
    default: value = ldl_le_p(at);   break;
    }
    if (dsp->trace) {
        fprintf(stderr, "cdj2000-dsp: mailbox read  +0x%04x = 0x%08x\n",
                (unsigned)(DSP_MAILBOX_OFFSET + offset), (uint32_t)value);
    }
    return value;
}

static void cdj_dsp_mailbox_write(void *opaque, hwaddr offset, uint64_t value,
                                  unsigned size)
{
    CdjDspState *dsp = opaque;
    uint8_t *at = dsp->ram + DSP_MAILBOX_OFFSET + offset;
    uint32_t previous = ldl_le_p(dsp->ram + DSP_MAILBOX_OFFSET + (offset & ~3ull));

    switch (size) {
    case 1:  *at = value;            break;
    case 2:  stw_le_p(at, value);    break;
    default: stl_le_p(at, value);    break;
    }
    if (dsp->trace) {
        fprintf(stderr, "cdj2000-dsp: mailbox write +0x%04x = 0x%08x\n",
                (unsigned)(DSP_MAILBOX_OFFSET + offset), (uint32_t)value);
    }

    /* A rising edge of the request word is what runs the model. */
    if (DSP_MAILBOX_OFFSET + (offset & ~3ull) == CDJ_DSP_MAIL_REQ
        && !previous
        && ldl_le_p(dsp->ram + CDJ_DSP_MAIL_REQ)) {
        dsp->doorbells++;
        cdj_dsp_model_doorbell(dsp->model, dsp->ram, CDJ_DSP_WINDOW_SIZE);
    }
}

static const MemoryRegionOps cdj_dsp_mailbox_ops = {
    .read = cdj_dsp_mailbox_read,
    .write = cdj_dsp_mailbox_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
};

static uint64_t cdj_dsp_ctl_read(void *opaque, hwaddr offset, unsigned size)
{
    CdjDspState *dsp = opaque;

    return offset ? 0 : dsp->ctl_value;
}

static void cdj_dsp_ctl_write(void *opaque, hwaddr offset, uint64_t value,
                              unsigned size)
{
    CdjDspState *dsp = opaque;

    if (offset) {
        return;
    }
    if (dsp->trace && (dsp->ctl_value ^ value) & (DSP_CTL_RUN | DSP_CTL_ACK)) {
        fprintf(stderr, "cdj2000-dsp: control 0x%08x -> 0x%08x%s%s\n",
                dsp->ctl_value, (uint32_t)value,
                (value & DSP_CTL_RUN) ? " run" : "",
                (value & DSP_CTL_ACK) ? " ack" : "");
    }
    if ((value & DSP_CTL_RUN) && !(dsp->ctl_value & DSP_CTL_RUN)) {
        cdj_dsp_model_reset(dsp->model, dsp->ram, CDJ_DSP_WINDOW_SIZE);
    }
    dsp->ctl_value = value;
}

static const MemoryRegionOps cdj_dsp_ctl_ops = {
    .read = cdj_dsp_ctl_read,
    .write = cdj_dsp_ctl_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
};

static void cdj_dsp_tick(void *opaque)
{
    CdjDspState *dsp = opaque;

    cdj_dsp_model_tick(dsp->model, dsp->ram, CDJ_DSP_WINDOW_SIZE);
    timer_mod(dsp->tick, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + DSP_TICK_NS);
}

bool cdj_dsp_is_window(hwaddr address)
{
    return address >= CDJ_DSP_WINDOW_BASE
        && address < CDJ_DSP_WINDOW_BASE + CDJ_DSP_WINDOW_SIZE;
}

void cdj_dsp_transfer_start(void)
{
    /*
     * Nothing to do yet beyond counting: the busy bit the firmware polls lives
     * in the 0xfff10000 register file, which the board file owns, and it is
     * driven from there.  The hook exists so a model that needs to see a
     * transfer begin — one that streams, rather than answering in place — has
     * somewhere to hang.
     */
    if (cdj_dsp) {
        cdj_dsp->transfers++;
    }
}

void cdj_dsp_transfer_done(hwaddr source, hwaddr destination, unsigned bytes)
{
    CdjDspState *dsp = cdj_dsp;

    if (!dsp) {
        return;
    }
    if (dsp->trace) {
        fprintf(stderr, "cdj2000-dsp: dma %#" HWADDR_PRIx " -> %#" HWADDR_PRIx
                " (%u bytes)%s\n", source, destination, bytes,
                cdj_dsp_is_window(destination) ? " into the window" : " out of it");
    }
    if (cdj_dsp_is_window(destination)) {
        cdj_dsp_model_firmware(dsp->model, dsp->ram, CDJ_DSP_WINDOW_SIZE,
                               destination - CDJ_DSP_WINDOW_BASE, bytes);
    }
}

void cdj_dsp_init(MemoryRegion *system, Chardev *external)
{
    CdjDspState *dsp = g_new0(CdjDspState, 1);

    dsp->trace = getenv("CDJ_DSP_TRACE") != NULL;

    memory_region_init_ram(&dsp->window, NULL, "cdj2000.dsp-window",
                           CDJ_DSP_WINDOW_SIZE, &error_fatal);
    memory_region_add_subregion(system, CDJ_DSP_WINDOW_BASE, &dsp->window);
    dsp->ram = memory_region_get_ram_ptr(&dsp->window);

    /*
     * Higher priority than the RAM underneath, so the guest's stores to the
     * request word reach this device instead of vanishing into memory.
     */
    memory_region_init_io(&dsp->mailbox, NULL, &cdj_dsp_mailbox_ops, dsp,
                          "cdj2000.dsp-mailbox", DSP_MAILBOX_SIZE);
    memory_region_add_subregion_overlap(system,
                                        CDJ_DSP_WINDOW_BASE + DSP_MAILBOX_OFFSET,
                                        &dsp->mailbox, 1);

    memory_region_init_io(&dsp->ctl, NULL, &cdj_dsp_ctl_ops, dsp,
                          "cdj2000.dsp-ctl", DSP_CTL_SIZE);
    memory_region_add_subregion(system, DSP_CTL_BASE, &dsp->ctl);

    /* The model is created after the window, because reset writes into it. */
    dsp->model = cdj_dsp_model_new(external);
    cdj_dsp_model_reset(dsp->model, dsp->ram, CDJ_DSP_WINDOW_SIZE);

    dsp->tick = timer_new_ns(QEMU_CLOCK_VIRTUAL, cdj_dsp_tick, dsp);
    timer_mod(dsp->tick, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + DSP_TICK_NS);

    cdj_dsp = dsp;
}
