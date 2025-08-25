import re
import requests


class CloudSaver:
    """
    CloudSaver 类，用于获取云盘资源
    """

    def __init__(self, server):
        self.server = server
        self.username = None
        self.password = None
        self.token = None
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def set_auth(self, username, password, token=""):
        self.username = username
        self.password = password
        self.token = token
        self.session.headers.update({"Authorization": f"Bearer {self.token}"})

    def login(self):
        if not self.username or not self.password:
            return {"success": False, "message": "CloudSaver未设置用户名或密码"}
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
            if (
                result.get("message") == "无效的 token"
                or result.get("message") == "未提供 token"
            ):
                login_result = self.login()
                if login_result.get("success"):
                    result = self.search(keyword, last_message_id)
                    result["new_token"] = login_result.get("token")
                    return result
                else:
                    return {
                        "success": False,
                        "message": login_result.get("message", "未知错误"),
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
        pattern_title = r"(名称|标题)[：:]?(.*)"
        pattern_content = r"(描述|简介)[：:]?(.*)(链接|标签)"
        clean_results = []
        link_array = []
        for channel in search_results:
            for item in channel.get("list", []):
                cloud_links = item.get("cloudLinks", [])
                for link in cloud_links:
                    if link.get("cloudType") == "quark":
                        # 清洗标题
                        title = item.get("title", "")
                        if match := re.search(pattern_title, title, re.DOTALL):
                            title = match.group(2)
                        title = title.replace("&amp;", "&").strip()
                        # 清洗内容
                        content = item.get("content", "")
                        if match := re.search(pattern_content, content, re.DOTALL):
                            content = match.group(2)
                        content = content.replace('<mark class="highlight">', "")
                        content = content.replace("</mark>", "")
                        content = content.strip()
                        # 获取发布时间 - 采用与原始实现一致的方式
                        pubdate = item.get("pubDate", "")  # 使用 pubDate 字段
                        if pubdate:
                            pubdate = self._iso_to_cst(pubdate)  # 转换为中国标准时间
                        # 链接去重
                        if link.get("link") not in link_array:
                            link_array.append(link.get("link"))
                            clean_results.append(
                                {
                                    "shareurl": link.get("link"),
                                    "taskname": title,
                                    "content": content,
                                    "datetime": pubdate,  # 使用 datetime 字段名，与原始实现一致
                                    "tags": item.get("tags", []),
                                    "channel": item.get("channelId", ""),
                                    "source": "CloudSaver"
                                }
                            )
        
        # 注意：排序逻辑已移至全局，这里不再进行内部排序
        # 返回原始顺序的结果，由全局排序函数统一处理
        return clean_results
    
    def _iso_to_cst(self, iso_time_str: str) -> str:
        """将 ISO 格式的时间字符串转换为 CST(China Standard Time) 时间并格式化为 %Y-%m-%d %H:%M:%S 格式
        
        Args:
            iso_time_str (str): ISO 格式时间字符串
            
        Returns:
            str: CST(China Standard Time) 时间字符串
        """
        try:
            from datetime import datetime, timezone, timedelta
            dt = datetime.fromisoformat(iso_time_str)
            dt_cst = dt.astimezone(timezone(timedelta(hours=8)))
            return dt_cst.strftime("%Y-%m-%d %H:%M:%S") if dt_cst.year >= 1970 else ""
        except:
            return iso_time_str  # 转换失败时返回原始字符串


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
