"""Talk to a model served by Ollama on this machine, and only on this machine.

Shell history is the most sensitive file most developers own: tokens typed
inline, database URLs with passwords, client names in paths. So the client
refuses any Ollama host that is not loopback unless you pass
``allow_remote=True`` yourself, and it sends nothing anywhere else.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

DEFAULT_MODEL = os.environ.get("WHATRAN_MODEL", "gemma4:12b")
DEFAULT_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")


class ModelUnavailable(RuntimeError):
    """Ollama is not running, or the model is not pulled."""


class RemoteHostRefused(ValueError):
    """The configured host would send history off this machine."""


def _base_url(host: str) -> str:
    if "://" not in host:
        host = "http://" + host
    parts = urlsplit(host)
    port = parts.port or 11434
    name = parts.hostname or ""
    if ":" in name:
        name = f"[{name}]"
    return f"{parts.scheme}://{name}:{port}"


def is_loopback(host: str) -> bool:
    """True for this machine: localhost, 127.0.0.0/8, ::1, and 0.0.0.0 or ::, which
    Ollama's own docs use as a listen address and which a client reaches locally."""
    name = urlsplit(_base_url(host)).hostname or ""
    if name == "localhost":
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


# No proxy, ever: urllib honours http_proxy and friends, which would carry the
# prompt to whatever the proxy is even though the host itself is local.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def chat(system: str, user: str, *, model: str = DEFAULT_MODEL, host: str = DEFAULT_HOST,
         allow_remote: bool = False, temperature: float = 0.2, timeout: float = 300) -> str:
    """One non-streaming chat turn. Returns the reply text."""
    if not allow_remote and not is_loopback(host):
        raise RemoteHostRefused(
            f"{host} is not this machine; your history stays local unless you pass --allow-remote-model")
    body = json.dumps({
        "model": model,
        "stream": False,
        "think": False,
        "options": {"temperature": temperature},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }).encode()
    request = urllib.request.Request(_base_url(host) + "/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        if error.code == 404:
            raise ModelUnavailable(f"model {model} is not pulled; run: ollama pull {model}") from error
        raise ModelUnavailable(f"Ollama answered {error.code}: {detail[:200]}") from error
    except (urllib.error.URLError, socket.timeout, ConnectionError) as error:
        raise ModelUnavailable(f"Ollama is not reachable at {host}; start it with: ollama serve") from error
    if payload.get("error"):
        raise ModelUnavailable(payload["error"])
    return (payload.get("message") or {}).get("content", "").strip()
