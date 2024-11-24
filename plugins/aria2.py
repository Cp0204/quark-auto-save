import os
import requests


class Aria2:

    default_config = {
        "host_port": "172.17.0.1:6800",  # Aria2 RPC地址
        "secret": "",  # Aria2 RPC 密钥
        "dir": "/Downloads",  # 下载目录，需要Aria2有权限访问
    }
    default_task_config = {
        "auto_download": False,  # 是否开启自动下载
    }
    is_active = False
    rpc_url = None

    def __init__(self, **kwargs):
        self.plugin_name = self.__class__.__name__.lower()
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.plugin_name} 模块缺少必要参数: {key}")
            if self.host_port and self.secret:
                self.rpc_url = f"http://{self.host_port}/jsonrpc"
                if self.get_version():
                    self.is_active = True

    def run(self, task, **kwargs):
        if task_config := task.get("addition", {}).get(self.plugin_name, {}):
            if not task_config.get("auto_download"):
                return
        if (tree := kwargs.get("tree")) and (account := kwargs.get("account")):
            for node in tree.all_nodes_itr():
                if not node.data.get("is_dir", True):
                    quark_path = node.data.get("path")
                    quark_fid = node.data.get("fid")
                    save_path = f"{self.dir}{quark_path}"
                    print(f"📥 Aria2下载: {quark_path}")
                    download_return, cookie = account.download([quark_fid])
                    download_url = [
                        item["download_url"] for item in download_return["data"]
                    ]
                    aria2_params = [
                        download_url,
                        {
                            "header": [
                                f"Cookie: {cookie}",
                                f"User-Agent: {account.common_headers().get('user-agent')}",
                            ],
                            "out": os.path.basename(save_path),
                            "dir": os.path.dirname(save_path),
                        },
                    ]
                    self.add_uri(aria2_params)

    def _make_rpc_request(self, method, params=None):
        """发出 JSON-RPC 请求."""
        jsonrpc_data = {
            "jsonrpc": "2.0",
            "id": "quark-auto-save",
            "method": method,
            "params": params or [],
        }
        if self.secret:
            jsonrpc_data["params"].insert(0, f"token:{self.secret}")
        try:
            response = requests.post(self.rpc_url, json=jsonrpc_data)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Aria2下载: 错误{e}")
            return None

    def get_version(self):
        """检查与 Aria2 的连接."""
        response = self._make_rpc_request("aria2.getVersion")
        if response.get("result"):
            print(f"Aria2下载: v{response['result']['version']}")
            return True
        else:
            print(f"Aria2下载: 连接失败{response.get('error')}")
            return False

    def add_uri(self, params=None):
        """添加 URI 下载任务."""
        response = self._make_rpc_request("aria2.addUri", params)
        return response.get("result") if response else None
