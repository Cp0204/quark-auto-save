import json
import unittest
from unittest.mock import patch

from plugins.alist_sync import Alist_sync


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class AlistSyncPathListTests(unittest.TestCase):
    def build_plugin(self):
        plugin = Alist_sync()
        plugin.url = "http://alist.local"
        plugin.token = "token"
        return plugin

    def test_refresh_parent_directory_when_child_not_found(self):
        plugin = self.build_plugin()
        calls = []
        responses = [
            FakeResponse(
                200,
                {
                    "code": 500,
                    "message": "failed get objs: failed get dir: object not found",
                    "data": None,
                },
            ),
            FakeResponse(
                200,
                {"code": 200, "message": "success", "data": {"content": []}},
            ),
            FakeResponse(
                200,
                {
                    "code": 200,
                    "message": "success",
                    "data": {"content": [{"name": "demo-file.mkv"}]},
                },
            ),
        ]

        def fake_send(method, url, data=None, **kwargs):
            calls.append(json.loads(data))
            return responses.pop(0)

        plugin._send_request = fake_send

        with patch("plugins.alist_sync.time.sleep", return_value=None):
            result = plugin.get_path_list("/kuake/video/demo")

        self.assertEqual([{"name": "demo-file.mkv"}], result)
        self.assertEqual("/kuake/video/demo", calls[0]["path"])
        self.assertEqual("/kuake/video", calls[1]["path"])
        self.assertEqual("/kuake/video/demo", calls[2]["path"])

    def test_return_false_when_alist_returns_null_data(self):
        plugin = self.build_plugin()
        calls = []
        responses = [
            FakeResponse(
                200,
                {"code": 500, "message": "transient", "data": None},
            )
            for _ in range(4)
        ]

        def fake_send(method, url, data=None, **kwargs):
            calls.append(json.loads(data))
            return responses.pop(0)

        plugin._send_request = fake_send

        with patch("plugins.alist_sync.time.sleep", return_value=None):
            result = plugin.get_path_list("/kuake/video/demo")

        self.assertFalse(result)
        self.assertEqual(4, len(calls))


if __name__ == "__main__":
    unittest.main()
