/* See tests/cstub/qemu/osdep.h.  Reports go to stderr so the harness's own
 * trace on stdout stays machine-readable. */
#ifndef CDJ_STUB_ERROR_REPORT_H
#define CDJ_STUB_ERROR_REPORT_H

void info_report(const char *format, ...);
void warn_report(const char *format, ...);

#endif
