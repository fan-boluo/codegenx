"""Request helpers."""

from __future__ import annotations

from fastapi import Request


def get_client_ip(request: Request) -> str:
    # 标准代理转发头
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        client_ip = forwarded_for.split(",")[0].strip()
        if client_ip:
            return client_ip
    # nginx常用真实IP
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"
