#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Trakt 服务模块
基于 TMDB ID 映射到 Trakt 节目，并使用节目级 aired_time + timezone 获取精确播出时间，
再转换为本地时区的统一播出时间（格式：HH:MM）。
"""

import logging
from datetime import datetime, date, time as dtime
from typing import Optional, Dict, Any

import requests

try:
    # Python 3.9+ 标准库时区支持
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - 兼容极老环境
    ZoneInfo = None  # type: ignore

logger = logging.getLogger(__name__)


class TraktService:
    """
    Trakt API 轻量封装：
    - 通过 TMDB ID 查找 Trakt 节目
    - 获取节目级播出时间 aired_time + timezone
    - 将播出地时区的 daily airtime 转换为本地时区 HH:MM
    """

    def __init__(self, client_id: Optional[str] = None, base_url: str = "https://api.trakt.tv"):
        self.client_id = (client_id or "").strip()
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        # 统一请求头
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "trakt-api-version": "2",
                "trakt-api-key": self.client_id or "",
            }
        )

    def is_configured(self) -> bool:
        """检查 Trakt 是否已配置有效的 Client ID。"""
        return bool(self.client_id)

    # ----------------- HTTP 基础封装 -----------------
    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Optional[Any]:
        """发起 GET 请求，失败时记录日志并返回 None，不抛出到上层。"""
        if not self.is_configured():
            return None
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(url, params=params or {}, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"Trakt GET 请求失败: {url}, err={e}")
            return None

    # ----------------- 节目级信息 -----------------
    def get_show_by_tmdb_id(self, tmdb_id: int) -> Optional[Dict[str, Any]]:
        """
        通过 TMDB ID 查找 Trakt 节目。

        返回结构示例：
        {
          "trakt_id": 195268,
          "slug": "it-welcome-to-derry",
          "title": "IT: Welcome to Derry",
          "year": 2025
        }
        """
        try:
            if not self.is_configured():
                return None
            if not tmdb_id:
                return None
            path = f"/search/tmdb/{int(tmdb_id)}"
            data = self._get(path, params={"type": "show"})
            if not data:
                return None
            # Trakt 搜索返回列表，取第一个 show 结果
            item = None
            for entry in data:
                if entry.get("type") == "show" and entry.get("show"):
                    item = entry.get("show") or {}
                    break
            if not item:
                return None
            return {
                "trakt_id": item.get("ids", {}).get("trakt"),
                "slug": item.get("ids", {}).get("slug"),
                "title": item.get("title") or "",
                "year": item.get("year"),
            }
        except Exception as e:
            logger.warning(f"通过 TMDB ID 获取 Trakt 节目失败: tmdb_id={tmdb_id}, err={e}")
            return None

    def get_show_airtime(self, trakt_show_id: Any) -> Optional[Dict[str, str]]:
        """
        获取节目级播出时间信息：aired_time + timezone。

        Trakt 节目详情通常包含:
        {
          "airs": {
            "day": "sunday",
            "time": "21:00",
            "timezone": "America/New_York"
          },
          ...
        }
        """
        try:
            if not self.is_configured():
                return None
            if not trakt_show_id:
                return None
            path = f"/shows/{trakt_show_id}"
            data = self._get(path, params={"extended": "full"})
            if not data:
                return None
            airs = data.get("airs") or {}
            aired_time = (airs.get("time") or "").strip()
            timezone = (airs.get("timezone") or "").strip()
            if not aired_time or not timezone:
                return None
            return {"aired_time": aired_time, "timezone": timezone}
        except Exception as e:
            logger.warning(f"获取 Trakt 节目播出时间失败: trakt_show_id={trakt_show_id}, err={e}")
            return None

    # ----------------- 时区转换 -----------------
    def convert_show_airtime_to_local(
        self, aired_time: str, source_tz: str, local_tz: str
    ) -> Optional[str]:
        """
        将播出地时区的 daily airtime 转换为本地时区的 HH:MM。

        参数:
            aired_time: 播出地时间（字符串形式，如 '21:00'）
            source_tz: 播出地时区（如 'America/New_York'）
            local_tz: 本地时区（如 'Asia/Shanghai'）
        返回:
            本地时区 HH:MM 字符串；失败时返回 None
        """
        try:
            aired_time = (aired_time or "").strip()
            source_tz = (source_tz or "").strip()
            local_tz = (local_tz or "").strip()
            if not aired_time or not source_tz or not local_tz:
                return None
            if ZoneInfo is None:
                # 环境不支持 zoneinfo 时，退化为直接返回原始播出时间
                return aired_time

            # 解析 HH:MM
            try:
                hh, mm = [int(x) for x in aired_time.split(":")]
            except Exception:
                return None

            # 使用任意日期承载“每日播出时间”含义，这里选择今天
            today = date.today()
            naive_dt = datetime.combine(today, dtime(hour=hh, minute=mm))
            try:
                src_zone = ZoneInfo(source_tz)
                dst_zone = ZoneInfo(local_tz)
            except Exception:
                # 时区字符串非法时，保守返回原 time
                return aired_time

            src_dt = naive_dt.replace(tzinfo=src_zone)
            local_dt = src_dt.astimezone(dst_zone)
            return local_dt.strftime("%H:%M")
        except Exception as e:
            logger.warning(
                f"转换节目播出时间到本地时区失败: aired_time={aired_time}, source_tz={source_tz}, local_tz={local_tz}, err={e}"
            )
            return None


# 方便其它模块直接导入一个全局实例时再按需注入 client_id
trakt_service: Optional[TraktService] = None


