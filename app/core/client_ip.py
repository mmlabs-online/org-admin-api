"""Client IP resolution for rate limiting behind Cloudflare Tunnel."""

import ipaddress

from fastapi import Request


def client_ip(request: Request, trusted_proxy_cidrs: list[str]) -> str:
    """The peer address, or `CF-Connecting-IP` when the peer is a trusted proxy.

    `X-Forwarded-For` is never read.
    """
    peer = request.client.host if request.client else "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if not any(peer_ip in ipaddress.ip_network(c, strict=False) for c in trusted_proxy_cidrs):
        return peer
    header = request.headers.get("cf-connecting-ip", "").strip()
    try:
        return str(ipaddress.ip_address(header))
    except ValueError:
        return peer


def ip_key(ip: str) -> str:
    """Rate-limit bucket for an IP: IPv4 as-is, IPv6 by its /64 network."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 6:
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)
