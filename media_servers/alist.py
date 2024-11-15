import os
import requests


class Alist:

    default_config = {
        "url": "",  # Alist服务器URL
        "token": "",  # Alist服务器Token
        "quark_root_path": "/quark",  # 夸克根目录在Alist中的挂载路径
    }
    is_active = False

    def __init__(self, **kwargs):
        if kwargs:
            for key, value in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.__class__.__name__} 模块缺少必要参数: {key}")
            if self.url and self.token:
                if self.get_info():
                    self.is_active = True

    def run(self, task):
        if task.get("savepath"):
            full_path = os.path.normpath(
                os.path.join(self.quark_root_path, task["savepath"].lstrip("/"))
            ).replace("\\", "/")
            self.refresh(full_path)

    def get_info(self):
        url = f"{self.url}/api/admin/setting/list"
        headers = {"Authorization": self.token}
        querystring = {"group": "1"}
        try:
            response = requests.request("GET", url, headers=headers, params=querystring)
            response.raise_for_status()
            response = response.json()
            if response.get("code") == 200:
                print(
                    f"Alist: {response.get('data',[])[1].get('value','')} {response.get('data',[])[0].get('value','')}"
                )
                return True
            else:
                print(f"Alist: 连接失败❌ {response.get('message')}")
        except requests.exceptions.RequestException as e:
            print(f"获取Alist信息出错: {e}")
        return False

    def refresh(self, path, force_refresh=True):
        url = f"{self.url}/api/fs/list"
        headers = {"Authorization": self.token}
        payload = {
            "path": path,
            "refresh": force_refresh,
            "password": "",
            "page": 1,
            "per_page": 0,
        }
        try:
            response = requests.request("POST", url, headers=headers, json=payload)
            response.raise_for_status()
            response = response.json()
            if response.get("code") == 200:
                print(f"📁 刷新Alist目录：[{path}] 成功✅")
                return response.get("data")
            elif "object not found" in response.get("message", ""):
                # 如果是根目录就不再往上查找
                if path == "/" or path == self.quark_root_path:
                    print(f"📁 刷新Alist目录：根目录不存在，请检查 Alist 配置")
                    return False
                # 获取父目录
                parent_path = os.path.dirname(path)
                print(f"📁 刷新Alist目录：[{path}] 不存在，转父目录 [{parent_path}]")
                # 递归刷新父目录
                return self.refresh(parent_path)
            else:
                print(f"📁 刷新Alist目录：失败❌ {response.get('message')}")
        except requests.exceptions.RequestException as e:
            print(f"刷新Alist目录出错: {e}")
        return False
