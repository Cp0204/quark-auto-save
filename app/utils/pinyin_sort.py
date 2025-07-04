# -*- coding: utf-8 -*-
"""
拼音排序工具模块
用于将中文字符串转换为拼音后进行排序
"""

try:
    from pypinyin import lazy_pinyin, Style
    PYPINYIN_AVAILABLE = True
except ImportError:
    PYPINYIN_AVAILABLE = False
    print("Warning: pypinyin not available, falling back to simple string sort")

def to_pinyin_for_sort(text):
    """
    将字符串转换为拼音用于排序
    保留非中文字符，只转换中文字符为拼音

    Args:
        text (str): 要转换的字符串

    Returns:
        str: 转换后的拼音字符串，用于排序比较
    """
    if not text:
        return ''

    if PYPINYIN_AVAILABLE:
        # 使用pypinyin库进行转换
        # Style.NORMAL: 普通风格，不带声调
        # errors='default': 保留无法转换的字符
        pinyin_list = lazy_pinyin(text, style=Style.NORMAL, errors='default')
        return ''.join(pinyin_list).lower()
    else:
        # 如果pypinyin不可用，直接返回小写字符串
        return text.lower()

def pinyin_compare(a, b):
    """
    拼音排序比较函数
    
    Args:
        a (str): 第一个字符串
        b (str): 第二个字符串
        
    Returns:
        int: 比较结果 (-1, 0, 1)
    """
    pinyin_a = to_pinyin_for_sort(a)
    pinyin_b = to_pinyin_for_sort(b)
    
    if pinyin_a < pinyin_b:
        return -1
    elif pinyin_a > pinyin_b:
        return 1
    else:
        return 0

def get_filename_pinyin_sort_key(filename):
    """
    为文件名生成拼音排序键
    保持完整内容，只将汉字转换为拼音

    Args:
        filename (str): 文件名

    Returns:
        str: 用于排序的拼音字符串
    """
    return to_pinyin_for_sort(filename)

def pinyin_sort_files(files, key_func=None, reverse=False):
    """
    使用拼音排序对文件列表进行排序
    
    Args:
        files (list): 文件列表
        key_func (callable): 从文件对象中提取文件名的函数，默认为None（假设files是字符串列表）
        reverse (bool): 是否逆序排序
        
    Returns:
        list: 排序后的文件列表
    """
    if key_func is None:
        # 假设files是字符串列表
        return sorted(files, key=to_pinyin_for_sort, reverse=reverse)
    else:
        # 使用key_func提取文件名后进行拼音排序
        return sorted(files, key=lambda x: to_pinyin_for_sort(key_func(x)), reverse=reverse)
