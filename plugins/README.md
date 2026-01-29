# 插件开发指南

通过开发自定义插件，你可以轻松扩展项目功能（如自动刷新媒体库、跨网盘同步、自动下载等）。

## 快速开始

- **插件位置**：所有插件均放置在 `plugins` 目录下。
- **命名规范**：
  - 文件名：小写（如 `emby.py`, `plex.py`）。
  - 类名：文件名首字母大写（如 `Emby`, `Plex`）。
- **加载逻辑**：程序启动时会自动加载该目录下所有符合规范的插件。可以通过 `PLUGIN_FLAGS` 环境变量排除特定插件（如 `-emby`）。

## 插件结构

每个插件应包含一些标准结构，你可以根据需要实现对应的钩子函数，示例如下：

/plugins/your_plugin.py

```python
class YourPlugin:
    # 插件名
    plugin_name = "your_plugin_name"

    # 1. 插件全局配置：首次运行后会自动同步到 quark_config.json 的 "plugins" 字段中
    default_config = {
        "url": "http://localhost:8080",
        "token": "your_token"
    }

    # 2. 任务独立配置（可选）：会合并到任务的 "addition" 字段中，供单个任务动态调整
    default_task_config = {
        "enable": True,
        "media_id": ""
    }

    # 3. 插件激活标志：初始化成功后应设为 True。若为 False，主程序将跳过此插件
    is_active = False

    def __init__(self, **kwargs):
        """
        插件初始化：加载全局配置并验证可用性
        :param kwargs: 传入的是配置文件中 plugins 目录下对应插件的参数
        """
        # 检查 `kwargs` 是否包含所有 `default_config` 中的必要参数
        # 检查服务能否正常连接（自定义校验逻辑）
        # 如效验通过则设置 `self.is_active` 为 True
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
            if self._check_config():
                print(f"{self.plugin_name}: 已激活")
                self.is_active = True

    def _check_config(self):
        """
        校验插件配置
        :return: True/False
        """
        # TODO: 自定义校验逻辑
        return True

    def task_before(self, tasklist, account):
        """
        【可选】开始所有转存任务前触发
        :param tasklist: 全量任务列表
        :param account: 当前执行的账号实例 (Quark 类)

        :return: 修改后的 tasklist（如果需要过滤或预处理）
        """
        # TODO: 自定义逻辑
        return tasklist

    def run(self, task, **kwargs):
        """
        【核心】单个转存任务成功后触发
        :param task: 当前正在执行的任务字典
        :param kwargs: 包含 account (账号实例) 和 tree (本次转存成功的文件树)

        :return: 修改后的 task (建议返回更新后的 task 字典)
        """
        account = kwargs.get("account")
        tree = kwargs.get("tree")
        # 执行插件逻辑（如：通知 Emby 刷新、自动下载文件等）
        # TODO: 自定义逻辑
        return task

    def task_after(self, tasklist, account):
        """
        【可选】所有任务全部结束后触发
        :param tasklist: 全量任务列表
        :param account: 当前执行的账号实例 (Quark 类)

        :return: 返回字典，可包含
          'tasklist' 可选，可更新到任务列表
          'config' 可选，可更新到插件配置
        """
        # TODO: 自定义逻辑

        # 获取更新后的插件配置
        config = {}
        for key, _ in self.default_config.items():
            config[key] = getattr(self, key)

        return {"tasklist": tasklist, "config": config}
```

## 配置文件

在 `quark_config.json` 中，插件配置分为全局和任务两级：

**全局配置：**
```json
"plugins": {
  "emby": {
    "url": "http://1.2.3.4:8096",
    "token": "your_token"
  }
}
```

**任务配置（可选）：**
```json
"tasklist": [
  {
    "taskname": "电影更新",
    "addition": {
      "emby": { "media_id": "12345" }
    }
  }
]
```

## 开发示例

参考 [emby.py](emby.py) 或 [aria2.py](aria2.py)。

### 最佳实践：异常处理
请务必使用 `try-except` 包裹网络请求，防止单个插件运行出错导致整个主程序崩溃。

```python
def run(self, task, **kwargs):
    try:
        # 获取当前任务的插件设置
        task_config = task.get("addition", {}).get("your_plugin_name", self.default_task_config)
        if not task_config.get("enable"):
            return

        # 执行逻辑...
        print(f"执行插件任务: {task['taskname']}")
    except Exception as e:
        print(f"插件运行出错: {e}")
```

## 使用自定义插件

放到 `/plugins` 目录即可识别，如果你使用 docker 运行：

```shell
docker run -d \
  # ... 例如添加这行挂载，其它一致
  -v ./quark-auto-save/plugins/plex.py:/app/plugins/plex.py \
  # ...
```

如果你有写自定义插件的能力，相信你也知道如何挂载自定义插件，算我啰嗦。🙃

## 🤝 贡献者

| 插件                | 功能说明                  | 贡献者                                  |
| :------------------ | :------------------------ | :-------------------------------------- |
| `plex.py`           | 自动刷新 Plex 媒体库      | [zhazhayu](https://github.com/zhazhayu) |
| `alist_strm_gen.py` | 自动生成 strm 文件        | [xiaoQQya](https://github.com/xiaoQQya) |
| `alist_sync.py`     | 调用 alist 实现跨网盘转存 | [jenfonro](https://github.com/jenfonro) |

欢迎贡献你的插件！提交 PR 前请确保插件包含必要的 `default_config` 和注释。
