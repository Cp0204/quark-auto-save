# !/usr/bin/env python3
# -*- coding: utf-8 -*-
# Modify: 2024-11-13
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
import requests
import importlib
import urllib.parse
from datetime import datetime

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


MAGIC_REGEX = {
    "$TV": {
        "pattern": r".*?([Ss]\d{1,2})?(?:[第EePpXx\.\-\_\( ]{1,2}|^)(\d{1,3})(?!\d).*?\.(mp4|mkv)",
        "replace": r"\1E\2.\3",
    },
    "$BLACK_WORD": {
        "pattern": r"^(?!.*纯享)(?!.*加更)(?!.*超前企划)(?!.*训练室)(?!.*蒸蒸日上).*",
        "replace": "",
    },
}


# 发送通知消息
def send_ql_notify(title, body):
    try:
        # 导入通知模块
        import notify

        # 如未配置 push_config 则使用青龙环境通知设置
        if CONFIG_DATA.get("push_config"):
            notify.push_config = CONFIG_DATA["push_config"].copy()
            notify.push_config["CONSOLE"] = notify.push_config.get("CONSOLE", True)
        notify.send(title, body)
    except Exception as e:
        if e:
            print("发送通知消息失败！")


# 添加消息
def add_notify(text):
    global NOTIFYS
    NOTIFYS.append(text)
    print(text)
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
        PLUGIN_FLAGS = os.environ.get("PLUGIN_FLAGS", "").split(",")
        plugins_available = {}
        task_plugins_config = {}
        all_modules = [
            f.replace(".py", "") for f in os.listdir(plugins_dir) if f.endswith(".py")
        ]
        # 调整模块优先级
        priority_path = os.path.join(plugins_dir, "_priority.json")
        try:
            with open(priority_path, encoding="utf-8") as f:
                priority_modules = json.load(f)
            if priority_modules:
                all_modules = [
                    module for module in priority_modules if module in all_modules
                ] + [module for module in all_modules if module not in priority_modules]
        except (FileNotFoundError, json.JSONDecodeError):
            priority_modules = []
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
        print()
        return plugins_available, plugins_config, task_plugins_config

    def breaking_change_update(config_data):
        if config_data.get("emby"):
            print("🔼 Update config v0.3.6.1 to 0.3.7")
            config_data.setdefault("media_servers", {})["emby"] = {
                "url": config_data["emby"]["url"],
                "token": config_data["emby"]["apikey"],
            }
            del config_data["emby"]
            for task in config_data.get("tasklist", {}):
                task["media_id"] = task.get("emby_id", "")
                if task.get("emby_id"):
                    del task["emby_id"]
        if config_data.get("media_servers"):
            print("🔼 Update config v0.3.8 to 0.3.9")
            config_data["plugins"] = config_data.get("media_servers")
            del config_data["media_servers"]
            for task in config_data.get("tasklist", {}):
                task["addition"] = {
                    "emby": {
                        "media_id": task.get("media_id", ""),
                    }
                }
                if task.get("media_id"):
                    del task["media_id"]
                    
        # 添加剧集识别模式配置
        if not config_data.get("episode_patterns"):
            print("🔼 添加剧集识别模式配置")
            config_data["episode_patterns"] = [
                {"description": "[]", "regex": "(\\d+)"},
                {"description": "[]-4K", "regex": "(\\d+)[-_\\s]*4[Kk]"},
                {"description": "[]话", "regex": "(\\d+)话"},
                {"description": "E[]", "regex": "[Ee](\\d+)"},
                {"description": "EP[]", "regex": "[Ee][Pp](\\d+)"},
                {"description": "第[]话", "regex": "第(\\d+)话"},
                {"description": "第[]集", "regex": "第(\\d+)集"},
                {"description": "第[]期", "regex": "第(\\d+)期"},
                {"description": "[] 4K", "regex": "(\\d+)\\s+4[Kk]"},
                {"description": "[]_4K", "regex": "(\\d+)[_\\s]4[Kk]"},
                {"description": "【[]】", "regex": "【(\\d+)】"},
                {"description": "[[]", "regex": "\\[(\\d+)\\]"},
                {"description": "_[]_", "regex": "_?(\\d+)_"}
            ]


class Quark:
    BASE_URL = "https://drive-pc.quark.cn"
    BASE_URL_APP = "https://drive-m.quark.cn"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) quark-cloud-drive/3.14.2 Chrome/112.0.5615.165 Electron/24.1.3.8 Safari/537.36 Channel/pckk_other_ch"

    def __init__(self, cookie, index=None):
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
            fake_response._content = b'{"status": 500, "message": "request error"}'
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
        if response.get("status") == 200:
            return True, response["data"]["stoken"]
        else:
            return False, response["message"]

    def get_detail(self, pwd_id, stoken, pdir_fid, _fetch_share=0):
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
            }
            response = self._send_request("GET", url, params=querystring).json()
            if response["code"] != 0:
                return {"error": response["message"]}
            if response["data"]["list"]:
                list_merge += response["data"]["list"]
                page += 1
            else:
                break
            if len(list_merge) >= response["metadata"]["_total"]:
                break
        response["data"]["list"] = list_merge
        return response["data"]

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
        file_list = []
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
            }
            response = self._send_request("GET", url, params=querystring).json()
            if response["code"] != 0:
                return {"error": response["message"]}
            if response["data"]["list"]:
                file_list += response["data"]["list"]
                page += 1
            else:
                break
            if len(file_list) >= response["metadata"]["_total"]:
                break
        return file_list

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
            if response["data"]["status"] != 0:
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

    # 魔法正则匹配
    def magic_regex_func(self, pattern, replace, taskname=None, magic_regex={}):
        magic_regex = magic_regex or CONFIG_DATA.get("magic_regex") or MAGIC_REGEX
        keyword = pattern
        if keyword in magic_regex:
            pattern = magic_regex[keyword]["pattern"]
            if replace == "":
                replace = magic_regex[keyword]["replace"]
        if taskname:
            replace = replace.replace("$TASKNAME", taskname)
        return pattern, replace

    # def get_id_from_url(self, url):
    #     url = url.replace("https://pan.quark.cn/s/", "")
    #     pattern = r"(\w+)(\?pwd=(\w+))?(#/list/share.*/(\w+))?"
    #     match = re.search(pattern, url)
    #     if match:
    #         pwd_id = match.group(1)
    #         passcode = match.group(3) if match.group(3) else ""
    #         pdir_fid = match.group(5) if match.group(5) else 0
    #         return pwd_id, passcode, pdir_fid
    #     else:
    #         return None

    def extract_url(self, url):
        # pwd_id
        match_id = re.search(r"/s/(\w+)", url)
        pwd_id = match_id.group(1) if match_id else None
        # passcode
        match_pwd = re.search(r"pwd=(\w+)", url)
        passcode = match_pwd.group(1) if match_pwd else ""
        # path: fid-name
        paths = []
        matches = re.findall(r"/(\w{32})-?([^/]+)?", url)
        for match in matches:
            fid = match[0]
            name = urllib.parse.unquote(match[1])
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
            _, stoken = self.get_stoken(pwd_id, passcode)
            share_file_list = self.get_detail(pwd_id, stoken, pdir_fid)["list"]
            fid_list = [item["fid"] for item in share_file_list]
            fid_token_list = [item["share_fid_token"] for item in share_file_list]
            file_name_list = [item["file_name"] for item in share_file_list]
            if not fid_list:
                return
            get_fids = self.get_fids([savepath])
            to_pdir_fid = (
                get_fids[0]["fid"] if get_fids else self.mkdir(savepath)["data"]["fid"]
            )
            save_file = self.save_file(
                fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken
            )
            if save_file["code"] == 41017:
                return
            elif save_file["code"] == 0:
                dir_file_list = self.ls_dir(to_pdir_fid)
                del_list = [
                    item["fid"]
                    for item in dir_file_list
                    if (item["file_name"] in file_name_list)
                    and ((datetime.now().timestamp() - item["created_at"]) < 60)
                ]
                if del_list:
                    self.delete(del_list)
                    recycle_list = self.recycle_list()
                    record_id_list = [
                        item["record_id"]
                        for item in recycle_list
                        if item["fid"] in del_list
                    ]
                    self.recycle_remove(record_id_list)
                return save_file
            else:
                return False
        except Exception as e:
            print(f"转存测试失败: {str(e)}")

    def do_save_task(self, task):
        # 判断资源失效记录
        if task.get("shareurl_ban"):
            print(f"分享资源已失效：{task['shareurl_ban']}")
            add_notify(f"❗《{task['taskname']}》分享资源已失效：{task['shareurl_ban']}\n")
            return
        # 提取链接参数
        pwd_id, passcode, pdir_fid, paths = self.extract_url(task["shareurl"])
        if not pwd_id:
            task["shareurl_ban"] = f"提取链接参数失败，请检查分享链接是否有效"
            print(f"提取链接参数失败，请检查分享链接是否有效")
            return
        # 获取分享详情
        is_sharing, stoken = self.get_stoken(pwd_id, passcode)
        if not is_sharing:
            task["shareurl_ban"] = stoken
            print(f"分享详情获取失败：{stoken}")
            add_notify(f"❗《{task['taskname']}》分享详情获取失败：{stoken}\n")
            return
        share_detail = self.get_detail(pwd_id, stoken, pdir_fid, _fetch_share=1)
        # 获取保存路径fid
        savepath = task["savepath"]
        if not self.savepath_fid.get(savepath):
            # 检查规范化路径是否已在字典中
            norm_savepath = re.sub(r"/{2,}", "/", f"/{savepath}")
            if norm_savepath != savepath and self.savepath_fid.get(norm_savepath):
                self.savepath_fid[savepath] = self.savepath_fid[norm_savepath]
            else:
                savepath_fids = self.get_fids([savepath])
                if not savepath_fids:
                    print(f"保存路径不存在，准备新建：{savepath}")
                    mkdir_result = self.mkdir(savepath)
                    if mkdir_result["code"] == 0:
                        self.savepath_fid[savepath] = mkdir_result["data"]["fid"]
                        print(f"保存路径新建成功：{savepath}")
                    else:
                        print(f"保存路径新建失败：{mkdir_result['message']}")
                        return
                else:
                    # 路径已存在，直接设置fid
                    self.savepath_fid[savepath] = savepath_fids[0]["fid"]

        # 支持顺序命名模式
        if task.get("use_sequence_naming") and task.get("sequence_naming"):
            # 顺序命名模式下已经在do_save中打印了顺序命名信息，这里不再重复打印
            # 设置正则模式为空
            task["regex_pattern"] = None
            # 构建顺序命名的正则表达式
            sequence_pattern = task["sequence_naming"]
            # 将{}替换为(\d+)用于匹配
            regex_pattern = re.escape(sequence_pattern).replace('\\{\\}', '(\\d+)')
            task["regex_pattern"] = regex_pattern
        # 支持剧集命名模式
        elif task.get("use_episode_naming") and task.get("episode_naming"):
            # 剧集命名模式下已经在do_save中打印了剧集命名信息，这里不再重复打印
            # 设置正则模式为空
            task["regex_pattern"] = None
            # 构建剧集命名的正则表达式
            episode_pattern = task["episode_naming"]
            # 先检查是否包含合法的[]字符
            if "[]" in episode_pattern:
                # 将[] 替换为 (\d+)
                # 先将模式字符串进行转义，确保其他特殊字符不会干扰
                escaped_pattern = re.escape(episode_pattern)
                # 然后将转义后的 \[ \] 替换为捕获组 (\d+)
                regex_pattern = escaped_pattern.replace('\\[\\]', '(\\d+)')
            else:
                # 如果输入模式不包含[]，则使用简单匹配模式，避免正则表达式错误
                print(f"⚠️ 剧集命名模式中没有找到 [] 占位符，将使用简单匹配")
                regex_pattern = "^" + re.escape(episode_pattern) + "(\\d+)$"
            
            task["regex_pattern"] = regex_pattern
        else:
            # 正则命名模式
            pattern, replace = self.magic_regex_func(
                task.get("pattern", ""), task.get("replace", ""), task["taskname"]
            )
            # 注释掉这里的正则表达式打印，因为在do_save函数中已经打印了
            # 只有在非魔法变量情况下才显示展开后的正则表达式
            # 对于魔法变量($TV等)，显示原始输入
            # if pattern and task.get("pattern") and task.get("pattern") not in CONFIG_DATA.get("magic_regex", MAGIC_REGEX):
            #     print(f"正则匹配: {pattern}")
            #     print(f"正则替换: {replace}")
            
        # 保存文件
        tree = self.dir_check_and_save(task, pwd_id, stoken, pdir_fid)
        
        # 检查是否有新文件转存
        if tree and tree.size() <= 1:  # 只有根节点意味着没有新文件
            return False
            
        return tree

    def dir_check_and_save(self, task, pwd_id, stoken, pdir_fid="", subdir_path=""):
        tree = Tree()
        # 获取分享文件列表
        share_file_list = self.get_detail(pwd_id, stoken, pdir_fid)["list"]
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
            )["list"]
            
        # 应用过滤词过滤
        if task.get("filterwords"):
            # 同时支持中英文逗号分隔
            filterwords = task["filterwords"].replace("，", ",")
            filterwords_list = [word.strip() for word in filterwords.split(',')]
            share_file_list = [file for file in share_file_list if not any(word in file['file_name'] for word in filterwords_list)]
            print(f"📑 应用过滤词: {task['filterwords']}，剩余{len(share_file_list)}个文件")
            print()

        # 获取目标目录文件列表
        savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}{subdir_path}")
        if not self.savepath_fid.get(savepath):
            # 检查规范化路径是否已在字典中
            norm_savepath = re.sub(r"/{2,}", "/", f"/{savepath}")
            if norm_savepath != savepath and self.savepath_fid.get(norm_savepath):
                self.savepath_fid[savepath] = self.savepath_fid[norm_savepath]
            else:
                savepath_fids = self.get_fids([savepath])
                if not savepath_fids:
                    print(f"保存路径不存在，准备新建：{savepath}")
                    mkdir_result = self.mkdir(savepath)
                    if mkdir_result["code"] == 0:
                        self.savepath_fid[savepath] = mkdir_result["data"]["fid"]
                        print(f"保存路径新建成功：{savepath}")
                    else:
                        print(f"保存路径新建失败：{mkdir_result['message']}")
                        return
                else:
                    # 路径已存在，直接设置fid
                    self.savepath_fid[savepath] = savepath_fids[0]["fid"]
        to_pdir_fid = self.savepath_fid[savepath]
        dir_file_list = self.ls_dir(to_pdir_fid)
        
        tree.create_node(
            savepath,
            pdir_fid,
            data={
                "is_dir": True,
            },
        )

        # 处理顺序命名模式
        if task.get("use_sequence_naming") and task.get("sequence_naming"):
            # 顺序命名模式
            current_sequence = 1
            sequence_pattern = task["sequence_naming"]
            regex_pattern = task.get("regex_pattern")
            
            # 查找目录中现有的最大序号
            for dir_file in dir_file_list:
                if not dir_file["dir"]:  # 只检查文件
                    if matches := re.match(regex_pattern, dir_file["file_name"]):
                        try:
                            seq_num = int(matches.group(1))
                            current_sequence = max(current_sequence, seq_num + 1)
                        except (ValueError, IndexError):
                            pass
            
            # 构建目标目录中所有文件的查重索引（按大小和修改时间）
            dir_files_map = {}
            for dir_file in dir_file_list:
                if not dir_file["dir"]:  # 仅处理文件
                    file_size = dir_file.get("size", 0)
                    file_ext = os.path.splitext(dir_file["file_name"])[1].lower()
                    update_time = dir_file.get("updated_at", 0)
                    
                    # 创建大小+扩展名的索引，用于快速查重
                    key = f"{file_size}_{file_ext}"
                    if key not in dir_files_map:
                        dir_files_map[key] = []
                    dir_files_map[key].append({
                        "file_name": dir_file["file_name"],
                        "updated_at": update_time,
                    })
            
            # 预先过滤掉已经存在的文件（按大小和扩展名比对）
            filtered_share_files = []
            for share_file in share_file_list:
                if share_file["dir"]:
                    # 处理子目录
                    if task.get("update_subdir") and re.search(task["update_subdir"], share_file["file_name"]):
                        filtered_share_files.append(share_file)
                    continue
                    
                file_size = share_file.get("size", 0)
                file_ext = os.path.splitext(share_file["file_name"])[1].lower()
                share_update_time = share_file.get("last_update_at", 0)
                
                # 检查是否已存在相同大小和扩展名的文件
                key = f"{file_size}_{file_ext}"
                is_duplicate = False
                
                if key in dir_files_map:
                    for existing_file in dir_files_map[key]:
                        existing_update_time = existing_file.get("updated_at", 0)
                        
                        # 如果修改时间相近（30天内）或者差距不大（10%以内），认为是同一个文件
                        if (abs(share_update_time - existing_update_time) < 2592000 or 
                            abs(1 - (share_update_time / existing_update_time if existing_update_time else 1)) < 0.1):
                            is_duplicate = True
                            break
                
                # 只有非重复文件才进行处理
                if not is_duplicate:
                    filtered_share_files.append(share_file)
                    
                # 指定文件开始订阅/到达指定文件（含）结束历遍
                if share_file["fid"] == task.get("startfid", ""):
                    break
            
            # 实现高级排序算法
            def extract_sorting_value(file):
                if file["dir"]:  # 跳过文件夹
                    return float('inf')
                
                filename = file["file_name"]
                
                # 提取文件名，不含扩展名
                file_name_without_ext = os.path.splitext(filename)[0]
                
                # 1. "第X期/集/话" 格式
                match_chinese = re.search(r'第(\d+)[期集话]', filename)
                episode_num = int(match_chinese.group(1)) if match_chinese else 0
                
                # 5. 文件名含"上中下"（优先处理，因为可能与其他格式同时存在）
                if match_chinese:
                    # 如果同时存在集数和上中下，则按照集数*10+位置排序
                    if '上' in filename:
                        return episode_num * 10 + 1
                    elif '中' in filename:
                        return episode_num * 10 + 2
                    elif '下' in filename:
                        return episode_num * 10 + 3
                elif '上' in filename:
                    return 1
                elif '中' in filename:
                    return 2
                elif '下' in filename:
                    return 3
                
                # 如果已经匹配到"第X期/集/话"格式，直接返回
                if episode_num > 0:
                    return episode_num * 10
                
                # 2.1 S01E01 格式，提取季数和集数
                match_s_e = re.search(r'[Ss](\d+)[Ee](\d+)', filename)
                if match_s_e:
                    season = int(match_s_e.group(1))
                    episode = int(match_s_e.group(2))
                    return season * 1000 + episode
                
                # 2.2 E01 格式，仅提取集数
                match_e = re.search(r'[Ee][Pp]?(\d+)', filename)
                if match_e:
                    return int(match_e.group(1))
                
                # 2.3 1x01 格式，提取季数和集数
                match_x = re.search(r'(\d+)[Xx](\d+)', filename)
                if match_x:
                    season = int(match_x.group(1))
                    episode = int(match_x.group(2))
                    return season * 1000 + episode
                
                # 3. 日期格式识别（支持多种格式）
                
                # 3.1 完整的YYYYMMDD格式
                match_date_compact = re.search(r'(20\d{2})(\d{2})(\d{2})', filename)
                if match_date_compact:
                    year = int(match_date_compact.group(1))
                    month = int(match_date_compact.group(2))
                    day = int(match_date_compact.group(3))
                    return year * 10000 + month * 100 + day
                
                # 3.2 YYYY-MM-DD 或 YYYY.MM.DD 或 YYYY/MM/DD 格式
                match_date_full = re.search(r'(20\d{2})[-./](\d{1,2})[-./](\d{1,2})', filename)
                if match_date_full:
                    year = int(match_date_full.group(1))
                    month = int(match_date_full.group(2))
                    day = int(match_date_full.group(3))
                    return year * 10000 + month * 100 + day
                
                # 3.3 MM/DD/YYYY 或 DD/MM/YYYY 格式
                match_date_alt = re.search(r'(\d{1,2})[-./](\d{1,2})[-./](20\d{2})', filename)
                if match_date_alt:
                    # 假设第一个是月，第二个是日（美式日期）
                    # 在实际应用中可能需要根据具体情况调整
                    month = int(match_date_alt.group(1))
                    day = int(match_date_alt.group(2))
                    year = int(match_date_alt.group(3))
                    # 检查月份值，如果大于12可能是欧式日期格式（DD/MM/YYYY）
                    if month > 12:
                        month, day = day, month
                    return year * 10000 + month * 100 + day
                
                # 3.4 MM/DD 格式（无年份），假设为当前年
                match_date_short = re.search(r'(\d{1,2})[-./](\d{1,2})', filename)
                if match_date_short:
                    # 假设第一个是月，第二个是日
                    month = int(match_date_short.group(1))
                    day = int(match_date_short.group(2))
                    # 检查月份值，如果大于12可能是欧式日期格式（DD/MM）
                    if month > 12:
                        month, day = day, month
                    # 由于没有年份，使用一个较低的基数，确保任何有年份的日期都排在后面
                    return month * 100 + day
                
                # 3.5 年期格式，如"2025年14期"
                match_year_issue = re.search(r'(20\d{2})[年].*?(\d+)[期]', filename)
                if match_year_issue:
                    year = int(match_year_issue.group(1))
                    issue = int(match_year_issue.group(2))
                    return year * 1000 + issue
                
                # 4. 纯数字格式（文件名开头是纯数字）
                match_num = re.match(r'^(\d+)', file_name_without_ext)
                if match_num:
                    return int(match_num.group(1))
                
                # 6. 默认使用更新时间
                try:
                    return file.get("updated_at", file.get("last_update_at", 0))
                except:
                    return 0
            
            # 过滤出非目录文件，排除已经排除掉的重复文件，然后排序
            files_to_process = [
                f for f in filtered_share_files 
                if not f["dir"] and not re.match(regex_pattern, f["file_name"])
            ]
            
            # 根据提取的排序值进行排序
            sorted_files = sorted(files_to_process, key=extract_sorting_value)
            
            # 需保存的文件清单
            need_save_list = []
            
            # 为每个文件分配序号
            for share_file in sorted_files:
                # 获取文件扩展名
                file_ext = os.path.splitext(share_file["file_name"])[1]
                # 生成新文件名
                save_name = sequence_pattern.replace("{}", f"{current_sequence:02d}") + file_ext
                
                # 检查目标目录是否已存在此文件
                file_exists = any(
                    dir_file["file_name"] == save_name for dir_file in dir_file_list
                )
                
                if not file_exists:
                    # 不打印保存信息
                    share_file["save_name"] = save_name
                    share_file["original_name"] = share_file["file_name"]  # 保存原文件名，用于排序
                    need_save_list.append(share_file)
                    current_sequence += 1
                else:
                    # print(f"跳过已存在的文件: {save_name}")
                    pass
                
                # 指定文件开始订阅/到达指定文件（含）结束历遍
                if share_file["fid"] == task.get("startfid", ""):
                    break
                    
            # 处理子文件夹
            for share_file in share_file_list:
                if share_file["dir"] and task.get("update_subdir", False):
                    if re.search(task["update_subdir"], share_file["file_name"]):
                        print(f"检查子文件夹: {savepath}/{share_file['file_name']}")
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
                
        else:
            # 正则命名模式
            need_save_list = []
            # 添加符合的
            for share_file in share_file_list:
                if share_file["dir"] and task.get("update_subdir", False):
                    pattern, replace = task["update_subdir"], ""
                else:
                    # 检查是否是剧集命名模式
                    if task.get("use_episode_naming") and task.get("regex_pattern"):
                        # 使用预先准备好的正则表达式
                        pattern = task["regex_pattern"]
                        replace = ""
                    else:
                        # 普通正则命名模式
                        pattern, replace = self.magic_regex_func(
                            task.get("pattern", ""), task.get("replace", ""), task["taskname"]
                        )
                
                # 确保pattern不为空，避免正则表达式错误
                if not pattern:
                    pattern = ".*"
                    
                # 正则文件名匹配
                try:
                    if re.search(pattern, share_file["file_name"]):
                        # 替换后的文件名
                        save_name = (
                            re.sub(pattern, replace, share_file["file_name"])
                            if replace != ""
                            else share_file["file_name"]
                        )
                        # 忽略后缀
                        if task.get("ignore_extension") and not share_file["dir"]:
                            compare_func = lambda a, b1, b2: (
                                os.path.splitext(a)[0] == os.path.splitext(b1)[0]
                                or os.path.splitext(a)[0] == os.path.splitext(b2)[0]
                            )
                        else:
                            compare_func = lambda a, b1, b2: (a == b1 or a == b2)
                        # 判断目标目录文件是否存在
                        file_exists = False
                        for dir_file in dir_file_list:
                            if compare_func(
                                dir_file["file_name"], share_file["file_name"], save_name
                            ):
                                file_exists = True
                                # print(f"跳过已存在的文件: {dir_file['file_name']}")
                                # 删除对文件打印部分
                                break
                                
                        if not file_exists:
                            # 不打印保存信息
                            share_file["save_name"] = save_name
                            share_file["original_name"] = share_file["file_name"]  # 保存原文件名，用于排序
                            need_save_list.append(share_file)
                        elif share_file["dir"]:
                            # 存在并是一个文件夹
                            if task.get("update_subdir", False):
                                if re.search(task["update_subdir"], share_file["file_name"]):
                                    print(f"检查子文件夹: {savepath}/{share_file['file_name']}")
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
                except Exception as e:
                    print(f"⚠️ 正则表达式错误: {str(e)}, pattern: {pattern}")
                    # 使用安全的默认值
                    share_file["save_name"] = share_file["file_name"]
                    share_file["original_name"] = share_file["file_name"]
                    need_save_list.append(share_file)
                # 指定文件开始订阅/到达指定文件（含）结束历遍
                if share_file["fid"] == task.get("startfid", ""):
                    break

        fid_list = [item["fid"] for item in need_save_list]
        fid_token_list = [item["share_fid_token"] for item in need_save_list]
        if fid_list:
            # 只在有新文件需要转存时才打印目录文件列表
            # 移除打印目标目录信息和文件列表的代码
            # print(f"📂 目标目录：{savepath} ({len(dir_file_list)}个文件)")
            # for file in dir_file_list:
            #     print(f"    {file['file_name']}")
            # print()
            
            save_file_return = self.save_file(
                fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken
            )
            err_msg = None
            if save_file_return["code"] == 0:
                task_id = save_file_return["data"]["task_id"]
                query_task_return = self.query_task(task_id)
                if query_task_return["code"] == 0:
                    # 建立目录树
                    saved_files = []
                    for index, item in enumerate(need_save_list):
                        icon = (
                            "📁"
                            if item["dir"] == True
                            else "🎞️" if item["obj_category"] == "video" else ""
                        )
                        saved_files.append(f"{icon}{item['save_name']}")
                        tree.create_node(
                            f"{icon}{item['save_name']}",
                            item["fid"],
                            parent=pdir_fid,
                            data={
                                "fid": f"{query_task_return['data']['save_as']['save_as_top_fids'][index]}",
                                "path": f"{savepath}/{item['save_name']}",
                                "is_dir": item["dir"],
                            },
                        )
                    
                    # 添加成功通知
                    add_notify(f"✅《{task['taskname']}》 添加追更:\n/{task['savepath']}{subdir_path}")
                    # 打印保存文件列表
                    for idx, file_name in enumerate(saved_files):
                        prefix = "├── " if idx < len(saved_files) - 1 else "└── "
                        add_notify(f"{prefix}{file_name}")
                    add_notify("")
                else:
                    err_msg = query_task_return["message"]
            else:
                err_msg = save_file_return["message"]
            if err_msg:
                add_notify(f"❌《{task['taskname']}》转存失败：{err_msg}\n")
        else:
            # 没有新文件需要转存
            if not subdir_path:  # 只在顶层（非子目录）打印一次消息
                pass
        return tree

    def do_rename_task(self, task, subdir_path=""):
        # 检查是否为顺序命名模式
        if task.get("use_sequence_naming") and task.get("sequence_naming"):
            # 使用顺序命名模式
            sequence_pattern = task["sequence_naming"]
            # 替换占位符为正则表达式捕获组
            regex_pattern = re.escape(sequence_pattern).replace('\\{\\}', '(\\d+)')
            savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}{subdir_path}")
            if not self.savepath_fid.get(savepath):
                # 路径已存在，直接设置fid
                self.savepath_fid[savepath] = self.get_fids([savepath])[0]["fid"]
            dir_file_list = self.ls_dir(self.savepath_fid[savepath])
            dir_file_name_list = [item["file_name"] for item in dir_file_list]
            
            # 找出当前最大序号
            max_sequence = 0
            for dir_file in dir_file_list:
                matches = re.match(regex_pattern, dir_file["file_name"])
                if matches:
                    try:
                        current_seq = int(matches.group(1))
                        max_sequence = max(max_sequence, current_seq)
                    except (IndexError, ValueError):
                        pass
            
            # 重命名文件
            current_sequence = max_sequence
            is_rename_count = 0
            
            # 定义自定义排序函数
            def custom_sort(file):
                file_name = file["file_name"]
                
                # 1. 提取文件名中的数字（期数/集数等）
                episode_num = 0
                
                # 尝试匹配"第X期/集/话"格式
                episode_match = re.search(r'第(\d+)[期集话]', file_name)
                if episode_match:
                    episode_num = int(episode_match.group(1))
                    
                    # 如果同时存在集数和上中下，则按照集数*10+位置排序
                    if '上' in file_name:
                        return (episode_num, 1, file.get("created_at", 0))
                    elif '中' in file_name:
                        return (episode_num, 2, file.get("created_at", 0))
                    elif '下' in file_name:
                        return (episode_num, 3, file.get("created_at", 0))
                    return (episode_num, 10, file.get("created_at", 0))
                
                # 如果文件名中包含"上中下"，优先处理
                if '上' in file_name:
                    return (0, 1, file.get("created_at", 0))
                elif '中' in file_name:
                    return (0, 2, file.get("created_at", 0))
                elif '下' in file_name:
                    return (0, 3, file.get("created_at", 0))
                
                # 尝试匹配常见视频格式 S01E01, E01, 1x01 等
                if re.search(r'[Ss](\d+)[Ee](\d+)', file_name):
                    match = re.search(r'[Ss](\d+)[Ee](\d+)', file_name)
                    season = int(match.group(1))
                    episode = int(match.group(2))
                    episode_num = season * 1000 + episode  # 确保季和集的排序正确
                elif re.search(r'[Ee][Pp]?(\d+)', file_name):
                    match = re.search(r'[Ee][Pp]?(\d+)', file_name)
                    episode_num = int(match.group(1))
                elif re.search(r'(\d+)[xX](\d+)', file_name):
                    match = re.search(r'(\d+)[xX](\d+)', file_name)
                    season = int(match.group(1))
                    episode = int(match.group(2))
                    episode_num = season * 1000 + episode
                
                # 3. 日期格式识别（支持多种格式）
                
                # 3.1 完整的YYYYMMDD格式
                match_date_compact = re.search(r'(20\d{2})(\d{2})(\d{2})', file_name)
                if match_date_compact:
                    year = int(match_date_compact.group(1))
                    month = int(match_date_compact.group(2))
                    day = int(match_date_compact.group(3))
                    return (year * 10000 + month * 100 + day, 0, file.get("created_at", 0))
                
                # 3.2 YYYY-MM-DD 或 YYYY.MM.DD 或 YYYY/MM/DD 格式
                match_date_full = re.search(r'(20\d{2})[-./](\d{1,2})[-./](\d{1,2})', file_name)
                if match_date_full:
                    year = int(match_date_full.group(1))
                    month = int(match_date_full.group(2))
                    day = int(match_date_full.group(3))
                    return (year * 10000 + month * 100 + day, 0, file.get("created_at", 0))
                
                # 3.3 MM/DD/YYYY 或 DD/MM/YYYY 格式
                match_date_alt = re.search(r'(\d{1,2})[-./](\d{1,2})[-./](20\d{2})', file_name)
                if match_date_alt:
                    # 假设第一个是月，第二个是日（美式日期）
                    month = int(match_date_alt.group(1))
                    day = int(match_date_alt.group(2))
                    year = int(match_date_alt.group(3))
                    # 检查月份值，如果大于12可能是欧式日期格式（DD/MM/YYYY）
                    if month > 12:
                        month, day = day, month
                    return (year * 10000 + month * 100 + day, 0, file.get("created_at", 0))
                
                # 3.4 MM/DD 格式（无年份）
                match_date_short = re.search(r'(\d{1,2})[-./](\d{1,2})', file_name)
                if match_date_short:
                    # 假设第一个是月，第二个是日
                    month = int(match_date_short.group(1))
                    day = int(match_date_short.group(2))
                    # 检查月份值，如果大于12可能是欧式日期格式（DD/MM）
                    if month > 12:
                        month, day = day, month
                    return (month * 100 + day, 0, file.get("created_at", 0))
                
                # 3.5 年期格式，如"2025年14期"
                match_year_issue = re.search(r'(20\d{2})[年].*?(\d+)[期]', file_name)
                if match_year_issue:
                    year = int(match_year_issue.group(1))
                    issue = int(match_year_issue.group(2))
                    return (year * 1000 + issue, 0, file.get("created_at", 0))
                
                # 默认使用数字排序或创建时间
                match_num = re.match(r'^(\d+)', os.path.splitext(file_name)[0])
                if match_num:
                    return (int(match_num.group(1)), 0, file.get("created_at", 0))
                
                # 最后按创建时间排序
                return (0, 0, file.get("created_at", 0))
            
            # 按自定义逻辑排序
            sorted_files = sorted([f for f in dir_file_list if not f["dir"] and not re.match(regex_pattern, f["file_name"])], key=custom_sort)
            
            for dir_file in sorted_files:
                current_sequence += 1
                file_ext = os.path.splitext(dir_file["file_name"])[1]
                save_name = sequence_pattern.replace("{}", f"{current_sequence:02d}") + file_ext
                
                if save_name != dir_file["file_name"] and save_name not in dir_file_name_list:
                    try:
                        rename_return = self.rename(dir_file["fid"], save_name)
                        # 防止网络问题导致的错误
                        if isinstance(rename_return, dict) and rename_return.get("code") == 0:
                            print(f"重命名: {dir_file['file_name']} → {save_name}")
                            is_rename_count += 1
                            dir_file_name_list.append(save_name)
                        else:
                            error_msg = rename_return.get("message", "未知错误")
                            print(f"重命名: {dir_file['file_name']} → {save_name} 失败，{error_msg}")
                    except Exception as e:
                        print(f"重命名出错: {dir_file['file_name']} → {save_name}，错误：{str(e)}")
            
            return is_rename_count > 0
            
        # 检查是否为剧集命名模式
        elif task.get("use_episode_naming") and task.get("episode_naming"):
            # 使用剧集命名模式
            episode_pattern = task["episode_naming"]
            regex_pattern = task.get("regex_pattern")
            
            # 获取目录文件列表 - 添加这行代码初始化dir_file_list
            savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}{subdir_path}")
            if not self.savepath_fid.get(savepath):
                # 路径已存在，直接设置fid
                savepath_fids = self.get_fids([savepath])
                if not savepath_fids:
                    print(f"保存路径不存在，准备新建：{savepath}")
                    mkdir_result = self.mkdir(savepath)
                    if mkdir_result["code"] == 0:
                        self.savepath_fid[savepath] = mkdir_result["data"]["fid"]
                        print(f"保存路径新建成功：{savepath}")
                    else:
                        print(f"保存路径新建失败：{mkdir_result['message']}")
                        return False
                else:
                    self.savepath_fid[savepath] = savepath_fids[0]["fid"]
            
            dir_file_list = self.ls_dir(self.savepath_fid[savepath])
            
            # 构建目标目录中所有文件的查重索引（按大小和修改时间）
            dir_files_map = {}
            for dir_file in dir_file_list:
                if not dir_file["dir"]:  # 仅处理文件
                    file_size = dir_file.get("size", 0)
                    file_ext = os.path.splitext(dir_file["file_name"])[1].lower()
                    update_time = dir_file.get("updated_at", 0)
                    
                    # 创建大小+扩展名的索引，用于快速查重
                    key = f"{file_size}_{file_ext}"
                    if key not in dir_files_map:
                        dir_files_map[key] = []
                    dir_files_map[key].append({
                        "file_name": dir_file["file_name"],
                        "updated_at": update_time,
                    })
            
            # 实现序号提取函数
            def extract_episode_number(filename):
                # 优先匹配SxxExx格式
                match_s_e = re.search(r'[Ss](\d+)[Ee](\d+)', filename)
                if match_s_e:
                    # 直接返回E后面的集数
                    return int(match_s_e.group(2))
                
                # 其次匹配E01格式
                match_e = re.search(r'[Ee][Pp]?(\d+)', filename)
                if match_e:
                    return int(match_e.group(1))
                
                # 尝试匹配更多格式
                default_patterns = [
                    r'(\d+)',
                    r'(\d+)[-_\s]*4[Kk]',
                    r'(\d+)话',
                    r'第(\d+)话',
                    r'第(\d+)集',
                    r'第(\d+)期',
                    r'(\d+)\s+4[Kk]',
                    r'(\d+)[_\s]4[Kk]',
                    r'【(\d+)】',
                    r'\[(\d+)\]',
                    r'_?(\d+)_'
                ]
                
                # 如果配置了自定义规则，优先使用
                if "config_data" in task and isinstance(task["config_data"].get("episode_patterns"), list) and task["config_data"]["episode_patterns"]:
                    patterns = [p.get("regex", "(\\d+)") for p in task["config_data"]["episode_patterns"]]
                else:
                    # 尝试从全局配置获取
                    global CONFIG_DATA
                    if isinstance(CONFIG_DATA.get("episode_patterns"), list) and CONFIG_DATA["episode_patterns"]:
                        patterns = [p.get("regex", "(\\d+)") for p in CONFIG_DATA["episode_patterns"]]
                    else:
                        patterns = default_patterns
                
                # 尝试使用每个正则表达式匹配文件名
                for pattern_regex in patterns:
                    try:
                        match = re.search(pattern_regex, filename)
                        if match:
                            return int(match.group(1))
                    except:
                        continue
                return None
                
            # 找出已命名的文件列表，避免重复转存
            existing_episode_numbers = set()
            for dir_file in dir_file_list:
                if not dir_file["dir"] and regex_pattern:
                    try:
                        matches = re.match(regex_pattern, dir_file["file_name"])
                        if matches:
                            episode_num = int(matches.group(1))
                            existing_episode_numbers.add(episode_num)
                    except:
                        pass
            
            # 检查是否需要从分享链接获取数据
            if task.get("shareurl"):
                try:
                    # 提取链接参数
                    pwd_id, passcode, pdir_fid, paths = self.extract_url(task["shareurl"])
                    if not pwd_id:
                        print(f"提取链接参数失败，请检查分享链接是否有效")
                        return False
                    
                    # 获取分享详情
                    is_sharing, stoken = self.get_stoken(pwd_id, passcode)
                    if not is_sharing:
                        print(f"分享详情获取失败：{stoken}")
                        return False
                    
                    # 获取分享文件列表
                    share_file_list = self.get_detail(pwd_id, stoken, pdir_fid)["list"]
                    if not share_file_list:
                        print("分享为空，文件已被分享者删除")
                        return False
                    
                    # 预先过滤分享文件列表，去除已存在的文件
                    filtered_share_files = []
                    for share_file in share_file_list:
                        if share_file["dir"]:
                            # 处理子目录
                            if task.get("update_subdir") and re.search(task["update_subdir"], share_file["file_name"]):
                                filtered_share_files.append(share_file)
                            continue
                            
                        # 检查文件是否已存在（基于大小和修改时间）
                        file_size = share_file.get("size", 0)
                        file_ext = os.path.splitext(share_file["file_name"])[1].lower()
                        share_update_time = share_file.get("last_update_at", 0)
                        
                        key = f"{file_size}_{file_ext}"
                        is_duplicate = False
                        
                        if key in dir_files_map:
                            for existing_file in dir_files_map[key]:
                                existing_update_time = existing_file.get("updated_at", 0)
                                if (abs(share_update_time - existing_update_time) < 2592000 or 
                                    abs(1 - (share_update_time / existing_update_time if existing_update_time else 1)) < 0.1):
                                    is_duplicate = True
                                    break
                        
                        # 检查剧集号是否已经存在
                        episode_num = extract_episode_number(share_file["file_name"])
                        if episode_num is not None and episode_num in existing_episode_numbers:
                            # print(f"跳过已存在的剧集号: {episode_num} ({share_file['file_name']})")
                            is_duplicate = True
                        
                        # 生成预期的目标文件名并检查是否已存在
                        if episode_num is not None and not is_duplicate:
                            file_ext = os.path.splitext(share_file["file_name"])[1]
                            expected_name = episode_pattern.replace("[]", f"{episode_num:02d}") + file_ext
                            # 检查目标文件名是否存在于目录中
                            if any(dir_file["file_name"] == expected_name for dir_file in dir_file_list):
                                # print(f"跳过已存在的文件名: {expected_name}")
                                is_duplicate = True
                        
                        # 只处理非重复文件
                        if not is_duplicate:
                            filtered_share_files.append(share_file)
                        
                        # 指定文件开始订阅/到达指定文件（含）结束历遍
                        if share_file["fid"] == task.get("startfid", ""):
                            break
                    
                    # 实现高级排序算法
                    def sort_by_episode(file):
                        if file["dir"]:
                            return (float('inf'), 0)
                        
                        filename = file["file_name"]
                        
                        # 优先匹配S01E01格式
                        match_s_e = re.search(r'[Ss](\d+)[Ee](\d+)', filename)
                        if match_s_e:
                            season = int(match_s_e.group(1))
                            episode = int(match_s_e.group(2))
                            return (season * 1000 + episode, 0)
                            
                        # 其他匹配方式
                        episode_num = extract_episode_number(filename)
                        if episode_num is not None:
                            return (episode_num, 0)
                            
                        # 无法识别，使用修改时间
                        return (float('inf'), file.get("last_update_at", 0))
                        
                    # 过滤出文件并排序
                    files_to_process = [f for f in filtered_share_files if not f["dir"]]
                    sorted_files = sorted(files_to_process, key=sort_by_episode)
                    
                    # 要保存的文件列表
                    need_save_list = []
                    
                    # 生成文件名并添加到列表
                    for share_file in sorted_files:
                        episode_num = extract_episode_number(share_file["file_name"])
                        if episode_num is not None:
                            # 生成新文件名
                            file_ext = os.path.splitext(share_file["file_name"])[1]
                            save_name = episode_pattern.replace("[]", f"{episode_num:02d}") + file_ext
                            
                            # 添加到保存列表
                            share_file["save_name"] = save_name
                            share_file["original_name"] = share_file["file_name"]
                            need_save_list.append(share_file)
                        else:
                            # 无法提取集号，使用原文件名
                            share_file["save_name"] = share_file["file_name"]
                            share_file["original_name"] = share_file["file_name"]
                            need_save_list.append(share_file)
                    
                    # 保存文件
                    if need_save_list:
                        fid_list = [item["fid"] for item in need_save_list]
                        fid_token_list = [item["share_fid_token"] for item in need_save_list]
                        save_file_return = self.save_file(
                            fid_list, fid_token_list, self.savepath_fid[savepath], pwd_id, stoken
                        )
                        if save_file_return["code"] == 0:
                            task_id = save_file_return["data"]["task_id"]
                            query_task_return = self.query_task(task_id)
                            
                            if query_task_return["code"] == 0:
                                # 建立目录树
                                tree = Tree()
                                tree.create_node(
                                    savepath,
                                    "root",
                                    data={
                                        "is_dir": True,
                                    },
                                )
                                
                                saved_files = []
                                for index, item in enumerate(need_save_list):
                                    icon = (
                                        "📁"
                                        if item["dir"] == True
                                        else "🎞️" if item.get("obj_category") == "video" else ""
                                    )
                                    saved_files.append(f"{icon}{item['save_name']}")
                                    tree.create_node(
                                        f"{icon}{item['save_name']}",
                                        item["fid"],
                                        parent="root",
                                        data={
                                            "fid": f"{query_task_return['data']['save_as']['save_as_top_fids'][index]}",
                                            "path": f"{savepath}/{item['save_name']}",
                                            "is_dir": item.get("dir", False),
                                            "original_name": item["original_name"],
                                            "save_name": item["save_name"]
                                        },
                                    )
                                
                                # 添加成功通知
                                add_notify(f"✅《{task['taskname']}》 添加追更:\n/{task['savepath']}{subdir_path}")
                                # 打印保存文件列表
                                for idx, file_name in enumerate(saved_files):
                                    prefix = "├── " if idx < len(saved_files) - 1 else "└── "
                                    add_notify(f"{prefix}{file_name}")
                                add_notify("")
                                
                                # 进行重命名操作，确保文件按照预览名称保存
                                time.sleep(1)  # 等待文件保存完成
                                
                                # 刷新目录列表以获取新保存的文件
                                fresh_dir_file_list = self.ls_dir(self.savepath_fid[savepath])
                                renamed_count = 0
                                
                                # 创建一个映射来存储原始文件名到保存项的映射
                                original_name_to_item = {}
                                for saved_item in need_save_list:
                                    # 使用文件名前缀作为键，处理可能的文件名变化
                                    file_prefix = saved_item["original_name"].split(".")[0]
                                    original_name_to_item[file_prefix] = saved_item
                                    # 同时保存完整文件名的映射
                                    original_name_to_item[saved_item["original_name"]] = saved_item
                                
                                # 创建一个集合来跟踪已经重命名的文件ID
                                renamed_fids = set()
                                
                                # 首先尝试使用剧集号进行智能匹配
                                for dir_file in fresh_dir_file_list:
                                    if dir_file["dir"] or dir_file["fid"] in renamed_fids:
                                        continue
                                        
                                    # 从文件名中提取剧集号
                                    episode_num = extract_episode_number(dir_file["file_name"])
                                    if episode_num is None:
                                        continue
                                        
                                    # 查找对应的目标文件
                                    for saved_item in need_save_list:
                                        saved_episode_num = extract_episode_number(saved_item["original_name"])
                                        if saved_episode_num == episode_num:
                                            # 匹配到对应的剧集号
                                            target_name = saved_item["save_name"]
                                            # 确保目标名称不重复
                                            if target_name not in [f["file_name"] for f in fresh_dir_file_list]:
                                                rename_result = self.rename(dir_file["fid"], target_name)
                                                if rename_result["code"] == 0:
                                                    print(f"重命名: {dir_file['file_name']} → {target_name}")
                                                    renamed_count += 1
                                                    renamed_fids.add(dir_file["fid"])
                                                    break
                                            else:
                                                # 如果目标文件名已存在，尝试加上序号
                                                name_base, ext = os.path.splitext(target_name)
                                                alt_name = f"{name_base} ({episode_num}){ext}"
                                                if alt_name not in [f["file_name"] for f in fresh_dir_file_list]:
                                                    rename_result = self.rename(dir_file["fid"], alt_name)
                                                    if rename_result["code"] == 0:
                                                        print(f"重命名: {dir_file['file_name']} → {alt_name}")
                                                        renamed_count += 1
                                                        renamed_fids.add(dir_file["fid"])
                                                        break
                                
                                # 对于未能通过剧集号匹配的文件，尝试使用文件名匹配
                                for dir_file in fresh_dir_file_list:
                                    if dir_file["dir"] or dir_file["fid"] in renamed_fids:
                                        continue
                                    
                                    # 尝试精确匹配
                                    if dir_file["file_name"] in original_name_to_item:
                                        saved_item = original_name_to_item[dir_file["file_name"]]
                                        target_name = saved_item["save_name"]
                                        
                                        if target_name not in [f["file_name"] for f in fresh_dir_file_list]:
                                            rename_result = self.rename(dir_file["fid"], target_name)
                                            if rename_result["code"] == 0:
                                                print(f"重命名: {dir_file['file_name']} → {target_name}")
                                                renamed_count += 1
                                                renamed_fids.add(dir_file["fid"])
                                        continue
                                    
                                    # 尝试模糊匹配（使用文件名前缀）
                                    dir_file_prefix = dir_file["file_name"].split(".")[0]
                                    for prefix, saved_item in original_name_to_item.items():
                                        if prefix in dir_file_prefix or dir_file_prefix in prefix:
                                            # 找到相似的文件名
                                            target_name = saved_item["save_name"]
                                            if target_name not in [f["file_name"] for f in fresh_dir_file_list]:
                                                rename_result = self.rename(dir_file["fid"], target_name)
                                                if rename_result["code"] == 0:
                                                    print(f"重命名: {dir_file['file_name']} → {target_name}")
                                                    renamed_count += 1
                                                    renamed_fids.add(dir_file["fid"])
                                                    original_name_to_item.pop(prefix, None)  # 避免重复使用
                                                    break
                                
                                if renamed_count > 0:
                                    # print(f"✅ 成功重命名 {renamed_count} 个文件")
                                    pass
                                
                                return tree
                            else:
                                err_msg = query_task_return["message"]
                                add_notify(f"❌《{task['taskname']}》转存失败：{err_msg}\n")
                                return False
                        else:
                            print(f"❌ 保存文件失败: {save_file_return['message']}")
                            add_notify(f"❌《{task['taskname']}》转存失败：{save_file_return['message']}\n")
                            return False
                    else:
                        # print("没有需要保存的新文件")
                        return False
                except Exception as e:
                    print(f"处理分享链接时发生错误: {str(e)}")
                    add_notify(f"❌《{task['taskname']}》处理分享链接时发生错误: {str(e)}\n")
                    return False

            # 对本地已有文件进行重命名（即使没有分享链接或处理失败也执行）
            is_rename_count = 0
            renamed_files = []
            
            # 筛选出需要重命名的文件
            for dir_file in dir_file_list:
                if dir_file["dir"]:
                    continue
                
                # 检查是否需要重命名
                episode_num = extract_episode_number(dir_file["file_name"])
                if episode_num is not None:
                    # 检查文件名是否符合指定的剧集命名格式
                    if not re.match(regex_pattern, dir_file["file_name"]):
                        file_ext = os.path.splitext(dir_file["file_name"])[1]
                        new_name = episode_pattern.replace("[]", f"{episode_num:02d}") + file_ext
                        renamed_files.append((dir_file, new_name))
            
            # 按剧集号排序
            renamed_files.sort(key=lambda x: extract_episode_number(x[0]["file_name"]) or 0)
            
            # 执行重命名
            for dir_file, new_name in renamed_files:
                # 防止重名
                if new_name not in [f["file_name"] for f in dir_file_list]:
                    try:
                        rename_return = self.rename(dir_file["fid"], new_name)
                        if rename_return["code"] == 0:
                            print(f"重命名: {dir_file['file_name']} → {new_name}")
                            is_rename_count += 1
                        else:
                            print(f"重命名: {dir_file['file_name']} → {new_name} 失败，{rename_return['message']}")
                    except Exception as e:
                        print(f"重命名出错: {dir_file['file_name']} → {new_name}，错误：{str(e)}")
            
            return is_rename_count > 0


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
        growth_message = f"💾 {'88VIP' if growth_info['88VIP'] else '普通用户'} 总空间：{format_bytes(growth_info['total_capacity'])}，签到累计获得：{format_bytes(growth_info['cap_composition'].get('sign_reward', 0))}"
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
    print()


def do_save(account, tasklist=[]):
    print(f"🧩 载入插件")
    plugins, CONFIG_DATA["plugins"], task_plugins_config = Config.load_plugins(
        CONFIG_DATA.get("plugins", {})
    )
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

    # 执行任务
    for index, task in enumerate(tasklist):
        print()
        print(f"#{index+1}------------------")
        print(f"任务名称: {task['taskname']}")
        print(f"分享链接: {task['shareurl']}")
        print(f"保存路径: {task['savepath']}")
        # 根据命名模式显示不同信息
        if task.get("use_sequence_naming") and task.get("sequence_naming"):
            print(f"顺序命名: {task['sequence_naming']}")
        elif task.get("use_episode_naming") and task.get("episode_naming"):
            print(f"剧集命名: {task['episode_naming']}")
        else:
            # 正则命名模式
            if task.get("pattern"):
                print(f"正则匹配: {task['pattern']}")
            if task.get("replace") is not None:  # 显示替换规则，即使为空字符串
                print(f"正则替换: {task['replace']}")
        if task.get("update_subdir"):
            print(f"更子目录: {task['update_subdir']}")
        if task.get("runweek") or task.get("enddate"):
            print(
                f"运行周期: WK{task.get("runweek",[])} ~ {task.get('enddate','forever')}"
            )
        print()
        # 判断任务周期
        if not is_time(task):
            print(f"任务不在运行周期内，跳过")
        else:
            is_new_tree = account.do_save_task(task)
            is_rename = account.do_rename_task(task)

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
            
            # 为任务添加剧集模式配置
            if task.get("use_episode_naming") and task.get("episode_naming"):
                task["config_data"] = {
                    "episode_patterns": CONFIG_DATA.get("episode_patterns", [])
                }
            # 调用插件
            if is_new_tree or is_rename:
                print(f"🧩 调用插件")
                for plugin_name, plugin in plugins.items():
                    if plugin.is_active and (is_new_tree or is_rename):
                        task = (
                            plugin.run(task, account=account, tree=is_new_tree) or task
                        )
            elif is_new_tree is False:  # 明确没有新文件
                print(f"任务完成：没有新的文件需要转存")
                print()
    print()


def main():
    global CONFIG_DATA
    start_time = datetime.now()
    print(f"===============程序开始===============")
    print(f"⏰ 执行时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    # 读取启动参数
    config_path = sys.argv[1] if len(sys.argv) > 1 else "quark_config.json"
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
        if not CONFIG_DATA.get("magic_regex"):
            CONFIG_DATA["magic_regex"] = MAGIC_REGEX
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
        send_ql_notify("【夸克自动追更】", notify_body)
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
