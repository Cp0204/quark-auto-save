<div align="center">

![quark-logo](img/icon.png)

# 夸克网盘自动转存

夸克网盘签到、自动转存、命名整理、发推送提醒和刷新媒体库一条龙。

对于一些持续更新的资源，隔段时间去转存十分麻烦。

定期执行本脚本自动转存、文件名整理，配合 [SmartStrm](https://github.com/Cp0204/SmartStrm) / [OpenList](https://github.com/OpenListTeam/OpenList) , Emby 可达到自动追更的效果。🥳


[![wiki][wiki-image]][wiki-url] [![github releases][gitHub-releases-image]][github-url] [![docker pulls][docker-pulls-image]][docker-url] [![docker image size][docker-image-size-image]][docker-url]

[wiki-image]: https://img.shields.io/badge/wiki-Documents-green?logo=github
[gitHub-releases-image]: https://img.shields.io/github/v/release/Cp0204/quark-auto-save?logo=github
[docker-pulls-image]: https://img.shields.io/docker/pulls/cp0204/quark-auto-save?logo=docker&&logoColor=white
[docker-image-size-image]: https://img.shields.io/docker/image-size/cp0204/quark-auto-save?logo=docker&&logoColor=white
[github-url]: https://github.com/Cp0204/quark-auto-save
[docker-url]: https://hub.docker.com/r/cp0204/quark-auto-save
[wiki-url]: https://github.com/Cp0204/quark-auto-save/wiki

![run_log](img/run_log.png)

</div>

> [!CAUTION]
> ⛔️⛔️⛔️ 注意！资源不会每时每刻更新，**严禁设定过高的定时运行频率！** 以免账号风控和给夸克服务器造成不必要的压力。雪山崩塌，每一片雪花都有责任！

> [!NOTE]
> 开发者≠客服，开源免费≠帮你解决使用问题；本项目 Wiki 已经相对完善，遇到问题请先翻阅 Issues 和 Wiki ，请勿盲目发问。

## 功能

- 部署方式
  - [x] 可能~~兼容青龙~~
  - [x] Docker 部署，WebUI 配置

- 分享链接
  - [x] 支持分享链接的子目录
  - [x] 记录失效分享并跳过任务
  - [x] 支持需提取码的分享链接 <sup>[?](https://github.com/Cp0204/quark-auto-save/wiki/使用技巧集锦#支持需提取码的分享链接)</sup>
  - [x] 智能搜索资源并自动填充 <sup>[?](https://github.com/Cp0204/quark-auto-save/wiki/CloudSaver搜索源)</sup>

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
  - [x] 媒体库模块化，用户可很方便地[开发自己的媒体库hook模块](./plugins)

- 其它
  - [x] 每日签到领空间 <sup>[?](https://github.com/Cp0204/quark-auto-save/wiki/使用技巧集锦#每日签到领空间)</sup>
  - [x] 支持多个通知推送渠道 <sup>[?](https://github.com/Cp0204/quark-auto-save/wiki/通知推送服务配置)</sup>
  - [x] 支持多账号（多账号签到，仅首账号转存）

## 部署

### Docker 部署

Docker 部署提供 WebUI 管理配置，图形化配置已能满足绝大多数需求。部署命令：

```shell
docker run -d \
  --name quark-auto-save \
  -p 5005:5005 \ # 映射端口，:前的可以改，即部署后访问的端口，:后的不可改
  -e WEBUI_USERNAME=admin \
  -e WEBUI_PASSWORD=admin123 \
  -v ./quark-auto-save/config:/app/config \ # 必须，配置持久化
  -v ./quark-auto-save/media:/media \ # 可选，模块alist_strm_gen生成strm使用
  --network bridge \
  --restart unless-stopped \
  cp0204/quark-auto-save:latest
  # registry.cn-shenzhen.aliyuncs.com/cp0204/quark-auto-save:latest # 国内镜像地址
```

docker-compose.yml

```yaml
name: quark-auto-save
services:
  quark-auto-save:
    image: cp0204/quark-auto-save:latest
    container_name: quark-auto-save
    network_mode: bridge
    ports:
      - 5005:5005
    restart: unless-stopped
    environment:
      WEBUI_USERNAME: "admin"
      WEBUI_PASSWORD: "admin123"
    volumes:
      - ./quark-auto-save/config:/app/config
      - ./quark-auto-save/media:/media
```

管理地址：http://yourhost:5005

| 环境变量         | 默认       | 备注                                     |
| ---------------- | ---------- | ---------------------------------------- |
| `WEBUI_USERNAME` | `admin`    | 管理账号                                 |
| `WEBUI_PASSWORD` | `admin123` | 管理密码                                 |
| `PORT`           | `5005`     | 管理后台端口                             |
| `PLUGIN_FLAGS`   |            | 插件标志，如 `-emby,-aria2` 禁用某些插件 |

#### 一键更新

```shell
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock containrrr/watchtower -cR quark-auto-save
```

<details open>
<summary>WebUI 预览</summary>

![screenshot_webui](img/screenshot_webui-1.png)

![screenshot_webui](img/screenshot_webui-2.png)

</details>

## 使用说明

### 正则处理示例

| pattern                                | replace                 | 效果                                                                   |
| -------------------------------------- | ----------------------- | ---------------------------------------------------------------------- |
| `.*`                                   |                         | 无脑转存所有文件，不整理                                               |
| `\.mp4$`                               |                         | 转存所有 `.mp4` 后缀的文件                                             |
| `^【电影TT】花好月圆(\d+)\.(mp4\|mkv)` | `\1.\2`                 | 【电影TT】花好月圆01.mp4 → 01.mp4<br>【电影TT】花好月圆02.mkv → 02.mkv |
| `^(\d+)\.mp4`                          | `S02E\1.mp4`            | 01.mp4 → S02E01.mp4<br>02.mp4 → S02E02.mp4                             |
| `$TV`                                  |                         | [魔法匹配](#魔法匹配)剧集文件                                          |
| `^(\d+)\.mp4`                          | `{TASKNAME}.S02E\1.mp4` | 01.mp4 → 任务名.S02E01.mp4                                             |

更多正则使用说明：[正则处理教程](https://github.com/Cp0204/quark-auto-save/wiki/正则处理教程)

> [!TIP]
>
> **魔法匹配和魔法变量**：在正则处理中，我们定义了一些“魔法匹配”模式，如果 表达式 的值以 $ 开头且 替换式 留空，程序将自动使用预设的正则表达式进行匹配和替换。
>
> 自 v0.6.0 开始，支持更多以 {} 包裹的我称之为“魔法变量”，可以更灵活地进行重命名。
>
> 更多说明请看[魔法匹配和魔法变量](https://github.com/Cp0204/quark-auto-save/wiki/魔法匹配和魔法变量)

### 刷新媒体库

在有新转存时，可触发完成相应功能，如自动刷新媒体库、生成 .strm 文件等。配置指南：[插件配置](https://github.com/Cp0204/quark-auto-save/wiki/插件配置)

媒体库模块以插件的方式的集成，如果你有兴趣请参考[插件开发指南](https://github.com/Cp0204/quark-auto-save/tree/main/plugins)。

### 更多使用技巧

请参考 Wiki ：[使用技巧集锦](https://github.com/Cp0204/quark-auto-save/wiki/使用技巧集锦)

## 生态项目

以下展示 QAS 生态项目，包括官方项目和第三方项目。

### 官方项目

* [QAS一键推送助手](https://greasyfork.org/zh-CN/scripts/533201-qas一键推送助手)

  油猴脚本，在夸克网盘分享页面添加推送到 QAS 的按钮

* [SmartStrm](https://github.com/Cp0204/SmartStrm)

  STRM 文件生成工具，用于转存后处理，媒体免下载入库播放。

### 第三方开源项目

> [!TIP]
>
> 以下第三方开源项目均由社区开发并保持开源，与 QAS 作者无直接关联。在部署到生产环境前，请自行评估相关风险。
>
> 如果您有新的项目没有在此列出，可以通过 Issues 提交。

* [nonebot-plugin-quark-autosave](https://github.com/fllesser/nonebot-plugin-quark-autosave)

  QAS Telegram 机器人，快速管理自动转存任务

* [Astrbot_plugin_quarksave](https://github.com/lm379/astrbot_plugin_quarksave)

  AstrBot 插件，调用 quark_auto_save 实现自动转存资源到夸克网盘

## 打赏

如果这个项目让你受益，你可以无偿赠与我1块钱，让我知道开源有价值。谢谢！

![WeChatPay](https://cdn.jsdelivr.net/gh/Cp0204/Cp0204@main/img/wechat_pay_qrcode.png)

## 声明

本项目为个人兴趣开发，旨在通过程序自动化提高网盘使用效率。

程序没有任何破解行为，只是对于夸克已有的API进行封装，所有数据来自于夸克官方API；本人不对网盘内容负责、不对夸克官方API未来可能的变动导致的影响负责，请自行斟酌使用。

开源仅供学习与交流使用，未盈利也未授权商业使用，严禁用于非法用途。

## Sponsor

CDN acceleration and security protection for this project are sponsored by Tencent EdgeOne.

<a href="https://edgeone.ai/?from=github" target="_blank"><img title="Best Asian CDN, Edge, and Secure Solutions - Tencent EdgeOne" src="https://edgeone.ai/media/34fe3a45-492d-4ea4-ae5d-ea1087ca7b4b.png" width="300"></a>