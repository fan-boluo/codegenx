import socket


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def camel_to_snake(data: dict) -> dict:
    import re
    return {re.sub(r'(?<!^)(?=[A-Z])', '_', k).lower(): v for k, v in data.items()}