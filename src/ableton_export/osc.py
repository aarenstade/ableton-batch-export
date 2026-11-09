"""Small synchronous OSC query transport for the installed AbletonOSC script."""

import socket
import struct
import time


def osc_string(value: str):
    """Encode one OSC padded UTF-8 string."""
    data = value.encode() + b"\0"
    return data + b"\0" * (-len(data) % 4)


def decode(data):
    """Decode the scalar reply types returned by AbletonOSC."""

    def string(offset):
        end = data.index(0, offset)
        return data[offset:end].decode(), (end + 4) // 4 * 4

    address, offset = string(0)
    tags, offset = string(offset)
    values = []
    for tag in tags.lstrip(","):
        if tag == "s":
            val, offset = string(offset)
        elif tag in "if":
            val = struct.unpack_from(">" + tag, data, offset)[0]
            offset += 4
        elif tag in "TF":
            val = tag == "T"
        elif tag == "N":
            val = None
        else:
            raise ValueError(f"Unsupported OSC type: {tag}")
        values.append(val)
    return address, values


def send(config, address, args=()):
    """Send a command whose AbletonOSC handler intentionally has no reply."""
    tags = ""
    payload = b""
    for arg in args:
        if isinstance(arg, str):
            tags += "s"
            payload += osc_string(arg)
        elif isinstance(arg, int):
            tags += "i"
            payload += struct.pack(">i", arg)
        else:
            tags += "f"
            payload += struct.pack(">f", arg)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(
            osc_string(address) + osc_string("," + tags) + payload,
            (config.osc_host, config.osc_send_port),
        )


def query(config, address, args=()):
    """Send one correlated local query and ignore unrelated broadcasts."""
    tags = ""
    payload = b""
    for arg in args:
        if isinstance(arg, str):
            tags += "s"
            payload += osc_string(arg)
        elif isinstance(arg, int):
            tags += "i"
            payload += struct.pack(">i", arg)
        else:
            tags += "f"
            payload += struct.pack(">f", arg)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((config.osc_host, config.osc_reply_port))
        sock.settimeout(3)
        sock.sendto(
            osc_string(address) + osc_string("," + tags) + payload,
            (config.osc_host, config.osc_send_port),
        )
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            sock.settimeout(max(0.001, deadline - time.monotonic()))
            reply, values = decode(sock.recv(65536))
            if reply == address:
                return values
        raise TimeoutError(address)
