# !/usr/bin/env python3
# -*- coding: utf-8 -*-
# Modify: 2026-02-04
# Repo: https://github.com/Cp0204/quark_auto_save
# ConfigFile: quark_config.json
"""
new Env('夸克自动追更');
0 8,18,20 * * * quark_auto_save.py
"""
import os
import re
import sys
import json
import time
import random
import platform
import requests
import importlib
import traceback
import urllib.parse
from datetime import datetime
from natsort import natsorted

# 兼容青龙
try:
    from treelib import Tree
except:
    print("正在尝试自动安装依赖...")
    os.system("pip3 install treelib &> /dev/null")
    from treelib import Tree


CONFIG_DATA = {}
NOTIFYS = []
GH_PROXY = os.environ.get("GH_PROXY", "https://ghproxy.net/")


# 发送通知消息
def send_ql_notify(title, body):
    try:
        # 导入通知模块
        import notify

        # 如未配置 push_config 则使用青龙环境通知设置
        if CONFIG_DATA.get("push_config"):
            notify.push_config.update(CONFIG_DATA["push_config"])
            notify.push_config["CONSOLE"] = notify.push_config.get("CONSOLE", True)
        notify.send(title, body)
    except Exception as e:
        if e:
            print("发送通知消息失败！")


# 添加消息
def add_notify(text):
    global NOTIFYS
    NOTIFYS.append(text)
    print("📢", text)
    return text


class Config:
    # 下载配置
    def download_file(url, save_path):
        response = requests.get(url)
        if response.status_code == 200:
            with open(save_path, "wb") as file:
                file.write(response.content)
            return True
        else:
            return False

    # 读取 JSON 文件内容
    def read_json(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data

    # 将数据写入 JSON 文件
    def write_json(config_path, data):
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, sort_keys=False, indent=2)

    # 读取CK
    def get_cookies(cookie_val):
        if isinstance(cookie_val, list):
            return cookie_val
        elif cookie_val:
            if "\n" in cookie_val:
                return cookie_val.split("\n")
            else:
                return [cookie_val]
        else:
            return False

    def load_plugins(plugins_config={}, plugins_dir="plugins"):
        PLUGIN_FLAGS = os.environ.get("PsLUGIN_FLAGS", "").split(",")
        plugins_available = {}
        task_plugins_config = {}
        # 获取所有模块
        sys_ext = "pyd" if platform.system() == "Windows" else "so"
        module_ext = [".py", f".{sys_ext}"]
        all_modules = [
            f.replace(ext, "")
            for f in os.listdir(plugins_dir)
            for ext in module_ext
            if f.endswith(ext)
        ]
        # 调整模块优先级
        priority_path = os.path.join(plugins_dir, "_priority.json")
        try:
            with open(priority_path, encoding="utf-8") as f:
                priority_modules = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            priority_modules = []
        if priority_modules:
            all_modules = [
                module for module in priority_modules if module in all_modules
            ] + [module for module in all_modules if module not in priority_modules]
        # 加载模块
        for module_name in all_modules:
            if f"-{module_name}" in PLUGIN_FLAGS:
                continue
            try:
                module = importlib.import_module(f"{plugins_dir}.{module_name}")
                ServerClass = getattr(module, module_name.capitalize())
                # 检查配置中是否存在该模块的配置
                if module_name in plugins_config:
                    plugin = ServerClass(**plugins_config[module_name])
                    plugins_available[module_name] = plugin
                else:
                    plugin = ServerClass()
                    plugins_config[module_name] = plugin.default_config
                # 检查插件是否支持单独任务配置
                if hasattr(plugin, "default_task_config"):
                    task_plugins_config[module_name] = plugin.default_task_config
            except (ImportError, AttributeError) as e:
                print(f"载入模块 {module_name} 失败: {e}")
        return plugins_available, plugins_config, task_plugins_config

    def breaking_change_update(config_data):
        # 🔼 Update config v0.5.x to 0.6.0
        for task in config_data.get("tasklist", []):
            if "$TASKNAME" in task.get("replace", ""):
                task["replace"] = task["replace"].replace("$TASKNAME", "{TASKNAME}")


class MagicRename:

    magic_regex = {
        "$TV": {
            "pattern": r".*?([Ss]\d{1,2})?(?:[第EePpXx\.\-\_\( ]{1,2}|^)(\d{1,3})(?!\d).*?\.(mp4|mkv)",
            "replace": r"\1E\2.\3",
        },
        "$BLACK_WORD": {
            "pattern": r"^(?!.*纯享)(?!.*加更)(?!.*超前企划)(?!.*训练室)(?!.*蒸蒸日上).*",
            "replace": "",
        },
    }

    magic_variable = {
        "{TASKNAME}": "",
        "{I}": 1,
        "{EXT}": [r"(?<=\.)\w+$"],
        "{CHINESE}": [r"[\u4e00-\u9fa5]{2,}"],
        "{DATE}": [
            r"(18|19|20)?\d{2}[\.\-/年]\d{1,2}[\.\-/月]\d{1,2}",
            r"(?<!\d)[12]\d{3}[01]?\d[0123]?\d",
            r"(?<!\d)[01]?\d[\.\-/月][0123]?\d",
        ],
        "{YEAR}": [r"(?<!\d)(18|19|20)\d{2}(?!\d)"],
        "{S}": [r"(?<=[Ss])\d{1,2}(?=[EeXx])", r"(?<=[Ss])\d{1,2}"],
        "{SXX}": [r"[Ss]\d{1,2}(?=[EeXx])", r"[Ss]\d{1,2}"],
        "{E}": [
            r"(?<=[Ss]\d\d[Ee])\d{1,3}",
            r"(?<=[Ee])\d{1,3}",
            r"(?<=[Ee][Pp])\d{1,3}",
            r"(?<=第)\d{1,3}(?=[集期话部篇])",
            r"(?<!\d)\d{1,3}(?=[集期话部篇])",
            r"(?!.*19)(?!.*20)(?<=[\._])\d{1,3}(?=[\._])",
            r"^\d{1,3}(?=\.\w+)",
            r"(?<!\d)\d{1,3}(?!\d)(?!$)",
        ],
        "{PART}": [
            r"(?<=[集期话部篇第])[上中下一二三四五六七八九十]",
            r"[上中下一二三四五六七八九十]",
        ],
        "{VER}": [r"[\u4e00-\u9fa5]+版"],
    }

    priority_list = [
        "上",
        "中",
        "下",
        "一",
        "二",
        "三",
        "四",
        "五",
        "六",
        "七",
        "八",
        "九",
        "十",
        "百",
        "千",
        "万",
    ]

    def __init__(self, magic_regex={}, magic_variable={}):
        self.magic_regex.update(magic_regex)
        self.magic_variable.update(magic_variable)
        self.dir_filename_dict = {}

    def set_taskname(self, taskname):
        """设置任务名称"""
        self.magic_variable["{TASKNAME}"] = taskname

    def magic_regex_conv(self, pattern, replace):
        """魔法正则匹配"""
        keyword = pattern
        if keyword in self.magic_regex:
            pattern = self.magic_regex[keyword]["pattern"]
            if replace == "":
                replace = self.magic_regex[keyword]["replace"]
        return pattern, replace

    def sub(self, pattern, replace, file_name):
        """魔法正则、变量替换"""
        if not replace:
            return file_name
        # 预处理替换变量
        for key, p_list in self.magic_variable.items():
            if key in replace:
                # 正则类替换变量
                if p_list and isinstance(p_list, list):
                    for p in p_list:
                        match = re.search(p, file_name)
                        if match:
                            # 匹配成功，替换为匹配到的值
                            value = match.group()
                            # 日期格式处理：补全、格式化
                            if key == "{DATE}":
                                value = "".join(
                                    [char for char in value if char.isdigit()]
                                )
                                value = (
                                    str(datetime.now().year)[: (8 - len(value))] + value
                                )
                            replace = replace.replace(key, value)
                            break
                # 非正则类替换变量
                if key == "{TASKNAME}":
                    replace = replace.replace(key, self.magic_variable["{TASKNAME}"])
                elif key == "{SXX}" and not match:
                    replace = replace.replace(key, "S01")
                elif key == "{I}":
                    continue
                else:
                    # 清理未匹配的 magic_variable key
                    replace = replace.replace(key, "")
        if pattern and replace:
            file_name = re.sub(pattern, replace, file_name)
        else:
            file_name = replace
        return file_name

    def _custom_sort_key(self, name):
        """自定义排序键"""
        for i, keyword in enumerate(self.priority_list):
            if keyword in name:
                name = name.replace(keyword, f"_{i:02d}_")  # 替换为数字，方便排序
        return name

    def sort_file_list(self, file_list, dir_filename_dict={}):
        """文件列表统一排序，给{I+}赋值"""
        filename_list = [
            # 强制加入`文件修改时间`字段供排序，效果：1无可排序字符时则按修改时间排序，2和目录已有文件重名时始终在其后
            f"{f['file_name_re']}_{f['updated_at']}"
            for f in file_list
            if f.get("file_name_re") and not f["dir"]
        ]
        # print(f"filename_list_before: {filename_list}")
        dir_filename_dict = dir_filename_dict or self.dir_filename_dict
        # print(f"dir_filename_list: {dir_filename_list}")
        # 合并目录文件列表
        filename_list = list(set(filename_list) | set(dir_filename_dict.values()))
        filename_list = natsorted(filename_list, key=self._custom_sort_key)
        filename_index = {}
        for name in filename_list:
            if name in dir_filename_dict.values():
                continue
            i = filename_list.index(name) + 1
            while i in dir_filename_dict.keys():
                i += 1
            dir_filename_dict[i] = name
            filename_index[name] = i
        for file in file_list:
            if file.get("file_name_re"):
                if match := re.search(r"\{I+\}", file["file_name_re"]):
                    i = filename_index.get(
                        f"{file['file_name_re']}_{file['updated_at']}", 0
                    )
                    file["file_name_re"] = re.sub(
                        match.group(),
                        str(i).zfill(match.group().count("I")),
                        file["file_name_re"],
                    )

    def set_dir_file_list(self, file_list, replace):
        """设置目录文件列表"""
        self.dir_filename_dict = {}
        filename_list = [f["file_name"] for f in file_list if not f["dir"]]
        filename_list.sort()
        if not filename_list:
            return
        if match := re.search(r"\{I+\}", replace):
            # 由替换式转换匹配式
            magic_i = match.group()
            pattern_i = r"\d" * magic_i.count("I")
            pattern = replace.replace(match.group(), "🔢")
            for key, _ in self.magic_variable.items():
                if key in pattern:
                    pattern = pattern.replace(key, "🔣")
            pattern = re.sub(r"\\[0-9]+", "🔣", pattern)  # \1 \2 \3
            pattern = f"({re.escape(pattern).replace('🔣', '.*?').replace('🔢', f')({pattern_i})(')})"
            # print(f"pattern: {pattern}")
            # 获取起始编号
            if match := re.match(pattern, filename_list[-1]):
                self.magic_variable["{I}"] = int(match.group(2))
            # 目录文件列表
            for filename in filename_list:
                if match := re.match(pattern, filename):
                    self.dir_filename_dict[int(match.group(2))] = (
                        match.group(1) + magic_i + match.group(3)
                    )
            # print(f"filename_list: {self.filename_list}")

    def is_exists(self, filename, filename_list, ignore_ext=False):
        """判断文件是否存在，处理忽略扩展名"""
        # print(f"filename: {filename} filename_list: {filename_list}")
        if ignore_ext:
            filename = os.path.splitext(filename)[0]
            filename_list = [os.path.splitext(f)[0] for f in filename_list]
        # {I+} 模式，用I通配数字序号
        if match := re.search(r"\{I+\}", filename):
            magic_i = match.group()
            pattern_i = r"\d" * magic_i.count("I")
            pattern = re.escape(filename).replace(re.escape(magic_i), pattern_i)
            for filename in filename_list:
                if re.match(pattern, filename):
                    return filename
            return None
        else:
            return filename if filename in filename_list else None


class Quark:
    BASE_URL = "https://drive-pc.quark.cn"
    BASE_URL_APP = "https://drive-m.quark.cn"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) quark-cloud-drive/3.14.2 Chrome/112.0.5615.165 Electron/24.1.3.8 Safari/537.36 Channel/pckk_other_ch"

    def __init__(self, cookie="", index=0):
        self.cookie = cookie.strip()
        self.index = index + 1
        self.is_active = False
        self.nickname = ""
        self.mparam = self._match_mparam_form_cookie(cookie)
        self.savepath_fid = {"/": "0"}

    def _match_mparam_form_cookie(self, cookie):
        mparam = {}
        kps_match = re.search(r"(?<!\w)kps=([a-zA-Z0-9%+/=]+)[;&]?", cookie)
        sign_match = re.search(r"(?<!\w)sign=([a-zA-Z0-9%+/=]+)[;&]?", cookie)
        vcode_match = re.search(r"(?<!\w)vcode=([a-zA-Z0-9%+/=]+)[;&]?", cookie)
        if kps_match and sign_match and vcode_match:
            mparam = {
                "kps": kps_match.group(1).replace("%25", "%"),
                "sign": sign_match.group(1).replace("%25", "%"),
                "vcode": vcode_match.group(1).replace("%25", "%"),
            }
        return mparam

    def _send_request(self, method, url, **kwargs):
        headers = {
            "cookie": self.cookie,
            "content-type": "application/json",
            "user-agent": self.USER_AGENT,
        }
        if "headers" in kwargs:
            headers = kwargs["headers"]
            del kwargs["headers"]
        if self.mparam and "share" in url and self.BASE_URL in url:
            url = url.replace(self.BASE_URL, self.BASE_URL_APP)
            kwargs["params"].update(
                {
                    "device_model": "M2011K2C",
                    "entry": "default_clouddrive",
                    "_t_group": "0%3A_s_vp%3A1",
                    "dmn": "Mi%2B11",
                    "fr": "android",
                    "pf": "3300",
                    "bi": "35937",
                    "ve": "7.4.5.680",
                    "ss": "411x875",
                    "mi": "M2011K2C",
                    "nt": "5",
                    "nw": "0",
                    "kt": "4",
                    "pr": "ucpro",
                    "sv": "release",
                    "dt": "phone",
                    "data_from": "ucapi",
                    "kps": self.mparam.get("kps"),
                    "sign": self.mparam.get("sign"),
                    "vcode": self.mparam.get("vcode"),
                    "app": "clouddrive",
                    "kkkk": "1",
                }
            )
            del headers["cookie"]
        try:
            response = requests.request(method, url, headers=headers, **kwargs)
            # print(f"{response.text}")
            # response.raise_for_status()  # 检查请求是否成功，但返回非200也会抛出异常
            return response
        except Exception as e:
            print(f"_send_request error:\n{e}")
            fake_response = requests.Response()
            fake_response.status_code = 500
            fake_response._content = (
                b'{"status": 500, "code": 1, "message": "request error"}'
            )
            return fake_response

    def init(self):
        account_info = self.get_account_info()
        if account_info:
            self.is_active = True
            self.nickname = account_info["nickname"]
            return account_info
        else:
            return False

    def get_account_info(self):
        url = "https://pan.quark.cn/account/info"
        querystring = {"fr": "pc", "platform": "pc"}
        response = self._send_request("GET", url, params=querystring).json()
        if response.get("data"):
            return response["data"]
        else:
            return False

    def get_growth_info(self):
        url = f"{self.BASE_URL_APP}/1/clouddrive/capacity/growth/info"
        querystring = {
            "pr": "ucpro",
            "fr": "android",
            "kps": self.mparam.get("kps"),
            "sign": self.mparam.get("sign"),
            "vcode": self.mparam.get("vcode"),
        }
        headers = {
            "content-type": "application/json",
        }
        response = self._send_request(
            "GET", url, headers=headers, params=querystring
        ).json()
        if response.get("data"):
            return response["data"]
        else:
            return False

    def get_growth_sign(self):
        url = f"{self.BASE_URL_APP}/1/clouddrive/capacity/growth/sign"
        querystring = {
            "pr": "ucpro",
            "fr": "android",
            "kps": self.mparam.get("kps"),
            "sign": self.mparam.get("sign"),
            "vcode": self.mparam.get("vcode"),
        }
        payload = {
            "sign_cyclic": True,
        }
        headers = {
            "content-type": "application/json",
        }
        response = self._send_request(
            "POST", url, json=payload, headers=headers, params=querystring
        ).json()
        if response.get("data"):
            return True, response["data"]["sign_daily_reward"]
        else:
            return False, response["message"]

    # 可验证资源是否失效
    def get_stoken(self, pwd_id, passcode=""):
        url = f"{self.BASE_URL}/1/clouddrive/share/sharepage/token"
        querystring = {"pr": "ucpro", "fr": "pc"}
        payload = {"pwd_id": pwd_id, "passcode": passcode}
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def get_detail(
        self, pwd_id, stoken, pdir_fid, _fetch_share=0, fetch_share_full_path=0
    ):
        list_merge = []
        page = 1
        while True:
            url = f"{self.BASE_URL}/1/clouddrive/share/sharepage/detail"
            querystring = {
                "pr": "ucpro",
                "fr": "pc",
                "pwd_id": pwd_id,
                "stoken": stoken,
                "pdir_fid": pdir_fid,
                "force": "0",
                "_page": page,
                "_size": "50",
                "_fetch_banner": "0",
                "_fetch_share": _fetch_share,
                "_fetch_total": "1",
                "_sort": "file_type:asc,updated_at:desc",
                "ver": "2",
                "fetch_share_full_path": fetch_share_full_path,
            }
            response = self._send_request("GET", url, params=querystring).json()
            if response["code"] != 0:
                return response
            if response["data"]["list"]:
                list_merge += response["data"]["list"]
                page += 1
            else:
                break
            if len(list_merge) >= response["metadata"]["_total"]:
                break
        response["data"]["list"] = list_merge
        return response

    def get_fids(self, file_paths):
        fids = []
        while True:
            url = f"{self.BASE_URL}/1/clouddrive/file/info/path_list"
            querystring = {"pr": "ucpro", "fr": "pc"}
            payload = {"file_path": file_paths[:50], "namespace": "0"}
            response = self._send_request(
                "POST", url, json=payload, params=querystring
            ).json()
            if response["code"] == 0:
                fids += response["data"]
                file_paths = file_paths[50:]
            else:
                print(f"获取目录ID：失败, {response['message']}")
                break
            if len(file_paths) == 0:
                break
        return fids

    def ls_dir(self, pdir_fid, **kwargs):
        list_merge = []
        page = 1
        while True:
            url = f"{self.BASE_URL}/1/clouddrive/file/sort"
            querystring = {
                "pr": "ucpro",
                "fr": "pc",
                "uc_param_str": "",
                "pdir_fid": pdir_fid,
                "_page": page,
                "_size": "50",
                "_fetch_total": "1",
                "_fetch_sub_dirs": "0",
                "_sort": "file_type:asc,updated_at:desc",
                "_fetch_full_path": kwargs.get("fetch_full_path", 0),
                "fetch_all_file": 1,  # 跟随Web端，作用未知
                "fetch_risk_file_name": 1,  # 如无此参数，违规文件名会被变 ***
            }
            response = self._send_request("GET", url, params=querystring).json()
            if response["code"] != 0:
                return response
            if response["data"]["list"]:
                list_merge += response["data"]["list"]
                page += 1
            else:
                break
            if len(list_merge) >= response["metadata"]["_total"]:
                break
        response["data"]["list"] = list_merge
        return response

    def save_file(self, fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken):
        url = f"{self.BASE_URL}/1/clouddrive/share/sharepage/save"
        querystring = {
            "pr": "ucpro",
            "fr": "pc",
            "uc_param_str": "",
            "app": "clouddrive",
            "__dt": int(random.uniform(1, 5) * 60 * 1000),
            "__t": datetime.now().timestamp(),
        }
        payload = {
            "fid_list": fid_list,
            "fid_token_list": fid_token_list,
            "to_pdir_fid": to_pdir_fid,
            "pwd_id": pwd_id,
            "stoken": stoken,
            "pdir_fid": "0",
            "scene": "link",
        }
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def query_task(self, task_id):
        retry_index = 0
        while True:
            url = f"{self.BASE_URL}/1/clouddrive/task"
            querystring = {
                "pr": "ucpro",
                "fr": "pc",
                "uc_param_str": "",
                "task_id": task_id,
                "retry_index": retry_index,
                "__dt": int(random.uniform(1, 5) * 60 * 1000),
                "__t": datetime.now().timestamp(),
            }
            response = self._send_request("GET", url, params=querystring).json()
            if response["status"] != 200:
                return response
            if response["data"]["status"] == 2:
                if retry_index > 0:
                    print()
                break
            else:
                if retry_index == 0:
                    print(
                        f"正在等待[{response['data']['task_title']}]执行结果",
                        end="",
                        flush=True,
                    )
                else:
                    print(".", end="", flush=True)
                retry_index += 1
                time.sleep(0.500)
        return response

    def download(self, fids):
        url = f"{self.BASE_URL}/1/clouddrive/file/download"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {"fids": fids}
        response = self._send_request("POST", url, json=payload, params=querystring)
        set_cookie = response.cookies.get_dict()
        cookie_str = "; ".join([f"{key}={value}" for key, value in set_cookie.items()])
        return response.json(), cookie_str

    def mkdir(self, dir_path):
        url = f"{self.BASE_URL}/1/clouddrive/file"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {
            "pdir_fid": "0",
            "file_name": "",
            "dir_path": dir_path,
            "dir_init_lock": False,
        }
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def rename(self, fid, file_name):
        url = f"{self.BASE_URL}/1/clouddrive/file/rename"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {"fid": fid, "file_name": file_name}
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def delete(self, filelist):
        url = f"{self.BASE_URL}/1/clouddrive/file/delete"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {"action_type": 2, "filelist": filelist, "exclude_fids": []}
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def recycle_list(self, page=1, size=30):
        url = f"{self.BASE_URL}/1/clouddrive/file/recycle/list"
        querystring = {
            "_page": page,
            "_size": size,
            "pr": "ucpro",
            "fr": "pc",
            "uc_param_str": "",
        }
        response = self._send_request("GET", url, params=querystring).json()
        return response["data"]["list"]

    def recycle_remove(self, record_list):
        url = f"{self.BASE_URL}/1/clouddrive/file/recycle/remove"
        querystring = {"uc_param_str": "", "fr": "pc", "pr": "ucpro"}
        payload = {
            "select_mode": 2,
            "record_list": record_list,
        }
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    # ↑ 请求函数
    # ↓ 操作函数

    def extract_url(self, url):
        # pwd_id
        match_id = re.search(r"/s/(\w+)", url)
        pwd_id = match_id.group(1) if match_id else None
        # passcode
        match_pwd = re.search(r"pwd=(\w+)", url)
        passcode = match_pwd.group(1) if match_pwd else ""
        # path: fid-name
        # Legacy 20250905
        paths = []
        matches = re.findall(r"/(\w{32})-?([^/]+)?", url)
        for match in matches:
            fid = match[0]
            name = urllib.parse.unquote(match[1]).replace("*101", "-")
            paths.append({"fid": fid, "name": name})
        pdir_fid = paths[-1]["fid"] if matches else 0
        return pwd_id, passcode, pdir_fid, paths

    def update_savepath_fid(self, tasklist):
        dir_paths = [
            re.sub(r"/{2,}", "/", f"/{item['savepath']}")
            for item in tasklist
            if not item.get("enddate")
            or (
                datetime.now().date()
                <= datetime.strptime(item["enddate"], "%Y-%m-%d").date()
            )
        ]
        if not dir_paths:
            return False
        dir_paths_exist_arr = self.get_fids(dir_paths)
        dir_paths_exist = [item["file_path"] for item in dir_paths_exist_arr]
        # 比较创建不存在的
        dir_paths_unexist = list(set(dir_paths) - set(dir_paths_exist) - set(["/"]))
        for dir_path in dir_paths_unexist:
            mkdir_return = self.mkdir(dir_path)
            if mkdir_return["code"] == 0:
                new_dir = mkdir_return["data"]
                dir_paths_exist_arr.append(
                    {"file_path": dir_path, "fid": new_dir["fid"]}
                )
                print(f"创建文件夹：{dir_path}")
            else:
                print(f"创建文件夹：{dir_path} 失败, {mkdir_return['message']}")
        # 储存目标目录的fid
        for dir_path in dir_paths_exist_arr:
            self.savepath_fid[dir_path["file_path"]] = dir_path["fid"]
        # print(dir_paths_exist_arr)

    def do_save_check(self, shareurl, savepath):
        try:
            pwd_id, passcode, pdir_fid, _ = self.extract_url(shareurl)
            stoken = self.get_stoken(pwd_id, passcode)["data"]["stoken"]
            share_file_list = self.get_detail(pwd_id, stoken, pdir_fid)["data"]["list"]
            print(f"获取分享: {share_file_list}")
            fid_list = [item["fid"] for item in share_file_list]
            fid_token_list = [item["share_fid_token"] for item in share_file_list]
            get_fids = self.get_fids([savepath])
            to_pdir_fid = (
                get_fids[0]["fid"] if get_fids else self.mkdir(savepath)["data"]["fid"]
            )
            save_file = self.save_file(
                fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken
            )
            print(f"转存文件: {save_file}")
            if save_file["code"] == 0:
                task_id = save_file["data"]["task_id"]
                query_task = self.query_task(task_id)
                print(f"查询转存: {query_task}")
                if query_task["code"] == 0:
                    del_list = query_task["data"]["save_as"]["save_as_top_fids"]
                    if del_list:
                        delete_return = self.delete(del_list)
                        print(f"删除转存: {delete_return}")
                        recycle_list = self.recycle_list()
                        record_id_list = [
                            item["record_id"]
                            for item in recycle_list
                            if item["fid"] in del_list
                        ]
                        recycle_remove = self.recycle_remove(record_id_list)
                        print(f"清理转存: {recycle_remove}")
                        print(f"✅ 转存测试成功")
                        return True
            print(f"❌ 转存测试失败: 中断")
            return False
        except Exception as e:
            print(f"❌ 转存测试失败: {str(e)}")
            traceback.print_exc()

    def do_save_task(self, task):
        # 判断资源失效记录
        if task.get("shareurl_ban"):
            print(f"《{task['taskname']}》：{task['shareurl_ban']}")
            return

        # 链接转换所需参数
        pwd_id, passcode, pdir_fid, _ = self.extract_url(task["shareurl"])

        # 获取stoken，同时可验证资源是否失效
        get_stoken = self.get_stoken(pwd_id, passcode)
        if get_stoken.get("status") == 200:
            stoken = get_stoken["data"]["stoken"]
        elif get_stoken.get("status") == 500:
            print(f"跳过任务：网络异常 {get_stoken.get('message')}")
            return
        else:
            message = get_stoken.get("message")
            add_notify(f"❌《{task['taskname']}》：{message}\n")
            task["shareurl_ban"] = message
            return
        # print("stoken: ", stoken)

        updated_tree = self.dir_check_and_save(task, pwd_id, stoken, pdir_fid)
        if updated_tree.size(1) > 0:
            self.do_rename(updated_tree)
            print()
            add_notify(f"✅《{task['taskname']}》添加追更：\n{updated_tree}")
            return updated_tree
        else:
            print(f"任务结束：没有新的转存任务")
            return False

    def dir_check_and_save(self, task, pwd_id, stoken, pdir_fid="", subdir_path=""):
        tree = Tree()
        # 获取分享文件列表
        share_file_list = self.get_detail(pwd_id, stoken, pdir_fid)["data"]["list"]
        # print("share_file_list: ", share_file_list)

        if not share_file_list:
            if subdir_path == "":
                task["shareurl_ban"] = "分享为空，文件已被分享者删除"
                add_notify(f"❌《{task['taskname']}》：{task['shareurl_ban']}\n")
            return tree
        elif (
            len(share_file_list) == 1
            and share_file_list[0]["dir"]
            and subdir_path == ""
        ):  # 仅有一个文件夹
            print("🧠 该分享是一个文件夹，读取文件夹内列表")
            share_file_list = self.get_detail(
                pwd_id, stoken, share_file_list[0]["fid"]
            )["data"]["list"]

        # 获取目标目录文件列表
        savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}{subdir_path}")
        if not self.savepath_fid.get(savepath):
            if get_fids := self.get_fids([savepath]):
                self.savepath_fid[savepath] = get_fids[0]["fid"]
            else:
                print(f"❌ 目录 {savepath} fid获取失败，跳过转存")
                return tree
        to_pdir_fid = self.savepath_fid[savepath]
        dir_file_list = self.ls_dir(to_pdir_fid)["data"]["list"]
        dir_filename_list = [dir_file["file_name"] for dir_file in dir_file_list]
        # print("dir_file_list: ", dir_file_list)

        tree.create_node(
            savepath,
            pdir_fid,
            data={
                "is_dir": True,
            },
        )

        # 文件命名类
        mr = MagicRename(CONFIG_DATA.get("magic_regex", {}))
        mr.set_taskname(task["taskname"])

        # 魔法正则转换
        pattern, replace = mr.magic_regex_conv(
            task.get("pattern", ""), task.get("replace", "")
        )
        # 需保存的文件清单
        need_save_list = []
        # 添加符合的
        for share_file in share_file_list:
            search_pattern = (
                task["update_subdir"]
                if share_file["dir"] and task.get("update_subdir")
                else pattern
            )
            # 正则文件名匹配
            if re.search(search_pattern, share_file["file_name"]):
                # 判断原文件名是否存在，处理忽略扩展名
                if not mr.is_exists(
                    share_file["file_name"],
                    dir_filename_list,
                    (task.get("ignore_extension") and not share_file["dir"]),
                ):
                    # 文件夹、子目录文件不进行重命名
                    if share_file["dir"] or subdir_path:
                        share_file["file_name_re"] = share_file["file_name"]
                        need_save_list.append(share_file)
                    else:
                        # 替换后的文件名
                        file_name_re = mr.sub(pattern, replace, share_file["file_name"])
                        # 判断替换后的文件名是否存在
                        if not mr.is_exists(
                            file_name_re,
                            dir_filename_list,
                            task.get("ignore_extension"),
                        ):
                            share_file["file_name_re"] = file_name_re
                            need_save_list.append(share_file)
                elif share_file["dir"]:
                    # 存在并是一个目录，历遍子目录
                    if task.get("update_subdir", False) and re.search(
                        task["update_subdir"], share_file["file_name"]
                    ):
                        if task.get("update_subdir_resave_mode", False):
                            # 重存模式：删除该目录下所有文件，重新转存
                            print(f"重存子目录：{savepath}/{share_file['file_name']}")
                            # 删除子目录、回收站中彻底删除
                            subdir = next(
                                (
                                    f
                                    for f in dir_file_list
                                    if f["file_name"] == share_file["file_name"]
                                ),
                                None,
                            )
                            delete_return = self.delete([subdir["fid"]])
                            self.query_task(delete_return["data"]["task_id"])
                            recycle_list = self.recycle_list()
                            record_id_list = [
                                item["record_id"]
                                for item in recycle_list
                                if item["fid"] == subdir["fid"]
                            ]
                            self.recycle_remove(record_id_list)
                            # 作为新文件添加到转存列表
                            share_file["file_name_re"] = share_file["file_name"]
                            need_save_list.append(share_file)
                        else:
                            # 递归模式
                            print(f"检查子目录：{savepath}/{share_file['file_name']}")
                            subdir_tree = self.dir_check_and_save(
                                task,
                                pwd_id,
                                stoken,
                                share_file["fid"],
                                f"{subdir_path}/{share_file['file_name']}",
                            )
                            if subdir_tree.size(1) > 0:
                                # 合并子目录树
                                tree.create_node(
                                    "📁" + share_file["file_name"],
                                    share_file["fid"],
                                    parent=pdir_fid,
                                    data={
                                        "is_dir": share_file["dir"],
                                    },
                                )
                                tree.merge(share_file["fid"], subdir_tree, deep=False)
            # 指定文件开始订阅/到达指定文件（含）结束历遍
            if share_file["fid"] == task.get("startfid", ""):
                break

        if re.search(r"\{I+\}", replace):
            mr.set_dir_file_list(dir_file_list, replace)
            mr.sort_file_list(need_save_list)

        # 转存文件
        fid_list = [item["fid"] for item in need_save_list]
        fid_token_list = [item["share_fid_token"] for item in need_save_list]
        if fid_list:
            err_msg = None
            save_as_top_fids = []
            while fid_list:
                # 分次转存，100个/次，因query_task返回save_as_top_fids最多100
                save_file_return = self.save_file(
                    fid_list[:100], fid_token_list[:100], to_pdir_fid, pwd_id, stoken
                )
                fid_list = fid_list[100:]
                fid_token_list = fid_token_list[100:]
                if save_file_return["code"] == 0:
                    # 转存成功，查询转存结果
                    task_id = save_file_return["data"]["task_id"]
                    query_task_return = self.query_task(task_id)
                    if query_task_return["code"] == 0:
                        save_as_top_fids.extend(
                            query_task_return["data"]["save_as"]["save_as_top_fids"]
                        )
                    else:
                        err_msg = query_task_return["message"]
                else:
                    err_msg = save_file_return["message"]
                if err_msg:
                    add_notify(f"❌《{task['taskname']}》转存失败：{err_msg}\n")
            # 建立目录树
            if len(need_save_list) == len(save_as_top_fids):
                for index, item in enumerate(need_save_list):
                    icon = self._get_file_icon(item)
                    tree.create_node(
                        f"{icon}{item['file_name_re']}",
                        item["fid"],
                        parent=pdir_fid,
                        data={
                            "file_name": item["file_name"],
                            "file_name_re": item["file_name_re"],
                            "fid": f"{save_as_top_fids[index]}",
                            "path": f"{savepath}/{item['file_name_re']}",
                            "is_dir": item["dir"],
                            "obj_category": item.get("obj_category", ""),
                        },
                    )
        return tree

    def do_rename(self, tree, node_id=None):
        if node_id is None:
            node_id = tree.root
        for child in tree.children(node_id):
            file = child.data
            if file.get("is_dir"):
                # self.do_rename(tree, child.identifier)
                pass
            elif file.get("file_name_re") and file["file_name_re"] != file["file_name"]:
                rename_ret = self.rename(file["fid"], file["file_name_re"])
                print(f"重命名：{file['file_name']} → {file['file_name_re']}")
                if rename_ret["code"] != 0:
                    print(f"      ↑ 失败，{rename_ret['message']}")

    def _get_file_icon(self, f):
        if f.get("dir"):
            return "📁"
        ico_maps = {
            "video": "🎞️",
            "image": "🖼️",
            "audio": "🎵",
            "doc": "📄",
            "archive": "📦",
            "default": "",
        }
        return ico_maps.get(f.get("obj_category"), "")


def verify_account(account):
    # 验证账号
    print(f"▶️ 验证第{account.index}个账号")
    if "__uid" not in account.cookie:
        print(f"💡 不存在cookie必要参数，判断为仅签到")
        return False
    else:
        account_info = account.init()
        if not account_info:
            add_notify(f"👤 第{account.index}个账号登录失败，cookie无效❌")
            return False
        else:
            print(f"👤 账号昵称: {account_info['nickname']}✅")
            return True


def format_bytes(size_bytes: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = 0
    while size_bytes >= 1024 and i < len(units) - 1:
        size_bytes /= 1024
        i += 1
    return f"{size_bytes:.2f} {units[i]}"


def do_sign(account):
    if not account.mparam:
        print("⏭️ 移动端参数未设置，跳过签到")
        print()
        return
    # 每日领空间
    growth_info = account.get_growth_info()
    if growth_info:
        VIP_MAP = {
            "NORMAL": "普通用户",
            "EXP_SVIP": "88VIP",
            "SUPER_VIP": "SVIP",
            "Z_VIP": "SVIP+",
        }
        growth_message = f"💾 {VIP_MAP.get(growth_info['member_type'], growth_info['member_type'])} 总空间：{format_bytes(growth_info['total_capacity'])}，签到累计获得：{format_bytes(growth_info['cap_composition'].get('sign_reward', 0))}"
        if growth_info["cap_sign"]["sign_daily"]:
            sign_message = f"📅 签到记录: 今日已签到+{int(growth_info['cap_sign']['sign_daily_reward']/1024/1024)}MB，连签进度({growth_info['cap_sign']['sign_progress']}/{growth_info['cap_sign']['sign_target']})✅"
            message = f"{sign_message}\n{growth_message}"
            print(message)
        else:
            sign, sign_return = account.get_growth_sign()
            if sign:
                sign_message = f"📅 执行签到: 今日签到+{int(sign_return/1024/1024)}MB，连签进度({growth_info['cap_sign']['sign_progress']+1}/{growth_info['cap_sign']['sign_target']})✅"
                message = f"{sign_message}\n{growth_message}"
                if (
                    str(
                        CONFIG_DATA.get("push_config", {}).get("QUARK_SIGN_NOTIFY")
                    ).lower()
                    == "false"
                    or os.environ.get("QUARK_SIGN_NOTIFY") == "false"
                ):
                    print(message)
                else:
                    message = message.replace("今日", f"[{account.nickname}]今日")
                    add_notify(message)
            else:
                print(f"📅 签到异常: {sign_return}")
    else:
        print("⏭️ 签到进度读取异常，可能登录失效，跳过签到")
    print()


def do_save(account, tasklist=[]):
    print(f"🧩 载入插件")
    plugins, CONFIG_DATA["plugins"], task_plugins_config = Config.load_plugins(
        CONFIG_DATA.get("plugins", {})
    )
    print()
    print(f"转存账号: {account.nickname}")
    # 获取全部保存目录fid
    account.update_savepath_fid(tasklist)

    def is_time(task):
        return (
            not task.get("enddate")
            or (
                datetime.now().date()
                <= datetime.strptime(task["enddate"], "%Y-%m-%d").date()
            )
        ) and (
            "runweek" not in task
            # 星期一为0，星期日为6
            or (datetime.today().weekday() + 1 in task.get("runweek"))
        )

    for plugin_name, plugin in plugins.items():
        if plugin.is_active and hasattr(plugin, "task_before"):
            tasklist = (
                plugin.task_before(tasklist=tasklist, account=account) or tasklist
            )

    # 执行任务
    for index, task in enumerate(tasklist):
        print()
        print(f"#{index+1}------------------")
        print(f"任务名称: {task['taskname']}")
        print(f"分享链接: {task['shareurl']}")
        print(f"保存路径: {task['savepath']}")
        if task.get("pattern"):
            print(f"正则匹配: {task['pattern']}")
        if task.get("replace"):
            print(f"正则替换: {task['replace']}")
        if task.get("update_subdir"):
            print(f"更子目录: {task['update_subdir']}")
        if task.get("runweek") or task.get("enddate"):
            print(
                f"运行周期: WK{task.get('runweek',[])} ~ {task.get('enddate','forever')}"
            )
        print()
        # 判断任务周期
        if not is_time(task):
            print(f"任务不在运行周期内，跳过")
        else:
            is_new_tree = account.do_save_task(task)

            # 补充任务的插件配置
            def merge_dicts(a, b):
                result = a.copy()
                for key, value in b.items():
                    if (
                        key in result
                        and isinstance(result[key], dict)
                        and isinstance(value, dict)
                    ):
                        result[key] = merge_dicts(result[key], value)
                    elif key not in result:
                        result[key] = value
                return result

            task["addition"] = merge_dicts(
                task.get("addition", {}), task_plugins_config
            )
            # 调用插件
            if is_new_tree:
                print(f"🧩 调用插件")
                for plugin_name, plugin in plugins.items():
                    if plugin.is_active and hasattr(plugin, "run"):
                        task = (
                            plugin.run(task, account=account, tree=is_new_tree) or task
                        )
    print()
    print(f"===============插件收尾===============")
    for plugin_name, plugin in plugins.items():
        if plugin.is_active and hasattr(plugin, "task_after"):
            data = plugin.task_after(tasklist=tasklist, account=account)
            if data.get("tasklist"):
                CONFIG_DATA["tasklist"] = data["tasklist"]
            if data.get("config"):
                CONFIG_DATA["plugins"][plugin_name] = data["config"]
    print()


def main():
    global CONFIG_DATA
    start_time = datetime.now()
    print(f"===============程序开始===============")
    print(f"⏰ 执行时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    # 读取启动参数
    config_path = sys.argv[1] if len(sys.argv) > 1 else "quark_config.json"
    # 推送测试
    if os.environ.get("QUARK_TEST", "").lower() == "true":
        print(f"===============通知测试===============")
        CONFIG_DATA["push_config"] = json.loads(os.environ.get("PUSH_CONFIG"))
        send_ql_notify(
            "【夸克自动转存】",
            f"通知测试\n\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        )
        print()
        if cookies := json.loads(os.environ.get("COOKIE", "[]")):
            print(f"===============转存测试===============")
            accounts = Quark(cookies[0])
            accounts.do_save_check("https://pan.quark.cn/s/1ed94d530d63", "/来自：分享")
            print()
        return
    # 从环境变量中获取 TASKLIST
    tasklist_from_env = []
    if tasklist_json := os.environ.get("TASKLIST"):
        try:
            tasklist_from_env = json.loads(tasklist_json)
        except Exception as e:
            print(f"从环境变量解析任务列表失败 {e}")
    # 检查本地文件是否存在，如果不存在就下载
    if not os.path.exists(config_path):
        if os.environ.get("QUARK_COOKIE"):
            print(
                f"⚙️ 读取到 QUARK_COOKIE 环境变量，仅签到领空间。如需执行转存，请删除该环境变量后配置 {config_path} 文件"
            )
            cookie_val = os.environ.get("QUARK_COOKIE")
            cookie_form_file = False
        else:
            print(f"⚙️ 配置文件 {config_path} 不存在❌，正远程从下载配置模版")
            config_url = f"{GH_PROXY}https://raw.githubusercontent.com/Cp0204/quark_auto_save/main/quark_config.json"
            if Config.download_file(config_url, config_path):
                print("⚙️ 配置模版下载成功✅，请到程序目录中手动配置")
            return
    else:
        print(f"⚙️ 正从 {config_path} 文件中读取配置")
        CONFIG_DATA = Config.read_json(config_path)
        Config.breaking_change_update(CONFIG_DATA)
        cookie_val = CONFIG_DATA.get("cookie")
        cookie_form_file = True
    # 获取cookie
    cookies = Config.get_cookies(cookie_val)
    if not cookies:
        print("❌ cookie 未配置")
        return
    accounts = [Quark(cookie, index) for index, cookie in enumerate(cookies)]
    # 签到
    print(f"===============签到任务===============")
    if tasklist_from_env:
        verify_account(accounts[0])
    else:
        for account in accounts:
            verify_account(account)
            do_sign(account)
    print()
    # 转存
    if accounts[0].is_active and cookie_form_file:
        print(f"===============转存任务===============")
        # 任务列表
        if tasklist_from_env:
            do_save(accounts[0], tasklist_from_env)
        else:
            do_save(accounts[0], CONFIG_DATA.get("tasklist", []))
        print()
    # 通知
    if NOTIFYS:
        notify_body = "\n".join(NOTIFYS)
        print(f"===============推送通知===============")
        send_ql_notify("【夸克自动转存】", notify_body)
        print()
    if cookie_form_file:
        # 更新配置
        Config.write_json(config_path, CONFIG_DATA)

    print(f"===============程序结束===============")
    duration = datetime.now() - start_time
    print(f"😃 运行时长: {round(duration.total_seconds(), 2)}s")
    print()


if __name__ == "__main__":
    main()
