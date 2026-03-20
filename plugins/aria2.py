import os
import requests


class Aria2:

    default_config = {
        "host_port": "172.17.0.1:6800",  # Aria2 RPC地址，填入格式支持：host:port 或 http(s)://host:port
        "secret": "",  # Aria2 RPC 密钥
        "dir": "/Downloads",  # 下载目录，需要Aria2有权限访问
    }
    default_task_config = {
        "auto_download": False,  # 是否自动添加下载任务
        "save_path": "",  # 留空时跟随夸克网盘目录结构（dir/夸克路径），填写时下载到 dir/save_path/
        "pause": False,  # 添加任务后为暂停状态，不自动开始（手动下载）
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
                self.rpc_url = self._get_rpc_url(self.host_port)
                if self.get_version():
                    self.is_active = True

    def _get_rpc_url(self, endpoint):
        """获取 RPC URL"""
        if "://" in endpoint:
            scheme, url_part = endpoint.split("://", 1)
            parts = url_part.split("/", 1)
            host_port = parts[0]
            path = parts[1] if len(parts) > 1 and parts[1] else "jsonrpc"
            return f"{scheme.lower()}://{host_port}/{path}"
        else:
            # 默认使用 HTTP
            return f"http://{self.host_port}/jsonrpc"

    def _recursive_dir(self, account, fid, parent_path, file_fids, file_paths):
        for item in account.ls_dir(fid)["data"]["list"]:
            item_fid = item["fid"]
            item_name = item["file_name"]
            item_path = f"{parent_path}/{item_name}" if parent_path else item_name

            if item["dir"]:
                self._recursive_dir(account, item_fid, item_path, file_fids, file_paths)
            else:
                file_fids.append(item_fid)
                file_paths.append(item_path)

    def run(self, task, **kwargs):
        task_config = task.get("addition", {}).get(
            self.plugin_name, self.default_task_config
        )
        if not task_config.get("auto_download"):
            return
        if (tree := kwargs.get("tree")) and (account := kwargs.get("account")):
            # 按文件路径排序添加下载任务
            nodes = sorted(
                tree.all_nodes_itr(), key=lambda node: node.data.get("path", "")
            )
            file_fids = []
            file_paths = []
            for node in nodes:
                if not node.data.get("is_dir", True):
                    file_fids.append(node.data.get("fid"))
                    file_paths.append(node.data.get("path"))
                elif not node.is_root():
                    self._recursive_dir(account, node.data.get("fid"), node.data.get("path"), file_fids, file_paths)
            if not file_fids:
                print(f"Aria2下载: 没有下载任务，跳过")
                return
            download_return, cookie = account.download(file_fids)
            file_urls = [item["download_url"] for item in download_return["data"]]
            for index, file_url in enumerate(file_urls):
                file_path = file_paths[index]
                print(f"📥 Aria2下载: {file_path}")
                if task_config.get("save_path"):
                    file_name = os.path.basename(file_path)
                    save_path = task_config["save_path"].strip("/")
                    local_path = f"{self.dir}/{save_path}/{file_name}"
                else:
                    local_path = f"{self.dir}{file_path}"
                aria2_params = [
                    [file_url],
                    {
                        "header": [
                            f"Cookie: {cookie or account.cookie}",
                            f"User-Agent: {account.USER_AGENT}",
                        ],
                        "out": os.path.basename(local_path),
                        "dir": os.path.dirname(local_path),
                        "pause": str(task_config.get("pause")).lower(),
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
            response = requests.post(
                self.rpc_url, json=jsonrpc_data, timeout=10, verify=False
            )
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"Aria2下载: 错误{e}")
        return {}

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
        return response.get("result") if response else {}
