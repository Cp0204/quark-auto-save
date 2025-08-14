import requests
import json
import hashlib
import random
import time
from urllib.parse import urlencode, urlsplit
from typing import Any, Optional


class Fnv:
    default_config = {
        "base_url": "http://10.0.0.6:5666",  # 飞牛影视服务器URL
        "app_name": "trimemedia-web",  # 飞牛影视应用名称
        "username": "",  # 飞牛影视用户名
        "password": "",  # 飞牛影视密码
        "secret_string": "",  # 飞牛影视密钥字符串
        "api_key": "",  # 飞牛影视API密钥
    }

    default_task_config = {
        "auto_refresh": False,  # 是否自动刷新媒体库
        "mdb_name": "",  # 飞牛影视目标媒体库名称
    }

    is_active = False
    token = None
    session = requests.Session()

    def __init__(self, **kwargs):
        self.plugin_name = self.__class__.__name__.lower()
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
            self.is_active = self._check_config()

    def _check_config(self):
        """检查配置是否完整"""
        missing_keys = [
            key for key in self.default_config if not getattr(self, key, None)
        ]
        if missing_keys:
            print(f"{self.plugin_name} 模块缺少必要参数: {', '.join(missing_keys)}")
            return False
        return True

    def run(self, task, **kwargs):
        """插件运行主入口"""
        if not self.is_active:
            return
        task_config = task.get("addition", {}).get(
            self.plugin_name, self.default_task_config
        )
        if not task_config.get("auto_refresh"):
            print("飞牛影视: 自动刷新未启用，跳过处理")
            return
        if not task_config.get("mdb_name"):
            print("飞牛影视: 未指定媒体库名称，跳过处理")
            return
        target_library_name = task_config["mdb_name"]
        app_name = self.app_name or self.default_config["app_name"]
        username = self.username or self.default_config["username"]
        password = self.password or self.default_config["password"]

        # 登录并执行后续操作
        if self._login(username=username, password=password, app_name=app_name):

            # 登录成功后，获取媒体库ID
            library_id = self._get_library_id(target_library_name)

            if library_id:
                # 获取ID成功后，刷新该媒体库
                self._refresh_library(library_id)

    def _make_request(self, method: str, rel_url: str, params: dict = None, data: dict = None) -> Optional[Any]:
        """
        一个统一的私有方法，用于发送所有API请求。
        它会自动处理签名、请求头、错误和响应解析。
        """
        url = f"{self.base_url.rstrip('/')}{rel_url}"

        # 1. 生成认证签名
        authx = self._cse_sign(method, rel_url, params, data)
        if not authx:
            print(f"飞牛影视: 为 {rel_url} 生成签名失败，请求中止。")
            return None

        # 2. 准备请求头
        headers = {
            "Content-Type": "application/json",
            "authx": authx,
        }
        if self.token:
            headers["Authorization"] = self.token

        # 3. 发送请求并处理异常
        try:
            response = self.session.request(
                method,
                url,
                headers=headers,
                params=params,
                json=data if data is not None else {}
            )
            response.raise_for_status()  # 对 4xx/5xx 响应抛出异常
            response_data = response.json()
        except requests.exceptions.RequestException as e:
            print(f"飞牛影视: 请求 {url} 时出错: {e}")
            return None
        except json.JSONDecodeError:
            print(f"飞牛影视: 解析来自 {url} 的响应失败，内容非JSON格式。")
            return None

        # 4. 统一处理API层面的错误
        if response_data.get("code") != 0:
            msg = response_data.get('msg', '未知错误')
            print(f"飞牛影视: API调用失败 ({rel_url}): {msg}")

        # 5. 返回响应
        return response_data

    def _login(self, username: str, password: str, app_name: str) -> bool:
        """
        登录到飞牛影视服务器并获取认证token。
        成功后，token会自动存储在客户端实例中。
        """
        print("飞牛影视: 正在尝试登录...")
        rel_url = "/v/api/v1/login"
        payload = {
            "username": username,
            "password": password,
            "app_name": app_name,
        }
        response_json = self._make_request('post', rel_url, data=payload)

        if response_json and response_json["data"]["token"]:
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
        rel_url = "/v/api/v1/mdb/list"
        response_json = self._make_request('get', rel_url)

        if response_json and response_json.get("data"):
            libraries = response_json.get("data", [])
            for library in libraries:
                if library.get("name") == library_name:
                    print(f"飞牛影视: 找到目标媒体库 ✅，ID: {library.get('guid')}")
                    return library.get("guid")
            print(f"飞牛影视: 未在媒体库列表中找到名为 '{library_name}' 的媒体库 ❌")
        return None

    def _refresh_library(self, library_id: str) -> bool:
        """
        根据给定的媒体库ID触发一次媒体库扫描/刷新。
        """
        if not self.token:
            print("飞牛影视: 必须先登录才能刷新媒体库。")
            return False

        print(f"飞牛影视: 正在为媒体库 {library_id} 发送刷新指令...")
        rel_url = f"/v/api/v1/mdb/scan/{library_id}"
        response_json = self._make_request('post', rel_url, data={})  # POST一个空对象
        response_code = response_json.get("code")
        if 0 == response_code:
            print(f"飞牛影视: 发送刷新指令成功 ✅")
            return True
        elif response_code == -14:
            if self._stop_refresh_task(library_id):
                print(f"飞牛影视: 发现重复任务，已停止旧任务，重新发送刷新指令...")
                response_json = self._make_request('post', rel_url, data={})  # POST一个空对象
                response_code = response_json.get("code")
                if 0 == response_code:
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
        rel_url = f"/v/api/v1/task/stop"
        response_json = self._make_request('post', rel_url, data={
            "guid": library_id,
            "type": "TaskItemScrap"
        })
        response_code = response_json.get("code")
        if 0 == response_code:
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

        if method.lower() == 'get' and params:
            serialized_str = urlencode(sorted(params.items()))
        else:
            # 对于POST/PUT等方法，序列化body
            serialized_str = self._serialize_data(data)
        body_hash = self._md5_hash(serialized_str)

        string_to_sign_parts = [
            self.secret_string,
            path,
            nonce,
            timestamp,
            body_hash,
            self.api_key
        ]
        string_to_sign = "_".join(string_to_sign_parts)
        final_sign = self._md5_hash(string_to_sign)

        return f"nonce={nonce}&timestamp={timestamp}&sign={final_sign}"

    @staticmethod
    def _md5_hash(s: str) -> str:
        """计算并返回字符串的小写 MD5 哈希值。"""
        return hashlib.md5(s.encode('utf-8')).hexdigest()

    @staticmethod
    def _serialize_data(data: Any) -> str:
        """
        将请求体数据序列化为紧凑的JSON字符串。
        - 字典 (包括`{}`) 会被正确序列化为JSON字符串 (例如 `"{}"`)。
        - 字符串会原样返回。
        - 其他`None`或falsy值会返回空字符串`""`。
        """
        # 优先检查dict，因为空字典`{}`在Python中为falsy。
        # 这样可以确保 `{}` 被正确序列化为字符串 `"{}"`。
        if isinstance(data, dict):
            return json.dumps(data, sort_keys=True, separators=(',', ':'))
        if isinstance(data, str):
            return data
            # 对于其他 falsy 值（如 None, []），返回空字符串。
        if not data:
            return ""
            # 其他情况（虽然少见），返回空字符串以保证安全。
        return ""
