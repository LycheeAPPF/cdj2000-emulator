/*
 * Pioneer CDJ-2000 audio DSP — the model.
 *
 * This is everything the virtual DSP *is*.  cdj2000_dsp.c owns the window, the
 * DMA and the registers and knows nothing about meaning; this file knows only
 * meaning and never touches a register.  The point of the split is that a real
 * engine can take this file's place, in-process or through the chardev, without
 * the device changing.
 *
 * What it deliberately is not: there is no audio path here at all.  No PCM, no
 * decoder, no filters, no output.  MAIN needs a DSP that answers and keeps a
 * position running; that is what this provides.
 *
 * The command vocabulary is being filled in from evidence rather than guessed.
 * What is settled so far:
 *
 *   - the request word is 0xac0cffec, the answer word 0xac0cfffc, and 0xac0cfff0
 *     carries an argument (every firmware record header repeats it as W[+0x0e]);
 *   - MAIN downloads two firmware records into the window, the second to offset
 *     0x7800 — which is where the addresses it reads all over the image
 *     (0xac0c7ba0, 0xac0c7ccc, 0xac0c8140, 0xac0c81a0 …) live, so that region is
 *     the shared control block rather than code;
 *   - DspTASK (0x1c80aa) dispatches on a byte through a 13-entry table at
 *     0x1c8054, and tsk_DJcontTxDspPCM/DEC each own a _cmd and a _ret buffer.
 *
 * Until each field is measured on a running machine it is left alone: writing
 * plausible values into a block the firmware then checksums is a good way to
 * turn a missing device into a wrong one, which is harder to diagnose.
 *
 * Copyright (C) 2026 LycheeAPPF
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/log.h"
#include "qemu/timer.h"
#include "qapi/error.h"
#include "chardev/char-fe.h"

#include "cdj2000_dsp.h"

/* Where MAIN's second firmware record lands, i.e. the shared control block. */
#define DSP_CONTROL_OFFSET   0x7800

/* Transport states.  Named after what the deck does, not after a wire value —
   the wire values are still being measured. */
typedef enum {
    CDJ_DSP_STOPPED = 0,
    CDJ_DSP_CUED,
    CDJ_DSP_PLAYING,
} CdjDspTransport;

struct CdjDspModel {
    CharFrontend external;
    bool have_external;

    /* What MAIN downloaded, so a run can say whether the transfer arrived. */
    uint64_t firmware_bytes;
    unsigned firmware_records;
    uint32_t last_offset;

    CdjDspTransport transport;
    int64_t position_ms;                /* playing position */
    int64_t last_tick_ns;
    int32_t tempo_ppm;                  /* parts per million, 0 = nominal */

    bool running;                       /* code loaded and the run bit up */
    bool absent;                        /* CDJ_DSP_ABSENT: never answer */
    uint64_t commands;
    bool trace;
};

CdjDspModel *cdj_dsp_model_new(Chardev *external)
{
    CdjDspModel *model = g_new0(CdjDspModel, 1);

    model->trace = getenv("CDJ_DSP_TRACE") != NULL;
    /*
     * CDJ_DSP_ABSENT keeps the window and the DMA but never answers, which is
     * the machine as it was before this device existed.  It is the control for
     * every claim made about the DSP: "the banner is gone" only means something
     * against a run in which it is still there.
     */
    model->absent = getenv("CDJ_DSP_ABSENT") != NULL;
    if (external) {
        qemu_chr_fe_init(&model->external, external, &error_abort);
        model->have_external = true;
    }
    return model;
}

void cdj_dsp_model_reset(CdjDspModel *model, uint8_t *window, size_t length)
{
    model->transport = CDJ_DSP_STOPPED;
    model->position_ms = 0;
    model->tempo_ppm = 0;
    model->running = false;
    model->last_tick_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    if (window) {
        /* A DSP being reset is not running, and must not claim to be. */
        stl_le_p(window + CDJ_DSP_MAIL_UP, 0);
        stl_le_p(window + CDJ_DSP_MAIL_ACK, 0);
    }
    if (model->trace) {
        fprintf(stderr, "cdj2000-dsp: model reset after %" PRIu64
                " bytes of firmware in %u pages\n",
                model->firmware_bytes, model->firmware_records);
    }
}

void cdj_dsp_model_firmware(CdjDspModel *model, uint8_t *window,
                            size_t length, uint32_t offset, unsigned bytes)
{
    model->firmware_records++;
    model->firmware_bytes += bytes;
    model->last_offset = offset;
    if (model->trace) {
        /*
         * The first eight bytes identify which record this is far better than a
         * count does: record 0's payload starts 2a 66 b2 07, and the shared
         * control block at 0x7800 does not.
         */
        const uint8_t *at = window + (offset < length ? offset : 0);

        fprintf(stderr, "cdj2000-dsp: firmware page %u at window+0x%04x "
                "%u bytes  %02x %02x %02x %02x\n",
                model->firmware_records, offset, bytes,
                at[0], at[1], at[2], at[3]);
    }

    /*
     * A real DSP boots as soon as it has code and reports that in the mailbox;
     * bring-up state 5 (0x1c7924 -> the poller 0x1c778a) waits for exactly
     * this word and gives up after three attempts of 3000 polls each, which is
     * the 9000 reads a run without it produces.
     *
     * Raising it on the first page rather than the last is deliberate: nothing
     * reads the word before state 5, and there is no field anywhere in the
     * transfer that says which page is the last one — inferring it from a short
     * page would be a guess, and a wrong guess here looks exactly like a hang.
     */
    if (!model->running && !model->absent) {
        model->running = true;
        stl_le_p(window + CDJ_DSP_MAIL_UP, 1);
        if (model->trace) {
            fprintf(stderr, "cdj2000-dsp: reporting running at window+0x%04x\n",
                    CDJ_DSP_MAIL_UP);
        }
    }
}

/*
 * MAIN raised the request word.  With a chardev attached the whole window's
 * control block goes out and the answer comes back into it; otherwise the
 * built-in model answers.
 *
 * Returning true tells the device to acknowledge.  Returning false leaves the
 * answer word alone, which is what a real DSP that has not finished would do —
 * and is the honest thing to return while a command is not yet understood,
 * because a false acknowledgement makes MAIN believe a value it never got.
 */
bool cdj_dsp_model_doorbell(CdjDspModel *model, uint8_t *window, size_t length)
{
    uint8_t *control = window + DSP_CONTROL_OFFSET;

    model->commands++;
    if (model->trace) {
        fprintf(stderr, "cdj2000-dsp: doorbell %" PRIu64 "  arg=%08x  "
                "control %02x %02x %02x %02x %02x %02x %02x %02x\n",
                model->commands,
                ldl_le_p(window + CDJ_DSP_MAIL_BASE),
                control[0], control[1], control[2], control[3],
                control[4], control[5], control[6], control[7]);
    }

    if (model->have_external) {
        /*
         * Frame: a 4-byte length, then the control block.  The semantics stay
         * in the window, so the far end needs no protocol of its own beyond
         * knowing where the block starts.
         */
        uint8_t header[4];
        const unsigned block = length - DSP_CONTROL_OFFSET;

        stl_le_p(header, block);
        qemu_chr_fe_write_all(&model->external, header, sizeof(header));
        qemu_chr_fe_write_all(&model->external, control, block);
        /*
         * Deliberately not blocking on a reply here: the caller is inside a
         * guest store and the link has 3 ms deadlines.  An external engine
         * answers into the window and the next doorbell picks it up.
         */
        return true;
    }

    /*
     * The built-in model.  Command decoding is not written yet — see the file
     * comment.  Acknowledging without understanding would be worse than not
     * answering, so this reports and declines until the vocabulary is measured.
     */
    return false;
}

void cdj_dsp_model_tick(CdjDspModel *model, uint8_t *window, size_t length)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    int64_t elapsed_ms = (now - model->last_tick_ns) / SCALE_MS;

    model->last_tick_ns = now;
    if (model->transport != CDJ_DSP_PLAYING || elapsed_ms <= 0) {
        return;
    }
    /* Tempo is a parts-per-million offset from nominal speed. */
    model->position_ms += elapsed_ms
        + (elapsed_ms * model->tempo_ppm) / 1000000;
}
