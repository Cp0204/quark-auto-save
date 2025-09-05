import requests


class Smartstrm:
    default_config = {
        "webhook": "",  # SmartStrm Webhook 地址
        "strmtask": "",  # SmartStrm 任务名，支持多个如 `tv,movie`
        "xlist_path_fix": "",  # 路径映射， SmartStrm 任务使用 quark 驱动时无须填写；使用 openlist 驱动时需填写 `/storage_mount_path:/quark_root_dir` ，例如把夸克根目录挂载在 OpenList 的 /quark 下，则填写 `/quark:/` ；以及 SmartStrm 会使 OpenList 强制刷新目录，无需再用 alist 插件刷新。
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
            if self.webhook and self.strmtask:
                if self.get_info():
                    self.is_active = True

    def get_info(self):
        """获取 SmartStrm 信息"""
        try:
            response = requests.request(
                "GET",
                self.webhook,
                timeout=5,
            )
            response = response.json()
            if response.get("success"):
                print(f"SmartStrm 触发任务: 连接成功 {response.get('version','')}")
                return response
            print(f"SmartStrm 触发任务：连接失败 {response.get('message','')}")
            return None
        except Exception as e:
            print(f"SmartStrm 触发任务：连接出错 {str(e)}")
            return None

    def run(self, task, **kwargs):
        """
        插件主入口函数
        :param task: 任务配置
        :param kwargs: 其他参数
        """
        try:
            # 准备发送的数据
            headers = {"Content-Type": "application/json"}
            payload = {
                "event": "qas_strm",
                "data": {
                    "strmtask": self.strmtask,
                    "savepath": task["savepath"],
                    "xlist_path_fix": self.xlist_path_fix,
                },
            }
            # 发送 POST 请求
            response = requests.request(
                "POST",
                self.webhook,
                headers=headers,
                json=payload,
                timeout=5,
            )
            response = response.json()
            if response.get("success"):
                print(
                    f"SmartStrm 触发任务: [{response['task']['name']}] {response['task']['storage_path']} 成功✅"
                )
            else:
                print(f"SmartStrm 触发任务: {response['message']}")
        except Exception as e:
            print(f"SmartStrm 触发任务：出错 {str(e)}")
