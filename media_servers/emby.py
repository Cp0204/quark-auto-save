import requests


class Emby:

    default_config = {"url": "", "token": ""}
    is_active = False

    def __init__(self, **kwargs):
        if kwargs:
            for key, value in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.__class__.__name__} 模块缺少必要参数: {key}")
            if self.url and self.token:
                if self.get_info():
                    self.is_active = True

    def run(self, task):
        if task.get("media_id"):
            if task["media_id"] != "0":
                self.refresh(task["media_id"])
        else:
            match_media_id = self.search(task["taskname"])
            if match_media_id:
                task["media_id"] = match_media_id
                self.refresh(match_media_id)
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
                    f"Emby媒体库: {response.get('ServerName','')} v{response.get('Version','')}"
                )
                return True
            else:
                print(f"Emby媒体库: 连接失败❌ {response.text}")
                return False
        except requests.exceptions.RequestException as e:
            print(f"获取Emby媒体库信息出错: {e}")
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
                print(f"🎞️ 刷新Emby媒体库：成功✅")
                return True
            else:
                print(f"🎞️ 刷新Emby媒体库：{response.text}❌")
                return False
        except requests.exceptions.RequestException as e:
            print(f"刷新Emby媒体库出错: {e}")
            return False

    def search(self, media_name):
        if not media_name:
            return False
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
                                f"🎞️ 《{item['Name']}》匹配到Emby媒体库ID：{item['Id']}"
                            )
                            return item["Id"]
            else:
                print(f"🎞️ 搜索Emby媒体库：{response.text}❌")
                return False
        except requests.exceptions.RequestException as e:
            print(f"搜索Emby媒体库出错: {e}")
            return False
