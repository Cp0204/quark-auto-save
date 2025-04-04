#!/usr/bin/python3
# -*- encoding: utf-8 -*-
"""
@File    :   decorators.py
@Desc    :   定义装饰器
@Version :   v1.0
@Time    :   2025/04/04
@Author  :   xiaoQQya
@Contact :   xiaoQQya@126.com
"""
import functools


def hook_action(name):
    """回调动作装饰器

    Args:
        name (str): 动作名称
    """
    def decorator(func):
        func.__hook_action_name__ = name

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper

    return decorator
