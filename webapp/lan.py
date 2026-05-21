# webapp/lan.py — 局域网 IP 探测，启动时打印让手机连得到

import socket
from typing import List


def get_lan_addresses() -> List[str]:
    """枚举本机所有非回环 IPv4 地址。"""
    addrs = set()
    try:
        host = socket.gethostname()
        for info in socket.getaddrinfo(host, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127."):
                addrs.add(ip)
    except Exception:
        pass
    try:
        # 经典 trick：UDP 假装连外网，拿到出口网卡 IP（不会发包）
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        addrs.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    return sorted(addrs)
