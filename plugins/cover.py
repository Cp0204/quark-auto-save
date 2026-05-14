#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
夸克视频封面插件
功能：根据转存完(重命名)后的视频，自动获取并上传视频封面到夸克网盘
"""
import os
import re
import requests
import time


class Cover:
    # 插件名
    plugin_name = "cover"

    # 1. 插件全局配置
    default_config = {
        "logger_debuger": "True",  # 是否打印详细日志,True打印详细日志，False或其他只打印结果
        "save_dir": "/app/covers",  # 下载目录
    }

    # 2. 任务独立配置
    default_task_config = {
        "enable": True,  # 是否启用该任务的封面上传
    }

    # 3. 插件激活标志
    is_active = False

    def __init__(self, **kwargs):
        """
        插件初始化
        :param kwargs: 传入的是配置文件中 plugins 目录下对应插件的参数
        """
        # 检查 kwargs 是否包含所有 default_config 中的必要参数
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
            # 确保临时目录存在
            if not os.path.exists(self.save_dir):
                try:
                    os.makedirs(self.save_dir, exist_ok=True)
                except Exception as e:
                    print(f"{self.plugin_name}: 创建临时目录失败: {e}")
                    return

            print(f"{self.plugin_name}: 已激活，保存目录: {self.save_dir}")     
            self.is_active = True

    def _log(self, msg):
        if self.logger_debuger == 'True':
            print(f"{self.plugin_name}: {msg}")

    def get_video_cover(self, account, video_fid):
        """
        获取视频封面的下载链接
        :param account: 夸克账号实例
        :param video_fid: 视频文件ID
        :return: (封面下载URL, Cookie)，失败返回(None, None)
        """
        try:
            # 获取视频下载链接（包含封面信息）
            download_return, cookie = account.download([video_fid])

            if download_return.get("code") == 0 and download_return.get("data"):
                download_url = download_return["data"][0].get("download_url", "")

                # 从下载URL中提取视频预览图URL
                # 夸克API通常会在下载链接附近提供视频缩略图
                # 这里我们尝试获取视频信息
                video_data = download_return["data"][0]

                if video_data.get("big_thumbnail"):
                    return video_data["big_thumbnail"], cookie
                if video_data.get("preview_url"):
                    return video_data["preview_url"], cookie
                if video_data.get("thumbnail"):
                    return video_data["thumbnail"], cookie

                # 如果都没有，尝试构造预览URL
                # 夸克视频预览URL通常格式如下
                # 注意：这是推测的URL格式，可能需要根据实际API调整
                preview_url = None

                self._log(f"{self.plugin_name}: 警告 - 未找到视频封面URL，fid={video_fid}")
                return preview_url, cookie

        except Exception as e:
            self._log(f"{self.plugin_name}: 获取视频封面出错: {e}")
            return None, None

    def download_cover(self, cover_url, save_path, account, cookie=None):
        """
        下载封面图片到本地
        :param cover_url: 封面URL
        :param save_path: 本地保存路径
        :param account: 夸克账号实例
        :param cookie: 可选，使用指定的Cookie（优先级高于account.cookie）
        :return: 成功返回True，失败返回False
        """
        try:
            if not cover_url:
                self._log(f"{self.plugin_name}: 封面URL为空，跳过下载")
                return False

            headers = {
                "User-Agent": account.USER_AGENT if account else "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }

            # 优先使用传入的cookie，否则使用账号的cookie
            cookie_to_use = cookie if cookie else (account.cookie if account and hasattr(account, 'cookie') else None)
            
            if cookie_to_use:
                headers["Cookie"] = cookie_to_use
                # 检查cookie是否有效
                if "__uid" not in cookie_to_use:
                    self._log(f"{self.plugin_name}: 警告 - Cookie中缺少__uid参数")
                    self._log(f"{self.plugin_name}: Cookie内容: {cookie_to_use[:100]}...")
            else:
                self._log(f"{self.plugin_name}: 警告 - Cookie为空")

            self._log(f"{self.plugin_name}: 请求头: {dict(headers)}")
            # 禁用自动重定向，手动处理
            response = requests.get(cover_url, headers=headers, timeout=30, allow_redirects=False)

            # 处理重定向
            redirect_count = 0
            max_redirects = 10
            while response.status_code in [301, 302, 303, 307, 308] and redirect_count < max_redirects:
                redirect_count += 1
                redirect_url = response.headers.get('Location')
                if not redirect_url:
                    self._log(f"{self.plugin_name}: 重定向失败: 未找到Location头")
                    return False

                self._log(f"{self.plugin_name}: 重定向 {redirect_count}: {response.status_code} -> {redirect_url}")
                response = requests.get(redirect_url, headers=headers, timeout=30, allow_redirects=False)

            if response.status_code == 200:
                self.save_file(save_path, response)
                return True
            else:
                self._log(f"{self.plugin_name}: 封面地址: {cover_url}")
                self._log(f"{self.plugin_name}: 封面下载失败: HTTP {response.status_code}")
                self._log(f"{self.plugin_name}: 响应内容: {response.text[:200]}")
                return False

        except Exception as e:
            self._log(f"{self.plugin_name}: 下载封面出错: {e}")
            return False

    def save_file(self, save_path, response):
        try:
            # 1. 检查响应
            self._log(f"HTTP 状态码：{response.status_code}")
            self._log(f"内容大小：{len(response.content)} bytes")
            
            # 2. 确保目录存在
            save_dir = os.path.dirname(save_path)
            if save_dir:
                os.makedirs(save_dir, exist_ok=True)
                self._log(f"目录已创建/确认：{save_dir}")
            
            # 3. 获取绝对路径
            abs_path = os.path.abspath(save_path)
            self._log(f"完整路径：{abs_path}")
            
            # 4. 写入文件
            with open(save_path, "wb") as f:
                written = f.write(response.content)
                self._log(f"成功写入 {written} bytes")
            
            # 5. 验证文件
            if os.path.exists(save_path):
                file_size = os.path.getsize(save_path)
                self._log(f"✓ 文件已创建，大小：{file_size} bytes")
            else:
                self._log("✗ 文件创建失败")
                
        except PermissionError as e:
            self._log(f"❌ 权限错误：{e}")
        except FileNotFoundError as e:
            self._log(f"❌ 路径错误：{e}")
        except Exception as e:
            self._log(f"❌ 未知错误：{e}")
            import traceback
            traceback.print_exc()
    def run(self, task, **kwargs):
        """
        核心功能：单个转存任务成功后触发
        :param task: 当前正在执行的任务字典
        :param kwargs: 包含 account (账号实例) 和 tree (本次转存成功的文件树)
        :return: 修改后的 task
        """
        try:
            # 获取当前任务的插件设置
            task_config = task.get("addition", {}).get(
                self.plugin_name, self.default_task_config
            )
            
            if not task_config.get("enable"):
                return task
            
            account = kwargs.get("account")
            tree = kwargs.get("tree")
            
            if not account or not tree:
                self._log(f"{self.plugin_name}: 缺少必要的参数，跳过")
                return task
            
            # 遍历文件树，处理视频文件
            self._log(f"{self.plugin_name}: 开始处理视频封面...")
            
            nodes = tree.all_nodes_itr()
            video_count = 0
            success_count = 0
            
            for node in nodes:
                file_data = node.data
                
                # 跳过目录和根节点
                if file_data.get("is_dir", True) or node.is_root():
                    continue
                
                # 只处理视频文件
                file_name = file_data.get("file_name", "")
                file_ext = os.path.splitext(file_name)[1].lower()
                
                if file_ext not in [".mp4", ".mkv", ".avi", ".mov", ".m4v", ".ts", ".flv"]:
                    continue

                video_count += 1
                video_fid = file_data.get("fid")
                video_path = file_data.get("path", "")

                # 生成封面文件名：视频文件名-thumb.webp
                cover_filename = f"{file_name}-thumb.webp"

                self._log(f"{self.plugin_name}: 处理视频 {video_count}: {file_name}")

                # 获取视频封面URL和最新Cookie
                cover_url, latest_cookie = self.get_video_cover(account, video_fid)

                if not cover_url:
                    self._log(f"{self.plugin_name}: 跳过 {file_name} - 无法获取封面")
                    continue

                # 下载封面到本地（使用最新Cookie），保持原视频的目录结构
                # 去掉开头的斜杠和文件名，只保留目录路径
                relative_dir = os.path.dirname(video_path).lstrip("/")
                temp_cover_path = os.path.join(
                    self.save_dir,
                    relative_dir,
                    cover_filename
                )

                # 确保目录存在
                save_dir = os.path.dirname(temp_cover_path)
                if not os.path.exists(save_dir):
                    os.makedirs(save_dir, exist_ok=True)

                if self.download_cover(cover_url, temp_cover_path, account, latest_cookie):
                    success_count += 1

            print(f"{self.plugin_name}: 处理完成 - 视频: {video_count}, 成功: {success_count}")

            return task

        except Exception as e:
            print(f"{self.plugin_name}: 运行出错: {e}")
            import traceback
            traceback.print_exc()
            return task
