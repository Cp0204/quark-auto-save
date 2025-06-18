# plugins: 调用 alist 实现跨网盘转存
# author: https://github.com/jenfonro

import re
import json
import requests


class Alist_sync:

    default_config = {
        "url": "",  # Alist服务器URL
        "token": "",  # Alist服务器Token
        "quark_storage_id": "",  # Alist 服务器夸克存储 ID
        "save_storage_id": "",  # Alist 服务器同步的存储 ID
        "tv_mode": "",  # TV库模式，填入非0值开启
        # TV库模式说明：
        # 1.开启后，会验证文件名是否包含S01E01的正则，格式目前仅包含mp4及mkv，
        # 2.会对比保存目录下是否存在该名称的mp4、mkv文件，如果不存在才会进行同步
        # 3.夸克目录及同步目录均会提取为S01E01的正则进行匹配，不受其它字符影响
    }
    is_active = False
    # 缓存参数

    default_task_config = {
        "enable": False,  # 当前任务开关，
        "save_path": "",  # 需要同步目录，默认空时路径则会与夸克的保存路径一致，不开启完整路径模式时，默认根目录为保存驱动的根目录
        "verify_path": "",  # 验证目录，主要用于影视库避免重复文件，一般配合alist的别名功能及full_path使用，用于多个网盘的源合并成一个目录
        "full_path": False,  # 完整路径模式
        # 完整路径模式开启后不再限制保存目录的存储驱动，将根据填入的路径进行保存，需要填写完整的alist目录
    }

    def __init__(self, **kwargs):
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.__class__.__name__} 模块缺少必要参数: {key}")
            if self.url and self.token:
                if self.verify_server():
                    self.is_active = True

    def _send_request(self, method, url, **kwargs):
        headers = {"Authorization": self.token, "Content-Type": "application/json"}
        if "headers" in kwargs:
            headers = kwargs["headers"]
            del kwargs["headers"]
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

    def verify_server(self):
        url = f"{self.url}/api/me"
        querystring = ""
        headers = {"Authorization": self.token, "Content-Type": "application/json"}
        try:
            response = requests.request("GET", url, headers=headers, params=querystring)
            response.raise_for_status()
            response = response.json()
            if response.get("code") == 200:
                if response.get("data").get("username") == "guest":
                    print(f"Alist登陆失败，请检查token")
                else:
                    print(
                        f"Alist登陆成功，当前用户: {response.get('data').get('username')}"
                    )
                    return True
            else:
                print(f"Alist同步: 连接服务器失败❌ {response.get('message')}")
        except requests.exceptions.RequestException as e:
            print(f"获取Alist信息出错: {e}")
        return False

    def run(self, task, **kwargs):
        if not task["addition"]["alist_sync"]["enable"]:
            return 0
        print(f"开始进行alist同步")
        # 这一块注释的是获取任务的参数，在web界面可以看
        # print("所有任务参数：")
        # print(task)
        # print(task['addition'])
        # print(task['addition']['alist_sync'])
        # print(task['addition']['alist_sync']['target_path'])

        # 获取夸克挂载根目录
        data = self.get_storage_path(self.quark_storage_id)
        if data["driver"] != "Quark":
            print(
                f"Alist同步: 存储{self.quark_storage_id}非夸克存储❌ {data["driver"]}"
            )
            return 0
        quark_mount_root_path = re.sub(r".*root_folder_id\":\"", "", data["addition"])
        quark_mount_root_path = re.sub(r"\",.*", "", quark_mount_root_path)
        if quark_mount_root_path != "0" and quark_mount_root_path != "":
            print(f"Alist同步: 存储{self.quark_storage_id}挂载的目录非夸克根目录❌")
            return 0
        self.quark_mount_path = data["mount_path"]

        # 获取保存路径的挂载根目录
        if self.save_storage_id != 0:
            data = self.get_storage_path(self.save_storage_id)
            self.save_mount_path = data["mount_path"]

        # 保存的目录初始化
        if task["addition"]["alist_sync"]["save_path"] == "":
            self.save_path = f"{self.save_mount_path}/{task['savepath']}"
        else:
            self.save_path = task["addition"]["alist_sync"]["save_path"]
            if not task["addition"]["alist_sync"]["full_path"]:
                if self.save_path.startswith("/"):
                    self.save_path = self.save_path[1:]
                if self.save_path.endswith("/"):
                    self.save_path = self.save_path[:-1]
                self.save_path = f"{self.save_mount_path}/{self.save_path}"
            else:
                # print('完整路径模式')
                if not self.save_path.startswith("/"):
                    self.save_path = "/" + self.save_path
                if self.save_path.endswith("/"):
                    self.save_path = self.save_path[:-1]

        # 获取保存目录是否存在
        if not self.get_path(self.save_path):
            dir_exists = False
            # 如果目录不存在判断两边路径是否一致，一致时直接创建复制目录任务即可

        else:
            dir_exists = True
            copy_dir = False

        # 初始化验证目录
        # 如果没有填验证目录，则验证目录与保存目录一致

        if task["addition"]["alist_sync"]["verify_path"]:
            self.verify_path = task["addition"]["alist_sync"]["verify_path"]
            if not task["addition"]["alist_sync"]["full_path"]:
                if self.verify_path.startswith("/"):
                    self.verify_path = self.save_path[1:]
                if self.verify_path.endswith("/"):
                    self.verify_path = self.save_path[:-1]
                self.verify_path = f"{self.save_mount_path}/{self.verify_path}"
            else:
                # print('完整路径模式')
                if not self.verify_path.startswith("/"):
                    self.verify_path = "/" + self.save_path
                if self.verify_path.endswith("/"):
                    self.verify_path = self.save_path[:-1]
        else:
            self.verify_path = self.save_path

        # 初始化夸克目录
        self.source_path = f"{self.quark_mount_path}/{task['savepath']}"
        # 初始化任务名
        self.taskname = f"{task['taskname']}"

        # 获取网盘已有文件
        source_dir_list = self.get_path_list(self.source_path)

        if self.TV_mode == 0 or self.TV_mode == "":
            self.tv_mode = False
        else:
            self.tv_mode = True

        # 如果是新建的目录则将所有文件直接复制
        if not dir_exists:
            self.get_save_file([], source_dir_list)
        else:
            verify_dir_list = self.get_path_list(self.verify_path)
            if verify_dir_list:
                self.get_save_file(verify_dir_list, source_dir_list)
            else:
                self.get_save_file([], source_dir_list)

        if self.save_file_data:
            self.save_start(self.save_file_data)
            print("同步的文件列表：")
            for save_file in self.save_file_data:
                print(f"└── 🎞️{save_file}")
        else:
            print("没有需要同步的文件")

    def save_start(self, save_file_data):
        url = f"{self.url}/api/fs/copy"
        payload = json.dumps(
            {
                "src_dir": self.source_path,
                "dst_dir": self.save_path,
                "names": save_file_data,
            }
        )
        response = self._send_request("POST", url, data=payload)
        if response.status_code != 200:
            print("未能进行Alist同步，请手动同步")
        else:
            print("Alist创建任务成功")
        self.copy_task = response.json()

    def get_save_file(self, target_dir_list, source_dir_list):
        self.save_file_data = []
        if target_dir_list == []:
            for source_list in source_dir_list:
                if self.tv_mode:
                    if re.search(
                        self.taskname + r"\.s\d{1,3}e\d{1,3}\.(mkv|mp4)",
                        source_list["name"],
                        re.IGNORECASE,
                    ):
                        self.save_file_data.append(source_list["name"])
                else:
                    self.save_file_data.append(source_list["name"])
        else:
            for source_list in source_dir_list:
                skip = False
                source_list_filename = (
                    source_list["name"]
                    .replace(".mp4", "")
                    .replace(".mkv", "")
                    .replace(self.taskname + ".", "")
                    .lower()
                )
                for target_list in target_dir_list:
                    if self.tv_mode:
                        target_list_filename = (
                            target_list["name"]
                            .replace(".mp4", "")
                            .replace(".mkv", "")
                            .replace(self.taskname + ".", "")
                            .lower()
                        )
                        if source_list_filename == target_list_filename:
                            # print(f"文件存在，名称为：{target_list['name']}")
                            skip = True
                            break
                    else:
                        if source_list["name"] == target_list["name"]:
                            # print(f"文件存在，名称为：{target_dir['name']}")
                            skip = True
                            break
                    if self.tv_mode:
                        if re.search(
                            self.taskname + r"\.s\d{1,3}e\d{1,3}\.(mkv|mp4)",
                            source_list["name"],
                            re.IGNORECASE,
                        ):
                            # 添加一句验证，如果有MKV，MP4存在时，则只保存某一个格式
                            if re.search(
                                self.taskname + r"\.s\d{1,3}e\d{1,3}\.mp4",
                                source_list["name"],
                                re.IGNORECASE,
                            ):
                                for all_file in source_dir_list:
                                    if (
                                        source_list["name"].replace(".mp4", ".mkv")
                                        == all_file["name"]
                                    ):
                                        print(
                                            f"{source_list['name']}拥有相同版本的MKV文件，跳过复制"
                                        )
                                        skip = True
                if not skip:
                    self.save_file_data.append(source_list["name"])

    def get_path_list(self, path):
        url = f"{self.url}/api/fs/list"
        payload = json.dumps(
            {"path": path, "password": "", "page": 1, "per_page": 0, "refresh": True}
        )
        response = self._send_request("POST", url, data=payload)
        if response.status_code != 200:
            print(f"获取Alist目录出错: {response}")
            return False
        else:
            return response.json()["data"]["content"]

    def get_path(self, path):
        url = f"{self.url}/api/fs/list"
        payload = json.dumps({"path": path, "password": "", "force_root": False})
        response = self._send_request("POST", url, data=payload)
        if response.status_code != 200 or response.json()["message"] != "success":
            return False
        else:
            return True

    def get_storage_path(self, storage_id):
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
                print(f"Alist同步: 存储{storage_id}连接失败❌ {data.get('message')}")
        except Exception as e:
            print(f"Alist同步: 获取Alist存储出错 {e}")
        return []
