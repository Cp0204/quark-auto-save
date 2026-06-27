import requests


class Emby:

    default_config = {
        "url": "",  # Emby服务器地址
        "token": "",  # Emby服务器token
    }
    default_task_config = {
        "try_match": True,  # 是否尝试匹配
        "media_id": "",  # 媒体ID，当为0时不刷新
    }
    is_active = False

    def __init__(self, **kwargs):
        self.plugin_name = self.__class__.__name__.lower()
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.plugin_name} 模块缺少必要参数: {key}")
            # 标准化 URL，避免末尾斜杠导致路径拼接出现 //
            if self.url:
                self.url = self.url.strip()
                if not self.url.startswith(("http://", "https://")):
                    self.url = f"http://{self.url}"
                self.url = self.url.rstrip("/")

            if self.url and self.token:
                if self.get_info():
                    self.is_active = True

    def run(self, task, **kwargs):
        task_config = task.get("addition", {}).get(
            self.plugin_name, self.default_task_config
        )
        if media_id := task_config.get("media_id"):
            if media_id != "0":
                self.refresh(media_id)
        elif task_config.get("try_match"):
            if match_media_id := self.search(task["taskname"]):
                self.refresh(match_media_id)
                task_config["media_id"] = match_media_id
                task.setdefault("addition", {})[self.plugin_name] = task_config
                return task

    def get_info(self):
        url = f"{self.url}/emby/System/Info"
        headers = {"X-Emby-Token": self.token}
        querystring = {}
        try:
            response = requests.request("GET", url, headers=headers, params=querystring)
            if "application/json" in response.headers["Content-Type"]:
                response = response.json()
                print(
                    f"Emby 媒体库: {response.get('ServerName','')} v{response.get('Version','')}"
                )
                return True
            else:
                print(f"Emby 媒体库: 连接失败 ❌ {response.text}")
        except Exception as e:
            print(f"获取 Emby 媒体库信息出错: {e}")
        return False

    def refresh(self, emby_id):
        if not emby_id:
            return False
        url = f"{self.url}/emby/Items/{emby_id}/Refresh"
        headers = {"X-Emby-Token": self.token}
        querystring = {
            "Recursive": "true",
            "MetadataRefreshMode": "FullRefresh",
            "ImageRefreshMode": "FullRefresh",
            "ReplaceAllMetadata": "false",
            "ReplaceAllImages": "false",
        }
        try:
            response = requests.request(
                "POST", url, headers=headers, params=querystring
            )
            if response.text == "":
                print(f"🎞️ 刷新 Emby 媒体库: 成功 ✅")
                return True
            else:
                print(f"🎞️ 刷新 Emby 媒体库: {response.text} ❌")
        except Exception as e:
            print(f"刷新 Emby 媒体库出错: {e}")
        return False

    def search(self, media_name):
        if not media_name:
            return ""
        url = f"{self.url}/emby/Items"
        headers = {"X-Emby-Token": self.token}
        querystring = {
            "IncludeItemTypes": "Series",
            "StartIndex": 0,
            "SortBy": "SortName",
            "SortOrder": "Ascending",
            "ImageTypeLimit": 0,
            "Recursive": "true",
            "SearchTerm": media_name,
            "Limit": 10,
            "IncludeSearchTypes": "false",
        }
        try:
            response = requests.request("GET", url, headers=headers, params=querystring)
            if "application/json" in response.headers["Content-Type"]:
                response = response.json()
                if response.get("Items"):
                    for item in response["Items"]:
                        if item["IsFolder"]:
                            print(
                                f"🎞️ 《{item['Name']}》匹配到 Emby 媒体库 ID: {item['Id']}"
                            )
                            return item["Id"]
            else:
                print(f"🎞️ 搜索 Emby 媒体库: {response.text}❌")
        except Exception as e:
            print(f"搜索 Emby 媒体库出错: {e}")
        return ""
