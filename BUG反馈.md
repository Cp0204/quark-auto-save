Bug反馈

https://pan.quark.cn/s/27c3a8221f69
这个链接里面是一个文件夹，然后里面又有文件夹，再往里面是文件
/【16】陈八十25年国考粉笔27季度解析/25年国考粉笔9季度解析/2024-03-24 言语理解【公众号：】.mp4
(.+)公众号(.+)  \1\2
我想把公众号这几个字去掉，但是不生效，我把部分代码弄下来发现是python可以去除的

我又测试了下这个规则发现是可以的
(.+)粉笔(.+)  \1\2

部分代码如下
'''
# 正则文件名匹配
import re
pattern = '(.+)【公众号】(.+)'
replace = '\\1\\2'
file_name = '2024-03-24 资料分析【公众号】.mp4'
if re.search(pattern, file_name):
    # 替换后的文件名
    save_name = (
        re.sub(pattern, replace, file_name)
        if replace != ""
        else file_name
    )
    print(save_name)
'''
