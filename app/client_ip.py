"""The visitor's real IP address.

The site sits behind Cloudflare, so the connecting address (after Caddy's
X-Forwarded-For) is a Cloudflare edge server. Cloudflare puts the visitor's
address in CF-Connecting-IP. That header is only trusted when the request
really came from a Cloudflare range, so it can't be spoofed by hitting the
origin directly.
"""
import ipaddress

from fastapi import Request

# https://www.cloudflare.com/ips/
CLOUDFLARE_RANGES = [ipaddress.ip_network(n) for n in (
    "173.245.48.0/20", "103.21.244.0/22", "103.22.200.0/22", "103.31.4.0/22",
    "141.101.64.0/18", "108.162.192.0/18", "190.93.240.0/20", "188.114.96.0/20",
    "197.234.240.0/22", "198.41.128.0/17", "162.158.0.0/15", "104.16.0.0/13",
    "104.24.0.0/14", "172.64.0.0/13", "131.0.72.0/22",
    "2400:cb00::/32", "2606:4700::/32", "2803:f800::/32", "2405:b500::/32",
    "2405:8100::/32", "2a06:98c0::/29", "2c0f:f248::/32",
)]


def _from_cloudflare(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in net for net in CLOUDFLARE_RANGES)


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "127.0.0.1"
    if _from_cloudflare(peer):
        real = request.headers.get("cf-connecting-ip", "").strip()
        if real:
            return real
    return peer
