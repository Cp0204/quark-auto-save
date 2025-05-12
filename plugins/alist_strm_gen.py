#!/usr/bin/python3
# -*- encoding: utf-8 -*-
"""
@File    :   alist_strm_gen.py
@Desc    :   Alist 生成 strm 文件简化版
@Version :   v1.1
@Time    :   2024/11/16
@Author  :   xiaoQQya
@Contact :   xiaoQQya@126.com
"""
import os
import re
import json
import requests


class Alist_strm_gen:

    video_exts = ["mp4", "mkv", "flv", "mov", "m4v", "avi", "webm", "wmv"]
    default_config = {
        "url": "",  # Alist 服务器 URL
        "token": "",  # Alist 服务器 Token
        "storage_id": "",  # Alist 服务器夸克存储 ID
        "strm_save_dir": "/media",  # 生成的 strm 文件保存的路径
        "strm_replace_host": "",  # strm 文件内链接的主机地址 （可选，缺省时=url）
    }
    default_task_config = {
        "auto_gen": True,  # 是否自动生成 strm 文件
    }
    is_active = False
    # 缓存参数
    storage_mount_path = None
    quark_root_dir = None
    strm_server = None

    def __init__(self, **kwargs):
        self.plugin_name = self.__class__.__name__.lower()
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.plugin_name} 模块缺少必要参数: {key}")
            if self.url and self.token and self.storage_id:
                success, result = self.storage_id_to_path(self.storage_id)
                if success:
                    self.is_active = True
                    # 存储挂载路径, 夸克根文件夹
                    self.storage_mount_path, self.quark_root_dir = result
                    # 替换strm文件内链接的主机地址
                    self.strm_replace_host = self.strm_replace_host.strip()
                    if self.strm_replace_host:
                        if self.strm_replace_host.startswith("http"):
                            self.strm_server = f"{self.strm_replace_host}/d"
                        else:
                            self.strm_server = f"http://{self.strm_replace_host}/d"
                    else:
                        self.strm_server = f"{self.url.strip()}/d"

    def run(self, task, **kwargs):
        task_config = task.get("addition", {}).get(
            self.plugin_name, self.default_task_config
        )
        if not task_config.get("auto_gen"):
            return
        if task.get("savepath") and task.get("savepath").startswith(
            self.quark_root_dir
        ):
            alist_path = os.path.normpath(
                os.path.join(
                    self.storage_mount_path,
                    task["savepath"].replace(self.quark_root_dir, "", 1).lstrip("/"),
                )
            ).replace("\\", "/")
            self.check_dir(alist_path)

    def storage_id_to_path(self, storage_id):
        storage_mount_path, quark_root_dir = None, None
        # 1. 检查是否符合 /aaa:/bbb 格式
        if match := re.match(r"^(\/[^:]*):(\/[^:]*)$", storage_id):
            # 存储挂载路径, 夸克根文件夹
            storage_mount_path, quark_root_dir = match.group(1), match.group(2)
            file_list = self.get_file_list(storage_mount_path)
            if file_list.get("code") != 200:
                print(f"Alist-Strm生成: 获取挂载路径失败❌ {file_list.get('message')}")
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
                        f"Alist-Strm生成: [QuarkTV]驱动⚠️ storage_id请手动填入 /Alist挂载路径:/Quark目录路径"
                    )
                else:
                    print(f"Alist-Strm生成: 不支持[{storage_info['driver']}]驱动 ❌")
        else:
            print(f"Alist-Strm生成: storage_id[{storage_id}]格式错误❌")
        # 返回结果
        if storage_mount_path and quark_root_dir:
            print(f"Alist-Strm生成: [{storage_mount_path}:{quark_root_dir}]")
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
                print(f"Alist-Strm生成: 获取存储失败❌ {data.get('message')}")
        except Exception as e:
            print(f"Alist-Strm生成: 获取存储出错 {e}")
        return []

    def check_dir(self, path):
        data = self.get_file_list(path)
        if data.get("code") != 200:
            print(f"📺 Alist-Strm生成: 获取文件列表失败❌{data.get('message')}")
            return
        elif files := data.get("data", {}).get("content"):
            for item in files:
                item_path = f"{path}/{item.get('name')}".replace("//", "/")
                if item.get("is_dir"):
                    self.check_dir(item_path)
                else:
                    self.generate_strm(item_path, item)

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
            print(f"📺 Alist-Strm生成: 获取文件列表出错❌ {e}")
        return {}

    def generate_strm(self, file_path, file_info):
        ext = file_path.split(".")[-1]
        if ext.lower() in self.video_exts:
            strm_path = (
                f"{self.strm_save_dir}{os.path.splitext(file_path)[0]}.strm".replace(
                    "//", "/"
                )
            )
            if os.path.exists(strm_path):
                return
            if not os.path.exists(os.path.dirname(strm_path)):
                os.makedirs(os.path.dirname(strm_path))
            sign_param = (
                "" if not file_info.get("sign") else f"?sign={file_info['sign']}"
            )
            with open(strm_path, "w", encoding="utf-8") as strm_file:
                strm_file.write(f"{self.strm_server}{file_path}{sign_param}")
            print(f"📺 生成STRM文件 {strm_path} 成功✅")

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
            print(f"Alist-Strm生成: 获取Quark路径出错 {e}")
        return ""
