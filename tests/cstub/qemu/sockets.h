/* See tests/cstub/qemu/osdep.h. */
#ifndef CDJ_STUB_SOCKETS_H
#define CDJ_STUB_SOCKETS_H

int socket_set_fast_reuse(int fd);
int socket_set_nodelay(int fd);

#endif
