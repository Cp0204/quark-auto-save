# 插件开发指南

本指南介绍如何开发自定义插件，你可以通过添加新的插件来扩展项目功能。

## 基本结构

* 插件位于 `plugins` 目录下.
* 每个插件是一个 `.py` 文件 (例如 `emby.py`, `plex.py`)，文件名小写。
* 每个插件文件包含一个与文件名对应的首字母大写命名类（例如 `emby.py` 中的 `Emby` 类）。

## 插件要求

每个插件类必须包含以下内容:

* **`default_config`**：字典，包含插件所需参数及其默认值。例如：

  ```python
  # 该插件必须配置的键，值可留空
  default_config = {"url": "", "token": ""}
  ```

* **`is_active`**：布尔值，默认为 `False`.

* **`__init__(self, **kwargs)`**：构造函数，接收配置参数 `kwargs`。它应该:
  1. 检查 `kwargs` 是否包含所有 `default_config` 中的参数，缺少参数则打印警告。
  2. 若参数完整，尝试连接服务器并验证配置，成功则设置 `self.is_active = True`。

* **`run(self, task, **kwargs)`**：整个插件入口函数，处理插件逻辑。
  * `task` 是一个字典，包含任务信息。如果需要修改任务参数，返回修改后的 `task` 字典；
  * 无修改则不返回或返回 `None`。

## 插件示例

参考 [emby.py](emby.py)

参考函数：

* **`get_info(self)`**：获取服务器信息（例如名称、版本），成功返回 `True`，失败返回 `False` 。用于验证赋值 `self.is_active` 。

* **`refresh(self, media_id)`**：刷新指定媒体信息，成功返回服务器响应数据（通常是字典），失败返回 `None` 。

* **`search(self, media_name)`**：搜索媒体. 成功返回服务器响应数据（通常包含媒体ID的字典）, 失败返回 `None` 。

### 最佳实践

requests 部分使用 try-except 块，以防插件请求出错中断整个转存任务。

```python
try:
    response = requests.request("GET", url, headers=headers, params=querystring)
    # 处理响应数据
    # ......
    # 返回
except Exception as e:
    print(f"Error: {e}")
    return False
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

## 配置文件

在 `quark_config.json` 的 `plugins` 中配置插件参数:

```json
{
  "plugins": {
    "emby": {
      "url": "http://your-emby-server:8096",
      "token": "YOUR_EMBY_TOKEN"
    }
  }
}
```

当插件代码正确赋值 `default_config` 时，首次运行会自动补充缺失的键。

## 🤝 贡献者

| 插件    | 说明                 | 贡献者                                  |
| ------- | -------------------- | --------------------------------------- |
| plex.py | 自动刷新 Plex 媒体库 | [zhazhayu](https://github.com/zhazhayu) |
| alist_strm_gen.py | 自动生成strm | [xiaoQQya](https://github.com/xiaoQQya) |
| alist_sync.py | 调用 alist 实现跨网盘转存 | [jenfonro](https://github.com/jenfonro) |
