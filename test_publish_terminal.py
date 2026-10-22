import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import publish_terminal as publisher


class RepairHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.site = Path(self.temp.name)
        self.ts = 1792666500
        self.data = {
            "ts": self.ts,
            "rows": [
                {"coin": "BTC", "hist": 1, "a1d": 3.5, "mark": 100000},
                {"coin": "xyz:PLTR", "hist": 0, "a1d": None, "mark": 123},
            ],
            "categories": ["Crypto", "Stocks"],
            "hist_days": 92,
        }
        self.prefix = "<!doctype html>\r\n<script>window.__TERMINAL_DATA__="
        self.suffix = ";</script>\r\n<body>Unchanged presentation: \u2014</body>\r\n"
        self.write_snapshot(self.data)
        self.histdir = self.site / "data" / "hist"
        self.histdir.mkdir(parents=True)
        (self.histdir / "BTC.json").write_bytes(b"[[1,0.0004]]\r\n")
        (self.site / "CNAME").write_bytes(b"terminal.example.com\n")
        hour = self.ts // 3600 * 3600
        self.history = [
            (hour - (47 - i) * 3600, 0.0001 if i < 24 else 0.0002)
            for i in range(48)
        ]

    def write_snapshot(self, data):
        (self.site / "index.html").write_bytes(
            (self.prefix + json.dumps(data) + self.suffix).encode("utf-8")
        )

    def files(self):
        return {
            str(path.relative_to(self.site)): path.read_bytes()
            for path in self.site.rglob("*") if path.is_file()
        }

    def run_repair(self, coin="xyz:PLTR", hist=None):
        with patch.object(publisher.sys, "argv", [
            "publish_terminal.py", str(self.site), "--render-only",
            "--repair-history", coin,
        ]), patch.object(publisher, "history", return_value=(
            self.history if hist is None else hist
        )) as fetch, patch.object(publisher, "snapshot_rows", side_effect=(
            AssertionError("repair must preserve the existing snapshot")
        )):
            result = publisher.main()
        return result, fetch

    def test_repair_preserves_snapshot_other_rows_html_and_files(self):
        before = self.files()
        original = copy.deepcopy(self.data)

        result, fetch = self.run_repair()

        self.assertEqual(result, 0)
        fetch.assert_called_once_with("xyz:PLTR")
        html = (self.site / "index.html").read_bytes().decode("utf-8")
        self.assertTrue(html.startswith(self.prefix))
        self.assertTrue(html.endswith(self.suffix))
        data = json.loads(html[len(self.prefix):-len(self.suffix)])
        repaired = data["rows"][1]
        self.assertEqual(repaired["hist"], 1)
        self.assertEqual(repaired["a1d"], 175.2)
        self.assertEqual(repaired["a3d"], 131.4)
        for key in ("a7d", "a2w", "a1m", "a3m"):
            self.assertIsNone(repaired[key])
        for key in original["rows"][1]:
            if key not in ("hist", "a1d"):
                self.assertEqual(repaired[key], original["rows"][1][key])
        data["rows"][1] = original["rows"][1]
        self.assertEqual(data, original)
        for name, content in before.items():
            if name != "index.html":
                self.assertEqual((self.site / name).read_bytes(), content)
        self.assertEqual(
            json.loads((self.histdir / "xyz_PLTR.json").read_text()),
            [[t, round(rate, 10)] for t, rate in self.history],
        )

    def test_invalid_coin_is_rejected_before_fetch_or_file_changes(self):
        for coin in ("xyz:MISSING", "../index", "xyz:../../PLTR", "xyz:PLTR\\x"):
            with self.subTest(coin=coin):
                before = self.files()
                result, fetch = self.run_repair(coin)
                self.assertEqual(result, 1)
                fetch.assert_not_called()
                self.assertEqual(self.files(), before)

    def test_invalid_history_leaves_all_existing_bytes_unchanged(self):
        hour = self.ts // 3600 * 3600
        bad_histories = [
            [], self.history[:23], self.history[:-1],
            self.history + [self.history[-1]], list(reversed(self.history)),
            self.history[:-1] + [(hour, float("nan"))],
            self.history[:-1] + [(hour, float("inf"))],
            self.history[:-1] + [(hour, "0.0001")],
            self.history[:-1] + [(hour, True)],
            self.history[:-1] + [(float(hour), 0.0001)],
            self.history[:-1] + [(hour,)],
        ]
        for bad in bad_histories:
            with self.subTest(last=bad[-1:] if bad else []):
                before = self.files()
                result, _ = self.run_repair(hist=bad)
                self.assertEqual(result, 1)
                self.assertEqual(self.files(), before)

    def test_malformed_snapshot_is_rejected_before_fetch_or_writes(self):
        for data in (
            {}, {"ts": True, "rows": self.data["rows"]},
            {"ts": self.ts, "rows": []},
            {"ts": self.ts, "rows": [{"coin": "xyz:PLTR"}] * 2},
            {"ts": self.ts, "rows": ["xyz:PLTR"]},
        ):
            with self.subTest(data=data):
                self.write_snapshot(data)
                before = self.files()
                result, fetch = self.run_repair()
                self.assertEqual(result, 1)
                fetch.assert_not_called()
                self.assertEqual(self.files(), before)

    def test_history_restored_if_index_replacement_fails(self):
        real_replace = publisher.os.replace
        target = self.histdir / "xyz_PLTR.json"
        for old in (None, b"[[100,0.0000000123]]\n"):
            with self.subTest(old_history_exists=old is not None):
                if old is not None:
                    target.write_bytes(old)
                else:
                    target.unlink(missing_ok=True)
                before = self.files()

                def fail_index(source, destination):
                    if Path(destination) == self.site / "index.html":
                        raise OSError("test index replacement failure")
                    return real_replace(source, destination)

                with patch.object(publisher.os, "replace", side_effect=fail_index):
                    result, _ = self.run_repair()
                self.assertEqual(result, 1)
                self.assertEqual(self.files(), before)

    def test_malformed_embedded_json_and_assignment_leave_files_unchanged(self):
        valid = (self.site / "index.html").read_bytes()
        for content in (
            b"<html>no snapshot</html>", valid + valid,
            self.prefix.encode() + b"{broken json};</script>",
            valid.replace(b";</script>", b" + unexpected;</script>"),
            valid.replace(b'"mark": 123', b'"mark": NaN'),
        ):
            with self.subTest(content=content[:80]):
                (self.site / "index.html").write_bytes(content)
                before = self.files()
                result, fetch = self.run_repair()
                self.assertEqual(result, 1)
                fetch.assert_not_called()
                self.assertEqual(self.files(), before)

    def test_repair_requires_render_only_and_coin_argument(self):
        for args in (
            ["--repair-history", "xyz:PLTR"],
            ["--render-only", "--repair-history"],
            ["--render-only", "--repair-history", "xyz:PLTR", "--repair-history"],
        ):
            with self.subTest(args=args), patch.object(publisher.sys, "argv", [
                "publish_terminal.py", str(self.site), *args,
            ]), patch.object(publisher, "history") as fetch:
                before = self.files()
                self.assertEqual(publisher.main(), 1)
                fetch.assert_not_called()
                self.assertEqual(self.files(), before)


class DeferredRetryTests(unittest.TestCase):
    def run_normal_render(self, repair_succeeds):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source, site = root / "source", root / "site"
            source.mkdir()
            site.mkdir()
            (source / "terminal_template.html").write_text(
                "<script>window.__TERMINAL_DATA__=__DATA__;</script>__LW_JS__",
                encoding="utf-8",
            )
            (source / "lw.js").write_text("/* chart */", encoding="utf-8")
            rows = [{"coin": f"COIN{i}", "cat": "Crypto \u2014 Other"}
                    for i in range(100)]
            calls = []
            hist = [(1792666500 - i * 3600, .0001) for i in reversed(range(24))]

            def fetch(coin):
                calls.append(coin)
                if coin != "COIN0":
                    return []  # Newly listed coins must not be retried.
                if calls.count(coin) == 1 or not repair_succeeds:
                    raise RuntimeError("temporary source failure")
                return hist

            with patch.object(publisher.sys, "argv", [
                "publish_terminal.py", str(site), "--render-only",
            ]), patch.object(publisher, "HERE", str(source)), patch.object(
                publisher, "load_map", return_value={}
            ), patch.object(publisher, "snapshot_rows", return_value=rows), patch.object(
                publisher, "history", side_effect=fetch
            ), patch.object(publisher, "market_caps", return_value={}), patch.object(
                publisher.time, "sleep"
            ):
                result = publisher.main()
            html = (site / "index.html").read_text(encoding="utf-8")
            payload = html.split("window.__TERMINAL_DATA__=", 1)[1].split(
                ";</script>", 1)[0]
            return result, json.loads(payload), calls, (
                site / "data" / "hist" / "COIN0.json").exists()

    def test_transient_failure_retried_after_other_coins(self):
        result, data, calls, exists = self.run_normal_render(True)
        self.assertEqual(result, 0)
        self.assertEqual(calls, [f"COIN{i}" for i in range(100)] + ["COIN0"])
        self.assertEqual(data["rows"][0]["hist"], 1)
        self.assertTrue(exists)

    def test_second_failure_stays_unavailable_without_additional_retry(self):
        result, data, calls, exists = self.run_normal_render(False)
        self.assertEqual(result, 0)
        self.assertEqual(calls, [f"COIN{i}" for i in range(100)] + ["COIN0"])
        self.assertEqual(data["rows"][0]["hist"], 0)
        self.assertFalse(exists)


if __name__ == "__main__":
    unittest.main()
