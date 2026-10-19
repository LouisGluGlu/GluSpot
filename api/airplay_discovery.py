"""Bounded mDNS discovery of audio receivers; no discovery runs in paint.

Uses python-zeroconf's public ServiceBrowser/ServiceListener APIs. HomePod
advertises both services: merge by device ID, prefer the AirPlay service's
control port, and keep an IPv4 address for the native sender.
"""
from __future__ import annotations

import ipaddress
import threading

from api.audio_routes import AudioRouteDevice

SERVICE_TYPES = ("_airplay._tcp.local.", "_raop._tcp.local.")


def receiver_from_service(info, service_type: str) -> AudioRouteDevice | None:
    addresses = [ipaddress.ip_address(a) for a in info.parsed_addresses()
                 if _usable_ipv4(a)]
    # Multi-interface receivers can advertise a link-local address first.
    # Prefer their normal LAN address, retaining link-local-only receivers.
    host = str(min(addresses, key=lambda ip: ip.is_link_local)) if addresses else ""
    if not host:
        return None
    properties = {}
    for key, value in (info.properties or {}).items():
        k = key.decode("utf-8", "replace") if isinstance(key, bytes) else str(key)
        v = value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value or "")
        properties[k.casefold()] = v
    label = str(info.name)
    suffix = "." + service_type
    if label.endswith(suffix):
        label = label[:-len(suffix)]
    if service_type == "_raop._tcp.local." and "@" in label:
        raop_id, label = label.split("@", 1)
    else:
        raop_id = ""
    device_id = properties.get("deviceid", raop_id).replace(":", "").replace("-", "").upper()
    identity = device_id or host
    model = properties.get("model", properties.get("am", ""))
    model_key = model.strip().casefold()
    if model_key in {"audioaccessory5,1", "homepod mini"}:
        kind = "homepod-mini"
    else:
        kind = "homepod" if "homepod" in model_key or model_key.startswith("audioaccessory") else "airplay"
    port = int(info.port)
    if not 1 <= port <= 65535:
        return None
    return AudioRouteDevice("airplay:" + identity, label[:160] or "AirPlay", kind,
                            "airplay", host=host, port=port, device_id=device_id)


def _usable_ipv4(value) -> bool:
    try:
        ip = ipaddress.ip_address(value)
        return (ip.version == 4 and not ip.is_unspecified and not ip.is_multicast
                and not ip.is_loopback and not ip.is_reserved)
    except ValueError:
        return False


class AirPlayDiscovery:
    def __init__(self):
        self._lock = threading.Lock()
        self._services = {}
        self._zc = None
        self._browser = None
        self._closed = False
        self.state = "idle"
        self.error = ""

    def start(self):
        if self._closed or self._zc is not None:
            return
        try:
            from zeroconf import IPVersion, ServiceBrowser, Zeroconf
            self._zc = Zeroconf(ip_version=IPVersion.V4Only)
            self._browser = ServiceBrowser(self._zc, list(SERVICE_TYPES), listener=self)
            self.state = "browsing"
            self.error = ""
        except Exception as exc:
            self.error = str(exc)[:240]
            self.state = "unavailable"
            self.close_resources()

    def add_service(self, zc, type_, name):
        self.update_service(zc, type_, name)

    def update_service(self, zc, type_, name):
        if self._closed:
            return
        try:
            info = zc.get_service_info(type_, name, timeout=800)
            device = receiver_from_service(info, type_) if info is not None else None
            if device is not None:
                with self._lock:
                    if not self._closed:
                        self._services[(type_, name)] = device
        except Exception:
            # A malformed/vanished advertisement must not kill discovery.
            return

    def remove_service(self, zc, type_, name):
        with self._lock:
            self._services.pop((type_, name), None)

    def devices(self) -> tuple[AudioRouteDevice, ...]:
        with self._lock:
            services = tuple(self._services.items())
        merged = {}
        for (type_, _name), device in sorted(services, key=lambda item: item[0][0] == SERVICE_TYPES[0]):
            merged[device.id] = device
        return tuple(sorted(merged.values(), key=lambda d: (d.name.casefold(), d.id)))

    def close_resources(self):
        browser, self._browser = self._browser, None
        zc, self._zc = self._zc, None
        if browser is not None:
            browser.cancel()
        if zc is not None:
            zc.close()

    def close(self):
        self._closed = True
        self.close_resources()
        with self._lock:
            self._services.clear()
