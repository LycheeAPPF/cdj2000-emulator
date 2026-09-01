# Patches

Three patches, against two upstreams. Each one exists because the CDJ's firmware
exercises something the upstream emulator gets wrong or does not implement, and
each says what was measured before and after. See `../THIRD_PARTY.md` for the
licence of each.

| patch | against | applied by |
|---|---|---|
| `qemu-sh-intc-priority-imask.patch` | QEMU 11.x | `scripts/build-qemu-sh4.sh` |
| `01-gdb-17.2-bfin-parallel-dsp32alu.patch` | GDB 17.2 | `scripts/build-bfin-sim.sh` |
| `02-gdb-17.2-bfin-cdj2000-board.patch` | GDB 17.2 | `scripts/build-bfin-sim.sh` |

The two GDB patches are ordered and must be applied in that order. Both build
scripts apply with `--forward`, so re-running them on an already-patched tree is
a no-op.

---

## `qemu-sh-intc-priority-imask.patch` — QEMU, SH-4 interrupt semantics

Four related omissions in QEMU's SH-4 interrupt path. All are invisible to the
r2d/Linux guest the code was written for, and all are fatal to a uITRON RTOS
that masks with `SR.IMASK`.

1. **`sh_intc_get_pending_vector` ignored interrupt priority.** It returned the
   first pending source unless `IMASK` was `0x0f`, so raising `IMASK` masked
   nothing. The patch adds `prio` to `struct intc_source`, defaulting to `0x0f`
   so existing controllers behave exactly as before, and accepts a source only
   while `prio > imask`.

2. **`sh_intc_write` never recorded the level a priority register carries.** It
   only asked whether the field was non-zero, i.e. whether the source was
   enabled, so with (1) in place every source stayed at the default level of 15
   and outranked everything. The patch stores the field value into
   `source->prio`, clamped to 15, and traces it as `sh_intc_prio`. This is what
   lets a board model its real priority registers instead of hardcoding levels:
   the CDJ's guest programs 8 for its tick and 4 for its links, and both now
   come out of the registers rather than out of the board file.

3. **Accepting an interrupt did not raise `SR.IMASK` to that interrupt's
   level.** Real SH-4 does, which is what lets a handler save `SR` on entry and
   restore it on exit while staying masked against its own source until it has
   acknowledged the device.

4. **A declined interrupt was never offered again after an `rte`.** (1) makes
   declines possible for the first time — stock QEMU has no way to say "not
   now" — and QEMU only ever kicks the CPU out of a chained run of translation
   blocks on the *rising edge* of an interrupt line, in `cpu_interrupt()`. A
   periodic timer whose handler did not run keeps its underflow flag set, so
   there is no second edge. The one offer that does happen lands in the `rte`
   delay slot, where it is declined because delay slots are indivisible, and
   the block holding that delay slot then chains straight into the resumed code
   through `lookup_and_goto_ptr` without ever returning to the interrupt check.
   The patch makes `use_exit_tb` true for `TB_FLAG_DELAY_SLOT_RTE`, so an `rte`
   delay slot leaves the CPU loop and the restored `SR` is re-tested.

### What each one costs

Without (3) the CDJ's RTOS never survives its first timer tick. Its interrupt
prologue at image `0x2e6426` installs an `SR` with `IMASK = 10`, does its work,
then restores the entry `SR` at `0x2e644a` — and with `IMASK` back at 0 the
still-asserted timer re-enters on the very next instruction, the `rts` at
`0x2e644c`. Measured: 170 645 interrupts in 12 s, every one of them interrupting
that same `rts`, and the device flag never cleared because the handler body was
never reached.

After the patch the same run reaches its tasks: the trap log grows from 300
lines to 4026 and MAIN initialises its GUI link.

Without (4) the RTOS system time at `0x04fc45ec` freezes for good within the
first thirty seconds — measured stalls at 203 and at 6851 ticks — while the
timer keeps underflowing and `CPU_INTERRUPT_HARD` stays set. Measured with all
four: 105 185 ticks over 150 s, a steady ~700/s, and the GuiCom mode word at
`0x04c06fb0` advances from 0 to 1 for the first time.

The interrupt priority level itself is not the lever, and raising it does not
substitute for (4). `TMU0_PRIO` must stay in 1..10: measured at 11, 14 and 15
the tick outranks the prologue's own `IMASK = 10`, re-enters it, and the guest
resets after ~2 s; at 10, 8 and 4 the guest runs, and 8 is the level the
firmware itself programs. The stall happens at every level in the working range,
because the decline itself — not which level caused it — is what wedges the CPU.

---

## `01-gdb-17.2-bfin-parallel-dsp32alu.patch` — one instruction, one packet

GNU sim's `decode_dsp32alu_0` commits one packed 16-bit ALU result directly to
its destination data register. That is incorrect when the DSP32 ALU operation
occupies slot 0 of a Blackfin 64-bit multi-issue packet: later slots must read
the register values from before the packet, and all register writes commit only
after all three slots execute.

CDJ-2000 GUI firmware relies on this:

```text
R0 = R0 -|- R0 || [P5] = R0 || NOP
```

With `R0 = 7`, real packet semantics store `7` through `P5` and then commit the
ALU result `R0 = 0`. Unpatched GNU sim commits zero early, so the parallel store
incorrectly writes zero and the firmware enters a fatal loop.

The patch uses the simulator's existing deferred `STORE` queue, matching the
other DSP32 ALU cases in the same decoder. A runtime trace after applying it
must show the memory store receiving `0x00000007` followed by the queued
register write committing `R0 = 0`.

This one is an ordinary correctness fix and is suitable upstream on its own,
which is why it is a patch of its own rather than part of the board work.

---

## `02-gdb-17.2-bfin-cdj2000-board.patch` — the GUI board

Sixteen files, twelve under `sim/bfin/` and four under `sim/common/`, adding no
new files. Everything it adds is off unless an environment variable switches it
on, so an unconfigured simulator behaves as it does upstream. The patch's own
header lists every piece; two are worth knowing about before you build.

### `sim/common/dv-cfi.c` — the AMD command set

Upstream implements CFI command set 1 (Intel) only. The CDJ's 2 MiB parallel
flash is an AMD part, so without this the board file is rejected with

```text
/core/bfin_ebiu_amc/cfi@0: cmdset 2 not supported
```

the simulator carries on with no flash behind the EBIU, and the firmware
double-faults a fraction of a second later at `0xffb00000`. The visible symptom
is a crash that says nothing about a missing device — worth recognising, because
it is what you get if you build the simulator without this patch.

### `sim/bfin/dv-bfin_dma.c` — DMA interrupt delivery

The upstream channel model raises its interrupt before setting `DMA_DONE`,
asserts after every row of a 2D transfer, and never deasserts. In the CDJ
firmware this makes the DMA3 ISR read a zero status and, once status ordering
alone is corrected, re-enter EVT8 for ever before it can acknowledge the
channel.

The patch publishes completion before delivery, interrupts only when the final
2D row completes, and treats the output as level sensitive: asserted while
`DONE`/`ERR` is pending, dropped when firmware acknowledges those bits in
`IRQ_STATUS`. A zero-length pulse could otherwise be lost before the CEC
services the SIC, leaving a receive task asleep for good.

`BFIN_PPI_DMA_DELAY` paces the display DMA. A value of `5000` approximates
scanline time well enough to stop the LCD consuming one hardware event per
simulated cycle.

### Two diagnostics that are there because they found something

`SIM_LOAD_TRACE` prints requested against actually-written length per loaded
section. The loop in `sim-load.c` discarded `do_write`'s short count, so a
section that was only partly written looked exactly like one fully written, and
nothing downstream could tell the difference.

`SIM_CORE_CENSUS=<lo>:<hi>` names which mapping served each stretch of a write.
A hardware device that accepts bytes and drops them still returns a full count,
so "written == requested" does not mean "stored".

### Not included

The tree this came from also carries changes under `sim/sh/` — an attempt to run
the CDJ's MAIN board on the SH simulator rather than on QEMU. That line of work
is not part of this emulator and is deliberately left out.

### Reproducing the patch

The patch is generated, not hand-written, and it is checked the same way: unpack
a pristine `gdb-17.2`, apply `01` then `02`, and the result must match the tree
it was taken from byte for byte. `.github/workflows/patches.yml` runs the
applying half on every push and fails on any fuzz or reject.
