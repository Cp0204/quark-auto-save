<div align="center">

![quark-logo](img/icon.png)

# 夸克网盘自动转存

夸克网盘签到、自动转存、命名整理、发推送提醒和刷新媒体库一条龙。

对于一些持续更新的资源，隔段时间去转存十分麻烦。

定期执行本脚本自动转存、文件名整理，配合 Alist, rclone, Emby 可达到自动追更的效果。🥳


[![github tag][gitHub-tag-image]][github-url] [![docker pulls][docker-pulls-image]][docker-url] [![docker image size][docker-image-size-image]][docker-url]

[gitHub-tag-image]: https://img.shields.io/github/v/tag/Cp0204/quark-auto-save
[docker-pulls-image]: https://img.shields.io/docker/pulls/cp0204/quark-auto-save
[docker-image-size-image]: https://img.shields.io/docker/image-size/cp0204/quark-auto-save
[github-url]: https://github.com/Cp0204/quark-auto-save
[docker-url]: https://hub.docker.com/r/cp0204/quark-auto-save

![run_log](img/run_log.png)

</div>

⛔️⛔️⛔️ 注意！资源不会每时每刻更新，**严禁设定过高的定时运行频率！** 以免账号异常或给夸克服务器造成不必要的压力。雪山崩塌，每一片雪花都有责任！

## 功能

- 部署方式
  - [x] 兼容青龙
  - [x] 支持 Docker 独立部署，WebUI 配置

- 分享链接
  - [x] 支持分享链接的子目录
  - [x] 记录失效分享并跳过任务

- 文件管理
  - [x] 目标目录不存在时自动新建
  - [x] 跳过已转存过的文件
  - [x] 正则过滤要转存的文件名
  - [x] 转存后文件名整理（正则替换）
  - [x] 可选忽略文件后缀

- 任务管理
  - [x] 支持多组任务
  - [x] 任务结束期限，期限后不执行此任务
  - [x] 可单独指定子任务星期几执行

- 媒体库整合
  - [x] 根据任务名搜索 Emby 媒体库
  - [x] 追更或整理后自动刷新 Emby 媒体库

- 其它
  - [x] 每日签到领空间
  - [x] 支持多个通知推送渠道
  - [x] 支持多账号（多账号签到，仅首账号转存）

## 使用

### Docker 部署（推荐）

提供 WebUI 管理配置，~~但目前 WebUI 并不完善，只供辅助使用，你也应该了解如何[手动配置](#程序配置)。~~ WebUI 已能满足绝大多数需求。

```shell
docker run -d \
  --name quark-auto-save \
  -p 5005:5005 \
  -e WEBUI_USERNAME=admin \
  -e WEBUI_PASSWORD=admin123 \
  -v ./quark-auto-save/config:/app/config \
  -v /etc/localtime:/etc/localtime \
  --network bridge \
  --restart unless-stopped \
  cp0204/quark-auto-save:latest
```

管理地址：http://yourhost:5005

| 环境变量         | 默认       | 备注     |
| ---------------- | ---------- | -------- |
| `WEBUI_USERNAME` | `admin`    | 管理账号 |
| `WEBUI_PASSWORD` | `admin123` | 管理密码 |

#### 一键更新

```shell
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock containrrr/watchtower -cR quark-auto-save
```

<details open>
<summary>WebUI 预览</summary>

![screenshot_webui](img/screenshot_webui-1.png)

![screenshot_webui](img/screenshot_webui-2.png)

</details>

### 青龙部署

1. 拉库命令：

    ```
    ql repo https://github.com/Cp0204/quark-auto-save.git "quark" "" "notify"
    ```

2. 首次运行程序将从本仓库下载配置模版。

3. 脚本管理中，手动编辑 `quark_config.json` 配置文件。

> 删除配置文件，且存在青龙环境变量 `QUARK_COOKIE` 时，则仅签到，多账号用换行分隔。

### 程序配置

首次运行脚本将从本仓库下载 `quark_config.json` 配置模版：

```json
{
  "cookie": [ //请用手机验证码登录，CK比较完整！
    "Your pan.quark.cn Cookie1, Only this one will do save task.",
    "Your pan.quark.cn Cookie2, Only sign after this."
  ],
  "push_config": { //无此字段则从环境变量（青龙设置）读取通知设置
    "QUARK_SIGN_NOTIFY": true, //是否发送签到成功通知，也可在环境变量中设置
    "QYWX_AM": "", //企业微信应用通知示例
    "其他推送渠道//此项可删": "配置方法同青龙"
  },
  "emby": {
    "url": "http://yourdomain.com:8096",
    "apikey": "" //在后台 高级-API秘钥 中生成
  },
  "tasklist": [ //无任务则只签到
    {
      "taskname": "鸣xx年",
      "shareurl": "https://pan.quark.cn/s/39xxxx35#/list/share/17xxxx72-鸣xx年",
      "savepath": "/video/tv/鸣xx年/S01",
      "pattern": "^广告内容(\\d+).(mp4|mkv)",
      "replace": "\\1.\\2",
      "enddate": "2024-01-30",  //可选，结束日期
      "emby_id": "",            //可选，缺省时按taskname搜索匹配，为0时强制不匹配
      "ignore_extension": true, //可选，忽略后缀
      "runweek": [1, 2, 3, 4, 6, 7], //可选，指定星期几执行，无此字段则均执行
      "update_subdir": "", // 可选，子目录递归更新的正则表达式，如 "4k|1080p"
      // 以下字段无需配置
      "shareurl_ban": "分享地址已失效" //记录分享是否失效；如有此字段将跳过任务，更新链接后请手动删去
    }
  ]
}
```

### 正则整理示例

| pattern                                | replace      | 效果                                                                   |
| -------------------------------------- | ------------ | ---------------------------------------------------------------------- |
| `.*`                                   |              | 无脑转存所有文件，不整理                                               |
| `\.mp4$`                               |              | 转存所有 `.mp4` 后缀的文件                                             |
| `^【电影TT】形似走肉(\d+)\.(mp4\|mkv)` | `\1.\2`      | 【电影TT】形似走肉01.mp4 → 01.mp4<br>【电影TT】形似走肉02.mkv → 02.mkv |
| `^(\d+)\.mp4`                          | `S02E\1.mp4` | 01.mp4 → S02E01.mp4<br>02.mp4 → S02E02.mp4                             |
| `$TV`                                  |              | [魔法匹配](#魔法匹配)剧集文件                                          |

> [!IMPORTANT]
> 直接写 json 配置注意`\`多加一重[字符转义](https://deerchao.cn/tutorials/regex/regex.htm#escape)：如`\d`写作`\\d`，匹配字符`.`写作`\\.`

#### 参考资料

- [正则表达式30分钟入门教程](https://deerchao.cn/tutorials/regex/regex.htm)

- 替换的[后向引用](https://deerchao.cn/tutorials/regex/regex.htm#backreference)：有些语言写作`$1`，Python中写作`\1`，json 转义后为`\\1`

### 特殊场景使用技巧

#### 忽略后缀

- 当目录已存*01.mp4、02.mp4*，新的源又有*01.mkv、02.mkv、03.mkv*，只希望获得*03.mkv*更新时。

- 一个部剧同时追更两个源，看谁更新快🤪，但两个源的视频格式不一时。

#### 使用青龙通知设置

删去配置文件中的整个 `push_config` 数组。

#### 自动刷新媒体库

同时配置 `emby.url` `emby.apikey` 和任务的 `emby_id` ，将在新存或整理后自动刷新 Emby 媒体库、刷新元数据。

#### 魔法匹配

当任务 `pattern` 值为 `$开头` 且 `replace` 留空时，实际将调用程序预设的正则表达式。

如 `$TV` 可适配和自动整理市面上90%分享剧集的文件名格式，具体实现见代码，欢迎贡献规则。

## 打赏

如果这个项目让你受益，你可以打赏我1块钱，让我知道开源有价值。谢谢！

*由于微信限制，无法反向联系付款人，如非必要微信不回复，项目问题请在 GitHub 提 Issue 。* 😉

![WeChatPay](img/wechat_pay_qrcode.png)

## 声明

本程序为个人兴趣开发，开源仅供学习与交流使用。

程序没有任何破解行为，只是对于夸克已有的API进行封装，所有数据来自于夸克官方API，本人不对网盘内容负责、不对夸克官方API未来可能的改动导致的后果负责。