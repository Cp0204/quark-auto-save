import requests


class CloudSaver:
    """
    CloudSaver 类，用于获取云盘资源
    """

    def __init__(self, server):
        self.server = server
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def set_auth(self, username, password, token=""):
        self.username = username
        self.password = password
        self.token = token
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})

    def login(self):
        try:
            url = f"{self.server}/api/user/login"
            data = {"username": self.username, "password": self.password}
            response = self.session.post(url, json=data)
            result = response.json()
            if result.get("success"):
                self.token = result.get("data", {}).get("token")
                self.session.headers.update({"Authorization": f"Bearer {self.token}"})
                return {"success": True, "token": self.token}
            else:
                return {
                    "success": False,
                    "message": f"CloudSaver登录{result.get('message', '未知错误')}",
                }
        except Exception as e:
            return {"success": False, "message": str(e)}

    def search(self, keyword, last_message_id=""):
        """
        搜索资源

        Args:
            keyword (str): 搜索关键词
            last_message_id (str): 上一条消息ID，用于分页

        Returns:
            list: 搜索结果列表
        """
        if not self.token:
            return {"success": False, "message": "CloudSaver未登录"}

        try:
            url = f"{self.server}/api/search"
            params = {"keyword": keyword, "lastMessageId": last_message_id}
            response = self.session.get(url, params=params)
            result = response.json()
            if result.get("success"):
                data = result.get("data", [])
                return {"success": True, "data": data}
            else:
                return {"success": False, "message": result.get("message", "未知错误")}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def auto_login_search(self, keyword, last_message_id=""):
        """
        自动登录并搜索资源

        Args:
            keyword (str): 搜索关键词
            last_message_id (str): 上一条消息ID，用于分页
        """
        result = self.search(keyword, last_message_id)
        if result.get("success"):
            return result
        else:
            if result.get("message") == "无效的 token":
                new_token = self.login()
                if new_token.get("success"):
                    result = self.search(keyword, last_message_id)
                    result["new_token"] = new_token.get("token")
                    return result
                else:
                    return {
                        "success": False,
                        "message": new_token.get("message", "未知错误"),
                    }
            return {"success": False, "message": result.get("message", "未知错误")}

    def clean_search_results(self, search_results):
        """
        清洗搜索结果

        Args:
            search_results (list): 搜索结果列表

        Returns:
            list: 夸克网盘链接列表
        """
        clean_results = []
        for channel in search_results:
            for item in channel.get("list", []):
                cloud_links = item.get("cloudLinks", [])
                for link in cloud_links:
                    if link.get("cloudType") == "quark":
                        clean_results.append(
                            {
                                "shareurl": link.get("link"),
                                "taskname": item.get("title", "").strip("名称：").replace("&amp;", "&"),
                                "content": item.get("content", "")
                                .split("描述：")[1]
                                .split("链接：")[0]
                                .replace('<mark class="highlight">', "")
                                .replace("</mark>", ""),
                                "tags": item.get("tags", []),
                            }
                        )
        return clean_results


# 测试示例
if __name__ == "__main__":
    # 创建CloudSaver实例
    server = ""
    username = ""
    password = ""
    token = ""
    cloud_saver = CloudSaver(server)
    cloud_saver.set_auth(username, password, token)
    # 搜索资源
    results = cloud_saver.auto_login_search("黑镜")
    # 提取夸克网盘链接
    clean_results = cloud_saver.clean_search_results(results.get("data", []))
    # 打印结果
    for item in clean_results:
        print(f"标题: {item['taskname']}")
        print(f"描述: {item['content']}")
        print(f"链接: {item['shareurl']}")
        print(f"标签: {' '.join(item['tags'])}")
        print("-" * 50)
