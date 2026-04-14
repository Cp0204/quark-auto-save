import os
import requests


class Plex:

    default_config = {
        "url": "",  # Plex服务器URL
        "token": "",  # Plex Token，可F12在请求中抓取
        "quark_root_path": "",  # 夸克根目录在Plex中的路径；假设夸克目录/media/tv在plex中对应的路径为/quark/media/tv，则为/quark
    }
    is_active = False
    _libraries = None  # 缓存库信息

    def __init__(self, **kwargs):
        if kwargs:
            for key, value in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    pass  # 不显示缺少参数的提示

            # 处理多账号配置：支持数组形式的quark_root_path
            if isinstance(self.quark_root_path, list):
                self.quark_root_paths = self.quark_root_path
                # 为了向后兼容，使用第一个路径作为默认值
                self.quark_root_path = self.quark_root_paths[0] if self.quark_root_paths else ""
            else:
                # 单一配置转换为数组格式
                self.quark_root_paths = [self.quark_root_path] if self.quark_root_path else []

            if self.url and self.token and self.quark_root_path:
                if self.get_info():
                    self.is_active = True

    def get_quark_root_path(self, account_index=0):
        """根据账号索引获取对应的quark_root_path"""
        if account_index < len(self.quark_root_paths):
            return self.quark_root_paths[account_index]
        else:
            # 如果索引超出范围，使用第一个路径作为默认值
            return self.quark_root_paths[0] if self.quark_root_paths else ""

    def run(self, task, **kwargs):
        if task.get("savepath"):
            # 检查是否已缓存库信息
            if self._libraries is None:
                self._libraries = self._get_libraries()
            # 拼接完整路径
            full_path = os.path.normpath(
                os.path.join(self.quark_root_path, task["savepath"].lstrip("/"))
            ).replace("\\", "/")
            self.refresh(full_path)

    def get_info(self):
        """获取Plex服务器信息"""
        headers = {"Accept": "application/json", "X-Plex-Token": self.token}
        try:
            response = requests.get(f"{self.url}/", headers=headers)
            if response.status_code == 200:
                info = response.json()["MediaContainer"]
                print(
                    f"Plex 媒体库: {info.get('friendlyName','')} v{info.get('version','')}"
                )
                return True
            else:
                print(f"Plex 媒体库: 连接失败 ❌ 状态码: {response.status_code}")
        except Exception as e:
            print(f"获取 Plex 媒体库信息出错: {e}")
        return False

    def refresh(self, folder_path):
        """刷新指定文件夹"""
        if not folder_path:
            return False
        # 统一路径格式，避免不同分隔符导致匹配异常
        folder_path = os.path.normpath(folder_path).replace("\\", "/")
        headers = {"Accept": "application/json", "X-Plex-Token": self.token}
        try:
            for library in self._libraries:
                for location in library.get("Location", []):
                    location_path = os.path.normpath(location.get("path", "")).replace(
                        "\\", "/"
                    )
                    # 按目录边界匹配，避免 /media/tv 误匹配 /media/tv2
                    if (
                        folder_path == location_path
                        or folder_path.startswith(f"{location_path}/")
                    ):
                        refresh_url = f"{self.url}/library/sections/{library['key']}/refresh"
                        # 使用 params 让 requests 自动进行 URL 编码，避免 #、?、& 等字符截断路径
                        refresh_response = requests.get(
                            refresh_url, headers=headers, params={"path": folder_path}
                        )
                        if refresh_response.status_code == 200:
                            print(
                                f"🎞️ 刷新 Plex 媒体库: {library['title']} [{folder_path}] 成功 ✅"
                            )
                            return True
                        else:
                            print(
                                f"🎞️ 刷新 Plex 媒体库: 刷新请求失败 ❌ 状态码: {refresh_response.status_code}"
                            )
            print(f"🎞️ 刷新 Plex 媒体库: {folder_path} 未找到匹配的媒体库 ❌")
        except Exception as e:
            print(f"刷新 Plex 媒体库出错: {e}")
        return False

    def _get_libraries(self):
        """获取Plex媒体库信息"""
        url = f"{self.url}/library/sections"
        headers = {"Accept": "application/json", "X-Plex-Token": self.token}
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                libraries = response.json()["MediaContainer"].get("Directory", [])
                return libraries
            else:
                print(f"🎞️ 获取 Plex 媒体库信息失败 ❌ 状态码: {response.status_code}")
        except Exception as e:
            print(f"获取 Plex 媒体库信息出错: {e}")
        return []
