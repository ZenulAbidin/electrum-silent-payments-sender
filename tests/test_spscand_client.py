import json
import unittest

from silent_payments_sender.spscand_client import (
    SpscandClient,
    SpscandError,
    check_url,
    parse_output,
    parse_pairing,
)


ADDRESS = "tsp1qexample"


def row(**changes):
    data = {
        "txid": "a" * 64,
        "vout": 1,
        "amount": 5000,
        "pubkey": "b" * 64,
        "tweak": "c" * 64,
        "label": None,
        "status": "confirmed",
        "height": 101,
        "confirmations": 3,
        "spent_txid": None,
        "spent_height": None,
        "pending_spent_txid": None,
        "block_hash": "d" * 64,
    }
    data.update(changes)
    return data


class FakeSpscand:
    def __init__(self, token="t0ken"):
        self.token = token
        self.requests = []
        self.bodies = []
        self.outputs = [row()]
        self.labels = {1: "shop"}

    def __call__(self, url, headers, use_proxy, json_body=None):
        self.requests.append((url, headers, use_proxy))
        self.bodies.append(json_body)
        if headers.get("Authorization") != f"Bearer {self.token}":
            return 401, b'{"error": "unauthorized"}'
        path = url.split("://", 1)[1].split("/", 1)[1]
        if path == "v1/status":
            body = {
                "version": "0.1.1",
                "network": "regtest",
                "scanned_height": 103,
                "tip": 103,
                "synced": True,
                "error": None,
                "mempool": None,
            }
        elif path == "v1/addresses":
            body = [{"label": None, "address": ADDRESS}] + [
                {"label": m, "comment": c, "address": f"tsp1qlabel{m}"}
                for m, c in self.labels.items()
            ]
        elif path.startswith("v1/events"):
            body = {
                "boot": "b00t",
                "last": 3,
                "reset": False,
                "events": [
                    {
                        "seq": 3,
                        "event": "block.scanned",
                        "time": 1,
                        "data": {"height": 104},
                    }
                ],
            }
        elif path == "v1/rescan" and json_body is not None:
            body = {"from": json_body["from"]}
        elif path == "v1/labels" and json_body is not None:
            m = max(self.labels, default=0) + 1
            self.labels[m] = json_body.get("comment", "")
            body = {"label": m, "comment": self.labels[m], "address": f"tsp1qlabel{m}"}
        elif path.startswith("v1/outputs"):
            body = self.outputs
        else:
            return 404, b"{}"
        return 200, json.dumps(body).encode()


class TestCheckUrl(unittest.TestCase):
    def test_loopback_is_direct(self):
        for url in (
            "http://127.0.0.1:7352",
            "http://localhost:7352/",
            "http://[::1]:7352",
        ):
            with self.subTest(url):
                endpoint = check_url(url, proxy_enabled=True)
                self.assertFalse(endpoint.use_proxy)
                self.assertFalse(endpoint.base.endswith("/"))

    def test_onion_needs_the_proxy(self):
        url = "http://" + "a" * 56 + ".onion:7352"
        self.assertTrue(check_url(url, proxy_enabled=True).use_proxy)
        with self.assertRaisesRegex(SpscandError, "needs Tor"):
            check_url(url, proxy_enabled=False)

    def test_remote_needs_https(self):
        with self.assertRaisesRegex(SpscandError, "https"):
            check_url("http://192.0.2.7:7352", proxy_enabled=True)
        self.assertFalse(
            check_url("https://spscand.example", proxy_enabled=False).use_proxy
        )
        self.assertTrue(
            check_url("https://spscand.example", proxy_enabled=True).use_proxy
        )

    def test_rejects_other_forms(self):
        for url in (
            "ftp://127.0.0.1",
            "127.0.0.1:7352",
            "http://user:pw@127.0.0.1:7352",
            "http://127.0.0.1:7352/?token=x",
        ):
            with self.subTest(url), self.assertRaises(SpscandError):
                check_url(url, proxy_enabled=False)


class TestParseOutput(unittest.TestCase):
    def test_confirmed_and_unconfirmed(self):
        output = parse_output(row())
        self.assertTrue(output.confirmed)
        self.assertEqual(output.outpoint, "a" * 64 + ":1")
        pending = parse_output(
            row(status="unconfirmed", height=None, confirmations=0, label=2)
        )
        self.assertFalse(pending.confirmed)
        self.assertEqual(pending.label, 2)

    def test_old_spscand_rows_count_as_confirmed(self):
        old = row()
        del old["status"], old["pending_spent_txid"]
        self.assertTrue(parse_output(old).confirmed)

    def test_rejects_bad_rows(self):
        for changes in (
            {"txid": "zz"},
            {"vout": -1},
            {"amount": 0},
            {"amount": True},
            {"tweak": "C" * 64},
            {"pubkey": "b" * 62},
            {"status": "maybe"},
            {"height": None},
            {"status": "unconfirmed", "confirmations": 0},
            {"status": "unconfirmed", "height": None, "confirmations": 2},
            {"label": "1"},
        ):
            with self.subTest(changes), self.assertRaises(SpscandError):
                parse_output(row(**changes))


class TestPairing(unittest.TestCase):
    def test_parse(self):
        pairing = parse_pairing(
            "spscand://pair?url=http%3A%2F%2Fabc.onion%3A7352&token=t0ken"
            "&network=regtest&address=tsp1qexample\n"
        )
        self.assertEqual(
            (pairing.url, pairing.token, pairing.network, pairing.address),
            ("http://abc.onion:7352", "t0ken", "regtest", "tsp1qexample"),
        )

    def test_rejects_others(self):
        for text in (
            "http://127.0.0.1:7352",
            "spscand://other?url=x",
            "spscand://pair?url=http%3A%2F%2Fx&network=main&address=sp1q",
        ):
            with self.subTest(text), self.assertRaises(SpscandError):
                parse_pairing(text)


class TestClient(unittest.TestCase):
    def test_reads_status_and_outputs(self):
        fake = FakeSpscand()
        client = SpscandClient(
            "http://127.0.0.1:7352", "t0ken", fake, proxy_enabled=False
        )
        self.assertEqual(client.check_pairing(ADDRESS)["scanned_height"], 103)
        fake.outputs = [
            row(),
            row(txid="e" * 64, status="unconfirmed", height=None, confirmations=0),
        ]
        outputs = client.outputs(since_height=100)
        self.assertEqual([o.confirmed for o in outputs], [True, False])
        self.assertTrue(fake.requests[-1][0].endswith("/v1/outputs?since_height=100"))
        self.assertFalse(any(use_proxy for _, _, use_proxy in fake.requests))

    def test_wrong_token(self):
        client = SpscandClient(
            "http://127.0.0.1:7352", "nope", FakeSpscand(), proxy_enabled=False
        )
        with self.assertRaisesRegex(SpscandError, "token"):
            client.status()

    def test_other_wallet(self):
        client = SpscandClient(
            "http://127.0.0.1:7352", "t0ken", FakeSpscand(), proxy_enabled=False
        )
        with self.assertRaisesRegex(SpscandError, "different"):
            client.check_pairing("tsp1qsomeoneelse")

    def test_old_spscand(self):
        fake = FakeSpscand()

        def old(url, headers, use_proxy, json_body=None):
            if url.endswith("/v1/status"):
                return 200, b'{"network": "regtest"}'
            return fake(url, headers, use_proxy, json_body)

        client = SpscandClient(
            "http://127.0.0.1:7352", "t0ken", old, proxy_enabled=False
        )
        with self.assertRaisesRegex(SpscandError, "too old"):
            client.status()

    def test_unreachable(self):
        def down(url, headers, use_proxy, json_body=None):
            raise OSError("connection refused")

        client = SpscandClient("http://127.0.0.1:7352", "", down, proxy_enabled=False)
        with self.assertRaisesRegex(
            SpscandError, "Cannot reach spscand: connection refused"
        ):
            client.status()

    def test_labels(self):
        fake = FakeSpscand()
        client = SpscandClient(
            "http://127.0.0.1:7352", "t0ken", fake, proxy_enabled=False
        )
        self.assertEqual(
            client.labels(), {1: {"comment": "shop", "address": "tsp1qlabel1"}}
        )
        added = client.add_label("donations")
        self.assertEqual((added["label"], added["address"]), (2, "tsp1qlabel2"))
        self.assertEqual(fake.bodies[-1], {"comment": "donations"})
        self.assertTrue(fake.requests[-1][0].endswith("/v1/labels"))

    def test_events(self):
        fake = FakeSpscand()
        client = SpscandClient(
            "http://127.0.0.1:7352", "t0ken", fake, proxy_enabled=False
        )
        news = client.events(2, "b00t", 25)
        self.assertEqual(news["last"], 3)
        self.assertIn("since=2", fake.requests[-1][0])
        self.assertIn("boot=b00t", fake.requests[-1][0])
        self.assertIn("wait=25", fake.requests[-1][0])

        def broken(url, headers, use_proxy, json_body=None):
            return 200, b'{"boot": "b", "events": "nope"}'

        client.fetch = broken
        with self.assertRaisesRegex(SpscandError, "invalid event list"):
            client.events(0, None, 0)

    def test_rescan(self):
        fake = FakeSpscand()
        client = SpscandClient(
            "http://127.0.0.1:7352", "t0ken", fake, proxy_enabled=False
        )
        self.assertEqual(client.rescan(250), 250)
        self.assertEqual(fake.bodies[-1], {"from": 250})

    def test_changes_without_a_token(self):
        def no_token(url, headers, use_proxy, json_body=None):
            return 403, b'{"error": "changes need [api] token to be set"}'

        client = SpscandClient(
            "http://127.0.0.1:7352", "", no_token, proxy_enabled=False
        )
        with self.assertRaisesRegex(SpscandError, "spscand pair"):
            client.add_label("x")


if __name__ == "__main__":
    unittest.main()
