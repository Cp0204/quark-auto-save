#!/usr/bin/python3
# -*- encoding: utf-8 -*-
"""
@File    :   alist_strm_lite.py
@Desc    :   Alist 生成 strm 文件简化版
@Version :   v1.0
@Time    :   2024/11/16
@Author  :   xiaoQQya
@Contact :   xiaoQQya@126.com
"""
import os
import requests


class Alist_strm_lite:

    video_exts = ["mp4", "mkv", "flv", "mov", "m4v", "avi", "webm", "wmv"]
    default_config = {
        "url": "",  # Alist 服务器 URL
        "token": "",  # Alist 服务器 Token
        "quark_root_path": "/quark",  # 夸克根目录在 Alist 中的挂载路径
        "quark_root_dir": "/",  # 夸克在 Alist 中挂载的根目录
        "strm_save_dir": "/media",  # 生成的 strm 文件保存的路径
        "strm_url_host": ""  # strm 文件内链接的主机地址
    }
    is_active = False

    def __init__(self, **kwargs):
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.__class__.__name__} 模块缺少必要参数: {key}")
            if self.url and self.token and self.get_info():
                self.is_active = True

    def run(self, task):
        if task.get("savepath") and task.get("savepath").startswith(self.quark_root_dir):
            full_path = os.path.normpath(
                os.path.join(self.quark_root_path, task["savepath"].lstrip("/").lstrip(self.quark_root_dir))
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
                print(f"Alist-strm Lite: {response.get('data',[])[1].get('value','')} {response.get('data',[])[0].get('value','')}")
                return True
            else:
                print(f"Alist-strm Lite: 连接失败❌ {response.get('message')}")
        except requests.exceptions.RequestException as e:
            print(f"Alist-strm Lite: 获取Alist信息出错 {e}")
        return False

    def refresh(self, path):
        try:
            response = self.list(path)
            if response.get("code") != 200:
                print(f"📺 生成 STRM 文件失败❌ {response.get('message')}")
                return
            else:
                files = response.get("data").get("content")
                for item in files:
                    full_path = f"{path}/{item.get('name')}".replace("//", "/")
                    if item.get("is_dir"):
                        self.refresh(full_path)
                    else:
                        self.generate_strm(full_path)
        except Exception as e:
            print(f"📺 获取 Alist 文件列表失败❌ {e}")

    def list(self, path):
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
            strm_path = f"{self.strm_save_dir}{file_path.rstrip(ext)}strm".replace("//", "/")
            if os.path.exists(strm_path):
                return
            if not os.path.exists(os.path.dirname(strm_path)):
                os.makedirs(os.path.dirname(strm_path))
            with open(strm_path, "w", encoding="utf-8") as strm_file:
                host = self.strm_url_host.rstrip("/") if self.strm_url_host.strip() else self.url.rstrip("/")
                strm_file.write(f"{host}/d{file_path}")
            print(f"📺 生成STRM文件 {strm_path} 成功✅")
