#!/usr/bin/python3
# -*- encoding: utf-8 -*-
"""
@File    :   alist_strm_gen.py
@Desc    :   AList 生成 STRM 文件简化版
@Time    :   2025/05/30
@Author  :   xiaoQQya, x1ao4
"""
import os
import re
import json
import requests
from quark_auto_save import sort_file_by_name


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
    # 缓存生成的文件列表
    generated_files = []

    def __init__(self, **kwargs):
        self.plugin_name = self.__class__.__name__.lower()
        
        # 检查必要配置
        missing_configs = []
        for key, _ in self.default_config.items():
            if key in kwargs:
                setattr(self, key, kwargs[key])
            else:
                missing_configs.append(key)
        
        if missing_configs:
            return  # 不显示缺少参数的提示

        if not self.url or not self.token or not self.storage_id:
            return  # 不显示配置不完整的提示
            
        # 检查 strm_save_dir 是否存在
        if not os.path.exists(self.strm_save_dir):
            try:
                os.makedirs(self.strm_save_dir)
            except Exception as e:
                print(f"创建 STRM 保存目录失败 ❌ {e}")
                return
                
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
        else:
            pass

    def run(self, task, **kwargs):
        if not self.is_active:
            return
            
        task_config = task.get("addition", {}).get(
            self.plugin_name, self.default_task_config
        )
        
        if not task_config.get("auto_gen"):
            return
            
        if not task.get("savepath"):
            return
            
        # 标准化路径
        savepath = os.path.normpath(task["savepath"]).replace("\\", "/")
        quark_root = os.path.normpath(self.quark_root_dir).replace("\\", "/")
        
        # 确保路径以 / 开头
        if not savepath.startswith("/"):
            savepath = "/" + savepath
        if not quark_root.startswith("/"):
            quark_root = "/" + quark_root
            
        if not savepath.startswith(quark_root):
            print(f"{self.plugin_name} 任务的保存路径不在配置的夸克根目录内，跳过处理")
            return
            
        alist_path = os.path.normpath(
            os.path.join(
                self.storage_mount_path,
                savepath.replace(quark_root, "", 1).lstrip("/"),
            )
        ).replace("\\", "/")
        
        # 清空生成的文件列表
        self.generated_files = []
        self.check_dir(alist_path)
        
        # 按顺序显示生成的文件
        if self.generated_files:
            sorted_files = sorted(self.generated_files, key=sort_file_by_name)
            for file_path in sorted_files:
                print(f"🌐 生成 STRM 文件: {file_path} 成功 ✅")

    def storage_id_to_path(self, storage_id):
        storage_mount_path, quark_root_dir = None, None
        # 1. 检查是否符合 /aaa:/bbb 格式
        if match := re.match(r"^(\/[^:]*):(\/[^:]*)$", storage_id):
            # 存储挂载路径, 夸克根文件夹
            storage_mount_path, quark_root_dir = match.group(1), match.group(2)
            file_list = self.get_file_list(storage_mount_path)
            if file_list.get("code") != 200:
                print(f"AList-Strm 生成: 获取挂载路径失败 ❌ {file_list.get('message')}")
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
                        f"AList-Strm 生成: [QuarkTV] 驱动 ⚠️ storage_id 请手动填入 /Alist挂载路径:/Quark目录路径"
                    )
                else:
                    print(f"AList-Strm 生成: 不支持 [{storage_info['driver']}] 驱动 ❌")
        else:
            print(f"AList-Strm 生成: storage_id [{storage_id}] 格式错误 ❌")
        # 返回结果
        if storage_mount_path and quark_root_dir:
            print(f"AList-Strm 生成: [{storage_mount_path}:{quark_root_dir}]")
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
                print(f"AList-Strm 生成: 获取存储失败 ❌ {data.get('message')}")
        except Exception as e:
            print(f"AList-Strm 生成: 获取存储出错 ❌ {e}")
        return []

    def check_dir(self, path):
        data = self.get_file_list(path)
        if data.get("code") != 200:
            print(f"🌐 AList-Strm 生成: 获取文件列表失败 ❌ {data.get('message')}")
            return
        elif files := data.get("data", {}).get("content"):
            for item in files:
                item_path = f"{path}/{item.get('name')}".replace("//", "/")
                if item.get("is_dir"):
                    self.check_dir(item_path)
                else:
                    self.generate_strm(item_path)

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
            print(f"🌐 AList-Strm 生成: 获取文件列表出错 ❌ {e}")
        return {}

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
                try:
                    os.makedirs(os.path.dirname(strm_path))
                except Exception as e:
                    print(f"🌐 创建目录失败: {os.path.dirname(strm_path)} ❌ {e}")
                    return
            try:
                with open(strm_path, "w", encoding="utf-8") as strm_file:
                    strm_file.write(f"{self.strm_server}{file_path}")
                # 将生成的文件添加到列表中，稍后统一显示
                self.generated_files.append(strm_path)
            except Exception as e:
                print(f"🌐 生成 STRM 文件: {strm_path} 失败 ❌ {e}")

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
            print(f"AList-Strm 生成: 获取 Quark 路径出错 ❌ {e}")
        return ""
