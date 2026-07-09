# api.py — 豆瓣榜单 API 调用
import time
import re
import urllib.parse
import logging
import requests
from threading import Lock

DOUBAN_BASE = "https://m.douban.com/rexxar/api/v2"

_PATH_MAP = {
    "movie/hot": ("movie", "热门"),
    "movie/latest": ("movie", "最新"),
    "movie/top": ("movie", "豆瓣高分"),
    "movie/underrated": ("movie", "冷门佳片"),
    "tv/drama": ("tv", "tv"),
    "tv/variety": ("tv", "show"),
}

_TV_TYPE_MAP = {
    "综合": "tv",
    "国产剧": "tv_domestic",
    "欧美剧": "tv_american",
    "日剧": "tv_japanese",
    "韩剧": "tv_korean",
    "动画": "tv_animation",
    "纪录片": "tv_documentary",
    "国内": "show_domestic",
    "国外": "show_foreign",
}

CATEGORIES = {
    "movie": {
        "name": "电影",
        "icon": "🎬",
        "subs": {
            "热门电影": {"path": "movie/hot", "types": ["全部", "华语", "欧美", "韩国", "日本"]},
            "最新电影": {"path": "movie/latest", "types": ["全部", "华语", "欧美", "韩国", "日本"]},
            "豆瓣高分": {"path": "movie/top", "types": ["全部", "华语", "欧美", "韩国", "日本"]},
            "冷门佳片": {"path": "movie/underrated", "types": ["全部", "华语", "欧美", "韩国", "日本"]},
        },
    },
    "tv": {
        "name": "电视剧",
        "icon": "📺",
        "subs": {
            "热门剧集": {"path": "tv/drama", "types": ["综合", "国产剧", "欧美剧", "日剧", "韩剧", "动画", "纪录片"]},
        },
    },
    "variety": {
        "name": "综艺",
        "icon": "🎭",
        "subs": {
            "热门综艺": {"path": "tv/variety", "types": ["综合", "国内", "国外"]},
        },
    },
}

_douban_cache = {}
_douban_lock = Lock()
_DOUBAN_TTL = 3600
_DOUBAN_CACHE_MAX = 100

_session = requests.Session()
_session.headers.update({
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15",
    "Accept": "application/json",
})


def _prune_cache():
    if len(_douban_cache) <= _DOUBAN_CACHE_MAX:
        return
    sorted_keys = sorted(_douban_cache.keys(), key=lambda k: _douban_cache[k][0])
    to_remove = len(_douban_cache) - _DOUBAN_CACHE_MAX
    for k in sorted_keys[:to_remove]:
        del _douban_cache[k]


def _parse_year(year_str):
    if not year_str:
        return 0
    try:
        if isinstance(year_str, int):
            return year_str
        s = str(year_str).strip()
        match = re.search(r"(\d{4})", s)
        if match:
            return int(match.group(1))
        return 0
    except Exception:
        return 0


def get_douban_list(path, sub_type, limit=20, min_rating=0, sort_by="rating", year_from=0, year_to=0):
    """调用豆瓣移动端 API 获取榜单"""
    cache_key = "{}/{}/{}/{}/{}/{}/{}".format(path, sub_type, limit, min_rating, sort_by, year_from, year_to)
    now = time.time()
    with _douban_lock:
        if cache_key in _douban_cache:
            ct, cd = _douban_cache[cache_key]
            if now - ct < _DOUBAN_TTL:
                return cd

    if path not in _PATH_MAP:
        logging.warning(f"douban: 未知路径 {path}")
        return []
    api_type, category = _PATH_MAP[path]

    if api_type == "movie":
        douban_type = sub_type
    else:
        douban_type = _TV_TYPE_MAP.get(sub_type, "tv")
        if sub_type == "综合" and category == "show":
            douban_type = "show"

    params = {
        "category": category,
        "type": douban_type,
        "start": 0,
        "limit": limit * 3,
    }
    encoded = urllib.parse.urlencode(params)
    url = "{}/subject/recent_hot/{}?{}".format(DOUBAN_BASE, api_type, encoded)

    try:
        resp = _session.get(url, timeout=15, headers={"Referer": "https://m.douban.com/"})
        resp.raise_for_status()
        data = resp.json()
        items = data.get("items") or data.get("subjects") or []
        result = [
            {
                "title": i.get("title", ""),
                "rating": i.get("rating", {}).get("value", 0),
                "year": _parse_year(i.get("year", "")),
            }
            for i in items
            if i.get("title")
        ]

        if min_rating > 0:
            result = [r for r in result if r["rating"] >= min_rating]
        if year_from > 0:
            result = [r for r in result if r["year"] >= year_from]
        if year_to > 0:
            result = [r for r in result if r["year"] <= year_to]

        if sort_by == "rating":
            result.sort(key=lambda x: x["rating"], reverse=True)
        elif sort_by == "year":
            result.sort(key=lambda x: x["year"], reverse=True)

        result = result[:limit]

        with _douban_lock:
            _douban_cache[cache_key] = (now, result)
            _prune_cache()
        logging.info(f"douban: {path}/{sub_type} → {len(result)} 条 (筛选后)")
        return result
    except Exception as e:
        logging.error(f"douban 获取错误: {e}")
        return []
