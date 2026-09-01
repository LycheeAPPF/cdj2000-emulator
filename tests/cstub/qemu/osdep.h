/*
 * Just enough of QEMU's osdep.h to build emulator/qemu/cdj2000_input.c on the
 * host, so that the pulse machine and the analogue ramp can be *measured*
 * rather than asserted about.
 *
 * This is not a reimplementation of the file's behaviour: the harness compiles
 * the real cdj2000_input.c, opens a real loopback socket and speaks the real
 * protocol.  The only thing faked is the virtual clock, because a test has to
 * be able to advance it, and the two QEMU helpers the file calls.
 *
 * See tests/cstub/harness.c and tests/test_input_channel.py.  Nothing here is
 * compiled into QEMU.
 */
#ifndef CDJ_STUB_OSDEP_H
#define CDJ_STUB_OSDEP_H

#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <inttypes.h>
#include <sys/types.h>

#include <glib.h>

#ifdef _WIN32
#  define WIN32_LEAN_AND_MEAN
#  include <winsock2.h>
#  include <ws2tcpip.h>

/*
 * QEMU wraps every socket call on Windows so that the rest of the tree can use
 * int file descriptors and POSIX errno.  The same wrapping, minimally.
 */
static inline int cdj_stub_errno(void)
{
    int code = WSAGetLastError();

    return code == WSAEWOULDBLOCK ? EAGAIN : EIO;
}

static inline int cdj_stub_socket(int domain, int type, int protocol)
{
    SOCKET handle = socket(domain, type, protocol);

    if (handle == INVALID_SOCKET) {
        errno = cdj_stub_errno();
        return -1;
    }
    return (int)handle;
}

static inline int cdj_stub_bind(int fd, const struct sockaddr *address,
                                int length)
{
    int result = bind((SOCKET)fd, address, length);

    if (result < 0) {
        errno = cdj_stub_errno();
    }
    return result;
}

static inline int cdj_stub_listen(int fd, int backlog)
{
    int result = listen((SOCKET)fd, backlog);

    if (result < 0) {
        errno = cdj_stub_errno();
    }
    return result;
}

static inline int cdj_stub_connect(int fd, const struct sockaddr *address,
                                   int length)
{
    int result = connect((SOCKET)fd, address, length);

    if (result < 0) {
        errno = cdj_stub_errno();
    }
    return result;
}

static inline int cdj_stub_accept(int fd, struct sockaddr *address,
                                  int *length)
{
    SOCKET handle = accept((SOCKET)fd, address, length);

    if (handle == INVALID_SOCKET) {
        errno = cdj_stub_errno();
        return -1;
    }
    return (int)handle;
}

static inline ssize_t cdj_stub_recv(int fd, void *buffer, size_t length,
                                    int flags)
{
    int got = recv((SOCKET)fd, (char *)buffer, (int)length, flags);

    if (got < 0) {
        errno = cdj_stub_errno();
    }
    return got;
}

static inline ssize_t cdj_stub_send(int fd, const void *buffer, size_t length,
                                    int flags)
{
    int wrote = send((SOCKET)fd, (const char *)buffer, (int)length, flags);

    if (wrote < 0) {
        errno = cdj_stub_errno();
    }
    return wrote;
}

static inline int cdj_stub_close(int fd)
{
    return closesocket((SOCKET)fd);
}

#  define socket  cdj_stub_socket
#  define bind    cdj_stub_bind
#  define listen  cdj_stub_listen
#  define connect cdj_stub_connect
#  define accept  cdj_stub_accept
#  define recv    cdj_stub_recv
#  define send    cdj_stub_send
#  define close   cdj_stub_close
#else
#  include <arpa/inet.h>
#  include <fcntl.h>
#  include <netinet/in.h>
#  include <netinet/tcp.h>
#  include <sys/socket.h>
#  include <unistd.h>
#endif

/* QEMU's own helper, declared here because cdj2000_input.c calls it. */
bool qemu_set_blocking(int fd, bool block, void *errp);

#endif /* CDJ_STUB_OSDEP_H */
