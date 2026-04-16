import os
import re
import json
import requests
import time


class Alist:

    default_config = {
        "url": "",  # Alist服务器URL
        "token": "",  # Alist服务器Token
        "storage_id": "",  # Alist 服务器夸克存储 ID
    }
    is_active = False
    # 缓存参数
    storage_mount_path = None
    quark_root_dir = None
    # 多账号支持
    storage_mount_paths = []
    quark_root_dirs = []

    def __init__(self, **kwargs):
        """初始化AList插件"""
        # 标记插件名称，便于日志识别
        self.plugin_name = self.__class__.__name__.lower()
        
        if kwargs:
            # 加载配置
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    pass  # 不显示缺少参数的提示

            # 处理多账号配置：支持数组形式的storage_id
            if isinstance(self.storage_id, list):
                self.storage_ids = self.storage_id
                # 为了向后兼容，使用第一个ID作为默认值
                self.storage_id = self.storage_ids[0] if self.storage_ids else ""
            else:
                # 单一配置转换为数组格式
                self.storage_ids = [self.storage_id] if self.storage_id else []

            # 检查基本配置
            if not self.url or not self.token or not self.storage_id:
                return
                
            # 确保URL格式正确
            if not self.url.startswith(("http://", "https://")):
                self.url = f"http://{self.url}"
            
            # 移除URL末尾的斜杠
            self.url = self.url.rstrip("/")
            
            # 验证AList连接
            if self.get_info():
                # 解析所有存储ID
                for i, storage_id in enumerate(self.storage_ids):
                    success, result = self.storage_id_to_path(storage_id)
                    if success:
                        mount_path, root_dir = result

                        # 确保路径格式正确
                        if root_dir != "/":
                            if not root_dir.startswith("/"):
                                root_dir = f"/{root_dir}"
                            root_dir = root_dir.rstrip("/")

                        if not mount_path.startswith("/"):
                            mount_path = f"/{mount_path}"
                        mount_path = mount_path.rstrip("/")

                        self.storage_mount_paths.append(mount_path)
                        self.quark_root_dirs.append(root_dir)

                        if i == 0:
                            # 设置默认值（向后兼容）
                            self.storage_mount_path = mount_path
                            self.quark_root_dir = root_dir


                    else:
                        print(f"AList 刷新: 存储ID [{i}] {storage_id} 解析失败")
                        # 添加空值保持索引对应
                        self.storage_mount_paths.append("")
                        self.quark_root_dirs.append("")

                # 只要有一个存储ID解析成功就激活插件
                if any(self.storage_mount_paths):
                    self.is_active = True
                else:
                    print(f"AList 刷新: 所有存储ID解析失败")
            else:
                print(f"AList 刷新: 服务器连接失败")

    def get_storage_config(self, account_index=0):
        """根据账号索引获取对应的存储配置"""
        if account_index < len(self.storage_mount_paths) and account_index < len(self.quark_root_dirs):
            return self.storage_mount_paths[account_index], self.quark_root_dirs[account_index]
        else:
            # 如果索引超出范围，使用第一个配置作为默认值
            if self.storage_mount_paths and self.quark_root_dirs:
                return self.storage_mount_paths[0], self.quark_root_dirs[0]
            else:
                return "", ""

    def run(self, task, **kwargs):
        """
        插件主入口，当有新文件保存时触发刷新AList目录
        
        Args:
            task: 任务信息，包含savepath等关键信息
            **kwargs: 其他参数，包括tree和rename_logs
            
        Returns:
            task: 返回原任务信息
        """
        # 检查路径是否在夸克根目录内
        if task.get("savepath"):
            # 确保路径符合要求
            quark_path = task.get("savepath", "")
            if not quark_path.startswith("/"):
                quark_path = f"/{quark_path}"
            
            # 检查路径是否在夸克根目录下（目录边界匹配，避免 /media/tv 误匹配 /media/tv2）
            in_root_dir = (
                self.quark_root_dir == "/"
                or quark_path == self.quark_root_dir
                or quark_path.startswith(f"{self.quark_root_dir}/")
            )
            if in_root_dir:
                # 映射到AList路径
                alist_path = self.map_quark_to_alist_path(quark_path)
                
                # 执行刷新
                self.refresh(alist_path)
            
        return task

    def get_info(self):
        """获取AList服务器信息"""
        url = f"{self.url}/api/admin/setting/list"
        headers = {"Authorization": self.token}
        querystring = {"group": "1"}
        try:
            response = requests.request("GET", url, headers=headers, params=querystring)
            response.raise_for_status()
            response = response.json()
            if response.get("code") == 200:
                print(f"AList 刷新: {response.get('data',[])[1].get('value','')} {response.get('data',[])[0].get('value','')}")
                return True
            else:
                print(f"AList 刷新: 连接失败 ❌ {response.get('message')}")
        except Exception as e:
            print(f"AList 刷新: 连接出错 ❌ {str(e)}")
        return False

    def storage_id_to_path(self, storage_id):
        """
        将存储ID转换为挂载路径和夸克根目录
        
        Args:
            storage_id: 存储ID
            
        Returns:
            tuple: (成功状态, (挂载路径, 夸克根目录))
        """
        storage_mount_path, quark_root_dir = None, None
        
        # 1. 检查是否符合 /aaa:/bbb 格式
        if match := re.match(r"^(\/[^:]*):(\/[^:]*)$", storage_id):
            # 存储挂载路径, 夸克根文件夹
            storage_mount_path, quark_root_dir = match.group(1), match.group(2)
            file_list = self.get_file_list(storage_mount_path)
            if file_list.get("code") != 200:
                print(f"AList 刷新: 挂载路径无效")
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
                else:
                    print(f"AList 刷新: 不支持 [{storage_info['driver']}] 驱动")
            else:
                print(f"AList 刷新: 获取存储信息失败")
                return False, (None, None)
        else:
            print(f"AList 刷新: storage_id 格式错误")
            return False, (None, None)
            
        # 返回结果
        if storage_mount_path and quark_root_dir:
            return True, (storage_mount_path, quark_root_dir)
        else:
            return False, (None, None)

    def get_storage_info(self, storage_id):
        """获取AList存储详细信息"""
        url = f"{self.url}/api/admin/storage/get"
        headers = {"Authorization": self.token}
        querystring = {"id": storage_id}
        try:
            response = requests.request("GET", url, headers=headers, params=querystring)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 200:
                return data.get("data", {})
            else:
                print(f"AList 刷新: 获取存储信息失败 ({data.get('message', '未知错误')})")
        except Exception as e:
            print(f"AList 刷新: 获取存储信息出错 ({str(e)})")
        return None

    def refresh(self, path, retry_count=2):
        """
        刷新AList目录，支持重试和自动回溯到父目录
        
        Args:
            path: 需要刷新的路径
            retry_count: 重试次数，默认重试2次
        """
        # 实现重试机制
        for attempt in range(retry_count + 1):
            if attempt > 0:
                # 不输出重试信息
                pass
                
            data = self.get_file_list(path, True)
            
            if data.get("code") == 200:
                print(f"📁 刷新 AList 目录: [{path}] 成功 ✅")
                return data.get("data")
            elif "object not found" in data.get("message", ""):
                # 如果是根目录就不再往上查找
                if path == "/" or path == self.storage_mount_path:
                    print(f"📁 AList 刷新: 根目录不存在，请检查配置")
                    return False
                
                # 自动获取父目录并尝试刷新
                parent_path = os.path.dirname(path)
                
                # 先刷新父目录
                parent_result = self.get_file_list(parent_path, True)
                if parent_result.get("code") == 200:
                    # 再次尝试刷新原目录
                    retry_data = self.get_file_list(path, True)
                    if retry_data.get("code") == 200:
                        print(f"📁 刷新 AList 目录: [{path}] 成功 ✅")
                        return retry_data.get("data")
                
                # 如果刷新父目录后仍不成功，则递归处理父目录
                return self.refresh(parent_path, retry_count)
            elif attempt < retry_count:
                # 如果还有重试次数，等待后继续
                time.sleep(1)  # 等待1秒后重试
            else:
                # 已达到最大重试次数
                error_msg = data.get("message", "未知错误") 
                print(f"📁 AList 刷新: 失败 ❌ {error_msg}")
                return None

    def map_quark_to_alist_path(self, quark_path):
        """
        将夸克路径映射到AList路径
        
        Args:
            quark_path: 夸克路径，例如 /Movies/2024
            
        Returns:
            str: 对应的AList路径，例如 /movies/2024
        """
        # 确保路径格式正确
        if not quark_path.startswith("/"):
            quark_path = f"/{quark_path}"
            
        # 特殊处理根目录的情况
        if self.quark_root_dir == "/":
            # 夸克根目录是/，直接映射到AList挂载路径
            if quark_path == "/":
                return self.storage_mount_path
            else:
                # 组合路径
                alist_path = os.path.normpath(
                    os.path.join(self.storage_mount_path, quark_path.lstrip("/"))
                ).replace("\\", "/")
                return alist_path
        
        # 常规情况：检查路径是否在夸克根目录下
        if not quark_path.startswith(self.quark_root_dir):
            # 尝试强制映射，去掉前导路径
            relative_path = quark_path.lstrip("/")
        else:
            # 去除夸克根目录前缀，并确保路径格式正确
            relative_path = quark_path.replace(self.quark_root_dir, "", 1).lstrip("/")
            
        # 构建AList路径
        alist_path = os.path.normpath(
            os.path.join(self.storage_mount_path, relative_path)
        ).replace("\\", "/")
        
        return alist_path

    def get_file_list(self, path, force_refresh=False):
        """
        获取AList指定路径下的文件列表
        
        Args:
            path: AList文件路径
            force_refresh: 是否强制刷新，默认False
            
        Returns:
            dict: AList API返回的数据
        """
        url = f"{self.url}/api/fs/list"
        headers = {
            "Authorization": self.token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "path": path,
            "refresh": force_refresh,
            "password": "",
            "page": 1,
            "per_page": 0,
        }
        
        try:
            response = requests.request(
                "POST", 
                url, 
                headers=headers, 
                json=payload, 
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"📁 AList 刷新: 网络请求出错 ❌ {str(e)}")
        except json.JSONDecodeError as e:
            print(f"📁 AList 刷新: 解析数据出错 ❌ {str(e)}")
        except Exception as e:
            print(f"📁 AList 刷新: 未知错误 ❌ {str(e)}")
        
        return {"code": 500, "message": "获取文件列表出错"}

    def get_root_folder_full_path(self, cookie, pdir_fid):
        """获取夸克根文件夹的完整路径"""
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
            print(f"AList 刷新: 获取路径出错 ❌ {str(e)}")
        return ""
