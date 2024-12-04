import os
import re
import json
import requests


class Alist:

    default_config = {
        "url": "",  # Alist服务器URL
        "token": "",  # Alist服务器Token
        "storage_id": "",  # Alist 服务器夸克存储 ID
    }
    is_active = False
    # 缓存参数
    storage_mount_path = None
    quark_root_dir = None

    def __init__(self, **kwargs):
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.__class__.__name__} 模块缺少必要参数: {key}")
            if self.url and self.token:
                if self.get_info():
                    success, result = self.storage_id_to_path(self.storage_id)
                    if success:
                        self.storage_mount_path, self.quark_root_dir = result
                        self.is_active = True

    def run(self, task, **kwargs):
        if task.get("savepath") and task.get("savepath").startswith(
            self.quark_root_dir
        ):
            alist_path = os.path.normpath(
                os.path.join(
                    self.storage_mount_path,
                    task["savepath"].replace(self.quark_root_dir, "", 1).lstrip("/"),
                )
            ).replace("\\", "/")
            self.refresh(alist_path)

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
                    f"Alist刷新: {response.get('data',[])[1].get('value','')} {response.get('data',[])[0].get('value','')}"
                )
                return True
            else:
                print(f"Alist刷新: 连接失败❌ {response.get('message')}")
        except requests.exceptions.RequestException as e:
            print(f"获取Alist信息出错: {e}")
        return False

    def storage_id_to_path(self, storage_id):
        storage_mount_path, quark_root_dir = None, None
        # 1. 检查是否符合 /aaa:/bbb 格式
        if match := re.match(r"^(\/[^:]*):(\/[^:]*)$", storage_id):
            # 存储挂载路径, 夸克根文件夹
            storage_mount_path, quark_root_dir = match.group(1), match.group(2)
            file_list = self.get_file_list(storage_mount_path)
            if file_list.get("code") != 200:
                print(f"Alist刷新: 获取挂载路径失败❌ {file_list.get('message')}")
                return False, (None, None)
        # 2. 检查是否数字，调用 Alist API 获取存储信息
        elif re.match(r"^\d+$", storage_id):
            if storage_info := self.get_storage_info(storage_id):
                if storage_info["driver"] == "Quark":
                    addition = json.loads(storage_info["addition"])
                    # 存储挂载路径
                    storage_mount_path = storage_info["mount_path"]
                    # 夸克根文件夹
                    quark_root_dir = self.get_root_folder_full_path(
                        addition["cookie"], addition["root_folder_id"]
                    )
                elif storage_info["driver"] == "QuarkTV":
                    print(
                        f"Alist刷新: [QuarkTV]驱动⚠️ storage_id请手动填入 /Alist挂载路径:/Quark目录路径"
                    )
                else:
                    print(f"Alist刷新: 不支持[{storage_info['driver']}]驱动 ❌")
        else:
            print(f"Alist刷新: storage_id[{storage_id}]格式错误❌")
        # 返回结果
        if storage_mount_path and quark_root_dir:
            return True, (storage_mount_path, quark_root_dir)
        else:
            return False, (None, None)

    def get_storage_info(self, storage_id):
        url = f"{self.url}/api/admin/storage/get"
        headers = {"Authorization": self.token}
        querystring = {"id": storage_id}
        try:
            response = requests.request("GET", url, headers=headers, params=querystring)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 200:
                return data.get("data", [])
            else:
                print(f"Alist刷新: 存储{storage_id}连接失败❌ {data.get('message')}")
        except Exception as e:
            print(f"Alist刷新: 获取Alist存储出错 {e}")
        return []

    def refresh(self, path):
        data = self.get_file_list(path, True)
        if data.get("code") == 200:
            print(f"📁 Alist刷新：目录[{path}] 成功✅")
            return data.get("data")
        elif "object not found" in data.get("message", ""):
            # 如果是根目录就不再往上查找
            if path == "/" or path == self.storage_mount_path:
                print(f"📁 Alist刷新：根目录不存在，请检查 Alist 配置")
                return False
            # 获取父目录
            parent_path = os.path.dirname(path)
            print(f"📁 Alist刷新：[{path}] 不存在，转父目录 [{parent_path}]")
            # 递归刷新父目录
            return self.refresh(parent_path)
        else:
            print(f"📁 Alist刷新：失败❌ {data.get('message')}")

    def get_file_list(self, path, force_refresh=False):
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
            return response.json()
        except Exception as e:
            print(f"📁 Alist刷新: 获取文件列表出错❌ {e}")
        return {}

    def get_root_folder_full_path(self, cookie, pdir_fid):
        if pdir_fid == "0":
            return "/"
        url = "https://drive-h.quark.cn/1/clouddrive/file/sort"
        headers = {
            "cookie": cookie,
            "content-type": "application/json",
        }
        querystring = {
            "pr": "ucpro",
            "fr": "pc",
            "uc_param_str": "",
            "pdir_fid": pdir_fid,
            "_page": 1,
            "_size": "50",
            "_fetch_total": "1",
            "_fetch_sub_dirs": "0",
            "_sort": "file_type:asc,updated_at:desc",
            "_fetch_full_path": 1,
        }
        try:
            response = requests.request(
                "GET", url, headers=headers, params=querystring
            ).json()
            if response["code"] == 0:
                path = ""
                for item in response["data"]["full_path"]:
                    path = f"{path}/{item['file_name']}"
                return path
        except Exception as e:
            print(f"Alist刷新: 获取Quark路径出错 {e}")
        return ""
