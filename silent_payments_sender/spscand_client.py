"""Client for a running spscand's JSON API.

spscand holds the scan key and does all the scanning; the plugin only reads
what it found. Responses are checked before use, since a wrong tweak or
amount would make a spend fail.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import urlencode, urlsplit

LOOPBACK = ("127.0.0.1", "::1", "localhost")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")

# fetch(url, headers, use_proxy, json_body) -> (HTTP status, body bytes); a POST
# when json_body is not None.
Fetch = Callable[[str, dict, bool, Optional[dict]], tuple]


class SpscandError(Exception):
    """A problem talking to spscand, shown to the user as is."""


@dataclass(frozen=True)
class Endpoint:
    base: str
    use_proxy: bool


def check_url(url: str, *, proxy_enabled: bool) -> Endpoint:
    """Where and how to reach spscand.

    Allowed: http to loopback (never through a proxy), http to a .onion name
    (only through Electrum's Tor proxy), and https to anything (through the
    proxy if Electrum uses one).
    """
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        raise SpscandError("The spscand URL must start with http:// or https://.")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise SpscandError("Put the API token in the token field, not in the URL.")
    base = f"{parts.scheme}://{parts.netloc}{parts.path.rstrip('/')}"
    if host in LOOPBACK:
        return Endpoint(base, use_proxy=False)
    if host.endswith(".onion"):
        if not proxy_enabled:
            raise SpscandError(
                "A .onion spscand needs Tor: enable a proxy in Electrum's "
                "network settings."
            )
        return Endpoint(base, use_proxy=True)
    if parts.scheme != "https":
        raise SpscandError(
            "A remote spscand must use https:// or a .onion address; plain "
            "http would expose your payments and token on the network."
        )
    return Endpoint(base, use_proxy=proxy_enabled)


@dataclass(frozen=True)
class ReceivedOutput:
    txid: str
    vout: int
    amount: int
    pubkey: str  # x-only, hex
    tweak: str  # spend with (b_spend + tweak) mod n; includes the label tweak
    label: Optional[int]
    status: str  # "confirmed" or "unconfirmed"
    height: Optional[int]
    confirmations: int
    spent_txid: Optional[str]
    spent_height: Optional[int]
    pending_spent_txid: Optional[str]

    @property
    def outpoint(self) -> str:
        return f"{self.txid}:{self.vout}"

    @property
    def confirmed(self) -> bool:
        return self.status == "confirmed"

    @property
    def spent(self) -> bool:
        return self.spent_height is not None


def _txid(value, field: str, *, optional: bool = False) -> Optional[str]:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not _HEX64.match(value):
        raise SpscandError(f"spscand sent an invalid {field}.")
    return value


def _int(
    value, field: str, *, optional: bool = False, minimum: int = 0
) -> Optional[int]:
    if value is None and optional:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise SpscandError(f"spscand sent an invalid {field}.")
    return value


def parse_output(row) -> ReceivedOutput:
    if not isinstance(row, dict):
        raise SpscandError("spscand sent an invalid output.")
    status = row.get("status", "confirmed")
    if status not in ("confirmed", "unconfirmed"):
        raise SpscandError("spscand sent an output with an unknown status.")
    output = ReceivedOutput(
        txid=_txid(row.get("txid"), "txid"),
        vout=_int(row.get("vout"), "output index"),
        amount=_int(row.get("amount"), "amount", minimum=1),
        pubkey=_txid(row.get("pubkey"), "output key"),
        tweak=_txid(row.get("tweak"), "tweak"),
        label=_int(row.get("label"), "label", optional=True),
        status=status,
        height=_int(row.get("height"), "height", optional=True),
        confirmations=_int(row.get("confirmations", 0), "confirmation count"),
        spent_txid=_txid(row.get("spent_txid"), "spending txid", optional=True),
        spent_height=_int(row.get("spent_height"), "spending height", optional=True),
        pending_spent_txid=_txid(
            row.get("pending_spent_txid"), "spending txid", optional=True
        ),
    )
    if output.confirmed != (output.height is not None):
        raise SpscandError("spscand sent an output whose status and height disagree.")
    if not output.confirmed and output.confirmations:
        raise SpscandError("spscand sent an unconfirmed output with confirmations.")
    return output


class SpscandClient:
    def __init__(
        self, url: str, token: str, fetch: Fetch, *, proxy_enabled: bool
    ) -> None:
        self.endpoint = check_url(url, proxy_enabled=proxy_enabled)
        self.token = token.strip()
        self.fetch = fetch

    def _get(
        self, path: str, query: Optional[dict] = None, *, post: Optional[dict] = None
    ):
        url = self.endpoint.base + path + (f"?{urlencode(query)}" if query else "")
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            status, body = self.fetch(url, headers, self.endpoint.use_proxy, post)
        except SpscandError:
            raise
        except Exception as exc:
            raise SpscandError(f"Cannot reach spscand: {exc}") from None
        if status == 401:
            raise SpscandError("spscand refused the API token.")
        if status == 403 and post is not None:
            raise SpscandError(
                "spscand only accepts changes with an API token: set one "
                "(spscand pair does it) and restart spscand."
            )
        if status != 200:
            raise SpscandError(f"spscand answered HTTP {status} for {path}.")
        try:
            return json.loads(body)
        except ValueError:
            raise SpscandError("spscand sent a response that is not JSON.") from None

    def status(self) -> dict:
        status = self._get("/v1/status")
        if not isinstance(status, dict) or not isinstance(status.get("version"), str):
            raise SpscandError(
                "This spscand is too old: version 0.2.0 or newer is needed."
            )
        return status

    def address(self) -> str:
        """The unlabelled receiving address spscand scans for."""
        rows = self._get("/v1/addresses")
        if not isinstance(rows, list):
            raise SpscandError("spscand sent an invalid address list.")
        for row in rows:
            if isinstance(row, dict) and row.get("label") is None:
                address = row.get("address")
                if isinstance(address, str):
                    return address
        raise SpscandError("spscand did not report its address.")

    def labels(self) -> dict[int, dict]:
        """Registered labels: number -> {"comment", "address"}."""
        rows = self._get("/v1/addresses")
        if not isinstance(rows, list):
            raise SpscandError("spscand sent an invalid address list.")
        labels = {}
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("label"), int):
                labels[row["label"]] = {
                    "comment": str(row.get("comment") or ""),
                    "address": str(row.get("address") or ""),
                }
        return labels

    def add_label(self, comment: str) -> dict:
        """A new label with the next free number: {"label", "comment", "address"}."""
        result = self._get("/v1/labels", post={"comment": comment})
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("label"), int)
            or not isinstance(result.get("address"), str)
        ):
            raise SpscandError("spscand sent an invalid answer for the new label.")
        return result

    def outputs(self, since_height: Optional[int] = None) -> list[ReceivedOutput]:
        query = {"since_height": since_height} if since_height is not None else None
        rows = self._get("/v1/outputs", query)
        if not isinstance(rows, list):
            raise SpscandError("spscand sent an invalid output list.")
        return [parse_output(row) for row in rows]

    def check_pairing(self, expected_address: str) -> dict:
        """Status, after making sure spscand scans for this wallet's address."""
        status = self.status()
        if self.address() != expected_address:
            raise SpscandError(
                "This spscand scans for a different silent payment address "
                "than this wallet's."
            )
        return status


def electrum_fetch(network) -> Fetch:
    """A fetch function on Electrum's asyncio loop, using its proxy when asked."""

    def fetch(url: str, headers: dict, use_proxy: bool, json_body=None):
        import asyncio

        from electrum import util

        async def get():
            proxy = network.proxy if (use_proxy and network is not None) else None
            async with util.make_aiohttp_session(proxy, timeout=30) as session:
                if json_body is None:
                    request = session.get(url, headers=headers)
                else:
                    request = session.post(url, headers=headers, json=json_body)
                async with request as response:
                    return response.status, await response.read()

        loop = util.get_asyncio_loop()
        return asyncio.run_coroutine_threadsafe(get(), loop).result(timeout=45)

    return fetch


def proxy_enabled(network) -> bool:
    proxy = getattr(network, "proxy", None) if network is not None else None
    return bool(proxy and getattr(proxy, "enabled", True))
