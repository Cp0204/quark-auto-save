#!/usr/bin/env python3
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
import traceback
import urllib.parse
from datetime import datetime

# 兼容青龙
try:
    from treelib import Tree
except:
    print("正在尝试自动安装依赖...")
    os.system("pip3 install treelib &> /dev/null")
    from treelib import Tree

# 【新增】引入 natsort 用于更智能的自然排序
try:
    from natsort import natsorted
except:
    print("正在尝试自动安装依赖 natsort...")
    os.system("pip3 install natsort &> /dev/null")
    from natsort import natsorted


CONFIG_DATA = {}
NOTIFYS = []
GH_PROXY = os.environ.get("GH_PROXY", "https://ghproxy.net/")

# 【保留】你的自定义API异常，用于更精确的错误捕获
class QuarkAPIError(Exception):
    pass

# 【保留】你的可复用的步骤重试函数
def execute_with_retry(action, description, retries=3, delay=2):
    """
    执行一个操作，并在失败时重试。
    :param action: 一个无参数的函数或lambda表达式，代表要执行的操作。
    :param description: 操作的描述，用于打印日志。
    :param retries: 最大尝试次数。
    :param delay: 每次尝试之间的延迟（秒）。
    :return: 操作的返回值。
    """
    last_exception = None
    for attempt in range(retries):
        try:
            print(f"   🔄 [尝试执行] {description} (第 {attempt + 1}/{retries} 次尝试)")
            return action()
        except Exception as e:
            last_exception = e
            print(f"   ⚠️ [步骤失败] {description} (第 {attempt + 1}/{retries} 次尝试): {e}")
            print(f"   📋 [详细错误] {type(e).__name__}: {str(e)}")
            if attempt < retries - 1:
                print(f"   ⏱️ [等待重试] 等待 {delay} 秒后重试...")
                time.sleep(delay)
    print(f"   ❌ [彻底失败] {description} 在 {retries} 次尝试后均告失败。")
    print(f"   📋 [最终错误] {type(last_exception).__name__}: {str(last_exception)}")
    raise last_exception


# 发送通知消息
def send_ql_notify(title, body):
    try:
        import notify
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

# 【新增】格式化字节大小的辅助函数，用于签到功能
def format_bytes(size_bytes: int) -> str:
    if not isinstance(size_bytes, (int, float)) or size_bytes < 0:
        return "0 B"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    i = 0
    while size_bytes >= 1024 and i < len(units) - 1:
        size_bytes /= 1024
        i += 1
    return f"{size_bytes:.2f} {units[i]}"


class Config:
    def download_file(url, save_path):
        response = requests.get(url)
        if response.status_code == 200:
            with open(save_path, "wb") as file:
                file.write(response.content)
            return True
        else:
            return False

    def read_json(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data

    def write_json(config_path, data):
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, sort_keys=False, indent=2)

    def get_cookies(cookie_val):
        if isinstance(cookie_val, list):
            return cookie_val
        elif cookie_val:
            return cookie_val.split("\n") if "\n" in cookie_val else [cookie_val]
        else:
            return False

    def load_plugins(plugins_config={}, plugins_dir="plugins"):
        PLUGIN_FLAGS = os.environ.get("PLUGIN_FLAGS", "").split(",")
        plugins_available = {}
        task_plugins_config = {}
        all_modules = [
            f.replace(".py", "") for f in os.listdir(plugins_dir) if f.endswith(".py")
        ]
        priority_path = os.path.join(plugins_dir, "_priority.json")
        try:
            with open(priority_path, encoding="utf-8") as f:
                priority_modules = json.load(f)
            if priority_modules:
                all_modules = [
                    module for module in priority_modules if module in all_modules
                ] + [module for module in all_modules if module not in priority_modules]
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        for module_name in all_modules:
            if f"-{module_name}" in PLUGIN_FLAGS:
                continue
            try:
                module = importlib.import_module(f"{plugins_dir}.{module_name}")
                ServerClass = getattr(module, module_name.capitalize())
                if module_name in plugins_config:
                    plugin = ServerClass(**plugins_config[module_name])
                    plugins_available[module_name] = plugin
                else:
                    plugin = ServerClass()
                    plugins_config[module_name] = plugin.default_config
                if hasattr(plugin, "default_task_config"):
                    task_plugins_config[module_name] = plugin.default_task_config
            except (ImportError, AttributeError) as e:
                print(f"载入模块 {module_name} 失败: {e}")
        print()
        return plugins_available, plugins_config, task_plugins_config

    def breaking_change_update(config_data):
        for task in config_data.get("tasklist", []):
            if "$TASKNAME" in task.get("replace", ""):
                task["replace"] = task["replace"].replace("$TASKNAME", "{TASKNAME}")


class MagicRename:
    magic_regex = {
        "$TV": {"pattern": r".*?([Ss]\d{1,2})?(?:[第EePpXx\.\-\_\( ]{1,2}|^)(\d{1,3})(?!\d).*?\.(mp4|mkv)", "replace": r"\1E\2.\3"},
        "$BLACK_WORD": {"pattern": r"^(?!.*纯享)(?!.*加更)(?!.*超前企划)(?!.*训练室)(?!.*蒸蒸日上).*", "replace": ""},
    }
    magic_variable = {
        "{TASKNAME}": "", "{I}": 1, "{EXT}": [r"(?<=\.)\w+$"], "{CHINESE}": [r"[\u4e00-\u9fa5]{2,}"],
        "{DATE}": [r"(18|19|20)?\d{2}[\.\-/年]\d{1,2}[\.\-/月]\d{1,2}", r"(?<!\d)[12]\d{3}[01]?\d[0123]?\d", r"(?<!\d)[01]?\d[\.\-/月][0123]?\d"],
        "{YEAR}": [r"(?<!\d)(18|19|20)\d{2}(?!\d)"], "{S}": [r"(?<=[Ss])\d{1,2}(?=[EeXx])", r"(?<=[Ss])\d{1,2}"],
        "{SXX}": [r"[Ss]\d{1,2}(?=[EeXx])", r"[Ss]\d{1,2}"],
        "{E}": [r"(?<=[Ss]\d\d[Ee])\d{1,3}", r"(?<=[Ee])\d{1,3}", r"(?<=[Ee][Pp])\d{1,3}", r"(?<=第)\d{1,3}(?=[集期话部篇])", r"(?<!\d)\d{1,3}(?=[集期话部篇])", r"(?!.*19)(?!.*20)(?<=[\._])\d{1,3}(?=[\._])", r"^\d{1,3}(?=\.\w+)", r"(?<!\d)\d{1,3}(?!\d)(?!$)"],
        "{PART}": [r"(?<=[集期话部篇第])[上中下一二三四五六七八九十]", r"[上中下一二三四五六七八九十]"], "{VER}": [r"[\u4e00-\u9fa5]+版"],
    }
    # 【优化】扩展中文数字并为排序键添加分隔符，避免影响相邻数字
    priority_list = ["上", "中", "下", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十", "百", "千", "万"]

    def __init__(self, magic_regex={}, magic_variable={}):
        self.magic_regex.update(magic_regex)
        self.magic_variable.update(magic_variable)
        self.dir_filename_dict = {}

    def set_taskname(self, taskname):
        self.magic_variable["{TASKNAME}"] = taskname

    def magic_regex_conv(self, pattern, replace):
        keyword = pattern
        if keyword in self.magic_regex:
            pattern = self.magic_regex[keyword]["pattern"]
            if replace == "":
                replace = self.magic_regex[keyword]["replace"]
        return pattern, replace

    def sub(self, pattern, replace, file_name):
        if not replace: return file_name
        for key, p_list in self.magic_variable.items():
            if key in replace:
                if p_list and isinstance(p_list, list):
                    match = None
                    for p in p_list:
                        match = re.search(p, file_name)
                        if match:
                            value = match.group()
                            if key == "{DATE}":
                                value = "".join([char for char in value if char.isdigit()])
                                value = str(datetime.now().year)[: (8 - len(value))] + value
                            replace = replace.replace(key, value)
                            break
                if key == "{TASKNAME}": replace = replace.replace(key, self.magic_variable["{TASKNAME}"])
                elif key == "{SXX}" and not match: replace = replace.replace(key, "S01")
                elif key == "{I}": continue
                else: replace = replace.replace(key, "")
        return re.sub(pattern, replace, file_name) if pattern and replace else replace

    # 【优化】使用更安全的替换方式，防止关键字误匹配
    def _custom_sort_key(self, name):
        for i, keyword in enumerate(self.priority_list):
            if keyword in name:
                name = name.replace(keyword, f"_{i:02d}_")
        return name

    # 【优化】使用 natsorted 进行自然排序，并加入文件修改时间作为排序因素
    def sort_file_list(self, file_list, dir_filename_dict={}):
        filename_list = [
            f"{f['file_name_re']}_{f['updated_at']}"
            for f in file_list
            if f.get("file_name_re") and not f["dir"]
        ]
        dir_filename_dict = dir_filename_dict or self.dir_filename_dict
        filename_list = list(set(filename_list) | set(dir_filename_dict.values()))
        # 使用 natsorted 进行自然排序
        filename_list = natsorted(filename_list, key=self._custom_sort_key)
        
        filename_index = {}
        for name in filename_list:
            if name in dir_filename_dict.values(): continue
            i = filename_list.index(name) + 1
            while i in dir_filename_dict.keys(): i += 1
            dir_filename_dict[i] = name
            filename_index[name] = i
        
        for file in file_list:
            if file.get("file_name_re"):
                if match := re.search(r"\{I+\}", file["file_name_re"]):
                    # 使用带时间戳的文件名来查找索引
                    i = filename_index.get(f"{file['file_name_re']}_{file['updated_at']}", 0)
                    file["file_name_re"] = re.sub(match.group(), str(i).zfill(match.group().count("I")), file["file_name_re"])

    def set_dir_file_list(self, file_list, replace):
        if not file_list: return
        self.dir_filename_dict = {}
        filename_list = [f["file_name"] for f in file_list if not f["dir"]]
        filename_list.sort()
        if match := re.search(r"\{I+\}", replace):
            magic_i = match.group()
            pattern_i = r"\d" * magic_i.count("I")
            pattern = replace.replace(match.group(), "🔢")
            for key, _ in self.magic_variable.items():
                if key in pattern: pattern = pattern.replace(key, "🔣")
            pattern = re.sub(r"\\[0-9]+", "🔣", pattern)
            pattern = f"({re.escape(pattern).replace('🔣', '.*?').replace('🔢', f')({pattern_i})(')})"
            if filename_list and (match_last := re.match(pattern, filename_list[-1])):
                self.magic_variable["{I}"] = int(match_last.group(2))
            for filename in filename_list:
                if match_item := re.match(pattern, filename):
                    self.dir_filename_dict[int(match_item.group(2))] = (match_item.group(1) + magic_i + match_item.group(3))

    def is_exists(self, filename, filename_list, ignore_ext=False):
        if not filename: return None
        if ignore_ext:
            filename_no_ext = os.path.splitext(filename)[0]
            filename_list_no_ext = [os.path.splitext(f)[0] for f in filename_list]
            for i, f in enumerate(filename_list_no_ext):
                if f == filename_no_ext: return filename_list[i]
            return None
        else:
            if match := re.search(r"\{I+\}", filename):
                magic_i = match.group()
                pattern_i = r"\d" * magic_i.count("I")
                pattern = re.escape(filename).replace(f"\\{magic_i}", pattern_i)
                for f in filename_list:
                    if re.fullmatch(pattern, f): return f
                return None
            else:
                return filename if filename in filename_list else None


class Quark:
    BASE_URL = "https://drive-pc.quark.cn"
    BASE_URL_APP = "https://drive-m.quark.cn"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) quark-cloud-drive/3.14.2 Chrome/112.0.5615.165 Electron/24.1.3.8 Safari/537.36 Channel/pckk_other_ch"
    
    def __init__(self, cookie="", index=0, blacklist=[]):
        self.cookie = cookie.strip()
        self.index = index + 1
        self.is_active = False
        self.nickname = ""
        self.mparam = self._match_mparam_form_cookie(cookie)
        self.file_blacklist = blacklist
        self.savepath_fid = {"/": "0"}

    def _match_mparam_form_cookie(self, cookie):
        mparam = {}
        kps_match = re.search(r"(?<!\w)kps=([a-zA-Z0-9%+/=]+)[;&]?", cookie)
        sign_match = re.search(r"(?<!\w)sign=([a-zA-Z0-9%+/=]+)[;&]?", cookie)
        vcode_match = re.search(r"(?<!\w)vcode=([a-zA-Z0-9%+/=]+)[;&]?", cookie)
        if kps_match and sign_match and vcode_match:
            return {"kps": kps_match.group(1).replace("%25", "%"), "sign": sign_match.group(1).replace("%25", "%"), "vcode": vcode_match.group(1).replace("%25", "%")}
        return mparam

    def _send_request(self, method, url, **kwargs):
        headers = {"cookie": self.cookie, "content-type": "application/json", "user-agent": self.USER_AGENT}
        if "headers" in kwargs: headers.update(kwargs.pop("headers"))
        
        # 设置默认超时时间
        kwargs.setdefault('timeout', (5, 30)) # (连接超时, 读取超时)
        
        # 处理移动端参数
        if self.mparam and "share" in url and self.BASE_URL in url:
            url = url.replace(self.BASE_URL, self.BASE_URL_APP)
            kwargs.setdefault("params", {}).update({
                "device_model": "M2011K2C", "entry": "default_clouddrive", "_t_group": "0%3A_s_vp%3A1", "dmn": "Mi%2B11", "fr": "android",
                "pf": "3300", "bi": "35937", "ve": "7.4.5.680", "ss": "411x875", "mi": "M2011K2C", "nt": "5", "nw": "0", "kt": "4",
                "pr": "ucpro", "sv": "release", "dt": "phone", "data_from": "ucapi", **self.mparam, "app": "clouddrive", "kkkk": "1",
            })
            if "cookie" in headers: del headers["cookie"]
        
        try:
            print(f"   📡 [发送请求] {method} {url}")
            response = requests.request(method, url, headers=headers, **kwargs)
            if response.status_code != 200:
                print(f"   ❌ [HTTP错误] 状态码: {response.status_code}, URL: {url}")
                response.raise_for_status()
            return response
        except requests.exceptions.Timeout:
            print(f"   ⏱️ [请求超时] URL: {url}, 超时时间: {kwargs.get('timeout')}秒")
            raise QuarkAPIError(f"请求超时: {url}")
        except requests.exceptions.RequestException as e:
            print(f"   ❌ [请求异常] URL: {url}, 错误: {type(e).__name__}: {str(e)}")
            raise QuarkAPIError(f"请求异常: {str(e)}")
        except Exception as e:
            print(f"   ❌ [未知错误] URL: {url}, 错误: {type(e).__name__}: {str(e)}")
            raise

    def init(self):
        account_info = self.get_account_info()
        self.is_active = True
        self.nickname = account_info["nickname"]

    def get_account_info(self):
        response = self._send_request("GET", "https://pan.quark.cn/account/info", params={"fr": "pc", "platform": "pc"}).json()
        if not response.get("data"): raise QuarkAPIError(f"获取账户信息失败: {response.get('message')}")
        return response["data"]

    # 【新增】获取签到信息的API
    def get_growth_info(self):
        if not self.mparam: return None
        params = {"pr": "ucpro", "fr": "android", **self.mparam}
        headers = {"content-type": "application/json"}
        response = self._send_request("GET", f"{self.BASE_URL_APP}/1/clouddrive/capacity/growth/info", params=params, headers=headers).json()
        if response.get("data"): return response["data"]
        return None

    # 【新增】执行签到的API
    def get_growth_sign(self):
        if not self.mparam: return False, "移动端参数未配置"
        params = {"pr": "ucpro", "fr": "android", **self.mparam}
        payload = {"sign_cyclic": True}
        headers = {"content-type": "application/json"}
        response = self._send_request("POST", f"{self.BASE_URL_APP}/1/clouddrive/capacity/growth/sign", json=payload, params=params, headers=headers).json()
        if response.get("data"):
            return True, response["data"]["sign_daily_reward"]
        return False, response.get("message", "签到失败")

    def get_stoken(self, pwd_id, passcode=""):
        response = self._send_request("POST", f"{self.BASE_URL}/1/clouddrive/share/sharepage/token", json={"pwd_id": pwd_id, "passcode": passcode}, params={"pr": "ucpro", "fr": "pc"}).json()
        if response.get("status") != 200: raise QuarkAPIError(response.get('message', '获取stoken失败'))
        return response["data"]["stoken"]

    def get_detail(self, pwd_id, stoken, pdir_fid):
        list_merge, page = [], 1
        print(f"   📡 [开始请求] 获取分享内容，pwd_id={pwd_id}, pdir_fid={pdir_fid}")
        while True:
            try:
                params = {"pr": "ucpro", "fr": "pc", "pwd_id": pwd_id, "stoken": stoken, "pdir_fid": pdir_fid, "force": "0", "_page": page, "_size": "50", "_fetch_banner": "0", "_fetch_share": 0, "_fetch_total": "1", "_sort": "file_type:asc,updated_at:desc"}
                print(f"   📡 [请求参数] 第{page}页: {params}")
                response = self._send_request("GET", f"{self.BASE_URL}/1/clouddrive/share/sharepage/detail", params=params, timeout=30).json()
                if response["code"] != 0: 
                    print(f"   ❌ [API错误] 获取分享详情失败: {response['message']}")
                    raise QuarkAPIError(f"获取分享详情失败: {response['message']}")
                print(f"   ✅ [请求成功] 第{page}页，获取到 {len(response['data']['list'])} 个项目")
                list_merge.extend(response["data"]["list"])
                if not response["data"]["list"] or len(list_merge) >= response["metadata"]["_total"]: 
                    print(f"   ✅ [请求完成] 共获取到 {len(list_merge)} 个项目")
                    break
                page += 1
            except Exception as e:
                print(f"   ❌ [请求异常] 获取分享内容第{page}页失败: {type(e).__name__}: {str(e)}")
                raise
        return list_merge

    def ls_dir(self, pdir_fid):
        list_merge, page = [], 1
        while True:
            params = {"pr": "ucpro", "fr": "pc", "pdir_fid": pdir_fid, "_page": page, "_size": "50", "_fetch_total": "1"}
            response = self._send_request("GET", f"{self.BASE_URL}/1/clouddrive/file/sort", params=params).json()
            if response["code"] != 0: raise QuarkAPIError(f"列出目录失败: {response['message']}")
            list_merge.extend(response["data"]["list"])
            if not response["data"]["list"] or len(list_merge) >= response["metadata"]["_total"]: break
            page += 1
        return list_merge

    def save_file(self, fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken):
        params = {"pr": "ucpro", "fr": "pc", "app": "clouddrive", "__t": int(time.time() * 1000)}
        payload = {"fid_list": fid_list, "fid_token_list": fid_token_list, "to_pdir_fid": to_pdir_fid, "pwd_id": pwd_id, "stoken": stoken}
        response = self._send_request("POST", f"{self.BASE_URL}/1/clouddrive/share/sharepage/save", json=payload, params=params).json()
        if response.get("code") != 0: raise QuarkAPIError(f"保存文件失败: {response.get('message')}")
        return self.query_task(response["data"]["task_id"])

    def query_task(self, task_id):
        description = f"查询任务 {task_id}"
        def action():
            params = {"pr": "ucpro", "fr": "pc", "task_id": task_id, "__t": int(time.time() * 1000)}
            response = self._send_request("GET", f"{self.BASE_URL}/1/clouddrive/task", params=params).json()
            if response.get("data", {}).get("status") == 0:
                raise QuarkAPIError(f"任务仍在执行中: {response.get('data', {}).get('task_title')}")
            if response.get("code") != 0:
                raise QuarkAPIError(f"查询任务失败: {response.get('message')}")
            return response["data"]
        return execute_with_retry(action, description, retries=10, delay=1)
    
    def mkdir(self, dir_path):
        payload = {"pdir_fid": "0", "file_name": "", "dir_path": dir_path}
        response = self._send_request("POST", f"{self.BASE_URL}/1/clouddrive/file", json=payload, params={"pr": "ucpro", "fr": "pc"}).json()
        if response.get("code") != 0: raise QuarkAPIError(f"创建目录 '{dir_path}' 失败: {response.get('message')}")
        return response["data"]

    def rename(self, fid, file_name):
        payload = {"fid": fid, "file_name": file_name}
        response = self._send_request("POST", f"{self.BASE_URL}/1/clouddrive/file/rename", json=payload, params={"pr": "ucpro", "fr": "pc"}).json()
        if response.get("code") != 0: raise QuarkAPIError(f"重命名失败: {response.get('message')}")
        return response
    
    # 【新增】删除文件的API，用于子目录重存
    def delete(self, filelist):
        payload = {"action_type": 2, "filelist": filelist, "exclude_fids": []}
        response = self._send_request("POST", f"{self.BASE_URL}/1/clouddrive/file/delete", json=payload, params={"pr": "ucpro", "fr": "pc"}).json()
        if response.get("code") != 0: raise QuarkAPIError(f"删除文件失败: {response.get('message')}")
        return response["data"]

    # 【新增】列出回收站的API，用于子目录重存
    def recycle_list(self, page=1, size=50):
        params = {"_page": page, "_size": size, "pr": "ucpro", "fr": "pc"}
        response = self._send_request("GET", f"{self.BASE_URL}/1/clouddrive/file/recycle/list", params=params).json()
        if response.get("code") != 0: raise QuarkAPIError(f"列出回收站失败: {response.get('message')}")
        return response["data"]["list"]

    # 【新增】从回收站彻底删除的API，用于子目录重存
    def recycle_remove(self, record_list):
        payload = {"select_mode": 2, "record_list": record_list}
        response = self._send_request("POST", f"{self.BASE_URL}/1/clouddrive/file/recycle/remove", json=payload, params={"pr": "ucpro", "fr": "pc"}).json()
        if response.get("code") != 0: raise QuarkAPIError(f"清理回收站失败: {response.get('message')}")
        return response

    def extract_url(self, url):
        match_id = re.search(r"/s/(\w+)", url)
        pwd_id = match_id.group(1) if match_id else None
        match_pwd = re.search(r"pwd=(\w+)", url)
        passcode = match_pwd.group(1) if match_pwd else ""
        pdir_fid = "0"
        if match := re.search(r"/(\w{32})", url.split('#')[-1]):
            pdir_fid = match.group(1)
        return pwd_id, passcode, pdir_fid

    def update_savepath_fid(self, tasklist):
        dir_paths = list(set([re.sub(r"/{2,}", "/", f"/{item['savepath']}") for item in tasklist if not item.get("enddate") or datetime.now().date() <= datetime.strptime(item["enddate"], "%Y-%m-%d").date()]))
        if not dir_paths: return
        for path in dir_paths:
            if path == "/": continue
            try:
                action = lambda: self.mkdir(path)
                dir_info = execute_with_retry(action, f"检查或创建目录 '{path}'")
                self.savepath_fid[path] = dir_info['fid']
                print(f"   ✅ [目录检查] 路径 '{path}' fid: {dir_info['fid']}")
            except QuarkAPIError as e:
                 print(f"   ❌ [目录检查] 路径 '{path}' 处理失败: {e}")
    
    def do_save_task(self, task):
        try:
            if task.get("shareurl_ban"):
                print(f"《{task['taskname']}》：{task['shareurl_ban']}")
                return

            pwd_id, passcode, pdir_fid = self.extract_url(task["shareurl"])
            stoken = execute_with_retry(lambda: self.get_stoken(pwd_id, passcode), "获取stoken")

            result_tree = Tree()
            mr = MagicRename(CONFIG_DATA.get("magic_regex", {}))
            mr.set_taskname(task["taskname"])
            
            root_save_path = re.sub(r"/{2,}", "/", f"/{task['savepath']}")
            print(f"▶️ 开始从分享根目录进行检查...")
            
            is_full_save = self._recursive_process_and_save(task, pwd_id, stoken, pdir_fid, root_save_path, result_tree, mr, is_root=True)

            if result_tree.size(1) > 0:
                notify_title = f"✅《{task['taskname']}》**{'完整保存' if is_full_save else '添加追更'}**："
                self.do_rename(result_tree)
                print()
                add_notify(f"{notify_title}\n{result_tree}")
                return result_tree
            else:
                print(f"任务结束：没有新的转存或保存任务")
                return False

        except Exception as e:
            add_notify(f"❌《{task['taskname']}》执行失败，发生致命错误: {e}")
            if isinstance(e, QuarkAPIError) and "访问次数" not in str(e) and "网络" not in str(e):
                task["shareurl_ban"] = str(e)
            return False

    # 【新增】检查文件是否是重复文件（如带有(1)、(2)等后缀的文件）
    def is_duplicate_file(self, filename):
        # 分离文件名和扩展名
        name_part, ext_part = os.path.splitext(filename)
        # 检查文件名是否包含(数字)模式，这通常是夸克网盘自动重命名的结果
        match = re.search(r"(.*?)\s*\((\d+)\)$", name_part)
        if match:
            # 提取原始文件名（没有数字后缀的部分）
            base_name = match.group(1).strip() + ext_part
            return True, base_name
        return False, filename

    def _recursive_process_and_save(self, task, pwd_id, stoken, share_pdir_fid, current_save_path, result_tree, mr: MagicRename, is_root=False):
        print(f"\n   🔍 [开始处理目录] '{current_save_path}'")
        print(f"   📝 [参数信息] pwd_id={pwd_id}, share_pdir_fid={share_pdir_fid}, is_root={is_root}")
        
        try:
            share_content_raw = execute_with_retry(lambda: self.get_detail(pwd_id, stoken, share_pdir_fid), f"获取分享内容 '{current_save_path}'")
            
            # 【新增】处理空分享链接的情况
            if not share_content_raw:
                print(f"   ⚠️ [分享内容为空] '{current_save_path}'")
                if is_root:
                    task["shareurl_ban"] = "分享为空，文件可能已被分享者删除"
                    add_notify(f"❌《{task['taskname']}》：{task['shareurl_ban']}")
                    return False
                else:
                    print(f"   ⏭️ [跳过空目录] '{current_save_path}'")
                    return False
                
            print(f"   ✅ [获取分享内容] '{current_save_path}' 成功，共 {len(share_content_raw)} 个项目")
        except Exception as e:
            print(f"   ❌ [获取分享内容失败] '{current_save_path}': {type(e).__name__}: {str(e)}")
            if is_root:
                task["shareurl_ban"] = f"获取分享内容失败: {str(e)}"
                add_notify(f"❌《{task['taskname']}》：{task['shareurl_ban']}")
            return False

        dir_info = execute_with_retry(lambda: self.mkdir(current_save_path), f"确保目录存在 '{current_save_path}'")
        target_dir_fid = dir_info['fid']
        self.savepath_fid[current_save_path] = target_dir_fid
        target_dir_content = execute_with_retry(lambda: self.ls_dir(target_dir_fid), f"列出目标目录 '{current_save_path}'")
        target_dir_filenames = [f["file_name"] for f in target_dir_content]
        print(f"   📂 [目标目录] '{current_save_path}' 包含 {len(target_dir_content)} 个文件/文件夹")

        # 【新增】创建一个映射表，将原始文件名（不含数字后缀）映射到目标目录中的实际文件名
        original_to_actual = {}
        for filename in target_dir_filenames:
            is_dup, base_name = self.is_duplicate_file(filename)
            original_to_actual[base_name] = original_to_actual.get(base_name, []) + [filename]

        is_full_save_mode = is_root and not target_dir_content
        if is_root:
            print(f"   ℹ️ [模式判断] 目标目录 '{current_save_path}' {'为空，判定为完整保存模式' if is_full_save_mode else '已有内容，判定为追更模式'}")

        # 获取全局正则配置
        global_regex_enabled = CONFIG_DATA.get("global_regex", {}).get("enabled", False)
        global_pattern = CONFIG_DATA.get("global_regex", {}).get("pattern", "")
        global_replace = CONFIG_DATA.get("global_regex", {}).get("replace", "")

        files_to_save = []
        for item in share_content_raw:
            item_name = item["file_name"]

            # 黑名单检查应该在所有处理之前进行
            if item_name in self.file_blacklist:
                print(f"   ⏭️ [黑名单] 跳过: '{current_save_path}/{item_name}'")
                continue
                
            # 【改进】检查是否为重复文件
            is_dup, base_name = self.is_duplicate_file(item_name)
            if is_dup:
                print(f"   ⏭️ [重复文件] 跳过: '{current_save_path}/{item_name}'，原始文件名为: '{base_name}'")
                continue
                
            # 【改进】检查原始文件是否已存在（包括可能带有数字后缀的版本）
            if base_name in original_to_actual:
                print(f"   ⏭️ [文件已存在] 跳过: '{current_save_path}/{item_name}'，已存在的文件: {original_to_actual[base_name]}")
                continue

            if item["dir"]:
                if task.get("update_subdir") and not re.search(task["update_subdir"], item_name):
                     print(f"   ⏭️ [目录不匹配] 跳过子目录: '{item_name}'")
                     continue
                
                # 【新增】处理子目录重存模式 (resave mode)
                is_dir_exists = item_name in target_dir_filenames
                if is_dir_exists and task.get("update_subdir_resave_mode"):
                    print(f"   🔄 [重存模式] 发现已存在目录 '{item_name}', 准备执行删除后重存。")
                    try:
                        # 找到旧目录的fid并删除
                        old_dir_info = next((f for f in target_dir_content if f["file_name"] == item_name), None)
                        if old_dir_info:
                            del_task_info = execute_with_retry(lambda: self.delete([old_dir_info["fid"]]), f"删除旧目录 '{item_name}'")
                            execute_with_retry(lambda: self.query_task(del_task_info["task_id"]), "查询删除任务状态")
                            
                            # 从回收站彻底删除
                            recycles = self.recycle_list()
                            record_to_remove = [r['record_id'] for r in recycles if r['fid'] == old_dir_info['fid']]
                            if record_to_remove:
                                execute_with_retry(lambda: self.recycle_remove(record_to_remove), f"从回收站清理 '{item_name}'")
                            print(f"   ✅ [重存模式] 旧目录 '{item_name}' 已彻底删除。")
                            
                            # 将其作为新文件加入待保存列表
                            item["file_name_re"] = item_name
                            files_to_save.append(item)
                    except Exception as e:
                        print(f"   ❌ [重存失败] 处理目录 '{item_name}' 失败: {e}")
                    continue # 不再递归进入

                print(f"   ▶️ [深入目录] 正在检查: '{current_save_path}/{item_name}'")
                self._recursive_process_and_save(task, pwd_id, stoken, item["fid"], f"{current_save_path}/{item_name}", result_tree, mr)
                continue

            # 初始化重命名后的文件名为原始文件名
            renamed_name = item_name
            applied_global_regex = False
            applied_task_regex = False

            # 应用全局正则（如果启用）
            if global_regex_enabled and global_pattern:
                if re.search(global_pattern, renamed_name):
                    global_renamed_name = re.sub(global_pattern, global_replace, renamed_name)
                    print(f"   🌐 [全局正则] '{renamed_name}' -> '{global_renamed_name}'")
                    renamed_name = global_renamed_name
                    applied_global_regex = True

            # 应用任务正则（如果存在）
            task_pattern, task_replace = mr.magic_regex_conv(task.get("pattern", ""), task.get("replace", ""))
            if task_pattern:
                if re.search(task_pattern, renamed_name):
                    task_renamed_name = mr.sub(task_pattern, task_replace, renamed_name)
                    print(f"   🔧 [任务正则] '{renamed_name}' -> '{task_renamed_name}'")
                    renamed_name = task_renamed_name
                    applied_task_regex = True

            # 【改进】检查重命名后的文件是否已存在
            is_dup_renamed, base_name_renamed = self.is_duplicate_file(renamed_name)
            if is_dup_renamed:
                print(f"   ⏭️ [重命名后为重复文件] 跳过: '{renamed_name}'，原始文件名为: '{base_name_renamed}'")
                continue
                
            if base_name_renamed in original_to_actual:
                print(f"   ⏭️ [重命名后文件已存在] 跳过: '{renamed_name}'，已存在的文件: {original_to_actual[base_name_renamed]}")
                continue

            # 检查是否应用了任何正则
            if applied_global_regex or applied_task_regex:
                original_exists = mr.is_exists(item_name, target_dir_filenames, task.get("ignore_extension"))
                renamed_exists = mr.is_exists(renamed_name, target_dir_filenames, task.get("ignore_extension"))
                if not original_exists and not renamed_exists:
                    print(f"   📥 [待处理] 添加到列表: '{item_name}' -> '{renamed_name}'")
                    item["file_name_re"] = renamed_name
                    files_to_save.append(item)
                else:
                    print(f"   ⏭️ [已存在] 跳过: '{item_name}' (本地存在: '{original_exists or renamed_exists}')")
            else:
                print(f"   ⏭️ [正则不匹配] 跳过: '{item_name}'")

        if files_to_save:
            # 处理文件名中的序号变量 {I}
            replace_for_i = ""
            if task.get("replace"):
                replace_for_i = mr.magic_regex_conv(task.get("pattern", ""), task.get("replace", ""))[1]
            elif global_regex_enabled and global_replace:
                replace_for_i = global_replace
                
            if replace_for_i and re.search(r"\{I+\}", replace_for_i):
                mr.set_dir_file_list(target_dir_content, replace_for_i)
                mr.sort_file_list(files_to_save)

            action_save = lambda: self.save_file([f["fid"] for f in files_to_save], [f["share_fid_token"] for f in files_to_save], target_dir_fid, pwd_id, stoken)
            query_ret = execute_with_retry(action_save, f"保存在 '{current_save_path}' 中的 {len(files_to_save)} 个文件")
            saved_fids = query_ret["save_as"]["save_as_top_fids"]

            if not result_tree.nodes:
                result_tree.create_node(current_save_path, target_dir_fid, data={"is_dir": True, "full_save": is_full_save_mode})
            parent_node_id = target_dir_fid
            if not result_tree.contains(parent_node_id):
                parent_path, dir_name = os.path.split(current_save_path)
                parent_dir_fid = self.savepath_fid.get(parent_path)
                parent_node_id_to_attach = parent_dir_fid if parent_dir_fid and result_tree.contains(parent_dir_fid) else result_tree.root
                result_tree.create_node(f"📁{dir_name}", parent_node_id, parent=parent_node_id_to_attach, data={"is_dir": True})

            for i, item in enumerate(files_to_save):
                new_fid = saved_fids[i]
                result_tree.create_node(f"{self._get_file_icon(item)}{item['file_name_re']}", new_fid, parent=parent_node_id,
                                        data={"file_name": item["file_name"], "file_name_re": item["file_name_re"], "fid": new_fid, "is_dir": False, "obj_category": item.get("obj_category", "")})
        return is_full_save_mode

    def do_rename(self, tree, node_id=None):
        if node_id is None: node_id = tree.root
        for child in list(tree.children(node_id)): self.do_rename(tree, child.identifier)
        node = tree.get_node(node_id)
        if not node or node.data.get("is_dir"): return
        file = node.data
        if file and file.get("file_name_re") and file["file_name_re"] != file["file_name"]:
            action = lambda: self.rename(file["fid"], file["file_name_re"])
            try:
                execute_with_retry(action, f"重命名 '{file['file_name']}'")
                print(f"重命名成功：'{file['file_name']}' → '{file['file_name_re']}'")
            except Exception as e:
                print(f"重命名失败：'{file['file_name']}' → '{file['file_name_re']}', 原因: {e}")

    def _get_file_icon(self, f):
        if f.get("dir"): return "📁"
        return {"video": "🎞️", "image": "🖼️", "audio": "🎵", "doc": "📄", "archive": "📦"}.get(f.get("obj_category"), "")

    def get_fids(self, path_list):
        """
        根据路径列表获取对应的文件ID
        :param path_list: 路径列表，如 ['/dir1', '/dir1/dir2']
        :return: 包含路径和文件ID的列表
        """
        result = []
        for path in path_list:
            if path in self.savepath_fid:
                result.append({"path": path, "fid": self.savepath_fid[path]})
                continue
                
            try:
                dir_info = execute_with_retry(
                    lambda: self.mkdir(path),
                    f"检查或创建目录 '{path}'"
                )
                self.savepath_fid[path] = dir_info['fid']
                result.append({"path": path, "fid": dir_info['fid']})
            except Exception as e:
                print(f"   ❌ [获取目录ID] 路径 '{path}' 处理失败: {e}")
                return None
                
        return result


def verify_account(account):
    try:
        action = lambda: account.init()
        execute_with_retry(action, f"验证账号 {account.index}")
        print(f"👤 账号昵称: {account.nickname}✅")
        return True
    except Exception as e:
        add_notify(f"👤 第{account.index}个账号登录失败: {e}❌")
        return False

# 【新增】每日签到函数
def do_sign(account):
    if not account.mparam:
        print(f"⏭️ 账号 {account.nickname} 移动端参数未设置，跳过签到")
        return
    print(f"▶️ 账号 {account.nickname} 开始执行每日签到...")
    try:
        growth_info = account.get_growth_info()
        if not growth_info:
            print(f"   ❌ 获取签到信息失败")
            return

        growth_message = (
            f"💾 {'88VIP' if growth_info.get('88VIP') else '普通用户'} "
            f"总空间：{format_bytes(growth_info.get('total_capacity', 0))}，"
            f"签到累计获得：{format_bytes(growth_info.get('cap_composition', {}).get('sign_reward', 0))}"
        )

        if growth_info.get("cap_sign", {}).get("sign_daily"):
            sign_progress = growth_info.get('cap_sign', {}).get('sign_progress', 0)
            sign_target = growth_info.get('cap_sign', {}).get('sign_target', 7)
            sign_message = f"   📅 今日已签到，连签进度({sign_progress}/{sign_target})✅"
            print(f"{sign_message}\n   {growth_message}")
        else:
            signed, reward = account.get_growth_sign()
            if signed:
                sign_progress = growth_info.get('cap_sign', {}).get('sign_progress', 0) + 1
                sign_target = growth_info.get('cap_sign', {}).get('sign_target', 7)
                reward_mb = int(reward) / 1024 / 1024 if isinstance(reward, int) else 0
                sign_message = f"   📅 执行签到成功! 获得 {reward_mb:.0f}MB 空间，连签进度({sign_progress}/{sign_target})✅"
                message_to_notify = f"[{account.nickname}] 签到成功: +{reward_mb:.0f}MB\n{growth_message}"
                print(f"{sign_message}\n   {growth_message}")

                push_sign_notify = str(CONFIG_DATA.get("push_config", {}).get("QUARK_SIGN_NOTIFY", "true")).lower() != "false" and \
                                   os.environ.get("QUARK_SIGN_NOTIFY", "true").lower() != "false"
                if push_sign_notify:
                    add_notify(message_to_notify)
            else:
                print(f"   ❌ 签到失败: {reward}")
    except Exception as e:
        print(f"   ❌ 签到时发生异常: {e}")

def do_save(account, tasklist=[]):
    print(f"🧩 载入插件...")
    plugins, _, _ = Config.load_plugins(CONFIG_DATA.get("plugins", {}))
    print(f"转存账号: {account.nickname}")
    try:
        account.update_savepath_fid(tasklist)
    except Exception as e:
        print(f"❌ 更新保存路径失败，任务中止: {e}")
        return

    def is_time(task):
        return (not task.get("enddate") or datetime.now().date() <= datetime.strptime(task["enddate"], "%Y-%m-%d").date()) and \
               ("runweek" not in task or datetime.today().weekday() + 1 in task.get("runweek"))

    for index, task in enumerate(tasklist):
        print(f"\n#{index+1}------------------\n任务名称: {task['taskname']}\n分享链接: {task['shareurl']}\n保存路径: {task['savepath']}")
        if task.get("pattern"): print(f"正则匹配: {task['pattern']}")
        if task.get("replace"): print(f"正则替换: {task['replace']}")
        if task.get("update_subdir"): print(f"更子目录: {task['update_subdir']}")
        print()

        if not is_time(task):
            print(f"任务不在运行周期内，跳过")
            continue
        
        is_new_tree = account.do_save_task(task)

        if is_new_tree:
            print(f"🧩 调用插件")
            for plugin_name, plugin in plugins.items():
                if plugin.is_active:
                    task = plugin.run(task, account=account, tree=is_new_tree) or task

def main():
    global CONFIG_DATA
    start_time = datetime.now()
    print(f"===============程序开始===============\n⏰ 执行时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
    config_path = sys.argv[1] if len(sys.argv) > 1 else "quark_config.json"
    
    if os.environ.get("QUARK_TEST", "").lower() == "true":
        print("===============通知测试===============")
        CONFIG_DATA["push_config"] = json.loads(os.environ.get("PUSH_CONFIG", "{}"))
        send_ql_notify("【夸克自动转存】", f"通知测试\n\n{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return

    tasklist_from_env = []
    if tasklist_json := os.environ.get("TASKLIST"):
        try: tasklist_from_env = json.loads(tasklist_json)
        except Exception as e: print(f"从环境变量解析任务列表失败 {e}")

    cookie_form_file = True
    if not os.path.exists(config_path):
        if os.environ.get("QUARK_COOKIE"):
            print(f"⚙️ 读取到 QUARK_COOKIE 环境变量，将仅执行签到。")
            cookie_val = os.environ.get("QUARK_COOKIE")
            cookie_form_file = False
        else:
            print(f"⚙️ 配置文件 {config_path} 不存在，正远程下载配置模版...")
            if Config.download_file(f"{GH_PROXY}https://raw.githubusercontent.com/Cp0204/quark_auto_save/main/quark_config.json", config_path):
                print("⚙️ 配置模版下载成功，请到程序目录中手动配置")
            return
    else:
        print(f"⚙️ 正从 {config_path} 文件中读取配置")
        CONFIG_DATA = Config.read_json(config_path)
        Config.breaking_change_update(CONFIG_DATA)
        cookie_val = CONFIG_DATA.get("cookie")

    cookies = Config.get_cookies(cookie_val)
    if not cookies:
        print("❌ cookie 未配置")
        return
    
    accounts = [Quark(cookie, index, blacklist=CONFIG_DATA.get("file_blacklist", [])) for index, cookie in enumerate(cookies)]

    print("\n===============账号验证与签到===============")
    valid_accounts = []
    for acc in accounts:
        if verify_account(acc):
            valid_accounts.append(acc)
            # 【新增】对每个验证通过的账号执行签到
            do_sign(acc)
        print()

    if not valid_accounts:
        print("无有效账号，程序退出。")
        return

    # 【优化】即使从环境变量读取任务，也使用文件中的配置
    if cookie_form_file or tasklist_from_env:
        print("\n===============转存任务===============")
        # 你的原逻辑是只用第一个有效账号执行转存，这里予以保留
        do_save(valid_accounts[0], tasklist_from_env or CONFIG_DATA.get("tasklist", []))

    if NOTIFYS:
        print("\n===============推送通知===============")
        send_ql_notify("【夸克自动转存】", "\n".join(NOTIFYS))
    
    if cookie_form_file:
        Config.write_json(config_path, CONFIG_DATA)

    duration = datetime.now() - start_time
    print(f"\n===============程序结束===============\n😃 运行时长: {round(duration.total_seconds(), 2)}s\n")


if __name__ == "__main__":
    main()
