import re
import requests

"""
    配合 alist-strm 项目，触发特定配置运行
    https://github.com/tefuirZ/alist-strm
"""


class Alist_strm:

    default_config = {
        "url": "",  # alist-strm服务器URL
        "cookie": "",  # alist-strm的cookie，F12抓取，关键参数：session=ey***
        "config_id": "",  # 要触发运行的配置ID，支持多个，用逗号分隔
    }
    is_active = False

    def __init__(self, **kwargs):
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.__class__.__name__} 模块缺少必要参数: {key}")
            if self.url and self.cookie and self.config_id:
                if self.get_info(self.config_id):
                    self.is_active = True

    def run(self, task, **kwargs):
        self.run_selected_configs(self.config_id)

    def get_info(self, config_id_str):
        url = f"{self.url}/configs"
        headers = {"Cookie": self.cookie}
        try:
            response = requests.request("GET", url, headers=headers)
            response.raise_for_status()
            html_content = response.text
            # 用正则提取 config_name 的值
            matchs = re.findall(
                r'value="(\d*)">\s*<strong>名称:</strong>([^<]+)', html_content
            )
            if matchs:
                config_name = [
                    item[1].strip()
                    for item in matchs
                    if item[0] in config_id_str.split(",")
                ]
                print(f"alist-strm配置运行: {config_name}")
                return True
            else:
                print(f"alist-strm配置运行: 匹配失败❌，请检查网络连通和cookie有效性")
        except Exception as e:
            print(f"获取alist-strm配置信息出错: {e}")
        return False

    def run_selected_configs(self, selected_configs_str):
        url = f"{self.url}/run_selected_configs"
        headers = {"Cookie": self.cookie}
        try:
            selected_configs = [int(x.strip()) for x in selected_configs_str.split(",")]
        except ValueError:
            print("🔗 alist-strm配置运行: 出错❌ id应以,分割")
            return False
        data = [("selected_configs", config_id) for config_id in selected_configs]
        data.append(("action", "run_selected"))
        try:
            response = requests.post(url, headers=headers, data=data)
            response.raise_for_status()
            html_content = response.text
            # 用正则提取 config_name 的值
            match = re.search(r'role="alert">\s*([^<]+)\s*<button', html_content)
            if match:
                alert = match.group(1).strip()
                print(f"🔗 alist-strm配置运行: {alert}✅")
                return True
            else:
                print(f"🔗 alist-strm配置运行: 失败❌")
        except Exception as e:
            print(f"Error: {e}")
        return False
