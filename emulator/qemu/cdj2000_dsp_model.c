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
/* The two fill levels MAIN reads and the count word of a stream header. */
#define DSP_LEVEL_BUFFER1   0x7cd0
#define DSP_LEVEL_BUFFER2   0x7ccc
#define DSP_HEADER_COUNT    0x8144

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
    bool ack_control;                   /* CDJ_DSP_ACK: clear control-block commands */
    bool control_cleared;               /* CDJ_DSP_ACK: block zeroed once MAIN saw "up" */
    unsigned stream_buffer;             /* CDJ_DSP_ACK: buffer the last data header named, 1 or 2 */

    /*
     * CDJ_DSP_TRACE: a copy of the control block as last reported, so each
     * second's report names only the words that changed since the previous one.
     */
    uint8_t *census;
    int64_t census_ns;
    uint64_t commands;
    uint64_t acknowledged;
    bool trace;
};

/*
 * CDJ_DSP_ACK=1 -- acknowledge the commands MAIN writes into the shared control
 * block.  Off by default; an experiment with its own switch, because the
 * built-in model declines everything it does not understand.
 *
 * What is measured (runs/nxs-swap/trackload-20-dsppoll, 2026-09-03): a track
 * load never rings the mailbox doorbell at all.  It writes 1 into the control
 * block at window+0x7ba0 and MAIN's DSP state machine (0x19feb0) then polls
 * that word and its neighbours with `cmp/pl`: a positive value is a request
 * still pending, zero is done, negative is an error, and the word at +4 is
 * read with `cmp/pz` as the result.  The same machine writes 5 and other codes
 * into the same word (0x1a0a86..0x1a0a90) and polls window+0x7b80 the same way.
 * Nothing answered, so the load stayed pending for ever behind MAIN's
 * NOW LOADING blink.
 *
 * With this on, every 10 ms tick turns a request word (the table below) into
 * 0 and, for the command words, writes 0 into the result word at +4, which is
 * the "done, no error" reading of the poller (the firmware record leaves a large positive
 * constant there, which the poller reads as "still pending" -- trackload-23
 * completed the load's first command that way and MAIN then stopped the
 * player with E-8302 qualifier 0x200f).  A command is a small positive code: 1, 5 and 6 are
 * the ones MAIN's machine writes (0x1a0a86..0x1a0a90).  The firmware record
 * MAIN downloads fills the same words with large constants (0x01c1e02b,
 * 0x2fc37011 -- trackload-22 cleared those at t=1.3 s before the rule was
 * narrowed), so anything at or above DSP_ACK_COMMAND_LIMIT is left alone.
 * Each acknowledgement is reported on stderr, so a run says which commands
 * MAIN issued and in what order -- that is the vocabulary the rest of this
 * file is waiting for.  Nothing here produces audio.
 */
#define DSP_ACK_COMMAND_LIMIT 0x100

typedef struct {
    unsigned offset;        /* window offset of the request word */
    int32_t limit;          /* values at or above this are not requests */
    bool clear_result;      /* the word at +4 is this request's result */
} DspAckWord;

/*
 * The request words measured so far.  Every one follows the same rule --
 * MAIN writes a positive value, polls the word with cmp/pl, and moves on once
 * the DSP has made it zero (negative would be the DSP's error code):
 *
 *   +0x7ba0  the player's DSP command word (0x1a0a86..0x1a0a90 write 1, 5, 6;
 *            the handshake helper 0x19fec0/0x19fef0 reads +0x7ba0 and the
 *            result at +0x7ba4), +0x7b80 the same helper's third channel
 *            (0x19ff2e);
 *   +0x7c9c  the load's parameter block: 0x1a0a3a..0x1a0a62 fills
 *            +0x7ca0..+0x7cac from the track record, writes a size-like
 *            positive value into +0x7c9c and 1 into +0x7ba0, and 0x1a037a..
 *            0x1a038e then polls +0x7c9c (trackload-27: 299 polls in 1.2 s,
 *            nothing answered, load reported failed 8.7 s later);
 *   +0x7cb0  the DJcont mid-manager's command word: 0x1b9d40..0x1b9d72 writes
 *            parameters into +0x7cb8..+0x7cc4, a code into +0x7cb0 (2 there,
 *            1 from the other writers), then polls it 3001 times 2 ms and
 *            gives up with -10 (trackload-26/27 reached the supervisor's
 *            "error STOP" branch with exactly that -10 in hand);
 *   +0x8100  the stream format word: 0x1aed5c first waits (2 ms polls, no
 *            limit) for +0x8100 to read 0, fills +0x8104..+0x8124 from the
 *            track's format record (a WAV: +0x8108 = 2, +0x810c = 2,
 *            +0x8110 = 0x2c, +0x811c = 0x39e2, +0x8120/+0x8124 = 1) and
 *            writes the kind, 2 for PCM (3 and 4 for the compressed kinds),
 *            into +0x8100 (trackload-29: written at t=165 and never
 *            answered, NOW LOADING for the remaining 165 s of the run);
 *   +0x8140  the stream header: 0x1a3962..0x1a3990 writes the stream's
 *            parameters to +0x8144..+0x814c and 0x04010000 or 0x04020000
 *            into +0x8140, hands the stream to tsk_DJcontTxDspPCM and waits
 *            (0x1a39f2.., +0x816c raised meanwhile; 0x1a4a90 waits 2501 x
 *            2 ms for the same word and returns -4) until the DSP has made it
 *            zero; a negative value there is the DSP's error code, which the
 *            PCM sender maps to E8302/E8304 (0x1c1ee2..0x1c1f1a);
 *   +0x81c4  the PCM buffer word: tsk_DJcontTxDspPCM (0x1c189a..0x1c1a72)
 *            fills +0x81e0 or +0xbea0, writes the buffer number to +0x81c8 and
 *            the sample count to +0x81cc, then 1 into +0x81c4 -- 2 for the
 *            last buffer -- and cmd_tx (0x1c1c0c) waits for it to read 0,
 *            1001 times 2 ms, else -5 ("DJcont DSP timeout"); a negative
 *            +0x8140 meanwhile is the DSP's error code.
 *
 * The limit keeps a leftover firmware byte pattern from being taken for a
 * request (trackload-22); the load's size word is the one request that is not
 * a small code, so it has none.  Only the two command words own a result word
 * at +4 -- +0x7ca0 is the parameter block and +0x81c8 the buffer number, and
 * a DSP does not erase its caller's parameters.
 */
static const DspAckWord dsp_ack_words[] = {
    { 0x7b80, DSP_ACK_COMMAND_LIMIT, true },
    { 0x7ba0, DSP_ACK_COMMAND_LIMIT, true },
    { 0x7c9c, INT32_MAX, false },
    { 0x7cb0, DSP_ACK_COMMAND_LIMIT, false },
    { 0x8100, DSP_ACK_COMMAND_LIMIT, false },
    { 0x8140, INT32_MAX, false },
    { 0x81c4, DSP_ACK_COMMAND_LIMIT, false },
};
#define DSP_ACK_COMMAND_WORDS ARRAY_SIZE(dsp_ack_words)

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
    model->ack_control = getenv("CDJ_DSP_ACK") != NULL;
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
    model->control_cleared = false;
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
    if (model->control_cleared) {
        /*
         * Once MAIN has seen the DSP up, a transfer into the window is stream
         * data -- tsk_DJcontTxDspPCM's 9408-byte buffers on DMAC channel 5 --
         * not another firmware page.
         *
         * MAIN does not write a header for every one of them: trackload-36
         * had 3704 data transfers and 3515 headers, trackload-39 1654 and 72,
         * and in the latter the stream crawled -- MAIN paces itself by the
         * level the DSP reports against what it has sent, so a level kept by
         * headers alone falls behind and stalls it (pairing headers with
         * DMAs double-booked instead, trackload-41: 6837 for 3704).  A DSP
         * counts what it is given: every data transfer is booked here, 40
         * units per 9408 bytes, to the buffer the last header named.
         */
        if (model->trace) {
            fprintf(stderr, "cdj2000-dsp: %u bytes into window+0x%04x "
                    "(stream data)\n", bytes, offset);
        }
        if (model->ack_control && bytes >= 4096 && length > 0x7cd4) {
            unsigned level = model->stream_buffer == 1 ? DSP_LEVEL_BUFFER1
                                                       : DSP_LEVEL_BUFFER2;
            int32_t units = (int32_t)((uint64_t)bytes * 40 / 9408);
            int32_t was = (int32_t)ldl_le_p(window + level);

            stl_le_p(window + level, was + units);
            if (model->trace) {
                fprintf(stderr, "cdj2000-dsp: buffer %u took %d -> level %d "
                        "(+0x%04x)\n", model->stream_buffer, units,
                        was + units, level);
            }
        }
        return;
    }
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
 * The control block after boot.  Pages 2..11 of the firmware are staged
 * through window+0x7800 (trackload-26 stderr: every one lands there, the last
 * is 24384 bytes), so once the download is over the block holds page 11's
 * bytes: +0x7b80 = 0x01c1e02b, +0x7ba0 = 0x2fc37011, +0x8140 = 0x020000fa,
 * +0x81c4 = 0x031402e6.  MAIN reads those as a command still pending (+0x7ba0,
 * cmp/pl) and as the PCM channel still busy (+0x81c4, which cmd_tx 0x1c1c0c
 * waits to see 0 for 1001 x 2 ms before giving up with -5).  A DSP that has
 * booted has initialised its memory; this does the same, once, at the moment
 * MAIN first reads the "up" word -- in every run so far that read follows the
 * last page.  Under CDJ_DSP_ACK, like the acknowledgements, so the default
 * machine is unchanged.
 */
#define DSP_CONTROL_END 0xffe0          /* the mailbox starts here */

void cdj_dsp_model_up_seen(CdjDspModel *model, uint8_t *window, size_t length)
{
    if (!model->ack_control || model->control_cleared || !window
        || length < DSP_CONTROL_END) {
        return;
    }
    memset(window + DSP_CONTROL_OFFSET, 0, DSP_CONTROL_END - DSP_CONTROL_OFFSET);
    model->control_cleared = true;
    fprintf(stderr, "cdj2000-dsp: control block +0x%04x..+0x%04x zeroed after "
            "%u firmware pages, t=%.3f\n", DSP_CONTROL_OFFSET, DSP_CONTROL_END,
            model->firmware_records,
            qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) / 1e9);
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

/*
 * CDJ_DSP_TRACE: once a second, name every word of the control block that
 * changed since the last report.  This is how the block's vocabulary gets
 * measured: a breakpoint shows one writer, this shows every write, including
 * the ones made through pointers that no literal pool names.  Words the model
 * zeroes itself show up too (as the write MAIN made, if the tick sees it
 * first, or not at all when acknowledged in between -- the acknowledgement
 * line covers those).  Up to 24 words per report; a bigger burst is counted.
 */
#define DSP_CENSUS_INTERVAL_NS  (1000 * 1000 * 1000)
#define DSP_CENSUS_END          0xffe0
#define DSP_CENSUS_MAX_WORDS    24

static void cdj_dsp_model_census(CdjDspModel *model, uint8_t *window,
                                 size_t length, int64_t now)
{
    unsigned offset, reported = 0, changed = 0;

    if (!model->trace || !window || length < DSP_CENSUS_END) {
        return;
    }
    if (!model->census) {
        model->census = g_malloc0(DSP_CENSUS_END - DSP_CONTROL_OFFSET);
        memcpy(model->census, window + DSP_CONTROL_OFFSET,
               DSP_CENSUS_END - DSP_CONTROL_OFFSET);
        model->census_ns = now;
        return;
    }
    if (now - model->census_ns < DSP_CENSUS_INTERVAL_NS) {
        return;
    }
    model->census_ns = now;
    for (offset = DSP_CONTROL_OFFSET; offset < DSP_CENSUS_END; offset += 4) {
        uint32_t was = ldl_le_p(model->census + offset - DSP_CONTROL_OFFSET);
        uint32_t is = ldl_le_p(window + offset);

        if (was == is) {
            continue;
        }
        if (changed == 0) {
            fprintf(stderr, "cdj2000-dsp: census t=%.1f", now / 1e9);
        }
        changed++;
        if (reported < DSP_CENSUS_MAX_WORDS) {
            fprintf(stderr, " +0x%04x=%08x", offset, is);
            reported++;
        }
        stl_le_p(model->census + offset - DSP_CONTROL_OFFSET, is);
    }
    if (changed) {
        if (changed > reported) {
            fprintf(stderr, " (+%u more)", changed - reported);
        }
        fprintf(stderr, "\n");
    }
}

/*
 * The stream header at +0x8140 and the two fill levels.
 *
 * MAIN keeps two buffers in the DSP and reads their fill levels at +0x7ccc
 * (buffer 2) and +0x7cd0 (buffer 1).  The header's bytes say what a transfer
 * is: byte 3 is the class -- 1 data, 2 drop -- and byte 2 the buffer.  The
 * count travels in +0x8144: the PCM sender writes 40 with every 9408-byte
 * transfer, and the drop routine (0x1be0ec..0x1be1be) writes min(level, 40)
 * and header 0x02000100 / 0x02000200 to take that much out of buffer 1 / 2
 * again, so the level's unit is the count's unit.
 *
 * The load's pre-fill (0x1b6ffe..0x1b7048) sends buffers until +0x7ccc >= 160
 * and +0x7cd0 >= 40 (the other branch, 0x1b6faa.., wants 80 and 160), and only
 * then reports the load done.  trackload-34: with nothing keeping the levels,
 * MAIN streamed the whole file at the acknowledgement rate and NOW LOADING
 * never ended.  A DSP that has taken a transfer adds its count to the level;
 * this does that.  Nothing is played, so nothing is subtracted yet.
 */

/*
 * How much a buffer holds: the whole track.  trackload-36 and -41: with every
 * transfer taken at once MAIN pushed the whole file (3704 transfers of 9408
 * bytes plus 1620 of 8192, 33 MB) and reported the load done at the last one
 * -- and that is the machine: the CDJ-2000 loads a track into the DSP's own
 * 32 MB SDRAM (IC505, K4S561632J, service manual p. 48), which is why the
 * card can be pulled while it plays.  trackload-37 tried a six-transfer
 * capacity instead: the seventh header stayed pending, MAIN waited on it
 * without a timeout (0x1a3a10: 2 ms polls, no limit) and the load never
 * finished.  So a data header is always taken; the levels only tell MAIN how
 * much is buffered.
 *
 * The format word: 2 is the load's PCM format; 3 (parameters 0, 2, 0x480,
 * 0x30) opens the second phase of the same load, in which MAIN sends
 * 8192-byte buffers through the doorbell path (+0x81c4, +0x81c8 alternating,
 * trackload-41 t=298.7 -- before any PLAY press, so not playback, as
 * trackload-36's coincidence with one had suggested).  Nothing is consumed:
 * playback is not modelled, the levels only grow.
 */

/*
 * The slot table at +0x7ce0 (132-byte entries, index * 33 * 4: 0x19fffe..,
 * 0x1a0048.., 0x1b39cc.., 0x1be974..) is where MAIN reads the DSP's position:
 * 0x1be946 combines word +100 * 2 and word +96 / 294 into half-sector units
 * (a CD sector is 588 stereo frames) and compares them with its own count,
 * 0x1b3a02 treats word +128 == 2 or 3 as "running".  Writing sector 0, frame
 * 0 and state 1 there every tick (trackload-38) did not fill the time fields
 * -- the status record's words 5..8 stayed 0xbbbb, the builder's "blank"
 * (0x216802).  (That run's stream also crawled, but so did trackload-39
 * without the report: the cause was the level bookkeeping, see the note in
 * cdj_dsp_model_firmware.)  Nothing is reported there until the entry's
 * layout is measured; the time display stays blank.
 */

/* A data header (class 1) names the buffer the following transfers fill; a
   drop (class 2) takes its count out of the level.  The data itself is booked
   when it arrives, in cdj_dsp_model_firmware. */
static void cdj_dsp_model_stream_header(CdjDspModel *model, uint8_t *window,
                                        uint32_t header, int64_t now)
{
    unsigned class = header >> 24, buffer = (header >> 16) & 0xff;
    unsigned level = buffer == 1 ? DSP_LEVEL_BUFFER1
                   : buffer == 2 ? DSP_LEVEL_BUFFER2 : 0;
    int32_t count = (int32_t)ldl_le_p(window + DSP_HEADER_COUNT);
    int32_t was, is;

    if (!level || count < 0) {
        return;
    }
    if (class == 1) {
        model->stream_buffer = buffer;
        return;
    }
    if (class != 2) {
        return;
    }
    was = (int32_t)ldl_le_p(window + level);
    is = was > count ? was - count : 0;
    stl_le_p(window + level, is);
    fprintf(stderr, "cdj2000-dsp: buffer %u dropped %d -> level %d (+0x%04x) t=%.3f\n",
            buffer, count, is, level, now / 1e9);
}

void cdj_dsp_model_tick(CdjDspModel *model, uint8_t *window, size_t length)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    int64_t elapsed_ms = (now - model->last_tick_ns) / SCALE_MS;

    model->last_tick_ns = now;
    if (model->ack_control && model->running && !model->absent && window) {
        unsigned i;

        for (i = 0; i < DSP_ACK_COMMAND_WORDS; i++) {
            const DspAckWord *req = &dsp_ack_words[i];
            int32_t word;

            if (req->offset + 8 > length) {
                continue;
            }
            word = (int32_t)ldl_le_p(window + req->offset);
            if (word <= 0 || word >= req->limit) {
                continue;
            }
            model->acknowledged++;
            if (req->offset == 0x8140 && (word >> 24) == 1
                && model->acknowledged > 4) {
                /* the data headers come thousands a run; the census and the
                   level lines say what they were */
            } else
            fprintf(stderr, "cdj2000-dsp: control +0x%04x command 0x%08x "
                    "(+4.. %08x %08x %08x %08x) acknowledged, #%" PRIu64
                    " t=%.3f\n",
                    req->offset, (uint32_t)word,
                    ldl_le_p(window + req->offset + 4),
                    ldl_le_p(window + req->offset + 8),
                    ldl_le_p(window + req->offset + 12),
                    ldl_le_p(window + req->offset + 16),
                    model->acknowledged, now / 1e9);
            if (req->offset == 0x8140) {
                cdj_dsp_model_stream_header(model, window, (uint32_t)word, now);
            }
            stl_le_p(window + req->offset, 0);
            if (req->clear_result) {
                stl_le_p(window + req->offset + 4, 0);
            }
        }
    }
    cdj_dsp_model_census(model, window, length, now);
    if (model->transport != CDJ_DSP_PLAYING || elapsed_ms <= 0) {
        return;
    }
    /* Tempo is a parts-per-million offset from nominal speed. */
    model->position_ms += elapsed_ms
        + (elapsed_ms * model->tempo_ppm) / 1000000;
}
