import requests
import json
from typing import List, Dict, Any


class PanSou:
    """PanSou 资源搜索客户端"""
    
    def __init__(self, server: str):
        self.server = server.rstrip("/") if server else ""
        self.session = requests.Session()
        # 使用标准请求头
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "QASX-PanSouClient/1.0"
        })

    def _request_json(self, url: str, params: dict):
        """发送 GET 请求并解析 JSON 响应"""
        try:
            resp = self.session.get(url, params=params, timeout=15)
            return resp.json()
        except Exception as e:
            return {"success": False, "message": str(e)}

    def _get_pansou_source(self, result_item: dict, merged_item: dict = None) -> str:
        """
        获取PanSou内部来源信息
        返回格式：
        - "tg:频道名称" - 来自Telegram频道，需要+8小时
        - "plugin:插件名" - 来自指定插件，不+8小时  
        - "unknown" - 未知来源，不+8小时
        """
        # 优先从 results 的 channel 字段判断
        if result_item and result_item.get("channel"):
            channel = result_item.get("channel", "").strip()
            if channel:
                return f"tg:{channel}"
        
        # 从 merged_by_type 的 source 字段获取
        if merged_item and merged_item.get("source"):
            source = merged_item.get("source", "").strip()
            if source:
                return source
        
        # 默认返回 unknown
        return "unknown"

    def search(self, keyword: str):
        """
        搜索资源（仅返回夸克网盘结果）
        返回：{"success": True, "data": [{taskname, content, shareurl, tags[], pansou_source}]}
        """
        if not self.server:
            return {"success": False, "message": "PanSou未配置服务器"}

        # 使用已验证的参数：kw + cloud_types=quark + res=all
        params = {
            "kw": keyword,
            "cloud_types": "quark",  # 单个类型用字符串，多个类型用逗号分隔
            "res": "all"
        }
        
        # 优先使用 /api/search 路径
        url = f"{self.server}/api/search"
        result = self._request_json(url, params)
        
        if not result:
            return {"success": False, "message": "PanSou请求失败"}
        
        # 解析响应：兼容 {code, message, data: {results, merged_by_type}} 格式
        payload = result
        if isinstance(result.get("data"), dict):
            payload = result["data"]
        
        # 检查错误码
        if "code" in result and result.get("code") != 0:
            return {"success": False, "message": result.get("message") or "PanSou搜索失败"}
        
        # 解析结果：优先 results，然后 merged_by_type
        cleaned = []
        # 工具：移除标题中的链接
        def strip_links(text: str) -> str:
            if not isinstance(text, str):
                return text
            s = text
            import re
            s = re.sub(r"https?://\S+", "", s)
            s = re.sub(r"\bpan\.quark\.cn/\S+", "", s)
            s = re.sub(r"\s+", " ", s).strip(" -|·,，：:；;" + " ")
            return s.strip()
        
        try:
            # 1) results: 主要结果数组，每个结果包含 title 和 links
            results = payload.get("results", [])
            if isinstance(results, list):
                for result_item in results:
                    if not isinstance(result_item, dict):
                        continue
                    
                    # 从 result_item 获取标题、内容和发布日期
                    title = result_item.get("title", "")
                    title = strip_links(title)
                    content = result_item.get("content", "")
                    datetime_str = result_item.get("datetime", "")  # 获取发布日期
                    
                    # 获取PanSou内部来源
                    pansou_source = self._get_pansou_source(result_item)
                    
                    # 从 links 获取具体链接
                    links = result_item.get("links", [])
                    if isinstance(links, list):
                        for link in links:
                            if isinstance(link, dict):
                                url = link.get("url", "")
                                link_type = link.get("type", "")
                                if url:  # 确保有有效链接
                                    cleaned.append({
                                        "taskname": title,
                                        "content": content,
                                        "shareurl": url,
                                        "tags": [link_type] if link_type else (result_item.get("tags", []) or []),
                                        "publish_date": datetime_str,  # 原始时间（可能是 ISO）
                                        "source": "PanSou",  # 添加来源标识
                                        "pansou_source": pansou_source  # 添加PanSou内部来源
                                    })
            
            # 2) merged_by_type: 兜底解析，使用 note 字段作为标题
            if not cleaned:
                merged = payload.get("merged_by_type")
                if isinstance(merged, dict):
                    for cloud_type, links in merged.items():
                        if isinstance(links, list):
                            for link in links:
                                if isinstance(link, dict):
                                    # 从 merged_by_type 获取链接信息
                                    url = link.get("url", "")
                                    note = link.get("note", "")  # 使用 note 字段作为标题
                                    note = strip_links(note)
                                    datetime_str = link.get("datetime", "")  # 获取发布日期
                                    
                                    # 获取PanSou内部来源
                                    pansou_source = self._get_pansou_source(None, link)
                                    
                                    if url:
                                        cleaned.append({
                                            "taskname": note,
                                            "content": note,  # 如果没有 content，使用 note
                                            "shareurl": url,
                                            "tags": [cloud_type] if cloud_type else [],
                                            "publish_date": datetime_str,  # 原始时间
                                            "source": "PanSou",  # 添加来源标识
                                            "pansou_source": pansou_source  # 添加PanSou内部来源
                                        })
            
            # 3) 直接 data 数组兜底
            if not cleaned and isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        cleaned.append({
                            "taskname": item.get("title", ""),
                            "content": item.get("content", ""),
                            "shareurl": item.get("url", ""),
                            "tags": item.get("tags", []) or [],
                            "publish_date": item.get("datetime", ""),  # 原始时间
                            "source": "PanSou",  # 添加来源标识
                            "pansou_source": "unknown"  # 兜底来源
                        })
                        
        except Exception as e:
            return {"success": False, "message": f"解析PanSou结果失败: {str(e)}"}
        
        # 二次过滤：确保只返回夸克网盘链接
        if cleaned:
            filtered = []
            for item in cleaned:
                try:
                    url = item.get("shareurl", "")
                    tags = item.get("tags", []) or []
                    # 检查是否为夸克网盘
                    is_quark = ("quark" in tags) or ("pan.quark.cn" in url)
                    if is_quark:
                        filtered.append(item)
                except Exception:
                    continue
            cleaned = filtered
        
        if not cleaned:
            return {"success": False, "message": "PanSou搜索无夸克网盘结果"}
        
        # 去重：按 shareurl 归并，保留发布时间最新的记录
        def to_ts(date_str: str) -> float:
            if not date_str:
                return 0
            try:
                s = str(date_str).strip()
                from datetime import datetime
                try:
                    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()
                except Exception:
                    pass
                try:
                    return datetime.strptime(s, "%Y-%m-%d").timestamp()
                except Exception:
                    pass
                try:
                    s2 = s.replace('Z', '+00:00')
                    return datetime.fromisoformat(s2).timestamp()
                except Exception:
                    return 0
            except Exception:
                return 0

        by_url = {}
        for item in cleaned:
            try:
                url = item.get("shareurl", "")
                if not url:
                    continue
                existed = by_url.get(url)
                if not existed:
                    by_url[url] = item
                else:
                    # 比较 publish_date（若不存在则视为0）
                    if to_ts(item.get("publish_date")) > to_ts(existed.get("publish_date")):
                        by_url[url] = item
            except Exception:
                continue

        unique_results = list(by_url.values())
        return {"success": True, "data": unique_results}
