#!/usr/bin/python3
# -*- encoding: utf-8 -*-
"""
@File    :   alist_strm_lite.py
@Desc    :   Alist 生成 strm 文件简化版（基于 WebDAV）
@Version :   v1.0
@Time    :   2024/11/16
@Author  :   xiaoQQya
@Contact :   xiaoQQya@126.com
"""
import os
import re
from webdav3.client import Client


class Alist_strm_lite:

    video_exts = ["mp4", "mkv", "flv", "mov", "m4v", "avi", "webm", "wmv"]
    default_config = {"url": "", "webdav_username": "", "webdav_password": "", "path_prefix": "/quark", "quark_root_dir": "/", "strm_save_dir": "/media", "strm_url_host": ""}
    is_active = False

    def __init__(self, **kwargs):
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.__class__.__name__} 模块缺少必要参数: {key}")

            options = {
                "webdav_hostname": f"{self.url.rstrip('/')}/dav/",
                "webdav_login": self.webdav_username,
                "webdav_password": self.webdav_password,
                "disable_check": True
            }
            self.client = Client(options)

            if self.url and self.webdav_username and self.webdav_password and self.get_info():
                self.is_active = True

    def run(self, task):
        if task.get("savepath") and task.get("savepath").startswith(self.quark_root_dir):
            path = self._normalize_path(task["savepath"])
            self.refresh(path)

    def get_info(self):
        try:
            response = self.client.info("/")
            if response.get("name"):
                print(f"Alist2strm Lite: {response.get('name')}")
                return True
            else:
                print(f"Alist2strm Lite: 连接失败❌ {response}")
        except Exception as e:
            print(f"Alist2strm Lite: 获取信息出错 {e}")
        return False

    def refresh(self, path):
        try:
            files = self.client.list(path)

            for item in files[1:]:
                full_path = re.sub(r"/{2,}", "/", f"{path}/{item}")
                if full_path.endswith("/"):
                    self.refresh(full_path)
                else:
                    self.generate_strm(full_path)
            return True
        except Exception as e:
            print(f"📺 生成STRM文件失败❌ {e}")
        return False

    def generate_strm(self, file_path):
        ext = file_path.split(".")[-1]
        if ext.lower() in self.video_exts:
            strm_path = re.sub(r"/{2,}", "/", f"{self.strm_save_dir}{file_path.rstrip(ext)}strm")
            if os.path.exists(strm_path):
                return
            if not os.path.exists(os.path.dirname(strm_path)):
                os.makedirs(os.path.dirname(strm_path))
            with open(strm_path, "w", encoding="utf-8") as strm_file:
                host = self.strm_url_host.rstrip("/") if self.strm_url_host.strip() else self.url.rstrip("/")
                strm_file.write(f"{host}/d{file_path}")
            print(f"📺 生成STRM文件 {strm_path} 成功✅")

    def _normalize_path(self, path):
        """标准化路径格式"""
        if not path.startswith(self.path_prefix):
            path = f"/{self.path_prefix}/{path.lstrip(self.quark_root_dir)}"
        return re.sub(r"/{2,}", "/", path)
