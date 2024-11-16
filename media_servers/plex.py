import os
import requests


class Plex:
    default_config = {
        "url": "",  # Plex服务器URL
        "token": "",  # Plex Token
        "base_path": ""  # Plex媒体库基础路径
    }
    is_active = False

    def __init__(self, **kwargs):
        if kwargs:
            for key, value in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.__class__.__name__} 模块缺少必要参数: {key}")
            if self.url and self.token and self.base_path:
                if self.get_info():
                    self.is_active = True

    def run(self, task):
        if task.get("savepath"):
            # 拼接完整路径
            full_path = os.path.join(self.base_path, task["savepath"].lstrip("/"))
            full_path = full_path.replace("\\", "/")
            self.refresh(full_path)
        return task

    def get_info(self):
        """获取Plex服务器信息"""
        headers = {
            'Accept': 'application/json',
            'X-Plex-Token': self.token
        }

        try:
            response = requests.get(
                f"{self.url}/",
                headers=headers
            )
            if response.status_code == 200:
                info = response.json()['MediaContainer']
                print(f"Plex媒体库: {info.get('friendlyName','')} v{info.get('version','')}")
                return True
            else:
                print(f"Plex媒体库: 连接失败❌ 状态码：{response.status_code}")
        except Exception as e:
            print(f"获取Plex媒体库信息出错: {e}")
        return False

    def refresh(self, folder_path):
        """刷新指定文件夹"""
        if not folder_path:
            return False

        headers = {
            'Accept': 'application/json',
            'X-Plex-Token': self.token
        }

        try:
            response = requests.get(
                f"{self.url}/library/sections",
                headers=headers
            )

            if response.status_code != 200:
                print(f"🎞️ 刷新Plex媒体库：获取库信息失败❌ 状态码：{response.status_code}")
                return False

            libraries = response.json()['MediaContainer']['Directory']

            for library in libraries:
                for location in library.get('Location', []):
                    if folder_path.startswith(location['path']):
                        library_id = library['key']
                        refresh_url = (
                            f"{self.url}/library/sections/{library_id}/refresh"
                            f"?path={folder_path}"
                        )
                        refresh_response = requests.get(refresh_url, headers=headers)

                        if refresh_response.status_code == 200:
                            print(f"🎞️ 刷新Plex媒体库：成功✅")
                            print(f"📁 扫描路径: {folder_path}")
                            return True
                        else:
                            print(f"🎞️ 刷新Plex媒体库：刷新请求失败❌ 状态码：{refresh_response.status_code}")
                            return False

            print(f"🎞️ 刷新Plex媒体库：未找到匹配的媒体库❌ {folder_path}")

        except Exception as e:
            print(f"刷新Plex媒体库出错: {e}")

        return False