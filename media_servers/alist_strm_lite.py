#!/usr/bin/python3
# -*- encoding: utf-8 -*-
"""
@File    :   alist_strm_lite.py
@Desc    :   Alist 生成 strm 文件简化版
@Version :   v1.1
@Time    :   2024/11/16
@Author  :   xiaoQQya
@Contact :   xiaoQQya@126.com
"""
import os
import json
import requests


class Alist_strm_lite:

    video_exts = ["mp4", "mkv", "flv", "mov", "m4v", "avi", "webm", "wmv"]
    default_config = {
        "url": "",  # Alist 服务器 URL
        "token": "",  # Alist 服务器 Token
        "storage_id": "",  # Alist 服务器夸克存储 ID
        "strm_save_dir": "/media",  # 生成的 strm 文件保存的路径
        "strm_url_host": "",  # strm 文件内链接的主机地址 （可选，缺省时=url）
    }
    is_active = False
    storage_mount_path = None
    quark_root_dir = None

    def __init__(self, **kwargs):
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.__class__.__name__} 模块缺少必要参数: {key}")
            if self.url and self.token and self.storage_id:
                storage_info = self.get_storage_info(self.storage_id)
                if storage_info:
                    addition = json.loads(storage_info["addition"])
                    # 存储挂载路径
                    self.storage_mount_path = storage_info["mount_path"]
                    # 夸克根文件夹
                    self.quark_root_dir = self.get_root_folder_full_path(
                        addition["cookie"], addition["root_folder_id"]
                    )
                    if self.storage_mount_path and self.quark_root_dir:
                        self.is_active = True

    def run(self, task):
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

    def get_storage_info(self, storage_id):
        url = f"{self.url}/api/admin/storage/get"
        headers = {"Authorization": self.token}
        querystring = {"id": storage_id}
        try:
            response = requests.request("GET", url, headers=headers, params=querystring)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 200:
                print(f"Alist-strm Lite: Storage[{data['data']['mount_path']}]")
                return data.get("data", [])
            else:
                print(f"Alist-strm Lite: 连接失败❌ {response.get('message')}")
        except requests.exceptions.RequestException as e:
            print(f"Alist-strm Lite: 获取Alist存储出错 {e}")
        return False

    def refresh(self, path):
        try:
            response = self.get_file_list(path)
            if response.get("code") != 200:
                print(f"📺 生成 STRM 文件失败❌ {response.get('message')}")
                return
            else:
                files = response.get("data").get("content")
                for item in files:
                    item_path = f"{path}/{item.get('name')}".replace("//", "/")
                    if item.get("is_dir"):
                        self.refresh(item_path)
                    else:
                        self.generate_strm(item_path)
        except Exception as e:
            print(f"📺 获取 Alist 文件列表失败❌ {e}")

    def get_file_list(self, path):
        url = f"{self.url}/api/fs/list"
        headers = {"Authorization": self.token}
        payload = {
            "path": path,
            "refresh": False,
            "password": "",
            "page": 1,
            "per_page": 0,
        }
        response = requests.request("POST", url, headers=headers, json=payload)
        response.raise_for_status()
        return response.json()

    def generate_strm(self, file_path):
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
            with open(strm_path, "w", encoding="utf-8") as strm_file:
                host = (
                    self.strm_url_host if self.strm_url_host.strip() else self.url
                ).rstrip("/")
                strm_file.write(f"{host}/d{file_path}")
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
                file_names = [
                    item["file_name"] for item in response["data"]["full_path"]
                ]
                return "/".join(file_names)
        except requests.exceptions.RequestException as e:
            print(f"Alist-strm Lite: 获取Quark路径出错 {e}")
        return False
