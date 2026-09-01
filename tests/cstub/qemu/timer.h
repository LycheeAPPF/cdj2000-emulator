/* See tests/cstub/qemu/osdep.h.
 *
 * The virtual clock is the one thing the harness has to fake: a test needs to
 * step it by an exact amount per panel exchange, which is the whole reason the
 * press hold and the rotary ramp can be checked at all.
 */
#ifndef CDJ_STUB_TIMER_H
#define CDJ_STUB_TIMER_H

#include <stdint.h>

typedef enum QEMUClockType {
    QEMU_CLOCK_VIRTUAL = 1,
} QEMUClockType;

extern int64_t cdj_stub_clock_ns;

int64_t qemu_clock_get_ns(QEMUClockType type);

#endif
