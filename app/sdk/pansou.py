import re

import requests

from sdk.common import iso_to_cst


class PanSou:
    """
    PanSou 类，用于获取云盘资源
    """

    def __init__(self, server):
        self.server = server
        self.session = requests.Session()

    def search(self, keyword: str, refresh: bool = False) -> list:
        """搜索资源

        Args:
            keyword (str): 搜索关键字

        Returns:
            list: 资源列表
        """
        try:
            url = f"{self.server.rstrip('/')}/api/search"
            params = {"kw": keyword, "cloud_types": ["quark"], "res": "merge", "refresh": refresh}
            response = self.session.get(url, params=params)
            result = response.json()
            if result.get("code") == 0:
                data = result.get("data", {}).get("merged_by_type", {}).get("quark", [])
                return self.format_search_results(data)
            return []
        except Exception as _:
            return []

    def format_search_results(self, search_results: list) -> list:
        """格式化搜索结果

        Args:
            search_results (list): 搜索结果列表

        Returns:
            list: 夸克网盘资源列表
        """
        pattern = (
            r'^(.*?)'
            r'(?:'
            r'[【\[]?'
            r'(?:简介|介绍|描述)'
            r'[】\]]?'
            r'[:：]?'
            r')'
            r'(.*)$'
        )
        format_results = []
        link_array = []
        for item in search_results:
            url = item.get("url", "")
            note = item.get("note", "")
            tm = item.get("datetime", "")
            if tm:
                tm = iso_to_cst(tm)

            match = re.search(pattern, note)
            if match:
                title = match.group(1)
                content = match.group(2)
            else:
                title = note
                content = ""

            if url != "" and url not in link_array:
                link_array.append(url)
                format_results.append({
                    "shareurl": url,
                    "taskname": title,
                    "content": content,
                    "datetime": tm,
                    "channel": item.get("source", ""),
                    "source": "PanSou"
                })

        return format_results


if __name__ == "__main__":
    server: str = "https://so.252035.xyz"
    pansou = PanSou(server)
    results = pansou.search("哪吒")
    for item in results:
        print(f"标题: {item['taskname']}")
        print(f"描述: {item['content']}")
        print(f"链接: {item['shareurl']}")
        print(f"时间: {item['datetime']}")
        print("-" * 50)
