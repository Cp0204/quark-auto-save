import requests
import json
import hashlib
import random
import time
from urllib.parse import urlencode
from typing import Any, Optional


# 飞牛影视插件
# 该插件用于与飞牛影视服务器API交互，支持自动刷新媒体库
# 通过配置用户名、密码和密钥字符串进行认证，并提供媒体库扫描功能
class Fnv:
    # --- 配置信息 ---
    default_config = {
        "base_url": "http://10.0.0.6:5666",  # 飞牛影视服务器URL
        "app_name": "trimemedia-web",  # 飞牛影视应用名称
        "username": "",  # 飞牛影视用户名
        "password": "",  # 飞牛影视密码
        "secret_string": "",  # 飞牛影视密钥字符串
        "api_key": "",  # 飞牛影视API密钥
        "token": None,  # 飞牛影视认证Token (可选)
    }

    default_task_config = {
        "auto_refresh": False,  # 是否自动刷新媒体库
        "mdb_name": "",  # 飞牛影视目标媒体库名称
        "mdb_dir_list": "",  # 飞牛影视目标媒体库文件夹路径列表，多个用逗号分隔
    }

    # 定义一个可选键的集合
    OPTIONAL_KEYS = {"token"}

    # --- API 端点常量 ---
    API_LOGIN = "/v/api/v1/login"  # 登录端点
    API_MDB_LIST = "/v/api/v1/mdb/list"  # 获取媒体库列表
    API_MDB_SCAN = "/v/api/v1/mdb/scan/{}"  # 刷新媒体库端点 ({}为媒体库ID)
    API_TASK_STOP = "/v/api/v1/task/stop"  # 停止任务端点

    # --- 实例状态 ---
    is_active = False
    session = requests.Session()
    token = None

    # =====================================================================
    # Public Methods / Entry Points (公共方法/入口)
    # =====================================================================

    def __init__(self, **kwargs):
        """
        初始化 Fnv 客户端。
        """
        self.plugin_name = self.__class__.__name__.lower()
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
            # 检查配置并尝试登录，以确定插件是否激活
            if self._check_config():
                if self.token is None or self.token == "":
                    self._login()
                self.is_active = self.token is not None or self.token != ""
                if self.is_active:
                    print(f"{self.plugin_name}: 插件已激活 ✅")
                else:
                    print(f"{self.plugin_name}: 插件未激活 ❌")

    def run(self, task, **kwargs):
        """
        插件运行主入口。
        根据任务配置，执行媒体库刷新操作。
        """
        if not self.is_active:
            print(f"飞牛影视: 插件未激活，跳过任务。")
            return

        task_config = task.get("addition", {}).get(
            self.plugin_name, self.default_task_config
        )
        if not task_config.get("auto_refresh"):
            print("飞牛影视: 自动刷新未启用，跳过处理。")
            return

        target_library_name = task_config.get("mdb_name")
        if not target_library_name:
            print("飞牛影视: 未指定媒体库名称，跳过处理。")
            return
        target_library_mdb_dir_list = task_config.get("mdb_dir_list")
        dir_list = []
        if target_library_mdb_dir_list:
            dir_list = [dir_path.strip() for dir_path in target_library_mdb_dir_list.split(",") if dir_path.strip()]

        # 获取媒体库ID
        library_id = self._get_library_id(target_library_name)

        if library_id:
            # 获取ID成功后，刷新该媒体库
            self._refresh_library(library_id, dir_list=dir_list)

    # =====================================================================
    # Internal Methods (内部实现方法)
    # =====================================================================

    def _check_config(self):
        """检查配置是否完整"""
        missing_keys = [
            key for key in self.default_config
            if key not in self.OPTIONAL_KEYS and not getattr(self, key, None)
        ]
        if missing_keys:
            # print(f"{self.plugin_name} 模块缺少必要参数: {', '.join(missing_keys)}")
            return False
        return True

    def _make_request(self, method: str, rel_url: str, params: dict = None, data: dict = None) -> Optional[Any]:
        """
        一个统一的私有方法，用于发送所有API请求。
        它会自动处理签名、请求头、错误和响应解析。
        当认证失败时，会自动尝试重新登录并重试，最多3次。
        """
        max_retries = 3
        for attempt in range(max_retries):
            url = f"{self.base_url.rstrip('/')}{rel_url}"

            authx = self._cse_sign(method, rel_url, params, data)
            if not authx:
                print(f"飞牛影视: 为 {rel_url} 生成签名失败，请求中止。")
                return None

            headers = {
                "Content-Type": "application/json",
                "authx": authx,
            }
            if self.token:
                headers["Authorization"] = self.token

            try:
                response = self.session.request(
                    method, url, headers=headers, params=params,
                    data=self._serialize_data(data if data is not None else {})
                )
                response.raise_for_status()
                response_data = response.json()
            except requests.exceptions.RequestException as e:
                print(f"飞牛影视: 请求 {url} 时出错: {e}")
                return None
            except json.JSONDecodeError:
                print(f"飞牛影视: 解析来自 {url} 的响应失败，内容非JSON格式。")
                return None

            response_code = response_data.get("code")
            if response_code is None:
                print(f"飞牛影视: 响应格式错误，未找到 'code' 字段。")
                return None

            if response_code == 0:
                return response_data

            if response_code == -2:
                print(f"飞牛影视: 认证失败 (尝试 {attempt + 1}/{max_retries})，尝试重新登录...")
                if rel_url == self.API_LOGIN:
                    print("飞牛影视: 登录接口认证失败，请检查用户名和密码。")
                    return response_data
                if not self._login():
                    print("飞牛影视: 重新登录失败，无法继续请求。")
                    return None
                continue
            else:
                msg = response_data.get('msg', '未知错误')
                print(f"飞牛影视: API调用失败 ({rel_url}): {msg}")
                return response_data

        print(f"飞牛影视: 请求 {rel_url} 在尝试 {max_retries} 次后仍然失败。")
        return None

    def _login(self) -> bool:
        """
        登录到飞牛影视服务器并获取认证token。
        """
        app_name = self.app_name or self.default_config["app_name"]
        username = self.username or self.default_config["username"]
        password = self.password or self.default_config["password"]
        print("飞牛影视: 正在尝试登录...")

        payload = {"username": username, "password": password, "app_name": app_name}
        response_json = self._make_request('post', self.API_LOGIN, data=payload)

        if response_json and response_json.get("data", {}).get("token"):
            self.token = response_json["data"]["token"]
            print("飞牛影视: 登录成功 ✅")
            return True
        else:
            print("飞牛影视: 登录失败 ❌")
            return False

    def _get_library_id(self, library_name: str) -> Optional[str]:
        """
        根据媒体库的名称获取其唯一ID (guid)。
        """
        if not self.token:
            print("飞牛影视: 必须先登录才能获取媒体库列表。")
            return None

        print(f"飞牛影视: 正在查找媒体库 '{library_name}'...")
        response_json = self._make_request('get', self.API_MDB_LIST)

        if response_json and response_json.get("data"):
            for library in response_json.get("data", []):
                if library.get("name") == library_name:
                    print(f"飞牛影视: 找到目标媒体库 ✅，ID: {library.get('guid')}")
                    return library.get("guid")
            print(f"飞牛影视: 未在媒体库列表中找到名为 '{library_name}' 的媒体库 ❌")
        return None

    def _refresh_library(self, library_id: str, dir_list: list[str] = None) -> bool:
        """
        根据给定的媒体库ID触发一次媒体库扫描/刷新。
        """
        if not self.token:
            print("飞牛影视: 必须先登录才能刷新媒体库。")
            return False

        if dir_list:
            print(f"飞牛影视: 正在为媒体库 {library_id} 发送部分目录{dir_list}刷新指令...")
        else:
            print(f"飞牛影视: 正在为媒体库 {library_id} 发送刷新指令...")
        rel_url = self.API_MDB_SCAN.format(library_id)
        request_body = {"dir_list": dir_list} if dir_list else {}
        response_json = self._make_request('post', rel_url, data=request_body)

        if not response_json: return False

        response_code = response_json.get("code")
        if response_code == 0:
            print(f"飞牛影视: 发送刷新指令成功 ✅")
            return True
        elif response_code == -14:
            if self._stop_refresh_task(library_id):
                print(f"飞牛影视: 发现重复任务，已停止旧任务，重新发送刷新指令...")
                response_json = self._make_request('post', rel_url, data={})
                if response_json and response_json.get("code") == 0:
                    print(f"飞牛影视: 发送刷新指令成功 ✅")
                    return True
                else:
                    print(f"飞牛影视: 重新发送刷新指令失败 ❌")
            else:
                print(f"飞牛影视: 停止旧任务失败，无法继续刷新操作 ❌")
        return False

    def _stop_refresh_task(self, library_id: str) -> bool:
        """
        停止指定的媒体库刷新任务。
        """
        if not self.token:
            print("飞牛影视: 必须先登录才能停止刷新任务。")
            return False

        print(f"飞牛影视: 正在停止媒体库刷新任务 {library_id}...")
        payload = {"guid": library_id, "type": "TaskItemScrap"}
        response_json = self._make_request('post', self.API_TASK_STOP, data=payload)

        if response_json and response_json.get("code") == 0:
            print(f"飞牛影视: 停止刷新任务成功 ✅")
            return True
        else:
            print(f"飞牛影视: 停止刷新任务失败 ❌")
            return False

    def _cse_sign(self, method: str, path: str, params: dict = None, data: dict = None) -> str:
        """
        为API请求生成 cse 签名参数字符串。
        """
        nonce = str(random.randint(100000, 999999))
        timestamp = str(int(time.time() * 1000))

        serialized_str = ""
        if method.lower() == 'get':
            if params:
                serialized_str = urlencode(sorted(params.items()))
        else:
            serialized_str = self._serialize_data(data)
        body_hash = self._md5_hash(serialized_str)

        string_to_sign_parts = [
            self.secret_string, path, nonce, timestamp, body_hash, self.api_key
        ]
        string_to_sign = "_".join(string_to_sign_parts)
        final_sign = self._md5_hash(string_to_sign)

        return f"nonce={nonce}&timestamp={timestamp}&sign={final_sign}"

    # =====================================================================
    # Static Utility Methods (静态工具方法)
    # =====================================================================

    @staticmethod
    def _md5_hash(s: str) -> str:
        """计算并返回字符串的小写 MD5 哈希值。"""
        return hashlib.md5(s.encode('utf-8')).hexdigest()

    @staticmethod
    def _serialize_data(data: Any) -> str:
        """
        将请求体数据序列化为紧凑的JSON字符串。
        """
        if isinstance(data, dict):
            return json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        if isinstance(data, str):
            return data
        if not data:
            return ""
        return ""
