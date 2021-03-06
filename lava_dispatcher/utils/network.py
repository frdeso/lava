# Copyright (C) 2014 Linaro Limited
#               2017 The Linux Foundation
#
# Author: Neil Williams <neil.williams@linaro.org>
#         Jan-Simon Moeller <jsmoeller@linuxfoundation.org>
#
# This file is part of LAVA Dispatcher.
#
# LAVA Dispatcher is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# LAVA Dispatcher is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along
# with this program; if not, see <http://www.gnu.org/licenses>.

# List just the subclasses supported for this base strategy
# imported by the parser to populate the list of subclasses.

import contextlib
import os
import netifaces
import random
import socket
import subprocess  # nosec - internal use.
from lava_common.exceptions import InfrastructureError
from lava_common.constants import XNBD_PORT_RANGE_MIN, XNBD_PORT_RANGE_MAX


def dispatcher_gateway():
    """
    Retrieves the IP address of the current default gateway.
    """
    gateways = netifaces.gateways()
    if "default" not in gateways:
        raise InfrastructureError("Unable to find default gateway")
    return gateways["default"][netifaces.AF_INET][0]


def dispatcher_ip(dispatcher_config):
    """
    Retrieves the IP address of the interface associated
    with the current default gateway.
    """
    with contextlib.suppress(KeyError, TypeError):
        return dispatcher_config["dispatcher_ip"]
    gateways = netifaces.gateways()
    if "default" not in gateways:
        raise InfrastructureError("Unable to find dispatcher 'default' gateway")
    iface = gateways["default"][netifaces.AF_INET][1]
    iface_addr = None

    try:
        addr = netifaces.ifaddresses(iface)
        iface_addr = addr[netifaces.AF_INET][0]["addr"]
    except KeyError:
        # TODO: This only handles first alias interface can be extended
        # to review all alias interfaces.
        addr = netifaces.ifaddresses(iface + ":0")
        iface_addr = addr[netifaces.AF_INET][0]["addr"]
    return iface_addr


def rpcinfo_nfs(server, version=3):
    """
    Calls rpcinfo nfs on the specified server.
    Only stderr matters
    :param server: the NFS server to check
    :return: None if success, message if fail
    """
    with open(os.devnull, "w") as devnull:
        proc = subprocess.Popen(  # nosec - internal use.
            ["/usr/sbin/rpcinfo", "-u", server, "nfs", "%s" % version],
            stdout=devnull,
            stderr=subprocess.PIPE,
        )
        msg = proc.communicate()
        if msg[1]:
            return "%s %s" % (server, msg[1])
    return None


def get_free_port(dispatcher_config):
    """
    Finds the next free port to use
    :param dispatcher_config: the dispatcher config to search for nbd_server_port
    :return: port number
    """
    port = None
    with contextlib.suppress(KeyError, TypeError):
        dcport = dispatcher_config["nbd_server_port"]
        if "auto" in dcport:
            pass
        elif dcport.isdigit():
            return dcport
    # use random
    rng = random.Random()
    for _ in range(10):
        randport = int(rng.randrange(XNBD_PORT_RANGE_MIN, XNBD_PORT_RANGE_MAX))
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("", randport))
            s.listen(1)
            port = s.getsockname()[1]
        except OSError:
            s.close()
            continue
        s.close()
        if port is not None:
            return port
    # fallthrough single default nbd port as per services file
    return 10809
