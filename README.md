# 夸克网盘自动转存

对于一些持续更新的资源，隔段时间去转存十分麻烦。

定期执行本脚本自动转存、文件名整理，配合 Alist, rclone, Emby 可达到自动追更的效果。🥳

![run_log](img/run_log.png)

## 功能

- [x] 兼容青龙
- [x] 支持多组任务
- [x] 支持分享链接的子目录
- [x] 记录失效分享并跳过任务
- [x] 目标目录不存在时自动新建
- [x] 正则过滤要转存的文件名
- [x] 跳过已转存过的文件
- [x] 目标目录文件名整理，正则替换
- [x] 任务完成后推送通知
- [x] 任务结束期限，期限后不执行此任务
- [x] 追更或整理后自动刷新 Emby 媒体库

## 使用

### 程序配置

首次运行程序将从本仓库下载 `quark_config.json` 配置模版：

```json
{
  "cookie": "夸克网盘网页cookie",
  "push_config": {
    "QYWX_AM": "",
    "其他推送渠道//此项可删": "配置方法同青龙"
  },
  "emby": {
    "url": "http://yourdomain.com:8096",
    "apikey": "" // 在后台 高级-API秘钥 中生成
  },
  "tasklist": [
    {
      "taskname": "鸣xx年",
      "shareurl": "https://pan.quark.cn/s/39xxxx35#/list/share/17xxxx72-鸣xx年",
      "savepath": "/video/tv/鸣xx年/S01",
      "pattern": "^广告内容(\\d+).(mp4|mkv)",
      "replace": "\\1.\\2",
      "enddate": "2024-01-30", //可选
      "emby_id": "",           //可选
      // 以下字段无需手动配置
      "savepath_fid": "0833xxxx712d7", // 程序运行获得的保存目录id
      "shareurl_ban": "分享地址已失效" // 记录分享是否失效；更新链接后请手动删去，如有此字段将跳过任务
    }
  ]
}
```

### 正则整理示例

| pattern | replace | 效果 |
| ------- |---------|------|
| `^【电影TT】形似走肉(\\d+).(mp4\|mkv)` | `\\1.\\2` | 【电影TT】形似走肉01.mp4 → 01.mp4<br>【电影TT】形似走肉02.mkv → 02.mkv |
| `^(\\d+).mp4` | `S02E\\1.mp4` | 01.mp4 → S02E01.mp4 |

#### 参考资料：

- [正则表达式30分钟入门教程](https://deerchao.cn/tutorials/regex/regex.htm)
- 注意`\`和[字符转义](https://deerchao.cn/tutorials/regex/regex.htm#escape)：如`\d`写作`\\d`，匹配字符`.`写作`\.`
- [后向引用](https://deerchao.cn/tutorials/regex/regex.htm#backreference)：有些语言写作`$1`，Python中写作`\1`，转义后为`\\1`

### 青龙拉库

1. 拉库命令：

    ```
    ql repo https://github.com/Cp0204/quark_auto_save.git "quark" "" "sendNotify"
    ```

2. 首次运行程序将从本仓库下载配置模版。

3. 脚本管理中，手动编辑 `quark_config.json` 配置文件。

## 打赏

如果这个项目让你受益，你可以打赏我1块钱，让我知道开源有价值。谢谢！

![WeChatPay](img/wechat_pay_qrcode.png)