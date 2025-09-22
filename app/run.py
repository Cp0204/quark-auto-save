# !/usr/bin/env python3
# -*- coding: utf-8 -*-
from flask import (
    json,
    Flask,
    url_for,
    session,
    jsonify,
    request,
    redirect,
    Response,
    render_template,
    send_from_directory,
    stream_with_context,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from queue import Queue
from sdk.cloudsaver import CloudSaver
try:
    from sdk.pansou import PanSou
except Exception:
    PanSou = None
from datetime import timedelta, datetime
import subprocess
import requests
import hashlib
import logging
import base64
import sys
import os
import re
import random
import time
import treelib
from functools import lru_cache
from threading import Lock

parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, parent_dir)
from quark_auto_save import Quark
from quark_auto_save import Config, format_bytes

# 添加导入全局extract_episode_number和sort_file_by_name函数
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quark_auto_save import extract_episode_number, sort_file_by_name, chinese_to_arabic, is_date_format

# 导入豆瓣服务
from sdk.douban_service import douban_service

# 导入追剧日历相关模块
from utils.task_extractor import TaskExtractor
from sdk.tmdb_service import TMDBService
from sdk.db import CalendarDB
def enrich_tasks_with_calendar_meta(tasks_info: list) -> list:
    """为任务列表注入日历相关元数据：节目状态、最新季处理后名称、已转存/已播出/本季总集数。
    返回新的任务字典列表，增加以下字段：
    - matched_status: 节目状态（如 播出中 等）
    - latest_season_name: 最新季名称（处理后）
    - season_counts: { transferred_count, aired_count, total_count }
    """
    try:
        if not tasks_info:
            return tasks_info
        db = CalendarDB()
        # 预取 shows 与 seasons 数据
        cur = db.conn.cursor()
        cur.execute('SELECT tmdb_id, status, latest_season_number FROM shows')
        rows = cur.fetchall() or []
        show_meta = {int(r[0]): {'status': r[1], 'latest_season_number': int(r[2])} for r in rows}

        cur.execute('SELECT tmdb_id, season_number, season_name, episode_count FROM seasons')
        rows = cur.fetchall() or []
        season_meta = {}
        for tid, sn, sname, ecount in rows:
            season_meta[(int(tid), int(sn))] = {'season_name': sname or '', 'episode_count': int(ecount or 0)}

        # 统计"已转存集数"：基于转存记录最新进度构建映射（按任务名）
        transferred_by_task = {}
        try:
            rdb = RecordDB()
            cursor = rdb.conn.cursor()
            cursor.execute(
                """
                SELECT task_name, MAX(transfer_time) as latest_transfer_time
                FROM transfer_records
                WHERE task_name NOT IN ('rename', 'undo_rename')
                GROUP BY task_name
                """
            )
            latest_times = cursor.fetchall() or []
            extractor = TaskExtractor()
            for task_name, latest_time in latest_times:
                if latest_time:
                    cursor.execute(
                        """
                        SELECT renamed_to, original_name, transfer_time, modify_date
                        FROM transfer_records
                        WHERE task_name = ? AND transfer_time >= ? AND transfer_time <= ?
                        ORDER BY id DESC
                        """,
                        (task_name, latest_time - 60000, latest_time + 60000)
                    )
                    files = cursor.fetchall() or []
                    best = files[0][0] if files else ''
                    if len(files) > 1:
                        file_list = [{'file_name': f[0], 'original_name': f[1], 'updated_at': f[2]} for f in files]
                        try:
                            best = sorted(file_list, key=sort_file_by_name)[-1]['file_name']
                        except Exception:
                            best = files[0][0]
                    if best:
                        name_wo_ext = os.path.splitext(best)[0]
                        processed = process_season_episode_info(name_wo_ext, task_name)
                        parsed = extractor.extract_progress_from_latest_file(processed)
                        if parsed and parsed.get('episode_number'):
                            # 包含集数的情况
                            transferred_by_task[task_name] = int(parsed['episode_number'])
                        elif parsed and parsed.get('air_date'):
                            # 只有日期的情况：通过查询数据库获取对应日期的最大集数
                            air_date = parsed['air_date']
                            try:
                                # 查找该任务对应的节目名称
                                task_info = next((t for t in tasks_info if (t.get('task_name') or t.get('taskname')) == task_name), None)
                                if task_info:
                                    show_name = (task_info.get('matched_show_name') or task_info.get('show_name') or '').strip()
                                    if show_name:
                                        # 查询该节目在该日期播出的最大集数
                                        cur.execute(
                                            """
                                            SELECT MAX(CAST(episode_number AS INTEGER))
                                            FROM episodes
                                            WHERE show_name = ? AND air_date = ?
                                            """,
                                            (show_name, air_date)
                                        )
                                        result = cur.fetchone()
                                        if result and result[0] is not None:
                                            transferred_by_task[task_name] = int(result[0])
                            except Exception:
                                pass
            rdb.close()
        except Exception:
            transferred_by_task = {}

        # 统计“已播出集数”：读取本地 episodes 表中有 air_date 且 <= 今天的集数
        from datetime import datetime as _dt
        today = _dt.now().strftime('%Y-%m-%d')
        aired_by_show_season = {}
        try:
            cur.execute(
                """
                SELECT tmdb_id, season_number, COUNT(1) FROM episodes
                WHERE air_date IS NOT NULL AND air_date != '' AND air_date <= ?
                GROUP BY tmdb_id, season_number
                """,
                (today,)
            )
            for tid, sn, cnt in (cur.fetchall() or []):
                aired_by_show_season[(int(tid), int(sn))] = int(cnt or 0)
        except Exception:
            aired_by_show_season = {}

        # 注入到任务
        enriched = []
        for t in tasks_info:
            tmdb_id = t.get('match_tmdb_id') or ((t.get('calendar_info') or {}).get('match') or {}).get('tmdb_id')
            task_name = t.get('task_name') or t.get('taskname') or ''
            status = ''
            latest_sn = None
            latest_season_name = ''
            total_count = None
            transferred_count = None
            aired_count = None

            try:
                if tmdb_id and int(tmdb_id) in show_meta:
                    meta = show_meta[int(tmdb_id)]
                    status = meta.get('status') or ''
                    latest_sn = int(meta.get('latest_season_number') or 0)
                    sm = season_meta.get((int(tmdb_id), latest_sn)) or {}
                    latest_season_name = sm.get('season_name') or ''
                    total_count = sm.get('episode_count')
                    aired_count = aired_by_show_season.get((int(tmdb_id), latest_sn))
            except Exception:
                pass

            if task_name in transferred_by_task:
                transferred_count = transferred_by_task[task_name]

            # 将状态本地化：returning_series 用本地已播/总集数判断；其余通过 TMDBService 的单表映射
            try:
                raw = (status or '').strip()
                key = raw.lower().replace(' ', '_')
                if key == 'returning_series':
                    # 新规则：存在 finale 类型 且 已播出集数 ≥ 本季总集数 => 本季终；否则 播出中
                    has_finale = False
                    try:
                        if tmdb_id and latest_sn:
                            cur.execute(
                                """
                                SELECT 1 FROM episodes
                                WHERE tmdb_id=? AND season_number=? AND LOWER(COALESCE(type, '')) LIKE '%finale%'
                                LIMIT 1
                                """,
                                (int(tmdb_id), int(latest_sn))
                            )
                            has_finale = cur.fetchone() is not None
                    except Exception:
                        has_finale = False

                    try:
                        aired = int(aired_count or 0)
                        total = int(total_count or 0)
                    except Exception:
                        aired, total = 0, 0

                    status = '本季终' if (has_finale and total > 0 and aired >= total) else '播出中'
                else:
                    try:
                        # 仅用到静态映射，不触发网络请求
                        _svc = TMDBService(config_data.get('tmdb_api_key', ''), get_poster_language_setting())
                        status = _svc.map_show_status_cn(raw)
                    except Exception:
                        status = raw
            except Exception:
                pass

            t['matched_status'] = status
            t['latest_season_name'] = latest_season_name
            t['season_counts'] = {
                'transferred_count': transferred_count or 0,
                'aired_count': aired_count or 0,
                'total_count': total_count or 0,
            }
            enriched.append(t)
        return enriched
    except Exception:
        return tasks_info

def advanced_filter_files(file_list, filterwords):
    """
    高级过滤函数，支持保留词和过滤词
    
    Args:
        file_list: 文件列表
        filterwords: 过滤规则字符串，支持以下格式：
            - "加更，企划，超前，(1)，mkv，nfo"  # 只有过滤词
            - "期|加更，企划，超前，(1)，mkv，nfo"  # 保留词|过滤词
            - "期，2160P|加更，企划，超前，(1)，mkv，nfo"  # 多个保留词(或关系)|过滤词
            - "期|2160P|加更，企划，超前，(1)，mkv，nfo"  # 多个保留词(并关系)|过滤词
            - "期，2160P|"  # 只有保留词，无过滤词
    
    Returns:
        过滤后的文件列表
    """
    if not filterwords or not filterwords.strip():
        return file_list
    
    # 检查是否包含分隔符 |
    if '|' not in filterwords:
        # 只有过滤词的情况
        filterwords = filterwords.replace("，", ",")
        filterwords_list = [word.strip().lower() for word in filterwords.split(',') if word.strip()]
        
        filtered_files = []
        for file in file_list:
            file_name = file['file_name'].lower()
            file_ext = os.path.splitext(file_name)[1].lower().lstrip('.')
            
            # 检查过滤词是否存在于文件名中，或者过滤词等于扩展名
            if not any(word in file_name for word in filterwords_list) and not any(word == file_ext for word in filterwords_list):
                filtered_files.append(file)
        
        return filtered_files
    
    # 包含分隔符的情况，需要解析保留词和过滤词
    parts = filterwords.split('|')
    if len(parts) < 2:
        # 格式错误，返回原列表
        return file_list
    
    # 最后一个|后面的是过滤词
    filter_part = parts[-1].strip()
    # 前面的都是保留词
    keep_parts = [part.strip() for part in parts[:-1] if part.strip()]
    
    # 解析过滤词
    filterwords_list = []
    if filter_part:
        filter_part = filter_part.replace("，", ",")
        filterwords_list = [word.strip().lower() for word in filter_part.split(',') if word.strip()]
    
    # 解析保留词：每个|分隔的部分都是一个独立的筛选条件
    # 这些条件需要按顺序依次应用，形成链式筛选
    keep_conditions = []
    for part in keep_parts:
        if part.strip():
            if ',' in part or '，' in part:
                # 包含逗号，表示或关系
                part = part.replace("，", ",")
                or_words = [word.strip().lower() for word in part.split(',') if word.strip()]
                keep_conditions.append(("or", or_words))
            else:
                # 不包含逗号，表示单个词
                keep_conditions.append(("single", [part.strip().lower()]))
    
    # 第一步：应用保留词筛选（链式筛选）
    if keep_conditions:
        for condition_type, words in keep_conditions:
            filtered_by_keep = []
            for file in file_list:
                file_name = file['file_name'].lower()
                
                if condition_type == "or":
                    # 或关系：包含任意一个词即可
                    if any(word in file_name for word in words):
                        filtered_by_keep.append(file)
                elif condition_type == "single":
                    # 单个词：必须包含
                    if words[0] in file_name:
                        filtered_by_keep.append(file)
            
            file_list = filtered_by_keep
    
    # 第二步：应用过滤词过滤
    if filterwords_list:
        filtered_files = []
        for file in file_list:
            file_name = file['file_name'].lower()
            file_ext = os.path.splitext(file_name)[1].lower().lstrip('.')
            
            # 检查过滤词是否存在于文件名中，或者过滤词等于扩展名
            if not any(word in file_name for word in filterwords_list) and not any(word == file_ext for word in filterwords_list):
                filtered_files.append(file)
        
        return filtered_files
    
    return file_list


def process_season_episode_info(filename, task_name=None):
    """
    处理文件名中的季数和集数信息

    Args:
        filename: 文件名（不含扩展名）
        task_name: 可选的任务名称，用于判断是否已包含季数信息

    Returns:
        处理后的显示名称
    """

    def task_contains_season_info(task_name):
        """
        判断任务名称中是否包含季数信息
        """
        if not task_name:
            return False

        # 清理任务名称中的连续空格和特殊符号
        clean_name = task_name.replace('\u3000', ' ').replace('\t', ' ')
        clean_name = re.sub(r'\s+', ' ', clean_name).strip()

        # 匹配常见的季数格式
        season_patterns = [
            r'^(.*?)[\s\.\-_]+S\d+$',  # 黑镜 - S07、折腰.S01、音你而来-S02
            r'^(.*?)[\s\.\-_]+Season\s*\d+$',  # 黑镜 - Season 1
            r'^(.*?)\s+S\d+$',  # 快乐的大人 S02
            r'^(.*?)[\s\.\-_]+S\d+E\d+$',  # 处理 S01E01 格式
            r'^(.*?)\s+第\s*\d+\s*季$',  # 处理 第N季 格式
            r'^(.*?)[\s\.\-_]+第\s*\d+\s*季$',  # 处理 - 第N季 格式
            r'^(.*?)\s+第[一二三四五六七八九十零]+季$',  # 处理 第一季、第二季 格式
            r'^(.*?)[\s\.\-_]+第[一二三四五六七八九十零]+季$',  # 处理 - 第一季、- 第二季 格式
        ]

        for pattern in season_patterns:
            if re.match(pattern, clean_name, re.IGNORECASE):
                return True

        return False

    # 匹配 SxxExx 格式（不区分大小写）
    # 支持 S1E1, S01E01, s13e10, S100E1000 等格式
    season_episode_match = re.search(r'[Ss](\d+)[Ee](\d+)', filename)
    if season_episode_match:
        season = season_episode_match.group(1).zfill(2)  # 确保两位数
        episode = season_episode_match.group(2).zfill(2)  # 确保两位数

        # 如果任务名称中已包含季数信息，只显示集数
        if task_contains_season_info(task_name):
            return f"E{episode}"
        else:
            return f"S{season}E{episode}"

    # 匹配只有 Exx 或 EPxx 格式（不区分大小写）
    # 支持 E1, E01, EP1, EP01, e10, ep10, E1000 等格式
    episode_only_match = re.search(r'[Ee][Pp]?(\d+)', filename)
    if episode_only_match:
        episode = episode_only_match.group(1).zfill(2)  # 确保两位数
        return f"E{episode}"

    # 如果没有匹配到季数集数信息，检查是否需要去除与任务名称相同的前缀
    if task_name:
        task_name_clean = task_name.strip()

        # 首先尝试完整任务名匹配
        if filename.startswith(task_name_clean):
            # 去除任务名前缀
            remaining = filename[len(task_name_clean):].strip()

            # 如果剩余部分以常见分隔符开头，也去除分隔符
            separators = [' - ', ' · ', '.', '_', '-', ' ']
            for sep in separators:
                if remaining.startswith(sep):
                    remaining = remaining[len(sep):].strip()
                    break

            # 如果剩余部分不为空，返回剩余部分；否则返回原文件名
            return remaining if remaining else filename

        # 如果完整任务名不匹配，尝试提取剧名部分进行匹配
        # 匹配常见的季数格式，提取剧名部分
        season_patterns = [
            r'^(.*?)[\s\.\-_]+S\d+$',  # 你好，星期六 - S04
            r'^(.*?)[\s\.\-_]+Season\s*\d+$',  # 黑镜 - Season 1
            r'^(.*?)\s+S\d+$',  # 快乐的大人 S02
            r'^(.*?)[\s\.\-_]+S\d+E\d+$',  # 处理 S01E01 格式
            r'^(.*?)\s+第\s*\d+\s*季$',  # 处理 第N季 格式
            r'^(.*?)[\s\.\-_]+第\s*\d+\s*季$',  # 处理 - 第N季 格式
            r'^(.*?)\s+第[一二三四五六七八九十零]+季$',  # 处理 第一季、第二季 格式
            r'^(.*?)[\s\.\-_]+第[一二三四五六七八九十零]+季$',  # 处理 - 第一季、- 第二季 格式
        ]

        for pattern in season_patterns:
            match = re.match(pattern, task_name_clean, re.IGNORECASE)
            if match:
                show_name = match.group(1).strip()
                # 去除末尾可能残留的分隔符
                show_name = re.sub(r'[\s\.\-_]+$', '', show_name)

                # 检查文件名是否以剧名开头
                if filename.startswith(show_name):
                    # 去除剧名前缀
                    remaining = filename[len(show_name):].strip()

                    # 如果剩余部分以常见分隔符开头，也去除分隔符
                    separators = [' - ', ' · ', '.', '_', '-', ' ']
                    for sep in separators:
                        if remaining.startswith(sep):
                            remaining = remaining[len(sep):].strip()
                            break

                    # 如果剩余部分不为空，返回剩余部分；否则返回原文件名
                    return remaining if remaining else filename

    # 如果没有匹配到季数集数信息，且没有找到相同前缀，返回原文件名
    return filename

# 导入拼音排序工具
try:
    from utils.pinyin_sort import get_filename_pinyin_sort_key
except ImportError:
    # 如果导入失败，使用简单的小写排序作为备用
    def get_filename_pinyin_sort_key(filename):
        return filename.lower()

# 导入数据库模块
try:
    # 先尝试相对导入
    from sdk.db import RecordDB
except ImportError:
    try:
        # 如果相对导入失败，尝试从app包导入
        from app.sdk.db import RecordDB
    except ImportError:
        # 如果没有数据库模块，定义一个空类
        class RecordDB:
            def __init__(self, *args, **kwargs):
                pass
            
            def get_records(self, *args, **kwargs):
                return {"records": [], "pagination": {"total_records": 0, "total_pages": 0, "current_page": 1, "page_size": 20}}

# 导入工具函数
try:
    # 先尝试相对导入
    from sdk.utils import format_bytes, get_file_icon, format_file_display
except ImportError:
    try:
        # 如果相对导入失败，尝试从app包导入
        from app.sdk.utils import format_bytes, get_file_icon, format_file_display
    except ImportError:
        # 如果导入失败，使用默认实现或从quark_auto_save导入
        # format_bytes已从quark_auto_save导入
        def get_file_icon(file_name, is_dir=False):
            return "📄" if not is_dir else "📁"
            
        def format_file_display(prefix, icon, name):
            return f"{prefix}{icon} {name}"


def get_app_ver():
    BUILD_SHA = os.environ.get("BUILD_SHA", "")
    BUILD_TAG = os.environ.get("BUILD_TAG", "")
    if BUILD_TAG[:1] == "v":
        return BUILD_TAG
    elif BUILD_SHA:
        return f"{BUILD_TAG}({BUILD_SHA[:7]})"
    else:
        return "dev"


# 文件路径
PYTHON_PATH = "python3" if os.path.exists("/usr/bin/python3") else "python"
SCRIPT_PATH = os.environ.get("SCRIPT_PATH", "./quark_auto_save.py")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "./config/quark_config.json")
PLUGIN_FLAGS = os.environ.get("PLUGIN_FLAGS", "")
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
# 从环境变量获取端口，默认为5005
PORT = int(os.environ.get("PORT", "5005"))

config_data = {}
task_plugins_config_default = {}

# 文件列表缓存
file_list_cache = {}
cache_lock = Lock()

# 默认性能参数（如果配置中没有设置）
DEFAULT_PERFORMANCE_CONFIG = {
    "api_page_size": 200,
    "cache_expire_time": 30
}

def get_performance_config():
    """获取性能配置参数"""
    try:
        if config_data and "file_performance" in config_data:
            perf_config = config_data["file_performance"]
            # 确保所有值都是整数类型
            result = {}
            for key, default_value in DEFAULT_PERFORMANCE_CONFIG.items():
                try:
                    result[key] = int(perf_config.get(key, default_value))
                except (ValueError, TypeError):
                    result[key] = default_value
            return result
    except Exception as e:
        print(f"获取性能配置失败: {e}")
    return DEFAULT_PERFORMANCE_CONFIG

def cleanup_expired_cache():
    """清理所有过期缓存"""
    current_time = time.time()
    perf_config = get_performance_config()
    cache_expire_time = perf_config.get("cache_expire_time", 30)

    with cache_lock:
        expired_keys = [k for k, (_, t) in file_list_cache.items() if current_time - t > cache_expire_time]
        for k in expired_keys:
            del file_list_cache[k]
        return len(expired_keys)

app = Flask(__name__)
app.config["APP_VERSION"] = get_app_ver()
app.secret_key = "ca943f6db6dd34823d36ab08d8d6f65d"
app.config["SESSION_COOKIE_NAME"] = "QUARK_AUTO_SAVE_X_SESSION"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=31)
app.json.ensure_ascii = False
app.json.sort_keys = False
app.jinja_env.variable_start_string = "[["
app.jinja_env.variable_end_string = "]]"

# 注册 AVIF MIME 类型，确保静态路由能正确返回 Content-Type
try:
    import mimetypes
    mimetypes.add_type('image/avif', '.avif')
except Exception:
    pass

scheduler = BackgroundScheduler()
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="[%(asctime)s][%(levelname)s] %(message)s",
    datefmt="%m-%d %H:%M:%S",
)
# 降低第三方网络库的重试噪音：将 urllib3/requests 的日志调为 ERROR，并把“Retrying ...”消息降级为 DEBUG
try:
    logging.getLogger("urllib3").setLevel(logging.ERROR)
    logging.getLogger("requests").setLevel(logging.ERROR)

    class _RetryWarningToDebug(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            try:
                msg = str(record.getMessage())
                if "Retrying (Retry(" in msg or "Retrying (" in msg:
                    # 将此条记录的等级降到 DEBUG
                    record.levelno = logging.DEBUG
                    record.levelname = "DEBUG"
            except Exception:
                pass
            return True

    _urllib3_logger = logging.getLogger("urllib3.connectionpool")
    _urllib3_logger.addFilter(_RetryWarningToDebug())
except Exception:
    pass
# 过滤werkzeug日志输出
if not DEBUG:
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

# 统一所有已有处理器（包括 werkzeug、apscheduler）的日志格式
_root_logger = logging.getLogger()
_standard_formatter = logging.Formatter(
    fmt="[%(asctime)s][%(levelname)s] %(message)s",
    datefmt="%m-%d %H:%M:%S",
)
for _handler in list(_root_logger.handlers):
    try:
        _handler.setFormatter(_standard_formatter)
    except Exception:
        pass

for _name in ("werkzeug", "apscheduler", "gunicorn.error", "gunicorn.access"):
    _logger = logging.getLogger(_name)
    for _handler in list(_logger.handlers):
        try:
            _handler.setFormatter(_standard_formatter)
        except Exception:
            pass


# 缓存目录：放在 /app/config/cache 下，用户无需新增映射
CACHE_DIR = os.path.abspath(os.path.join(app.root_path, '..', 'config', 'cache'))
CACHE_IMAGES_DIR = os.path.join(CACHE_DIR, 'images')
os.makedirs(CACHE_IMAGES_DIR, exist_ok=True)


# --------- 追剧日历：SSE 事件中心，用于实时通知前端 DB 变化 ---------
calendar_subscribers = set()

def notify_calendar_changed(reason: str = ""):
    try:
        for q in list(calendar_subscribers):
            try:
                q.put_nowait({'type': 'calendar_changed', 'reason': reason})
            except Exception:
                pass
    except Exception:
        pass

@app.route('/api/calendar/stream')
def calendar_stream():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})

    def event_stream():
        q = Queue()
        calendar_subscribers.add(q)
        try:
            # 连接建立后立即发一条心跳，便于前端立刻触发一次检查
            yield f"event: ping\n" f"data: connected\n\n"
            import time as _t
            last_heartbeat = _t.time()
            while True:
                try:
                    item = q.get(timeout=15)
                    yield f"event: calendar_changed\n" f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                except Exception:
                    # 心跳
                    now = _t.time()
                    if now - last_heartbeat >= 15:
                        last_heartbeat = now
                        yield f"event: ping\n" f"data: keepalive\n\n"
        except GeneratorExit:
            pass
        finally:
            try:
                calendar_subscribers.discard(q)
            except Exception:
                pass

    return Response(stream_with_context(event_stream()), content_type='text/event-stream;charset=utf-8')

def gen_md5(string):
    md5 = hashlib.md5()
    md5.update(string.encode("utf-8"))
    return md5.hexdigest()


def get_login_token():
    username = config_data["webui"]["username"]
    password = config_data["webui"]["password"]
    return gen_md5(f"token{username}{password}+-*/")[8:24]


def is_login():
    login_token = get_login_token()
    if session.get("token") == login_token or request.args.get("token") == login_token:
        return True
    else:
        return False
DEFAULT_REFRESH_SECONDS = 21600



# 设置icon 及缓存静态映射
@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static", "images"),
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )

# 将 /cache/images/* 映射到宿主缓存目录，供前端访问
@app.route('/cache/images/<path:filename>')
def serve_cache_images(filename):
    resp = send_from_directory(CACHE_IMAGES_DIR, filename)
    try:
        # 启用长期缓存：依赖前端通过 `?t=` 穿透参数在变更时刷新
        # 说明：图片文件名稳定且内容稳定，正常情况应命中浏览器缓存；
        # 当用户主动更换海报时，前端会为该节目生成新的时间戳参数，形成新的 URL，从而触发重新下载。
        resp.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
        # 清理可能的历史字段（部分代理可能保留）
        resp.headers.pop('Pragma', None)
        resp.headers.pop('Expires', None)
    except Exception:
        pass
    return resp


# 登录页面
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = config_data["webui"]["username"]
        password = config_data["webui"]["password"]
        input_username = request.form.get("username")
        input_password = request.form.get("password")
        
        # 验证用户名和密码
        if not input_username or not input_password:
            logging.info(">>> 登录失败：用户名或密码为空")
            return render_template("login.html", message="用户名和密码不能为空")
        elif username != input_username:
            logging.info(f">>> 登录失败：用户名错误 {input_username}")
            return render_template("login.html", message="用户名或密码错误")
        elif password != input_password:
            logging.info(f">>> 用户 {input_username} 登录失败：密码错误")
            return render_template("login.html", message="用户名或密码错误")
        else:
            logging.info(f">>> 用户 {username} 登录成功")
            session.permanent = True
            session["token"] = get_login_token()
            return redirect(url_for("index"))

    if is_login():
        return redirect(url_for("index"))
    return render_template("login.html", error=None)


# 退出登录
@app.route("/logout")
def logout():
    session.pop("token", None)
    return redirect(url_for("login"))


# 管理页面
@app.route("/")
def index():
    if not is_login():
        return redirect(url_for("login"))
    return render_template(
        "index.html", version=app.config["APP_VERSION"], plugin_flags=PLUGIN_FLAGS
    )


# 已移除图片代理逻辑（测试版不再需要跨域代理）


# 获取配置数据
@app.route("/data")
def get_data():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    data = Config.read_json(CONFIG_PATH)
    # 确保有秒级刷新默认值（不做迁移逻辑）
    perf = data.get('performance') if isinstance(data, dict) else None
    if not isinstance(perf, dict):
        data['performance'] = {'calendar_refresh_interval_seconds': DEFAULT_REFRESH_SECONDS}
    else:
        if 'calendar_refresh_interval_seconds' not in perf:
            data['performance']['calendar_refresh_interval_seconds'] = DEFAULT_REFRESH_SECONDS
    
    # 确保海报语言有默认值
    if 'poster_language' not in data:
        data['poster_language'] = 'zh-CN'

    # 处理插件配置中的多账号支持字段，将数组格式转换为逗号分隔的字符串用于显示
    if "plugins" in data:
        # 处理Plex的quark_root_path
        if "plex" in data["plugins"] and "quark_root_path" in data["plugins"]["plex"]:
            data["plugins"]["plex"]["quark_root_path"] = format_array_config_for_display(
                data["plugins"]["plex"]["quark_root_path"]
            )

        # 处理AList的storage_id
        if "alist" in data["plugins"] and "storage_id" in data["plugins"]["alist"]:
            data["plugins"]["alist"]["storage_id"] = format_array_config_for_display(
                data["plugins"]["alist"]["storage_id"]
            )

    # 初始化插件配置模式（如果不存在）
    if "plugin_config_mode" not in data:
        data["plugin_config_mode"] = {
            "aria2": "independent",
            "alist_strm_gen": "independent",
            "emby": "independent"
        }
    
    # 初始化全局插件配置（如果不存在）
    if "global_plugin_config" not in data:
        data["global_plugin_config"] = {
            "aria2": {
                "auto_download": True,
                "pause": False,
                "auto_delete_quark_files": False
            },
            "alist_strm_gen": {
                "auto_gen": True
            },
            "emby": {
                "try_match": True,
                "media_id": ""
            }
        }

    # 初始化推送通知类型配置（如果不存在）
    if "push_notify_type" not in data:
        data["push_notify_type"] = "full"

    # 初始化TMDB配置（如果不存在）
    if "tmdb_api_key" not in data:
        data["tmdb_api_key"] = ""

    # 初始化搜索来源默认结构
    if "source" not in data or not isinstance(data.get("source"), dict):
        data["source"] = {}
    # CloudSaver 默认字段
    data["source"].setdefault("cloudsaver", {"server": "", "username": "", "password": "", "token": ""})
    # PanSou 默认字段
    data["source"].setdefault("pansou", {"server": "https://so.252035.xyz"})

    # 发送webui信息，但不发送密码原文
    data["webui"] = {
        "username": config_data["webui"]["username"],
        "password": config_data["webui"]["password"]
    }
    data["api_token"] = get_login_token()
    data["task_plugins_config_default"] = task_plugins_config_default
    return jsonify({"success": True, "data": data})


def sync_task_plugins_config():
    """同步更新所有任务的插件配置
    
    1. 检查每个任务的插件配置
    2. 如果插件配置不存在，使用默认配置
    3. 如果插件配置存在但缺少新的配置项，添加默认值
    4. 保留原有的自定义配置
    5. 只处理已启用的插件（通过PLUGIN_FLAGS检查）
    6. 清理被禁用插件的配置
    7. 应用全局插件配置（如果启用）
    """
    global config_data, task_plugins_config_default
    
    # 如果没有任务列表，直接返回
    if not config_data.get("tasklist"):
        return
        
    # 获取禁用的插件列表
    disabled_plugins = set()
    if PLUGIN_FLAGS:
        disabled_plugins = {name.lstrip('-') for name in PLUGIN_FLAGS.split(',')}
    
    # 获取插件配置模式
    plugin_config_mode = config_data.get("plugin_config_mode", {})
    global_plugin_config = config_data.get("global_plugin_config", {})
        
    # 遍历所有任务
    for task in config_data["tasklist"]:
        # 确保任务有addition字段
        if "addition" not in task:
            task["addition"] = {}
            
        # 清理被禁用插件的配置
        for plugin_name in list(task["addition"].keys()):
            if plugin_name in disabled_plugins:
                del task["addition"][plugin_name]
            
        # 遍历所有插件的默认配置
        for plugin_name, default_config in task_plugins_config_default.items():
            # 跳过被禁用的插件
            if plugin_name in disabled_plugins:
                continue
            
            # 检查是否使用全局配置模式
            if plugin_name in plugin_config_mode and plugin_config_mode[plugin_name] == "global":
                # 使用全局配置
                if plugin_name in global_plugin_config:
                    task["addition"][plugin_name] = global_plugin_config[plugin_name].copy()
                else:
                    task["addition"][plugin_name] = default_config.copy()
            else:
                # 使用独立配置
                if plugin_name not in task["addition"]:
                    task["addition"][plugin_name] = default_config.copy()
                else:
                    # 如果任务中有该插件的配置，检查是否有新的配置项
                    current_config = task["addition"][plugin_name]
                    # 确保current_config是字典类型
                    if not isinstance(current_config, dict):
                        # 如果不是字典类型，使用默认配置
                        task["addition"][plugin_name] = default_config.copy()
                        continue
                        
                    # 遍历默认配置的每个键值对
                    for key, default_value in default_config.items():
                        if key not in current_config:
                            current_config[key] = default_value


def parse_comma_separated_config(value):
    """解析逗号分隔的配置字符串为数组"""
    if isinstance(value, str) and value.strip():
        # 分割字符串，去除空白字符
        items = [item.strip() for item in value.split(',') if item.strip()]
        # 如果只有一个项目，返回字符串（向后兼容）
        return items[0] if len(items) == 1 else items
    return value

def format_array_config_for_display(value):
    """将数组配置格式化为逗号分隔的字符串用于显示"""
    if isinstance(value, list):
        return ', '.join(value)
    return value

# 更新数据
@app.route("/update", methods=["POST"])
def update():
    global config_data
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    # 保存前先记录旧任务名与其 tmdb 绑定，用于自动清理
    old_tasks = config_data.get("tasklist", [])
    old_task_map = {}
    for t in old_tasks:
        name = t.get("taskname") or t.get("task_name") or ""
        cal = (t.get("calendar_info") or {})
        match = (cal.get("match") or {})
        if name:
            old_task_map[name] = {
                "tmdb_id": match.get("tmdb_id") or cal.get("tmdb_id")
            }
    
    # 记录旧的海报语言设置
    old_poster_language = config_data.get('poster_language', 'zh-CN')
    dont_save_keys = ["task_plugins_config_default", "api_token"]
    for key, value in request.json.items():
        if key not in dont_save_keys:
            if key == "webui":
                # 更新webui凭据
                config_data["webui"]["username"] = value.get("username", config_data["webui"]["username"])
                config_data["webui"]["password"] = value.get("password", config_data["webui"]["password"])
            elif key == "plugins":
                # 处理插件配置中的多账号支持字段
                if "plex" in value and "quark_root_path" in value["plex"]:
                    value["plex"]["quark_root_path"] = parse_comma_separated_config(
                        value["plex"]["quark_root_path"]
                    )

                if "alist" in value and "storage_id" in value["alist"]:
                    value["alist"]["storage_id"] = parse_comma_separated_config(
                        value["alist"]["storage_id"]
                    )

                config_data.update({key: value})
            elif key == "tmdb_api_key":
                # 更新TMDB API密钥
                config_data[key] = value
            else:
                config_data.update({key: value})
    
    # 同步更新任务的插件配置
    sync_task_plugins_config()
    
    # 保存配置的执行步骤，异步执行
    try:
        prev_tmdb = (config_data.get('tmdb_api_key') or '').strip()
        new_tmdb = None
        for key, value in request.json.items():
            if key == 'tmdb_api_key':
                new_tmdb = (value or '').strip()
                break
    except Exception:
        prev_tmdb, new_tmdb = '', None

    import threading
    def _post_update_tasks(old_task_map_snapshot, prev_tmdb_value, new_tmdb_value, old_poster_lang):
        # 检查海报语言设置是否改变
        new_poster_language = config_data.get('poster_language', 'zh-CN')
        if old_poster_lang != new_poster_language:
            try:
                # 清空现有海报
                clear_all_posters()
                # 重新下载所有海报
                redownload_all_posters()
            except Exception as e:
                logging.error(f"重新下载海报失败: {e}")
        
        # 在保存配置时自动进行任务信息提取与TMDB匹配（若缺失则补齐）
        try:
            changed = ensure_calendar_info_for_tasks()
        except Exception as e:
            logging.warning(f"ensure_calendar_info_for_tasks 发生异常: {e}")

        # 在清理逻辑之前，先同步数据库绑定关系到任务配置
        try:
            sync_task_config_with_database_bindings()
        except Exception as e:
            logging.warning(f"同步任务配置失败: {e}")
        
        # 同步内容类型数据
        try:
            sync_content_type_between_config_and_database()
        except Exception as e:
            logging.warning(f"同步内容类型失败: {e}")

        # 基于任务变更进行自动清理：删除的任务、或 tmdb 绑定变更的旧绑定
        try:
            current_tasks = config_data.get('tasklist', [])
            current_map = {}
            for t in current_tasks:
                name = t.get('taskname') or t.get('task_name') or ''
                cal = (t.get('calendar_info') or {})
                match = (cal.get('match') or {})
                if name:
                    current_map[name] = {
                        'tmdb_id': match.get('tmdb_id') or cal.get('tmdb_id')
                    }
            # 被删除的任务
            for name, info in (old_task_map_snapshot or {}).items():
                if name not in current_map:
                    tid = info.get('tmdb_id')
                    if tid:
                        purge_calendar_by_tmdb_id_internal(int(tid))
            # 绑定变更：旧与新 tmdb_id 不同，清理旧的
            for name, info in (old_task_map_snapshot or {}).items():
                if name in current_map:
                    old_tid = info.get('tmdb_id')
                    new_tid = current_map[name].get('tmdb_id')
                    if old_tid and old_tid != new_tid:
                        purge_calendar_by_tmdb_id_internal(int(old_tid))
        except Exception as e:
            logging.warning(f"自动清理本地日历缓存失败: {e}")


        # 根据最新性能配置重启自动刷新任务
        try:
            restart_calendar_refresh_job()
        except Exception as e:
            logging.warning(f"重启追剧日历自动刷新任务失败: {e}")

        # 如果 TMDB API 从无到有，自动执行一次 bootstrap
        try:
            if (prev_tmdb_value == '' or prev_tmdb_value is None) and new_tmdb_value and new_tmdb_value != '':
                success, msg = do_calendar_bootstrap()
                logging.info(f"首次配置 TMDB API，自动初始化: {success}, {msg}")
            else:
                # 若此次更新产生了新的匹配绑定，也执行一次轻量初始化，确保前端能立刻读到本地数据
                try:
                    if 'changed' in locals() and changed:
                        # 只处理新增的任务，不处理所有任务
                        process_new_tasks_async()
                except Exception as _e:
                    logging.warning(f"配置更新触发日历初始化失败: {_e}")
        except Exception as e:
            logging.warning(f"自动初始化 bootstrap 失败: {e}")

    threading.Thread(target=_post_update_tasks, args=(old_task_map, prev_tmdb, new_tmdb, old_poster_language), daemon=True).start()
    
    # 确保性能配置包含秒级字段
    if not isinstance(config_data.get('performance'), dict):
        config_data['performance'] = {'calendar_refresh_interval_seconds': DEFAULT_REFRESH_SECONDS}
    else:
        config_data['performance'].setdefault('calendar_refresh_interval_seconds', DEFAULT_REFRESH_SECONDS)
    Config.write_json(CONFIG_PATH, config_data)
    # 更新session token，确保当前会话在用户名密码更改后仍然有效
    session["token"] = get_login_token()
    # 重新加载任务
    if reload_tasks():
        # 通知前端任务数据已更新，触发内容管理页面热更新
        try:
            notify_calendar_changed('task_updated')
        except Exception:
            pass
        
        # 立即启动新任务的异步处理，不等待
        threading.Thread(target=process_new_tasks_async, daemon=True).start()
        
        logging.info(f">>> 配置更新成功")
        return jsonify({"success": True, "message": "配置更新成功"})
    else:
        logging.info(f">>> 配置更新失败")
        return jsonify({"success": False, "message": "配置更新失败"})


# 处理运行脚本请求
@app.route("/run_script_now", methods=["POST"])
def run_script_now():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    tasklist = request.json.get("tasklist", [])
    command = [PYTHON_PATH, "-u", SCRIPT_PATH, CONFIG_PATH]
    logging.info(
        f">>> 手动运行任务 [{tasklist[0].get('taskname') if len(tasklist)>0 else 'ALL'}] 开始执行..."
    )

    def generate_output():
        # 设置环境变量
        process_env = os.environ.copy()
        process_env["PYTHONIOENCODING"] = "utf-8"
        if tasklist:
            process_env["TASKLIST"] = json.dumps(tasklist, ensure_ascii=False)
            # 添加原始任务索引的环境变量
            if len(tasklist) == 1 and 'original_index' in request.json:
                process_env["ORIGINAL_TASK_INDEX"] = str(request.json['original_index'])
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            env=process_env,
        )
        try:
            for line in iter(process.stdout.readline, ""):
                logging.info(line.strip())
                yield f"data: {line}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            process.stdout.close()
            process.wait()

    return Response(
        stream_with_context(generate_output()),
        content_type="text/event-stream;charset=utf-8",
    )


# -------------------- 追剧日历：任务提取与匹配辅助 --------------------
def purge_calendar_by_tmdb_id_internal(tmdb_id: int, force: bool = False) -> bool:
    """内部工具：按 tmdb_id 清理 shows/seasons/episodes，并尝试删除本地海报文件
    :param force: True 时无视是否仍被其他任务引用，强制删除
    """
    try:
        # 若仍有其他任务引用同一 tmdb_id，则（默认）跳过删除以避免误删共享资源；带 force 则继续
        if not force:
            try:
                tasks = config_data.get('tasklist', [])
                for t in tasks:
                    cal = (t.get('calendar_info') or {})
                    match = (cal.get('match') or {})
                    bound = match.get('tmdb_id') or cal.get('tmdb_id')
                    if bound and int(bound) == int(tmdb_id):
                        logging.info(f"跳过清理 tmdb_id={tmdb_id}（仍被其他任务引用）")
                        return True
            except Exception:
                pass
        db = CalendarDB()
        # 删除数据库前，尝试删除本地海报文件
        try:
            show = db.get_show(int(tmdb_id))
            poster_rel = (show or {}).get('poster_local_path') or ''
            if poster_rel and poster_rel.startswith('/cache/images/'):
                fname = poster_rel.replace('/cache/images/', '')
                fpath = os.path.join(CACHE_IMAGES_DIR, fname)
                if os.path.exists(fpath):
                    os.remove(fpath)
        except Exception as e:
            logging.warning(f"删除海报文件失败: {e}")
        db.delete_show(int(tmdb_id))
        return True
    except Exception as e:
        logging.warning(f"内部清理失败 tmdb_id={tmdb_id}: {e}")
        return False


def purge_orphan_calendar_shows_internal() -> int:
    """删除未被任何任务引用的 shows（连带 seasons/episodes 与海报），返回清理数量"""
    try:
        tasks = config_data.get('tasklist', [])
        referenced = set()
        for t in tasks:
            cal = (t.get('calendar_info') or {})
            match = (cal.get('match') or {})
            tid = match.get('tmdb_id') or cal.get('tmdb_id')
            if tid:
                try: referenced.add(int(tid))
                except Exception: pass
        db = CalendarDB()
        cur = db.conn.cursor()
        try:
            cur.execute('SELECT tmdb_id FROM shows')
            ids = [int(r[0]) for r in cur.fetchall()]
        except Exception:
            ids = []
        removed = 0
        for tid in ids:
            if tid not in referenced:
                if purge_calendar_by_tmdb_id_internal(int(tid), force=True):
                    removed += 1
        return removed
    except Exception:
        return 0
def sync_content_type_between_config_and_database() -> bool:
    """同步任务配置和数据库之间的内容类型数据。
    确保两个数据源的 content_type 保持一致。
    """
    try:
        from app.sdk.db import CalendarDB
        cal_db = CalendarDB()
        
        tasks = config_data.get('tasklist', [])
        changed = False
        synced_count = 0
        
        for task in tasks:
            task_name = task.get('taskname') or task.get('task_name') or ''
            if not task_name:
                continue
                
            # 获取任务配置中的内容类型
            cal = task.get('calendar_info') or {}
            extracted = cal.get('extracted') or {}
            config_content_type = extracted.get('content_type', '')
            
            # 通过任务名称查找绑定的节目
            bound_show = cal_db.get_show_by_task_name(task_name)
            
            if bound_show:
                # 任务已绑定到数据库中的节目
                tmdb_id = bound_show['tmdb_id']
                db_content_type = bound_show.get('content_type', '')
                
                # 如果数据库中没有内容类型，但任务配置中有，则同步到数据库
                if not db_content_type and config_content_type:
                    cal_db.update_show_content_type(tmdb_id, config_content_type)
                    changed = True
                    synced_count += 1
                    logging.debug(f"同步内容类型到数据库 - 任务: '{task_name}', 类型: '{config_content_type}', tmdb_id: {tmdb_id}")
                
                # 若两边不一致：以任务配置优先，同步到数据库
                elif db_content_type and config_content_type and db_content_type != config_content_type:
                    cal_db.update_show_content_type(tmdb_id, config_content_type)
                    changed = True
                    synced_count += 1
                    logging.debug(f"覆盖数据库内容类型为任务配置值 - 任务: '{task_name}', 配置: '{config_content_type}', 原DB: '{db_content_type}', tmdb_id: {tmdb_id}")
                # 如果数据库有类型但任务配置没有，则回填到配置
                elif db_content_type and not config_content_type:
                    if 'calendar_info' not in task:
                        task['calendar_info'] = {}
                    if 'extracted' not in task['calendar_info']:
                        task['calendar_info']['extracted'] = {}
                    task['calendar_info']['extracted']['content_type'] = db_content_type
                    changed = True
                    synced_count += 1
                    logging.debug(f"同步内容类型到任务配置 - 任务: '{task_name}', 类型: '{db_content_type}', tmdb_id: {tmdb_id}")
            else:
                # 任务没有绑定到数据库中的节目，但任务配置中有内容类型
                # 这种情况需要先通过 TMDB 匹配建立绑定关系，然后同步内容类型
                if config_content_type:
                    # 检查任务是否有 TMDB 匹配信息
                    match = cal.get('match') or {}
                    tmdb_id = match.get('tmdb_id')
                    
                    if tmdb_id:
                        # 任务有 TMDB 匹配信息，检查数据库中是否有对应的节目
                        show = cal_db.get_show(tmdb_id)
                        if show:
                            # 节目存在，建立绑定关系并设置内容类型
                            cal_db.bind_task_to_show(tmdb_id, task_name)
                            cal_db.update_show_content_type(tmdb_id, config_content_type)
                            changed = True
                            synced_count += 1
                            logging.debug(f"建立绑定关系并同步内容类型 - 任务: '{task_name}', 类型: '{config_content_type}', tmdb_id: {tmdb_id}")
                        else:
                            logging.debug(f"任务有 TMDB 匹配信息但数据库中无对应节目 - 任务: '{task_name}', tmdb_id: {tmdb_id}")
                    else:
                        logging.debug(f"任务无 TMDB 匹配信息，无法建立绑定关系 - 任务: '{task_name}'")
        
        if changed:
            logging.debug(f"内容类型同步完成，共同步了 {synced_count} 个任务")
            Config.write_json(CONFIG_PATH, config_data)
            
        return changed
        
    except Exception as e:
        logging.debug(f"内容类型同步失败: {e}")
        return False

def sync_task_config_with_database_bindings() -> bool:
    """双向同步任务配置和数据库之间的 TMDB 匹配信息。
    确保两个数据源的 TMDB 绑定关系保持一致。
    """
    try:
        from app.sdk.db import CalendarDB
        cal_db = CalendarDB()
        
        tasks = config_data.get('tasklist', [])
        changed = False
        synced_count = 0
        
        for task in tasks:
            task_name = task.get('taskname') or task.get('task_name') or ''
            if not task_name:
                continue
                
            # 获取任务配置中的 TMDB 匹配信息
            cal = task.get('calendar_info') or {}
            match = cal.get('match') or {}
            config_tmdb_id = match.get('tmdb_id')
            
            # 通过任务名称查找数据库中的绑定关系
            bound_show = cal_db.get_show_by_task_name(task_name)
            db_tmdb_id = bound_show['tmdb_id'] if bound_show else None
            
            # 双向同步逻辑
            if config_tmdb_id and not db_tmdb_id:
                # 任务配置中有 TMDB ID，但数据库中没有绑定关系 → 同步到数据库
                # 首先检查该 TMDB ID 对应的节目是否存在
                show = cal_db.get_show(config_tmdb_id)
                if show:
                    # 节目存在，建立绑定关系
                    cal_db.bind_task_to_show(config_tmdb_id, task_name)
                    changed = True
                    synced_count += 1
                    logging.debug(f"同步绑定关系到数据库 - 任务: '{task_name}', tmdb_id: {config_tmdb_id}, 节目: '{show.get('name', '')}'")
                else:
                    logging.debug(f"任务配置中的 TMDB ID 在数据库中不存在 - 任务: '{task_name}', tmdb_id: {config_tmdb_id}")
                    
            elif db_tmdb_id and not config_tmdb_id:
                # 数据库中有绑定关系，但任务配置中没有 TMDB ID → 同步到任务配置
                show = cal_db.get_show(db_tmdb_id)
                if show:
                    # 确保 calendar_info 结构存在
                    if 'calendar_info' not in task:
                        task['calendar_info'] = {}
                    if 'match' not in task['calendar_info']:
                        task['calendar_info']['match'] = {}
                    
                    # 同步数据库信息到任务配置
                    latest_season_number = show.get('latest_season_number', 1)  # 使用数据库中的实际季数
                    task['calendar_info']['match'].update({
                        'tmdb_id': db_tmdb_id,
                        'matched_show_name': show.get('name', ''),
                        'matched_year': show.get('year', ''),
                        'latest_season_fetch_url': f"/tv/{db_tmdb_id}/season/{latest_season_number}",
                        'latest_season_number': latest_season_number
                    })
                    
                    changed = True
                    synced_count += 1
                    logging.debug(f"同步 TMDB 匹配信息到任务配置 - 任务: '{task_name}', tmdb_id: {db_tmdb_id}, 节目: '{show.get('name', '')}'")
                    
            elif config_tmdb_id and db_tmdb_id and config_tmdb_id != db_tmdb_id:
                # 两个数据源都有 TMDB ID，但不一致 → 以数据库为准（因为数据库是权威数据源）
                show = cal_db.get_show(db_tmdb_id)
                if show:
                    # 确保 calendar_info 结构存在
                    if 'calendar_info' not in task:
                        task['calendar_info'] = {}
                    if 'match' not in task['calendar_info']:
                        task['calendar_info']['match'] = {}
                    
                    # 以数据库为准，更新任务配置
                    latest_season_number = show.get('latest_season_number', 1)  # 使用数据库中的实际季数
                    task['calendar_info']['match'].update({
                        'tmdb_id': db_tmdb_id,
                        'matched_show_name': show.get('name', ''),
                        'matched_year': show.get('year', ''),
                        'latest_season_fetch_url': f"/tv/{db_tmdb_id}/season/{latest_season_number}",
                        'latest_season_number': latest_season_number
                    })
                    
                    changed = True
                    synced_count += 1
                    logging.debug(f"统一 TMDB 匹配信息（以数据库为准） - 任务: '{task_name}', 原配置: {config_tmdb_id}, 数据库: {db_tmdb_id}, 节目: '{show.get('name', '')}'")
        
        if changed:
            logging.debug(f"TMDB 匹配信息双向同步完成，共同步了 {synced_count} 个任务")
            Config.write_json(CONFIG_PATH, config_data)
            
        return changed
        
    except Exception as e:
        logging.debug(f"TMDB 匹配信息同步失败: {e}")
        return False

def ensure_calendar_info_for_tasks() -> bool:
    """为所有任务确保存在 calendar_info.extracted 与 calendar_info.match 信息。
    - extracted: 从任务保存路径/名称提取的剧名、年份、类型（只在缺失时提取）
    - match: 使用 TMDB 进行匹配（只在缺失时匹配）
    """
    tasks = config_data.get('tasklist', [])
    if not tasks:
        return

    extractor = TaskExtractor()
    tmdb_api_key = config_data.get('tmdb_api_key', '')
    poster_language = get_poster_language_setting()
    tmdb_service = TMDBService(tmdb_api_key, poster_language) if tmdb_api_key else None

    changed = False
    # 简单去重缓存，避免同一名称/年份重复请求 TMDB
    search_cache = {}
    details_cache = {}
    for task in tasks:
        # 跳过标记为无需参与日历匹配的任务
        if task.get('skip_calendar') is True:
            continue
        task_name = task.get('taskname') or task.get('task_name') or ''
        save_path = task.get('savepath', '')

        cal = task.get('calendar_info') or {}
        extracted = cal.get('extracted') or {}

        # 提取 extracted（仅在缺失时）
        need_extract = not extracted or not extracted.get('show_name')
        if need_extract:
            info = extractor.extract_show_info_from_path(save_path)
            extracted = {
                'show_name': info.get('show_name', ''),
                'year': info.get('year', ''),
                'content_type': info.get('type', 'other'),
            }
            cal['extracted'] = extracted
            changed = True

        # 匹配 TMDB（仅在缺失时）
        match = cal.get('match') or {}
        if tmdb_service and not match.get('tmdb_id') and extracted.get('show_name'):
            try:
                key = (extracted.get('show_name') or '', extracted.get('year') or '')
                if key in search_cache:
                    search = search_cache[key]
                else:
                    search = tmdb_service.search_tv_show(extracted.get('show_name'), extracted.get('year') or None)
                    search_cache[key] = search
                if search and search.get('id'):
                    tmdb_id = search['id']
                    if tmdb_id in details_cache:
                        details = details_cache[tmdb_id]
                    else:
                        details = tmdb_service.get_tv_show_details(tmdb_id) or {}
                        details_cache[tmdb_id] = details
                    seasons = details.get('seasons', [])
                    logging.debug(f"TMDB季数数据: {seasons}")
                    
                    # 选择已播出的季中最新的一季
                    current_date = datetime.now().date()
                    latest_season_number = 0
                    latest_air_date = None
                    
                    for s in seasons:
                        sn = s.get('season_number', 0)
                        air_date = s.get('air_date')
                        logging.debug(f"季数: {sn}, 播出日期: {air_date}, 类型: {type(sn)}")
                        
                        # 只考虑已播出的季（有air_date且早于或等于当前日期）
                        if sn and sn > 0 and air_date:  # 排除第0季（特殊季）
                            try:
                                season_air_date = datetime.strptime(air_date, '%Y-%m-%d').date()
                                if season_air_date <= current_date:
                                    # 选择播出日期最新的季
                                    if latest_air_date is None or season_air_date > latest_air_date:
                                        latest_season_number = sn
                                        latest_air_date = season_air_date
                            except (ValueError, TypeError):
                                # 日期格式错误，跳过
                                continue
                    
                    logging.debug(f"计算出的最新已播季数: {latest_season_number}")
                    
                    # 如果没有找到已播出的季，回退到第1季
                    if latest_season_number == 0:
                        latest_season_number = 1
                        logging.debug(f"没有找到已播出的季，回退到第1季: {latest_season_number}")

                    # 使用新的方法获取中文标题，支持从别名中获取中国地区的别名
                    chinese_title = tmdb_service.get_chinese_title_with_fallback(tmdb_id, extracted.get('show_name', ''))
                    
                    cal['match'] = {
                        'matched_show_name': chinese_title,
                        'matched_year': (details.get('first_air_date') or '')[:4],
                        'tmdb_id': tmdb_id,
                        'latest_season_number': latest_season_number,
                        'latest_season_fetch_url': f"/tv/{tmdb_id}/season/{latest_season_number}",
                    }
                    changed = True
            except Exception as e:
                logging.warning(f"TMDB 匹配失败: task={task_name}, err={e}")

        if cal and task.get('calendar_info') != cal:
            task['calendar_info'] = cal

    if changed:
        config_data['tasklist'] = tasks
    return changed


# 刷新Plex媒体库
@app.route("/refresh_plex_library", methods=["POST"])
def refresh_plex_library():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    
    task_index = request.json.get("task_index")
    if task_index is None:
        return jsonify({"success": False, "message": "缺少任务索引"})
        
    # 获取任务信息
    task = config_data["tasklist"][task_index]
    if not task.get("savepath"):
        return jsonify({"success": False, "message": "任务没有保存路径"})
        
    # 导入Plex插件
    from plugins.plex import Plex
    
    # 初始化Plex插件
    plex = Plex(**config_data["plugins"]["plex"])
    if not plex.is_active:
        return jsonify({"success": False, "message": "Plex 插件未正确配置"})
        
    # 执行刷新
    plex.run(task)
    
    return jsonify({"success": True, "message": "成功刷新 Plex 媒体库"})


# 刷新AList目录
@app.route("/refresh_alist_directory", methods=["POST"])
def refresh_alist_directory():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    
    task_index = request.json.get("task_index")
    if task_index is None:
        return jsonify({"success": False, "message": "缺少任务索引"})
        
    # 获取任务信息
    task = config_data["tasklist"][task_index]
    if not task.get("savepath"):
        return jsonify({"success": False, "message": "任务没有保存路径"})
        
    # 导入AList插件
    from plugins.alist import Alist
    
    # 初始化AList插件
    alist = Alist(**config_data["plugins"]["alist"])
    if not alist.is_active:
        return jsonify({"success": False, "message": "AList 插件未正确配置"})
        
    # 执行刷新
    alist.run(task)
    
    return jsonify({"success": True, "message": "成功刷新 AList 目录"})


# 文件整理页面刷新Plex媒体库
@app.route("/refresh_filemanager_plex_library", methods=["POST"])
def refresh_filemanager_plex_library():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})

    folder_path = request.json.get("folder_path")
    account_index = request.json.get("account_index", 0)

    if not folder_path:
        return jsonify({"success": False, "message": "缺少文件夹路径"})

    # 检查Plex插件配置
    if not config_data.get("plugins", {}).get("plex", {}).get("url"):
        return jsonify({"success": False, "message": "Plex 插件未配置"})

    # 导入Plex插件
    from plugins.plex import Plex

    # 初始化Plex插件
    plex = Plex(**config_data["plugins"]["plex"])
    if not plex.is_active:
        return jsonify({"success": False, "message": "Plex 插件未正确配置"})

    # 获取夸克账号信息
    try:
        account = Quark(config_data["cookie"][account_index], account_index)

        # 将文件夹路径转换为实际的保存路径
        # folder_path是相对于夸克网盘根目录的路径
        # quark_root_path是夸克网盘在本地文件系统中的挂载点
        # 根据账号索引获取对应的夸克根路径
        quark_root_path = plex.get_quark_root_path(account_index)
        if not quark_root_path:
            return jsonify({"success": False, "message": f"Plex 插件未配置账号 {account_index} 的夸克根路径"})

        if folder_path == "" or folder_path == "/":
            # 空字符串或根目录表示夸克网盘根目录
            full_path = quark_root_path
        else:
            # 确保路径格式正确
            if not folder_path.startswith("/"):
                folder_path = "/" + folder_path

            # 拼接完整路径：夸克根路径 + 相对路径
            import os
            full_path = os.path.normpath(os.path.join(quark_root_path, folder_path.lstrip("/"))).replace("\\", "/")

        # 确保库信息已加载
        if plex._libraries is None:
            plex._libraries = plex._get_libraries()

        # 执行刷新
        success = plex.refresh(full_path)

        if success:
            return jsonify({"success": True, "message": "成功刷新 Plex 媒体库"})
        else:
            return jsonify({"success": False, "message": "刷新 Plex 媒体库失败，请检查路径配置"})

    except Exception as e:
        return jsonify({"success": False, "message": f"刷新 Plex 媒体库失败: {str(e)}"})


# 文件整理页面刷新AList目录
@app.route("/refresh_filemanager_alist_directory", methods=["POST"])
def refresh_filemanager_alist_directory():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})

    folder_path = request.json.get("folder_path")
    account_index = request.json.get("account_index", 0)

    if not folder_path:
        return jsonify({"success": False, "message": "缺少文件夹路径"})

    # 检查AList插件配置
    if not config_data.get("plugins", {}).get("alist", {}).get("url"):
        return jsonify({"success": False, "message": "AList 插件未配置"})

    # 导入AList插件
    from plugins.alist import Alist

    # 初始化AList插件
    alist = Alist(**config_data["plugins"]["alist"])
    if not alist.is_active:
        return jsonify({"success": False, "message": "AList 插件未正确配置"})

    # 获取夸克账号信息
    try:
        account = Quark(config_data["cookie"][account_index], account_index)

        # 将文件夹路径转换为实际的保存路径
        # folder_path是相对于夸克网盘根目录的路径，如 "/" 或 "/测试/文件夹"
        # 根据账号索引获取对应的存储配置
        storage_mount_path, quark_root_dir = alist.get_storage_config(account_index)

        if not storage_mount_path or not quark_root_dir:
            return jsonify({"success": False, "message": f"AList 插件未配置账号 {account_index} 的存储信息"})

        if folder_path == "/":
            # 根目录，直接使用夸克根路径
            full_path = quark_root_dir
        else:
            # 子目录，拼接路径
            import os
            # 移除folder_path开头的/，然后拼接
            relative_path = folder_path.lstrip("/")
            if quark_root_dir == "/":
                full_path = "/" + relative_path
            else:
                full_path = os.path.normpath(os.path.join(quark_root_dir, relative_path)).replace("\\", "/")

        # 检查路径是否在夸克根目录内
        if quark_root_dir == "/" or full_path.startswith(quark_root_dir):
            # 使用账号对应的存储配置映射到AList路径
            # 构建AList路径
            if quark_root_dir == "/":
                relative_path = full_path.lstrip("/")
            else:
                relative_path = full_path.replace(quark_root_dir, "", 1).lstrip("/")

            alist_path = os.path.normpath(
                os.path.join(storage_mount_path, relative_path)
            ).replace("\\", "/")

            # 执行刷新
            alist.refresh(alist_path)
            return jsonify({"success": True, "message": "成功刷新 AList 目录"})
        else:
            return jsonify({"success": False, "message": "路径不在AList配置的夸克根目录内"})

    except Exception as e:
        return jsonify({"success": False, "message": f"刷新 AList 目录失败: {str(e)}"})


@app.route("/task_suggestions")
def get_task_suggestions():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    query = request.args.get("q", "").lower()
    deep = request.args.get("d", "").lower()
    
    # 提取剧名，去除季数信息
    def extract_show_name(task_name):
        # 清理任务名称中的连续空格和特殊符号
        clean_name = task_name.replace('\u3000', ' ').replace('\t', ' ')
        clean_name = re.sub(r'\s+', ' ', clean_name).strip()
        
        # 匹配常见的季数格式
        # 例如：黑镜 - S07、人生若如初见 - S01、折腰.S01、音你而来-S02、快乐的大人 S02
        season_patterns = [
            r'^(.*?)[\s\.\-_]+S\d+$',  # 黑镜 - S07、折腰.S01、音你而来-S02
            r'^(.*?)[\s\.\-_]+Season\s*\d+$',  # 黑镜 - Season 1
            r'^(.*?)\s+S\d+$',  # 快乐的大人 S02
            r'^(.*?)[\s\.\-_]+S\d+E\d+$',  # 处理 S01E01 格式
            r'^(.*?)\s+第\s*\d+\s*季$',  # 处理 第N季 格式
            r'^(.*?)[\s\.\-_]+第\s*\d+\s*季$',  # 处理 - 第N季 格式
            r'^(.*?)\s+第[一二三四五六七八九十零]+季$',  # 处理 第一季、第二季 格式
            r'^(.*?)[\s\.\-_]+第[一二三四五六七八九十零]+季$',  # 处理 - 第一季、- 第二季 格式
        ]
        
        for pattern in season_patterns:
            match = re.match(pattern, clean_name, re.IGNORECASE)
            if match:
                show_name = match.group(1).strip()
                # 去除末尾可能残留的分隔符
                show_name = re.sub(r'[\s\.\-_]+$', '', show_name)
                return show_name
                
        # 如果没有匹配到季数格式，返回原名称
        return clean_name
    
    # 处理搜索关键词，提取剧名
    search_query = extract_show_name(query)
    
    try:
        sources_cfg = config_data.get("source", {}) or {}
        cs_data = sources_cfg.get("cloudsaver", {})
        ps_data = sources_cfg.get("pansou", {})

        merged = []
        providers = []

        # CloudSaver
        if (
            cs_data.get("server")
            and cs_data.get("username")
            and cs_data.get("password")
        ):
            cs = CloudSaver(cs_data.get("server"))
            cs.set_auth(
                cs_data.get("username", ""),
                cs_data.get("password", ""),
                cs_data.get("token", ""),
            )
            search = cs.auto_login_search(search_query)
            if search.get("success"):
                if search.get("new_token"):
                    cs_data["token"] = search.get("new_token")
                    Config.write_json(CONFIG_PATH, config_data)
                search_results = cs.clean_search_results(search.get("data"))
                if isinstance(search_results, list):
                    merged.extend(search_results)
                    providers.append("CloudSaver")

        # PanSou
        if ps_data and ps_data.get("server") and PanSou is not None:
            try:
                ps = PanSou(ps_data.get("server"))
                result = ps.search(search_query)
                if result.get("success") and isinstance(result.get("data"), list):
                    merged.extend(result.get("data"))
                    providers.append("PanSou")
            except Exception as e:
                logging.warning(f"PanSou 搜索失败: {str(e)}")

        # 去重并统一时间字段为 publish_date
        # 规则：
        # 1) 首轮仅按 shareurl 归并：同一链接保留发布时间最新的一条（展示以该条为准）
        # 2) 兜底（极少）：无链接时按完整指纹（shareurl|title|date|source）归并
        # 3) 二次归并：对所有候选结果再按 标题+发布时间 做一次归并（无论 shareurl 是否相同），取最新
        # 注意：当发生归并冲突时，始终保留发布时间最新的记录
        dedup_map = {}          # 按 shareurl 归并
        fingerprint_map = {}    # 兜底：完整指纹归并（仅当缺失链接时）
        # 规范化工具
        def normalize_shareurl(url: str) -> str:
            try:
                if not url:
                    return ""
                u = url.strip()
                # 仅取夸克分享ID: pan.quark.cn/s/<id>[?...]
                # 同时支持直接传入ID的情况
                match = re.search(r"/s/([^\?/#\s]+)", u)
                if match:
                    return match.group(1)
                # 如果没有域名路径，尝试去掉查询参数
                return u.split('?')[0]
            except Exception:
                return url or ""
        def normalize_title(title: str) -> str:
            try:
                if not title:
                    return ""
                import unicodedata
                t = unicodedata.normalize('NFKC', title)
                t = t.replace('\u3000', ' ').replace('\t', ' ')
                t = re.sub(r"\s+", " ", t).strip()
                return t
            except Exception:
                return title or ""
        def normalize_date(date_str: str) -> str:
            try:
                if not date_str:
                    return ""
                import unicodedata
                ds = unicodedata.normalize('NFKC', date_str).strip()
                return ds
            except Exception:
                return (date_str or "").strip()
        # 解析时间供比较
        def to_ts(datetime_str):
            if not datetime_str:
                return 0
            try:
                s = str(datetime_str).strip()
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

        for item in merged:
            if not isinstance(item, dict):
                continue
            # 统一时间字段：优先使用已存在的 publish_date，否则使用 datetime，并写回 publish_date
            try:
                if not item.get("publish_date") and item.get("datetime"):
                    item["publish_date"] = item.get("datetime")
            except Exception:
                pass

            shareurl = normalize_shareurl(item.get("shareurl") or "")
            title = normalize_title(item.get("taskname") or "")
            pubdate = normalize_date(item.get("publish_date") or "")
            source = (item.get("source") or "").strip()

            timestamp = to_ts(pubdate)

            # 条件1：按 shareurl 归并，取最新
            if shareurl:
                existed = dedup_map.get(shareurl)
                if not existed or to_ts(existed.get("publish_date")) < timestamp:
                    dedup_map[shareurl] = item
            else:
                # 条件2（兜底）：完整指纹归并（极少发生），依然取最新
                fingerprint = f"{shareurl}|{title}|{pubdate}|{source}"
                existed = fingerprint_map.get(fingerprint)
                if not existed or to_ts(existed.get("publish_date")) < timestamp:
                    fingerprint_map[fingerprint] = item

        # 第一轮：汇总归并后的候选结果
        candidates = list(dedup_map.values()) + list(fingerprint_map.values())

        # 第二轮：无论 shareurl 是否相同，再按 标题+发布时间 归并一次（使用时间戳作为键，兼容不同时间格式），保留最新
        final_map = {}
        for item in candidates:
            try:
                t = normalize_title(item.get("taskname") or "")
                d = normalize_date(item.get("publish_date") or "")
                s = normalize_shareurl(item.get("shareurl") or "")
                src = (item.get("source") or "").strip()
                # 优先采用 标题+时间 作为归并键
                ts_val = to_ts(d)
                if t and ts_val:
                    key = f"TD::{t}||{int(ts_val)}"
                elif s:
                    key = f"URL::{s}"
                else:
                    key = f"FP::{s}|{t}|{d}|{src}"
                existed = final_map.get(key)
                current_ts = to_ts(item.get("publish_date"))
                if not existed:
                    final_map[key] = item
                else:
                    existed_ts = to_ts(existed.get("publish_date"))
                    if current_ts > existed_ts:
                        final_map[key] = item
                    elif current_ts == existed_ts:
                        # 时间完全相同，使用确定性优先级打破平手
                        source_priority = {"CloudSaver": 2, "PanSou": 1}
                        existed_pri = source_priority.get((existed.get("source") or "").strip(), 0)
                        current_pri = source_priority.get(src, 0)
                        if current_pri > existed_pri:
                            final_map[key] = item
                        elif current_pri == existed_pri:
                            # 进一步比较信息丰富度（content 长度）
                            if len(str(item.get("content") or "")) > len(str(existed.get("content") or "")):
                                final_map[key] = item
            except Exception:
                # 出现异常则跳过该项
                continue

        dedup = list(final_map.values())

        # 仅在排序时对多种格式进行解析（优先解析 YYYY-MM-DD HH:mm:ss，其次 ISO）
        if dedup:
            def parse_datetime_for_sort(item):
                """解析时间字段，返回可比较的时间戳（统一以 publish_date 为准）"""
                datetime_str = item.get("publish_date")
                if not datetime_str:
                    return 0  # 没有时间的排在最后
                from datetime import datetime
                s = str(datetime_str).strip()
                # 优先解析标准显示格式
                try:
                    dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
                    return dt.timestamp()
                except Exception:
                    pass
                # 补充解析仅日期格式
                try:
                    dt = datetime.strptime(s, "%Y-%m-%d")
                    return dt.timestamp()
                except Exception:
                    pass
                # 其次尝试 ISO（支持 Z/偏移）
                try:
                    s2 = s.replace('Z', '+00:00')
                    dt = datetime.fromisoformat(s2)
                    return dt.timestamp()
                except Exception:
                    return 0  # 解析失败排在最后
            
            # 按时间倒序排序（最新的在前）
            dedup.sort(key=parse_datetime_for_sort, reverse=True)
            
            return jsonify({
                "success": True,
                "source": ", ".join(providers) if providers else "聚合",
                "data": dedup
            })

        # 若无本地可用来源，回退到公开网络
        base_url = base64.b64decode("aHR0cHM6Ly9zLjkxNzc4OC54eXo=").decode()
        url = f"{base_url}/task_suggestions?q={search_query}&d={deep}"
        response = requests.get(url)
        return jsonify({
            "success": True,
            "source": "网络公开",
            "data": response.json()
        })

    except Exception as e:
        return jsonify({"success": True, "message": f"error: {str(e)}"})


# 获取分享详情接口
@app.route("/get_share_detail", methods=["GET", "POST"])
def get_share_detail():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    
    # 支持GET和POST请求
    if request.method == "GET":
        shareurl = request.args.get("shareurl", "")
        stoken = request.args.get("stoken", "")
    else:
        shareurl = request.json.get("shareurl", "")
        stoken = request.json.get("stoken", "")
        
    account = Quark("", 0)
    # 设置account的必要属性
    account.episode_patterns = request.json.get("regex", {}).get("episode_patterns", []) if request.method == "POST" else []
    
    pwd_id, passcode, pdir_fid, paths = account.extract_url(shareurl)
    if not stoken:
        is_sharing, stoken = account.get_stoken(pwd_id, passcode)
        if not is_sharing:
            return jsonify({"success": False, "data": {"error": stoken}})
    share_detail = account.get_detail(pwd_id, stoken, pdir_fid, _fetch_share=1)
    # 统一错误返回，避免前端崩溃
    if isinstance(share_detail, dict) and share_detail.get("error"):
        return jsonify({"success": False, "data": {"error": share_detail.get("error")}})

    share_detail["paths"] = paths
    share_detail["stoken"] = stoken

    # 处理文件夹的include_items字段，确保空文件夹显示为0项而不是undefined
    if "list" in share_detail and isinstance(share_detail["list"], list):
        for file_item in share_detail["list"]:
            if file_item.get("dir", False):
                # 如果是文件夹，确保include_items字段存在且为数字
                if "include_items" not in file_item or file_item["include_items"] is None:
                    file_item["include_items"] = 0
                elif not isinstance(file_item["include_items"], (int, float)):
                    # 如果include_items不是数字类型，尝试转换为整数，失败则设为0
                    try:
                        file_item["include_items"] = int(file_item["include_items"])
                    except (ValueError, TypeError):
                        file_item["include_items"] = 0

    # 如果是GET请求或者不需要预览正则，直接返回分享详情
    if request.method == "GET" or not request.json.get("regex"):
        return jsonify({"success": True, "data": share_detail})
        
    # 正则命名预览
    def preview_regex(share_detail):
        regex = request.json.get("regex")
        # 检查是否为顺序命名模式
        if regex.get("use_sequence_naming") and regex.get("sequence_naming"):
            # 顺序命名模式预览
            sequence_pattern = regex.get("sequence_naming")
            current_sequence = 1
            
            # 构建顺序命名的正则表达式
            if sequence_pattern == "{}":
                # 对于单独的{}，使用特殊匹配
                regex_pattern = "(\\d+)"
            else:
                regex_pattern = re.escape(sequence_pattern).replace('\\{\\}', '(\\d+)')
            
            # 实现与实际重命名相同的排序算法
            def extract_sort_value(file):
                return sort_file_by_name(file)
            
            # 过滤出非目录文件，并且排除已经符合命名规则的文件
            files_to_process = []
            for f in share_detail["list"]:
                if f["dir"]:
                    continue  # 跳过目录
                
                # 检查文件是否已符合命名规则
                if sequence_pattern == "{}":
                    # 对于单独的{}，检查文件名是否为纯数字
                    file_name_without_ext = os.path.splitext(f["file_name"])[0]
                    if file_name_without_ext.isdigit():
                        # 增加判断：如果是日期格式的纯数字，不视为已命名
                        if not is_date_format(file_name_without_ext):
                            continue  # 跳过已符合命名规则的文件
                elif re.match(regex_pattern, f["file_name"]):
                    continue  # 跳过已符合命名规则的文件
                
                # 添加到待处理文件列表
                files_to_process.append(f)
            
            # 根据提取的排序值进行排序
            sorted_files = sorted(files_to_process, key=extract_sort_value)
            
            # 应用高级过滤词过滤
            filterwords = regex.get("filterwords", "")
            if filterwords:
                # 使用高级过滤函数
                filtered_files = advanced_filter_files(sorted_files, filterwords)
                # 标记被过滤的文件
                for item in sorted_files:
                    if item not in filtered_files:
                        item["filtered"] = True
            
            # 为每个文件分配序号
            for file in sorted_files:
                if not file.get("filtered"):
                    # 获取文件扩展名
                    file_ext = os.path.splitext(file["file_name"])[1]
                    # 生成预览文件名
                    if sequence_pattern == "{}":
                        # 对于单独的{}，直接使用数字序号作为文件名
                        file["file_name_re"] = f"{current_sequence:02d}{file_ext}"
                    else:
                        # 替换所有的{}为当前序号
                        file["file_name_re"] = sequence_pattern.replace("{}", f"{current_sequence:02d}") + file_ext
                    current_sequence += 1
            
            return share_detail
        elif regex.get("use_episode_naming") and regex.get("episode_naming"):
            # 剧集命名模式预览
            episode_pattern = regex.get("episode_naming")
            episode_patterns = regex.get("episode_patterns", [])
            
            # 获取默认的剧集模式
            default_episode_pattern = {"regex": '第(\\d+)集|第(\\d+)期|第(\\d+)话|(\\d+)集|(\\d+)期|(\\d+)话|[Ee][Pp]?(\\d+)|(\\d+)[-_\\s]*4[Kk]|\\[(\\d+)\\]|【(\\d+)】|_?(\\d+)_?'}
            
            # 获取配置的剧集模式，确保每个模式都是字典格式
            episode_patterns = []
            raw_patterns = config_data.get("episode_patterns", [default_episode_pattern])
            for p in raw_patterns:
                if isinstance(p, dict) and p.get("regex"):
                    episode_patterns.append(p)
                elif isinstance(p, str):
                    episode_patterns.append({"regex": p})
            
            # 如果没有有效的模式，使用默认模式
            if not episode_patterns:
                episode_patterns = [default_episode_pattern]
                
            # 添加中文数字匹配模式
            chinese_patterns = [
                {"regex": r'第([一二三四五六七八九十百千万零两]+)集'},
                {"regex": r'第([一二三四五六七八九十百千万零两]+)期'},
                {"regex": r'第([一二三四五六七八九十百千万零两]+)话'},
                {"regex": r'([一二三四五六七八九十百千万零两]+)集'},
                {"regex": r'([一二三四五六七八九十百千万零两]+)期'},
                {"regex": r'([一二三四五六七八九十百千万零两]+)话'}
            ]
            episode_patterns.extend(chinese_patterns)
            
            # 应用高级过滤词过滤
            filterwords = regex.get("filterwords", "")
            if filterwords:
                # 使用高级过滤函数
                filtered_files = advanced_filter_files(share_detail["list"], filterwords)
                # 标记被过滤的文件
                for item in share_detail["list"]:
                    if item not in filtered_files:
                        item["filtered"] = True
                        item["file_name_re"] = "×"
            
            # 处理未被过滤的文件
            for file in share_detail["list"]:
                if not file["dir"] and not file.get("filtered"):  # 只处理未被过滤的非目录文件
                    extension = os.path.splitext(file["file_name"])[1]
                    # 从文件名中提取集号
                    episode_num = extract_episode_number(file["file_name"], episode_patterns=episode_patterns)

                    if episode_num is not None:
                        file["file_name_re"] = episode_pattern.replace("[]", f"{episode_num:02d}") + extension
                        # 添加episode_number字段用于前端排序
                        file["episode_number"] = episode_num
                    else:
                        # 没有提取到集号，显示无法识别的提示
                        file["file_name_re"] = "× 无法识别剧集编号"
            
            return share_detail
        else:
            # 普通正则命名预览
            pattern, replace = account.magic_regex_func(
                regex.get("pattern", ""),
                regex.get("replace", ""),
                regex.get("taskname", ""),
                regex.get("magic_regex", {}),
            )
            
            # 应用高级过滤词过滤
            filterwords = regex.get("filterwords", "")
            if filterwords:
                # 使用高级过滤函数
                filtered_files = advanced_filter_files(share_detail["list"], filterwords)
                # 标记被过滤的文件
                for item in share_detail["list"]:
                    if item not in filtered_files:
                        item["filtered"] = True
                
            # 应用正则命名
            for item in share_detail["list"]:
                # 只对未被过滤的文件应用正则命名
                if not item.get("filtered") and re.search(pattern, item["file_name"]):
                    file_name = item["file_name"]
                    item["file_name_re"] = (
                        re.sub(pattern, replace, file_name) if replace != "" else file_name
                    )
            return share_detail

    share_detail = preview_regex(share_detail)

    # 再次处理文件夹的include_items字段，确保预览后的数据也正确
    if "list" in share_detail and isinstance(share_detail["list"], list):
        for file_item in share_detail["list"]:
            if file_item.get("dir", False):
                # 如果是文件夹，确保include_items字段存在且为数字
                if "include_items" not in file_item or file_item["include_items"] is None:
                    file_item["include_items"] = 0
                elif not isinstance(file_item["include_items"], (int, float)):
                    # 如果include_items不是数字类型，尝试转换为整数，失败则设为0
                    try:
                        file_item["include_items"] = int(file_item["include_items"])
                    except (ValueError, TypeError):
                        file_item["include_items"] = 0

    return jsonify({"success": True, "data": share_detail})


@app.route("/get_savepath_detail")
def get_savepath_detail():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})

    # 获取账号索引参数
    account_index = int(request.args.get("account_index", 0))

    # 验证账号索引
    if account_index < 0 or account_index >= len(config_data["cookie"]):
        return jsonify({"success": False, "message": "账号索引无效"})

    account = Quark(config_data["cookie"][account_index], account_index)
    paths = []
    if path := request.args.get("path"):
        if path == "/":
            fid = 0
        else:
            dir_names = path.split("/")
            if dir_names[0] == "":
                dir_names.pop(0)
            path_fids = []
            current_path = ""
            for dir_name in dir_names:
                current_path += "/" + dir_name
                path_fids.append(current_path)
            if get_fids := account.get_fids(path_fids):
                fid = get_fids[-1]["fid"]
                paths = [
                    {"fid": get_fid["fid"], "name": dir_name}
                    for get_fid, dir_name in zip(get_fids, dir_names)
                ]
            else:
                return jsonify({"success": False, "data": {"error": "获取fid失败"}})
    else:
        fid = request.args.get("fid", "0")

    # 获取文件列表
    files = account.ls_dir(fid)

    # 处理文件夹的include_items字段，确保空文件夹显示为0项而不是undefined
    if isinstance(files, list):
        for file_item in files:
            if file_item.get("dir", False):
                # 如果是文件夹，确保include_items字段存在且为数字
                if "include_items" not in file_item or file_item["include_items"] is None:
                    file_item["include_items"] = 0
                elif not isinstance(file_item["include_items"], (int, float)):
                    # 如果include_items不是数字类型，尝试转换为整数，失败则设为0
                    try:
                        file_item["include_items"] = int(file_item["include_items"])
                    except (ValueError, TypeError):
                        file_item["include_items"] = 0

    file_list = {
        "list": files,
        "paths": paths,
    }
    return jsonify({"success": True, "data": file_list})


@app.route("/delete_file", methods=["POST"])
def delete_file():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})

    # 获取账号索引参数
    account_index = int(request.json.get("account_index", 0))

    # 验证账号索引
    if account_index < 0 or account_index >= len(config_data["cookie"]):
        return jsonify({"success": False, "message": "账号索引无效"})

    account = Quark(config_data["cookie"][account_index], account_index)
    if fid := request.json.get("fid"):
        response = account.delete([fid])
        
        # 处理delete_records参数
        if request.json.get("delete_records") and response.get("code") == 0:
            try:
                # 初始化数据库
                db = RecordDB()
                
                # 获取save_path参数
                save_path = request.json.get("save_path", "")
                
                # 如果没有提供save_path，则不删除任何记录
                if not save_path:
                    response["deleted_records"] = 0
                    # logging.info(f">>> 删除文件 {fid} 但未提供save_path，不删除任何记录")
                    return jsonify(response)
                
                # 查询与该文件ID和save_path相关的所有记录
                cursor = db.conn.cursor()
                
                # 使用file_id和save_path进行精确匹配
                cursor.execute("SELECT id FROM transfer_records WHERE file_id = ? AND save_path = ?", (fid, save_path))
                record_ids = [row[0] for row in cursor.fetchall()]
                
                # 如果没有找到匹配的file_id记录，尝试通过文件名查找
                if not record_ids:
                    # 获取文件名（如果有的话）
                    file_name = request.json.get("file_name", "")
                    if file_name:
                        # 使用文件名和save_path进行精确匹配
                        cursor.execute("""
                            SELECT id FROM transfer_records 
                            WHERE (original_name = ? OR renamed_to = ?) 
                            AND save_path = ?
                        """, (file_name, file_name, save_path))
                        
                        record_ids = [row[0] for row in cursor.fetchall()]
                
                # 删除找到的所有记录
                deleted_count = 0
                for record_id in record_ids:
                    deleted_count += db.delete_record(record_id)
                
                # 添加删除记录的信息到响应中
                response["deleted_records"] = deleted_count
                # logging.info(f">>> 删除文件 {fid} 同时删除了 {deleted_count} 条相关记录")
                # 若存在记录删除，通知前端刷新（影响：最近转存、进度、今日更新等）
                try:
                    if deleted_count > 0:
                        notify_calendar_changed('delete_records')
                except Exception:
                    pass
                
            except Exception as e:
                logging.error(f">>> 删除记录时出错: {str(e)}")
                # 不影响主流程，即使删除记录失败也返回文件删除成功
    else:
        response = {"success": False, "message": "缺失必要字段: fid"}
    return jsonify(response)


@app.route("/move_file", methods=["POST"])
def move_file():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})

    # 获取账号索引参数
    account_index = int(request.json.get("account_index", 0))

    # 验证账号索引
    if account_index < 0 or account_index >= len(config_data["cookie"]):
        return jsonify({"success": False, "message": "账号索引无效"})

    account = Quark(config_data["cookie"][account_index], account_index)

    # 获取参数
    file_ids = request.json.get("file_ids", [])
    target_folder_id = request.json.get("target_folder_id")

    if not file_ids:
        return jsonify({"success": False, "message": "缺少文件ID列表"})

    if not target_folder_id:
        return jsonify({"success": False, "message": "缺少目标文件夹ID"})

    try:
        # 调用夸克网盘的移动API
        response = account.move(file_ids, target_folder_id)

        if response["code"] == 0:
            return jsonify({
                "success": True,
                "message": f"成功移动 {len(file_ids)} 个文件",
                "moved_count": len(file_ids)
            })
        else:
            return jsonify({
                "success": False,
                "message": response.get("message", "移动失败")
            })
    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"移动文件时出错: {str(e)}"
        })


@app.route("/create_folder", methods=["POST"])
def create_folder():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})

    # 获取请求参数
    data = request.json
    parent_folder_id = data.get("parent_folder_id")
    folder_name = data.get("folder_name", "新建文件夹")
    account_index = int(data.get("account_index", 0))

    # 验证参数
    if not parent_folder_id:
        return jsonify({"success": False, "message": "缺少父目录ID"})

    # 验证账号索引
    if account_index < 0 or account_index >= len(config_data["cookie"]):
        return jsonify({"success": False, "message": "账号索引无效"})

    try:
        # 初始化夸克网盘客户端
        account = Quark(config_data["cookie"][account_index], account_index)

        # 调用新建文件夹API
        response = account.mkdir_in_folder(parent_folder_id, folder_name)

        if response.get("code") == 0:
            # 创建成功，返回新文件夹信息
            new_folder = response.get("data", {})
            return jsonify({
                "success": True,
                "message": "文件夹创建成功",
                "data": {
                    "fid": new_folder.get("fid"),
                    "file_name": new_folder.get("file_name", folder_name),
                    "dir": True,
                    "size": 0,
                    "updated_at": new_folder.get("updated_at"),
                    "include_items": 0
                }
            })
        else:
            # 处理特定的错误信息
            error_message = response.get("message", "创建文件夹失败")

            # 检查是否是同名文件夹冲突
            if "同名" in error_message or "已存在" in error_message or "重复" in error_message or "doloading" in error_message:
                error_message = "已存在同名文件夹，请修改名称后再试"

            return jsonify({
                "success": False,
                "message": error_message
            })

    except Exception as e:
        logging.error(f">>> 创建文件夹时出错: {str(e)}")
        return jsonify({
            "success": False,
            "message": f"创建文件夹时出错: {str(e)}"
        })


# 添加任务接口
@app.route("/api/add_task", methods=["POST"])
def add_task():
    global config_data
    # 验证token
    if not is_login():
        return jsonify({"success": False, "code": 1, "message": "未登录"}), 401
    # 必选字段
    request_data = request.json
    required_fields = ["taskname", "shareurl", "savepath"]
    for field in required_fields:
        if field not in request_data or not request_data[field]:
            return (
                jsonify(
                    {"success": False, "code": 2, "message": f"缺少必要字段: {field}"}
                ),
                400,
            )
    # 添加任务
    config_data["tasklist"].append(request_data)
    Config.write_json(CONFIG_PATH, config_data)
    logging.info(f">>> 通过API添加任务: {request_data['taskname']}")
    return jsonify(
        {"success": True, "code": 0, "message": "任务添加成功", "data": request_data}
    )


# 定时任务执行的函数
def run_python(args):
    logging.info(f">>> 定时运行任务")

    # 在定时任务开始前清理过期缓存
    try:
        cleaned_count = cleanup_expired_cache()
        if cleaned_count > 0:
            logging.info(f">>> 清理了 {cleaned_count} 个过期缓存项")
    except Exception as e:
        logging.warning(f">>> 清理缓存时出错: {e}")

    # 检查是否需要随机延迟执行
    if delay := config_data.get("crontab_delay"):
        try:
            delay_seconds = int(delay)
            if delay_seconds > 0:
                # 在0到设定值之间随机选择一个延迟时间
                random_delay = random.randint(0, delay_seconds)
                logging.info(f">>> 随机延迟执行 {random_delay}秒")
                time.sleep(random_delay)
        except (ValueError, TypeError):
            logging.warning(f">>> 延迟执行设置无效: {delay}")

    os.system(f"{PYTHON_PATH} {args}")


# 重新加载任务
def reload_tasks():
    # 读取定时规则
    if crontab := config_data.get("crontab"):
        if scheduler.state == 1:
            scheduler.pause()  # 暂停调度器
        trigger = CronTrigger.from_crontab(crontab)
        scheduler.remove_all_jobs()
        scheduler.add_job(
            run_python,
            trigger=trigger,
            args=[f"{SCRIPT_PATH} {CONFIG_PATH}"],
            id=SCRIPT_PATH,
        )
        if scheduler.state == 0:
            scheduler.start()
        elif scheduler.state == 2:
            scheduler.resume()
        scheduler_state_map = {0: "停止", 1: "运行", 2: "暂停"}
        logging.info(">>> 重载调度器")
        logging.info(f"调度状态: {scheduler_state_map[scheduler.state]}")
        logging.info(f"定时规则: {crontab}")
        # 记录延迟执行设置
        if delay := config_data.get("crontab_delay"):
            logging.info(f"延迟执行: 0-{delay}秒")
        logging.info(f"现有任务: {scheduler.get_jobs()}")
        return True
    else:
        logging.info(">>> no crontab")
        return False


def init():
    global config_data, task_plugins_config_default
    logging.info(f">>> 初始化配置")
    # 检查配置文件是否存在
    if not os.path.exists(CONFIG_PATH):
        if not os.path.exists(os.path.dirname(CONFIG_PATH)):
            os.makedirs(os.path.dirname(CONFIG_PATH))
        with open("quark_config.json", "rb") as src, open(CONFIG_PATH, "wb") as dest:
            dest.write(src.read())

    # 读取配置
    config_data = Config.read_json(CONFIG_PATH)
    Config.breaking_change_update(config_data)

    # 默认管理账号
    config_data["webui"] = {
        "username": os.environ.get("WEBUI_USERNAME")
        or config_data.get("webui", {}).get("username", "admin"),
        "password": os.environ.get("WEBUI_PASSWORD")
        or config_data.get("webui", {}).get("password", "admin"),
    }

    # 默认定时规则
    if not config_data.get("crontab"):
        config_data["crontab"] = "0 8,18,20 * * *"
    
    # 默认延迟执行设置
    if "crontab_delay" not in config_data:
        config_data["crontab_delay"] = 0

    # 初始化插件配置
    _, plugins_config_default, task_plugins_config_default = Config.load_plugins()
    plugins_config_default.update(config_data.get("plugins", {}))
    config_data["plugins"] = plugins_config_default
    
    # 获取禁用的插件列表
    disabled_plugins = set()
    if PLUGIN_FLAGS:
        disabled_plugins = {name.lstrip('-') for name in PLUGIN_FLAGS.split(',')}
    
    # 清理所有任务中被禁用插件的配置
    if config_data.get("tasklist"):
        for task in config_data["tasklist"]:
            if "addition" in task:
                for plugin_name in list(task["addition"].keys()):
                    if plugin_name in disabled_plugins:
                        del task["addition"][plugin_name]
    
    # 初始化插件配置模式（如果不存在）
    if "plugin_config_mode" not in config_data:
        config_data["plugin_config_mode"] = {
            "aria2": "independent",
            "alist_strm_gen": "independent",
            "emby": "independent"
        }
    
    # 初始化全局插件配置（如果不存在）
    if "global_plugin_config" not in config_data:
        config_data["global_plugin_config"] = {
            "aria2": {
                "auto_download": True,
                "pause": False,
                "auto_delete_quark_files": False
            },
            "alist_strm_gen": {
                "auto_gen": True
            },
            "emby": {
                "try_match": True,
                "media_id": ""
            }
        }

    # 初始化推送通知类型配置（如果不存在）
    if "push_notify_type" not in config_data:
        config_data["push_notify_type"] = "full"

    # 同步更新任务的插件配置
    sync_task_plugins_config()

    # 更新配置
    Config.write_json(CONFIG_PATH, config_data)


# 获取历史转存记录
@app.route("/history_records")
def get_history_records():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
        
    # 获取请求参数
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 20))
    sort_by = request.args.get("sort_by", "transfer_time")
    order = request.args.get("order", "desc")
    
    # 获取筛选参数
    task_name_filter = request.args.get("task_name", "")
    keyword_filter = request.args.get("keyword", "")
    
    # 是否只请求所有任务名称
    get_all_task_names = request.args.get("get_all_task_names", "").lower() in ["true", "1", "yes"]
    
    # 初始化数据库
    db = RecordDB()
    
    # 如果请求所有任务名称，单独查询并返回
    if get_all_task_names:
        cursor = db.conn.cursor()
        cursor.execute("SELECT DISTINCT task_name FROM transfer_records WHERE task_name NOT IN ('rename', 'undo_rename') ORDER BY task_name")
        all_task_names = [row[0] for row in cursor.fetchall()]
        
        # 如果同时请求分页数据，继续常规查询
        if page > 0 and page_size > 0:
            result = db.get_records(
                page=page, 
                page_size=page_size, 
                sort_by=sort_by, 
                order=order,
                task_name_filter=task_name_filter,
                keyword_filter=keyword_filter,
                exclude_task_names=["rename", "undo_rename"]
            )
            # 添加所有任务名称到结果中
            result["all_task_names"] = all_task_names
            
            # 处理记录格式化
            format_records(result["records"])
            
            return jsonify({"success": True, "data": result})
        else:
            # 只返回任务名称
            return jsonify({"success": True, "data": {"all_task_names": all_task_names}})
    
    # 常规查询
    result = db.get_records(
        page=page, 
        page_size=page_size, 
        sort_by=sort_by, 
        order=order,
        task_name_filter=task_name_filter,
        keyword_filter=keyword_filter,
        exclude_task_names=["rename", "undo_rename"]
    )
    
    # 处理记录格式化
    format_records(result["records"])
    
    return jsonify({"success": True, "data": result})


# 删除转存记录
@app.route("/delete_history_records", methods=["POST"])
def delete_history_records():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    
    # 获取要删除的记录ID列表
    record_ids = request.json.get("record_ids", [])
    
    if not record_ids:
        return jsonify({"success": False, "message": "未提供要删除的记录ID"})
    
    # 初始化数据库
    db = RecordDB()
    
    # 删除记录
    deleted_count = 0
    for record_id in record_ids:
        deleted_count += db.delete_record(record_id)

    # 广播通知：有记录被删除，触发前端刷新任务/日历
    try:
        if deleted_count > 0:
            notify_calendar_changed('delete_records')
    except Exception:
        pass

    return jsonify({
        "success": True,
        "message": f"成功删除 {deleted_count} 条记录",
        "deleted_count": deleted_count
    })


# 获取任务最新转存信息（包括日期和文件）
@app.route("/task_latest_info")
def get_task_latest_info():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})

    try:
        # 初始化数据库
        db = RecordDB()
        cursor = db.conn.cursor()

        # 获取所有任务的最新转存时间
        query = """
        SELECT task_name, MAX(transfer_time) as latest_transfer_time
        FROM transfer_records
        WHERE task_name NOT IN ('rename', 'undo_rename')
        GROUP BY task_name
        """
        cursor.execute(query)
        latest_times = cursor.fetchall()

        task_latest_records = {}  # 存储最新转存日期
        task_latest_files = {}    # 存储最新转存文件

        for task_name, latest_time in latest_times:
            if latest_time:
                # 1. 处理最新转存日期
                try:
                    # 确保时间戳在合理范围内
                    timestamp = int(latest_time)
                    if timestamp > 9999999999:  # 检测是否为毫秒级时间戳（13位）
                        timestamp = timestamp / 1000  # 转换为秒级时间戳

                    if 0 < timestamp < 4102444800:  # 从1970年到2100年的合理时间戳范围
                        # 格式化为月-日格式（用于显示）和完整日期（用于今日判断）
                        date_obj = datetime.fromtimestamp(timestamp)
                        formatted_date = date_obj.strftime("%m-%d")
                        full_date = date_obj.strftime("%Y-%m-%d")
                        task_latest_records[task_name] = {
                            "display": formatted_date,  # 显示用的 MM-DD 格式
                            "full": full_date          # 比较用的 YYYY-MM-DD 格式
                        }
                except (ValueError, TypeError, OverflowError):
                    pass  # 忽略无效的时间戳

                # 2. 处理最新转存文件
                # 获取该任务在最新转存时间附近（同一分钟内）的所有文件
                # 这样可以处理同时转存多个文件但时间戳略有差异的情况
                time_window = 60000  # 60秒的时间窗口（毫秒）
                query = """
                SELECT renamed_to, original_name, transfer_time, modify_date
                FROM transfer_records
                WHERE task_name = ? AND transfer_time >= ? AND transfer_time <= ?
                ORDER BY id DESC
                """
                cursor.execute(query, (task_name, latest_time - time_window, latest_time + time_window))
                files = cursor.fetchall()

                if files:
                    if len(files) == 1:
                        # 如果只有一个文件，直接使用
                        best_file = files[0][0]  # renamed_to
                    else:
                        # 如果有多个文件，使用全局排序函数进行排序
                        file_list = []
                        for renamed_to, original_name, transfer_time, modify_date in files:
                            # 构造文件信息字典，模拟全局排序函数需要的格式
                            file_info = {
                                'file_name': renamed_to,
                                'original_name': original_name,
                                'updated_at': transfer_time  # 使用转存时间而不是文件修改时间
                            }
                            file_list.append(file_info)

                        # 使用全局排序函数进行正向排序，最后一个就是最新的
                        try:
                            sorted_files = sorted(file_list, key=sort_file_by_name)
                            best_file = sorted_files[-1]['file_name']  # 取排序后的最后一个文件
                        except Exception as e:
                            # 如果排序失败，使用第一个文件作为备选
                            best_file = files[0][0]

                    # 去除扩展名并处理季数集数信息
                    if best_file:
                        file_name_without_ext = os.path.splitext(best_file)[0]
                        processed_name = process_season_episode_info(file_name_without_ext, task_name)
                        task_latest_files[task_name] = processed_name

        # 注入"追剧日历"层面的签名信息，便于前端在无新增转存文件时也能检测到新增/删除剧目
        try:
            caldb = CalendarDB()
            cur = caldb.conn.cursor()
            # shows 数量
            try:
                cur.execute('SELECT COUNT(*) FROM shows')
                shows_count = cur.fetchone()[0] if cur.fetchone is not None else 0
            except Exception:
                shows_count = 0
            # tmdb_id 列表（排序后拼接，变化即触发前端刷新）
            tmdb_sig = ''
            try:
                cur.execute('SELECT tmdb_id FROM shows ORDER BY tmdb_id ASC')
                ids = [str(r[0]) for r in cur.fetchall()]
                tmdb_sig = ','.join(ids)
            except Exception:
                tmdb_sig = ''
            # 写入到 latest_files，以复用前端现有签名计算逻辑
            task_latest_files['__calendar_shows__'] = str(shows_count)
            if tmdb_sig:
                task_latest_files['__calendar_tmdb_ids__'] = tmdb_sig
        except Exception:
            pass

        db.close()

        return jsonify({
            "success": True,
            "data": {
                "latest_records": task_latest_records,
                "latest_files": task_latest_files
            }
        })

    except Exception as e:
        return jsonify({
            "success": False,
            "message": f"获取任务最新信息失败: {str(e)}"
        })


# 获取当日更新的剧集（本地DB，按 transfer_records 当天记录聚合）
@app.route("/api/calendar/today_updates_local")
def get_calendar_today_updates_local():
    try:
        # 计算今天 00:00:00 与 23:59:59 的时间戳（毫秒）范围
        now = datetime.now()
        start_of_day = datetime(now.year, now.month, now.day)
        end_of_day = datetime(now.year, now.month, now.day, 23, 59, 59)
        start_ms = int(start_of_day.timestamp() * 1000)
        end_ms = int(end_of_day.timestamp() * 1000)

        # 读取今日的转存记录
        rdb = RecordDB()
        cur = rdb.conn.cursor()
        cur.execute(
            """
            SELECT task_name, renamed_to, original_name, transfer_time
            FROM transfer_records
            WHERE task_name NOT IN ('rename', 'undo_rename')
              AND transfer_time >= ? AND transfer_time <= ?
            ORDER BY id DESC
            """,
            (start_ms, end_ms)
        )
        rows = cur.fetchall() or []

        # 建立 task_name -> (tmdb_id, show_name) 的映射，优先使用已绑定的 shows
        tmdb_map = {}
        try:
            cal_db = CalendarDB()
            # shows 表有 bound_task_names，逐条解析绑定
            cur2 = cal_db.conn.cursor()
            cur2.execute('SELECT tmdb_id, name, bound_task_names FROM shows')
            for tmdb_id, name, bound in (cur2.fetchall() or []):
                try:
                    bound_list = []
                    if bound:
                        bound_list = [b.strip() for b in str(bound).split(',') if b and b.strip()]
                    for tname in bound_list:
                        if tname:
                            tmdb_map[tname] = { 'tmdb_id': int(tmdb_id), 'show_name': name or '' }
                except Exception:
                    continue
        except Exception:
            tmdb_map = {}

        # 提取剧集编号/日期
        extractor = TaskExtractor()
        items = []
        for task_name, renamed_to, original_name, transfer_time in rows:
            # 解析进度信息
            base_name = os.path.splitext(renamed_to or '')[0]
            parsed = extractor.extract_progress_from_latest_file(base_name)
            season = parsed.get('season_number')
            ep = parsed.get('episode_number')
            air_date = parsed.get('air_date')
            if not season and not ep and not air_date:
                # 无法解析到有效信息则跳过
                continue
            bind = tmdb_map.get(task_name, {})
            items.append({
                'task_name': task_name or '',
                'tmdb_id': bind.get('tmdb_id'),
                'show_name': bind.get('show_name') or '',
                'season_number': season,
                'episode_number': ep,
                'air_date': air_date
            })

        return jsonify({ 'success': True, 'data': { 'items': items } })
    except Exception as e:
        return jsonify({ 'success': False, 'message': f'读取当日更新失败: {str(e)}', 'data': { 'items': [] } })


# 删除单条转存记录
@app.route("/delete_history_record", methods=["POST"])
def delete_history_record():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    
    # 获取要删除的记录ID
    record_id = request.json.get("id")
    
    if not record_id:
        return jsonify({"success": False, "message": "未提供要删除的记录ID"})
    
    # 初始化数据库
    db = RecordDB()
    
    # 删除记录
    deleted = db.delete_record(record_id)

    # 广播通知：有记录被删除，触发前端刷新任务/日历
    try:
        if deleted:
            notify_calendar_changed('delete_records')
    except Exception:
        pass

    if deleted:
        return jsonify({
            "success": True, 
            "message": "成功删除 1 条记录",
        })
    else:
        return jsonify({
            "success": False, 
            "message": "记录删除失败，可能记录不存在",
        })


# 辅助函数：格式化记录
def format_records(records):
    for record in records:
        # 格式化时间戳为可读形式
        if "transfer_time" in record:
            try:
                # 确保时间戳在合理范围内
                timestamp = int(record["transfer_time"])
                if timestamp > 9999999999:  # 检测是否为毫秒级时间戳（13位）
                    timestamp = timestamp / 1000  # 转换为秒级时间戳
                
                if 0 < timestamp < 4102444800:  # 从1970年到2100年的合理时间戳范围
                    record["transfer_time_readable"] = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
                else:
                    record["transfer_time_readable"] = "无效日期"
            except (ValueError, TypeError, OverflowError):
                record["transfer_time_readable"] = "无效日期"
                
        if "modify_date" in record:
            try:
                # 确保时间戳在合理范围内
                timestamp = int(record["modify_date"])
                if timestamp > 9999999999:  # 检测是否为毫秒级时间戳（13位）
                    timestamp = timestamp / 1000  # 转换为秒级时间戳
                    
                if 0 < timestamp < 4102444800:  # 从1970年到2100年的合理时间戳范围
                    record["modify_date_readable"] = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
                else:
                    record["modify_date_readable"] = "无效日期"
            except (ValueError, TypeError, OverflowError):
                record["modify_date_readable"] = "无效日期"
                
        # 格式化文件大小
        if "file_size" in record:
            try:
                record["file_size_readable"] = format_bytes(int(record["file_size"]))
            except (ValueError, TypeError):
                record["file_size_readable"] = "未知大小"


@app.route("/get_user_info")
def get_user_info():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})

    user_info_list = []
    for idx, cookie in enumerate(config_data["cookie"]):
        account = Quark(cookie, idx)
        account_info = account.init()
        if account_info:
            # 检查是否有移动端参数
            has_mparam = bool(account.mparam)
            user_info_list.append({
                "index": idx,
                "nickname": account_info["nickname"],
                "is_active": account.is_active,
                "has_mparam": has_mparam
            })
        else:
            # 检查是否有移动端参数
            has_mparam = bool(account.mparam)
            user_info_list.append({
                "index": idx,
                "nickname": "",
                "is_active": False,
                "has_mparam": has_mparam
            })

    return jsonify({"success": True, "data": user_info_list})


@app.route("/get_accounts_detail")
def get_accounts_detail():
    """获取所有账号的详细信息，包括昵称和空间使用情况"""
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})

    accounts_detail = []
    for idx, cookie in enumerate(config_data["cookie"]):
        account = Quark(cookie, idx)
        account_info = account.init()

        # 如果无法获取账号信息，检查是否有移动端参数
        if not account_info:
            has_mparam = bool(account.mparam)
            # 如果只有移动端参数，跳过此账号（不显示在文件整理页面的账号选择栏中）
            if has_mparam:
                continue
            else:
                # 如果既没有账号信息也没有移动端参数，显示为未登录
                account_detail = {
                    "index": idx,
                    "nickname": "",
                    "is_active": False,
                    "used_space": 0,
                    "total_space": 0,
                    "usage_rate": 0,
                    "display_text": f"账号{idx + 1}（未登录）"
                }
                accounts_detail.append(account_detail)
                continue

        # 成功获取账号信息的情况
        account_detail = {
            "index": idx,
            "nickname": account_info["nickname"],
            "is_active": account.is_active,
            "used_space": 0,
            "total_space": 0,
            "usage_rate": 0,
            "display_text": ""
        }

        # 检查是否有移动端参数
        has_mparam = bool(account.mparam)
        
        if has_mparam:
            # 同时有cookie和移动端参数，尝试获取空间信息
            try:
                growth_info = account.get_growth_info()
                if growth_info:
                    total_capacity = growth_info.get("total_capacity", 0)
                    account_detail["total_space"] = total_capacity
                    # 显示昵称和总容量
                    total_str = format_bytes(total_capacity)
                    account_detail["display_text"] = f"{account_info['nickname']} · {total_str}"
                else:
                    # 获取空间信息失败，只显示昵称
                    account_detail["display_text"] = account_info["nickname"]
            except Exception as e:
                logging.error(f"获取账号 {idx} 空间信息失败: {str(e)}")
                # 获取空间信息失败，只显示昵称
                account_detail["display_text"] = account_info["nickname"]
        else:
            # 只有cookie，没有移动端参数，只显示昵称
            account_detail["display_text"] = account_info["nickname"]

        accounts_detail.append(account_detail)

    return jsonify({"success": True, "data": accounts_detail})


# 重置文件夹（删除文件夹内所有文件和相关记录）
@app.route("/reset_folder", methods=["POST"])
def reset_folder():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})

    # 获取请求参数
    save_path = request.json.get("save_path", "")
    account_index = int(request.json.get("account_index", 0))  # 新增账号索引参数

    if not save_path:
        return jsonify({"success": False, "message": "保存路径不能为空"})

    try:
        # 验证账号索引
        if account_index < 0 or account_index >= len(config_data["cookie"]):
            return jsonify({"success": False, "message": "账号索引无效"})

        # 初始化夸克网盘客户端
        account = Quark(config_data["cookie"][account_index], account_index)
        
        # 1. 获取文件夹ID
        # 先检查是否已有缓存的文件夹ID
        folder_fid = account.savepath_fid.get(save_path)
        
        # 如果没有缓存的ID，则尝试创建文件夹以获取ID
        if not folder_fid:
            mkdir_result = account.mkdir(save_path)
            if mkdir_result.get("code") == 0:
                folder_fid = mkdir_result["data"]["fid"]
                account.savepath_fid[save_path] = folder_fid
            else:
                return jsonify({"success": False, "message": f"获取文件夹ID失败: {mkdir_result.get('message', '未知错误')}"})
        
        # 2. 获取文件夹内的所有文件
        file_list = account.ls_dir(folder_fid)
        if isinstance(file_list, dict) and file_list.get("error"):
            return jsonify({"success": False, "message": f"获取文件列表失败: {file_list.get('error', '未知错误')}"})
        
        # 收集所有文件ID
        file_ids = []
        for item in file_list:
            file_ids.append(item["fid"])
        
        # 3. 删除所有文件
        deleted_files = 0
        if file_ids:
            delete_result = account.delete(file_ids)
            if delete_result.get("code") == 0:
                deleted_files = len(file_ids)
        
        # 4. 删除相关的历史记录
        deleted_records = 0
        try:
            # 初始化数据库
            db = RecordDB()
            
            # 查询与该保存路径相关的所有记录
            cursor = db.conn.cursor()
            cursor.execute("SELECT id FROM transfer_records WHERE save_path = ?", (save_path,))
            record_ids = [row[0] for row in cursor.fetchall()]
            
            # 删除找到的所有记录
            for record_id in record_ids:
                deleted_records += db.delete_record(record_id)
                
        except Exception as e:
            logging.error(f">>> 删除记录时出错: {str(e)}")
            # 即使删除记录失败，也返回文件删除成功
        
        return jsonify({
            "success": True, 
            "message": f"重置成功，删除了 {deleted_files} 个文件和 {deleted_records} 条记录",
            "deleted_files": deleted_files,
            "deleted_records": deleted_records
        })
        
    except Exception as e:
        logging.error(f">>> 重置文件夹时出错: {str(e)}")
        return jsonify({"success": False, "message": f"重置文件夹时出错: {str(e)}"})


# 获取文件列表
@app.route("/file_list")
def get_file_list():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})

    # 获取请求参数
    folder_id = request.args.get("folder_id", "root")
    sort_by = request.args.get("sort_by", "file_name")
    order = request.args.get("order", "asc")
    page = int(request.args.get("page", 1))
    page_size = int(request.args.get("page_size", 15))
    account_index = int(request.args.get("account_index", 0))
    force_refresh = request.args.get("force_refresh", "false").lower() == "true"  # 新增账号索引参数

    try:
        # 验证账号索引
        if account_index < 0 or account_index >= len(config_data["cookie"]):
            return jsonify({"success": False, "message": "账号索引无效"})

        # 初始化夸克网盘客户端
        account = Quark(config_data["cookie"][account_index], account_index)

        # 获取文件列表
        if folder_id == "root":
            folder_id = "0"  # 根目录的ID为0
            paths = []  # 根目录没有路径
        else:
            # 获取当前文件夹的路径
            paths = account.get_paths(folder_id)

        # 获取性能配置
        perf_config = get_performance_config()
        api_page_size = perf_config.get("api_page_size", 200)
        cache_expire_time = perf_config.get("cache_expire_time", 30)

        # 缓存键
        cache_key = f"{account_index}_{folder_id}_{sort_by}_{order}"
        current_time = time.time()

        # 检查缓存（除非强制刷新）
        if not force_refresh:
            with cache_lock:
                if cache_key in file_list_cache:
                    cache_data, cache_time = file_list_cache[cache_key]
                    if current_time - cache_time < cache_expire_time:
                        # 使用缓存数据
                        cached_files = cache_data
                        total = len(cached_files)
                        start_idx = (page - 1) * page_size
                        end_idx = min(start_idx + page_size, total)
                        paginated_files = cached_files[start_idx:end_idx]

                        return jsonify({
                            "success": True,
                            "data": {
                                "list": paginated_files,
                                "total": total,
                                "paths": paths
                            }
                        })
        else:
            # 强制刷新时，清除当前目录的缓存
            with cache_lock:
                if cache_key in file_list_cache:
                    del file_list_cache[cache_key]

        # 无论分页还是全部模式，都必须获取所有文件才能进行正确的全局排序
        files = account.ls_dir(folder_id, page_size=api_page_size)

        if isinstance(files, dict) and files.get("error"):
            # 检查是否是目录不存在的错误
            error_msg = files.get('error', '未知错误')
            if "不存在" in error_msg or "无效" in error_msg or "找不到" in error_msg:
                return jsonify({"success": False, "message": f"目录不存在或无权限访问: {error_msg}"})
            else:
                return jsonify({"success": False, "message": f"获取文件列表失败: {error_msg}"})

        # 处理文件夹的include_items字段，确保空文件夹显示为0项而不是undefined
        for file_item in files:
            if file_item.get("dir", False):
                # 如果是文件夹，确保include_items字段存在且为数字
                if "include_items" not in file_item or file_item["include_items"] is None:
                    file_item["include_items"] = 0
                elif not isinstance(file_item["include_items"], (int, float)):
                    # 如果include_items不是数字类型，尝试转换为整数，失败则设为0
                    try:
                        file_item["include_items"] = int(file_item["include_items"])
                    except (ValueError, TypeError):
                        file_item["include_items"] = 0

        # 计算总数
        total = len(files)

        # 优化排序：使用更高效的排序方法
        def get_sort_key(file_item):
            if sort_by == "file_name":
                # 使用拼音排序
                return get_filename_pinyin_sort_key(file_item["file_name"])
            elif sort_by == "file_size":
                # 文件夹按项目数量排序，文件按大小排序
                return file_item.get("include_items", 0) if file_item["dir"] else file_item["size"]
            else:  # updated_at
                return file_item["updated_at"]

        # 分离文件夹和文件以优化排序
        if sort_by == "updated_at":
            # 修改日期排序时严格按照日期排序，不区分文件夹和文件
            files.sort(key=get_sort_key, reverse=(order == "desc"))
            sorted_files = files
        else:
            # 其他排序时目录始终在前面，分别排序以提高效率
            directories = [f for f in files if f["dir"]]
            normal_files = [f for f in files if not f["dir"]]

            directories.sort(key=get_sort_key, reverse=(order == "desc"))
            normal_files.sort(key=get_sort_key, reverse=(order == "desc"))

            sorted_files = directories + normal_files

        # 更新缓存
        with cache_lock:
            file_list_cache[cache_key] = (sorted_files, current_time)

        # 分页
        start_idx = (page - 1) * page_size
        end_idx = min(start_idx + page_size, total)
        paginated_files = sorted_files[start_idx:end_idx]

        return jsonify({
            "success": True,
            "data": {
                "list": paginated_files,
                "total": total,
                "paths": paths
            }
        })
        
    except Exception as e:
        logging.error(f">>> 获取文件列表时出错: {str(e)}")
        return jsonify({"success": False, "message": f"获取文件列表时出错: {str(e)}"})


# 预览重命名
@app.route("/preview_rename")
def preview_rename():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    
    # 获取请求参数
    folder_id = request.args.get("folder_id", "root")
    pattern = request.args.get("pattern", "")
    replace = request.args.get("replace", "")
    naming_mode = request.args.get("naming_mode", "regex")  # regex, sequence, episode
    include_folders = request.args.get("include_folders", "false") == "true"
    filterwords = request.args.get("filterwords", "")
    account_index = int(request.args.get("account_index", 0))  # 新增账号索引参数

    if not pattern:
        pattern = ".*"

    try:
        # 验证账号索引
        if account_index < 0 or account_index >= len(config_data["cookie"]):
            return jsonify({"success": False, "message": "账号索引无效"})

        # 初始化夸克网盘客户端
        account = Quark(config_data["cookie"][account_index], account_index)
        
        # 获取文件列表
        if folder_id == "root":
            folder_id = "0"  # 根目录的ID为0
        
        files = account.ls_dir(folder_id)
        if isinstance(files, dict) and files.get("error"):
            return jsonify({"success": False, "message": f"获取文件列表失败: {files.get('error', '未知错误')}"})
        
        # 使用高级过滤函数过滤文件
        filtered_files = advanced_filter_files(files, filterwords)
        
        # 如果不包含文件夹，进一步过滤掉文件夹
        if not include_folders:
            filtered_files = [file for file in filtered_files if not file["dir"]]
        
        # 按不同命名模式处理
        preview_results = []
        
        if naming_mode == "sequence":
            # 顺序命名模式
            # 排序文件（按文件名或修改时间）
            filtered_files.sort(key=lambda x: sort_file_by_name(x["file_name"]))
            
            sequence = 1
            for file in filtered_files:
                extension = os.path.splitext(file["file_name"])[1] if not file["dir"] else ""
                new_name = pattern.replace("{}", f"{sequence:02d}") + extension
                preview_results.append({
                    "original_name": file["file_name"],
                    "new_name": new_name,
                    "file_id": file["fid"]
                })
                sequence += 1
                
        elif naming_mode == "episode":
            # 剧集命名模式
            # 获取默认的剧集模式
            default_episode_pattern = {"regex": '第(\\d+)集|第(\\d+)期|第(\\d+)话|(\\d+)集|(\\d+)期|(\\d+)话|[Ee][Pp]?(\\d+)|(\\d+)[-_\\s]*4[Kk]|\\[(\\d+)\\]|【(\\d+)】|_?(\\d+)_?'}
            
            # 获取配置的剧集模式，确保每个模式都是字典格式
            episode_patterns = []
            raw_patterns = config_data.get("episode_patterns", [default_episode_pattern])
            for p in raw_patterns:
                if isinstance(p, dict) and p.get("regex"):
                    episode_patterns.append(p)
                elif isinstance(p, str):
                    episode_patterns.append({"regex": p})
            
            # 如果没有有效的模式，使用默认模式
            if not episode_patterns:
                episode_patterns = [default_episode_pattern]
                
            # 添加中文数字匹配模式
            chinese_patterns = [
                {"regex": r'第([一二三四五六七八九十百千万零两]+)集'},
                {"regex": r'第([一二三四五六七八九十百千万零两]+)期'},
                {"regex": r'第([一二三四五六七八九十百千万零两]+)话'},
                {"regex": r'([一二三四五六七八九十百千万零两]+)集'},
                {"regex": r'([一二三四五六七八九十百千万零两]+)期'},
                {"regex": r'([一二三四五六七八九十百千万零两]+)话'}
            ]
            episode_patterns.extend(chinese_patterns)
            
            # 处理每个文件
            for file in filtered_files:
                extension = os.path.splitext(file["file_name"])[1] if not file["dir"] else ""
                # 从文件名中提取集号
                episode_num = extract_episode_number(file["file_name"], episode_patterns=episode_patterns)
                
                if episode_num is not None:
                    new_name = pattern.replace("[]", f"{episode_num:02d}") + extension
                    preview_results.append({
                        "original_name": file["file_name"],
                        "new_name": new_name,
                        "file_id": file["fid"],
                        "episode_number": episode_num  # 添加集数字段用于前端排序
                    })
                else:
                    # 没有提取到集号，显示无法识别的提示
                    preview_results.append({
                        "original_name": file["file_name"],
                        "new_name": "× 无法识别剧集编号",
                        "file_id": file["fid"]
                    })
        
        else:
            # 正则命名模式
            for file in filtered_files:
                try:
                    # 应用正则表达式
                    if replace:
                        new_name = re.sub(pattern, replace, file["file_name"])
                    else:
                        # 如果没有提供替换表达式，则检查是否匹配
                        if re.search(pattern, file["file_name"]):
                            new_name = file["file_name"]  # 匹配但不替换
                        else:
                            new_name = ""  # 表示不匹配
                            
                    preview_results.append({
                        "original_name": file["file_name"],
                        "new_name": new_name if new_name != file["file_name"] else "",  # 如果没有改变，返回空表示不重命名
                        "file_id": file["fid"]
                    })
                except Exception as e:
                    # 正则表达式错误
                    preview_results.append({
                        "original_name": file["file_name"],
                        "new_name": "",  # 表示无法重命名
                        "file_id": file["fid"],
                        "error": str(e)
                    })
        
        return jsonify({
            "success": True, 
            "data": preview_results
        })
        
    except Exception as e:
        logging.error(f">>> 预览重命名时出错: {str(e)}")
        return jsonify({"success": False, "message": f"预览重命名时出错: {str(e)}"})


# 批量重命名
@app.route("/batch_rename", methods=["POST"])
def batch_rename():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    
    # 获取请求参数
    data = request.json
    files = data.get("files", [])
    account_index = int(data.get("account_index", 0))  # 新增账号索引参数

    if not files:
        return jsonify({"success": False, "message": "没有文件需要重命名"})

    try:
        # 验证账号索引
        if account_index < 0 or account_index >= len(config_data["cookie"]):
            return jsonify({"success": False, "message": "账号索引无效"})

        # 初始化夸克网盘客户端
        account = Quark(config_data["cookie"][account_index], account_index)
        
        # 批量重命名
        success_count = 0
        failed_files = []
        save_path = data.get("save_path", "")
        batch_time = int(time.time() * 1000)  # 批次时间戳，确保同一批transfer_time一致
        for file_item in files:
            file_id = file_item.get("file_id")
            new_name = file_item.get("new_name")
            original_name = file_item.get("old_name") or ""
            if file_id and new_name:
                try:
                    result = account.rename(file_id, new_name)
                    if result.get("code") == 0:
                        success_count += 1
                        # 记录重命名
                        record_db.add_record(
                            task_name="rename",
                            original_name=original_name,
                            renamed_to=new_name,
                            file_size=0,
                            modify_date=int(time.time()),
                            file_id=file_id,
                            file_type="file",
                            save_path=save_path,
                            transfer_time=batch_time
                        )
                    else:
                        failed_files.append({
                            "file_id": file_id,
                            "new_name": new_name,
                            "error": result.get("message", "未知错误")
                        })
                except Exception as e:
                    failed_files.append({
                        "file_id": file_id,
                        "new_name": new_name,
                        "error": str(e)
                    })
        
        return jsonify({
            "success": True, 
            "message": f"成功重命名 {success_count} 个文件，失败 {len(failed_files)} 个",
            "success_count": success_count,
            "failed_files": failed_files
        })
        
    except Exception as e:
        logging.error(f">>> 批量重命名时出错: {str(e)}")
        return jsonify({"success": False, "message": f"批量重命名时出错: {str(e)}"})


# 撤销重命名接口
@app.route("/undo_rename", methods=["POST"])
def undo_rename():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    data = request.json
    save_path = data.get("save_path", "")
    account_index = int(data.get("account_index", 0))  # 新增账号索引参数

    if not save_path:
        return jsonify({"success": False, "message": "缺少目录参数"})

    try:
        # 验证账号索引
        if account_index < 0 or account_index >= len(config_data["cookie"]):
            return jsonify({"success": False, "message": "账号索引无效"})

        account = Quark(config_data["cookie"][account_index], account_index)
        # 查询该目录下最近一次重命名（按transfer_time分组，task_name=rename）
        records = record_db.get_records_by_save_path(save_path)
        rename_records = [r for r in records if r["task_name"] == "rename"]
        if not rename_records:
            return jsonify({"success": False, "message": "没有可撤销的重命名记录"})
        # 找到最近一批（transfer_time最大值）
        latest_time = max(r["transfer_time"] for r in rename_records)
        latest_batch = [r for r in rename_records if r["transfer_time"] == latest_time]
        success_count = 0
        failed_files = []
        deleted_ids = []
        for rec in latest_batch:
            file_id = rec["file_id"]
            old_name = rec["original_name"]
            try:
                result = account.rename(file_id, old_name)
                if result.get("code") == 0:
                    success_count += 1
                    deleted_ids.append(rec["id"])
                else:
                    failed_files.append({
                        "file_id": file_id,
                        "old_name": old_name,
                        "error": result.get("message", "未知错误")
                    })
            except Exception as e:
                failed_files.append({
                    "file_id": file_id,
                    "old_name": old_name,
                    "error": str(e)
                })
        # 撤销成功的，直接删除对应rename记录
        for rid in deleted_ids:
            record_db.delete_record(rid)
        return jsonify({
            "success": True,
            "message": f"成功撤销 {success_count} 个文件重命名，失败 {len(failed_files)} 个",
            "success_count": success_count,
            "failed_files": failed_files
        })
    except Exception as e:
        logging.error(f">>> 撤销重命名时出错: {str(e)}")
        return jsonify({"success": False, "message": f"撤销重命名时出错: {str(e)}"})


@app.route("/api/has_rename_record")
def has_rename_record():
    save_path = request.args.get("save_path", "")
    db = RecordDB()
    records = db.get_records_by_save_path(save_path)
    has_rename = any(r["task_name"] == "rename" for r in records)
    return jsonify({"has_rename": has_rename})


# 手动同步任务配置与数据库绑定关系
@app.route("/api/calendar/sync_task_config", methods=["POST"])
def sync_task_config_api():
    """手动触发任务配置与数据库绑定关系的同步"""
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    
    try:
        success = sync_task_config_with_database_bindings()
        if success:
            return jsonify({"success": True, "message": "同步完成"})
        else:
            return jsonify({"success": False, "message": "没有需要同步的数据"})
    except Exception as e:
        return jsonify({"success": False, "message": f"同步失败: {str(e)}"})

# 手动同步内容类型数据
@app.route("/api/calendar/sync_content_type", methods=["POST"])
def sync_content_type_api():
    """手动触发内容类型数据同步"""
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    
    try:
        success = sync_content_type_between_config_and_database()
        if success:
            return jsonify({"success": True, "message": "内容类型同步完成"})
        else:
            return jsonify({"success": False, "message": "没有需要同步的内容类型数据"})
    except Exception as e:
        return jsonify({"success": False, "message": f"内容类型同步失败: {str(e)}"})

# 追剧日历API路由
@app.route("/api/calendar/tasks")
def get_calendar_tasks():
    """获取追剧日历任务信息"""
    try:
        logging.debug("进入get_calendar_tasks函数")
        logging.debug(f"config_data类型: {type(config_data)}")
        logging.debug(f"config_data内容: {config_data}")
        
        # 获取任务列表
        tasks = config_data.get('tasklist', [])
        
        # 获取任务的最近转存文件信息
        db = RecordDB()
        cursor = db.conn.cursor()
        
        # 获取所有任务的最新转存时间
        query = """
        SELECT task_name, MAX(transfer_time) as latest_transfer_time
        FROM transfer_records
        WHERE task_name NOT IN ('rename', 'undo_rename')
        GROUP BY task_name
        """
        cursor.execute(query)
        latest_times = cursor.fetchall()
        
        task_latest_files = {}
        
        for task_name, latest_time in latest_times:
            if latest_time:
                # 获取该任务在最新转存时间附近的所有文件
                time_window = 60000  # 60秒的时间窗口（毫秒）
                query = """
                SELECT renamed_to, original_name, transfer_time, modify_date
                FROM transfer_records
                WHERE task_name = ? AND transfer_time >= ? AND transfer_time <= ?
                ORDER BY id DESC
                """
                cursor.execute(query, (task_name, latest_time - time_window, latest_time + time_window))
                files = cursor.fetchall()
                
                if files:
                    if len(files) == 1:
                        best_file = files[0][0]  # renamed_to
                    else:
                        # 如果有多个文件，使用全局排序函数进行排序
                        file_list = []
                        for renamed_to, original_name, transfer_time, modify_date in files:
                            file_info = {
                                'file_name': renamed_to,
                                'original_name': original_name,
                                'updated_at': transfer_time
                            }
                            file_list.append(file_info)
                        
                        try:
                            sorted_files = sorted(file_list, key=sort_file_by_name)
                            best_file = sorted_files[-1]['file_name']
                        except Exception:
                            best_file = files[0][0]
                    
                    # 去除扩展名并处理季数集数信息
                    if best_file:
                        file_name_without_ext = os.path.splitext(best_file)[0]
                        processed_name = process_season_episode_info(file_name_without_ext, task_name)
                        task_latest_files[task_name] = processed_name
        
        db.close()
        
        # 提取任务信息
        logging.debug("开始提取任务信息")
        logging.debug(f"tasks数量: {len(tasks)}")
        logging.debug(f"task_latest_files数量: {len(task_latest_files)}")
        
        try:
            extractor = TaskExtractor()
            logging.debug("TaskExtractor创建成功")
            tasks_info = extractor.extract_all_tasks_info(tasks, task_latest_files)
            logging.debug("extract_all_tasks_info调用成功")
        except Exception as e:
            logging.debug(f"TaskExtractor相关操作失败: {e}")
            import traceback
            traceback.print_exc()
            raise
        
        logging.debug(f"提取的任务信息数量: {len(tasks_info)}")
        if tasks_info:
            logging.debug(f"第一个任务信息: {tasks_info[0]}")

        # 首先同步数据库绑定关系到任务配置
        sync_task_config_with_database_bindings()
        
        # 同步内容类型数据
        sync_content_type_between_config_and_database()
        
        # 基于 tmdb 绑定补充"实际匹配结果"（show 名称/年份/本地海报）
        try:
            from app.sdk.db import CalendarDB
            cal_db = CalendarDB()
            enriched = []
            # 先获取所有 shows 数据，用于按名称匹配
            cursor = cal_db.conn.cursor()
            cursor.execute('SELECT tmdb_id, name, year, poster_local_path, bound_task_names FROM shows')
            all_shows = cursor.fetchall()
            shows_by_name = {s[1]: {'tmdb_id': s[0], 'name': s[1], 'year': s[2], 'poster_local_path': s[3], 'bound_task_names': s[4]} for s in all_shows}
            
            for idx, t in enumerate(tasks_info):
                # 1) 优先通过 calendar_info.match.tmdb_id 查找
                raw = tasks[idx] if idx < len(tasks) else None
                cal = (raw or {}).get('calendar_info') or {}
                match = cal.get('match') or {}
                tmdb_id = match.get('tmdb_id')
                
                task_name = t.get('task_name', '').strip()
                matched_show = None
                
                # 优先通过任务的 TMDB 匹配结果查找节目
                if tmdb_id:
                    try:
                        show = cal_db.get_show(int(tmdb_id)) or {}
                        if show:
                            matched_show = show
                            logging.debug(f"通过任务 TMDB 匹配结果找到节目 - 任务: '{task_name}', tmdb_id: {tmdb_id}, 节目: '{show.get('name', '')}'")
                    except Exception as e:
                        logging.debug(f"通过任务 TMDB 匹配结果查找失败 - 任务: '{task_name}', tmdb_id: {tmdb_id}, 错误: {e}")
                        pass
                else:
                    logging.debug(f"任务配置中无 TMDB 匹配结果，尝试通过已绑定关系查找 - 任务: '{task_name}'")
                
                # 2) 如果找到匹配的节目，检查是否需要建立绑定关系
                if matched_show:
                    # 检查任务是否已经绑定到该节目，避免重复绑定
                    bound_tasks = cal_db.get_bound_tasks_for_show(matched_show['tmdb_id'])
                    # 清理空格，确保比较准确
                    bound_tasks_clean = [task.strip() for task in bound_tasks if task.strip()]
                    needs_binding = task_name not in bound_tasks_clean
                    
                    logging.debug(f"检查绑定状态 - 任务: '{task_name}', 已绑定任务: {bound_tasks_clean}, 需要绑定: {needs_binding}")
                    
                    if needs_binding:
                        # 建立任务与节目的绑定关系，同时设置内容类型
                        task_content_type = t.get('content_type', '')
                        cal_db.bind_task_and_content_type(matched_show['tmdb_id'], task_name, task_content_type)
                        logging.debug(f"成功绑定任务到节目 - 任务: '{task_name}', 节目: '{matched_show['name']}', tmdb_id: {matched_show['tmdb_id']}")
                    else:
                        logging.debug(f"任务已绑定到节目，跳过重复绑定 - 任务: '{task_name}', 节目: '{matched_show['name']}', tmdb_id: {matched_show['tmdb_id']}")
                    
                    t['match_tmdb_id'] = matched_show['tmdb_id']
                    t['matched_show_name'] = matched_show['name']
                    t['matched_year'] = matched_show['year']
                    t['matched_poster_local_path'] = matched_show['poster_local_path']
                    # 提供最新季数用于前端展示
                    try:
                        t['matched_latest_season_number'] = matched_show.get('latest_season_number')
                    except Exception:
                        pass
                    # 从数据库获取实际的内容类型（如果已设置）
                    db_content_type = cal_db.get_show_content_type(matched_show['tmdb_id'])
                    t['matched_content_type'] = db_content_type if db_content_type else t.get('content_type', '')
                    # 优先保持任务配置中的类型；仅当任务未设置任何类型时，回退为数据库类型
                    extracted_ct = ((t.get('calendar_info') or {}).get('extracted') or {}).get('content_type')
                    config_ct = extracted_ct or t.get('content_type')
                    if not (config_ct and str(config_ct).strip()):
                        if t.get('matched_content_type'):
                            t['content_type'] = t['matched_content_type']
                else:
                    # 如果任务配置中没有 TMDB 匹配结果，检查是否已通过其他方式绑定到节目
                    # 通过任务名称在数据库中搜索已绑定的节目
                    try:
                        cursor.execute('SELECT tmdb_id, name, year, poster_local_path FROM shows WHERE bound_task_names LIKE ?', (f'%{task_name}%',))
                        bound_show = cursor.fetchone()
                        if bound_show:
                            t['match_tmdb_id'] = bound_show[0]
                            t['matched_show_name'] = bound_show[1]
                            t['matched_year'] = bound_show[2]
                            t['matched_poster_local_path'] = bound_show[3]
                            # 查询完整信息以提供最新季数
                            try:
                                full_show = cal_db.get_show(int(bound_show[0]))
                                if full_show and 'latest_season_number' in full_show:
                                    t['matched_latest_season_number'] = full_show['latest_season_number']
                            except Exception:
                                pass
                            # 从数据库获取实际的内容类型（如果已设置）
                            db_content_type = cal_db.get_show_content_type(bound_show[0])
                            t['matched_content_type'] = db_content_type if db_content_type else t.get('content_type', '')
                            # 优先任务配置类型；仅在未配置类型时用数据库类型
                            extracted_ct = ((t.get('calendar_info') or {}).get('extracted') or {}).get('content_type')
                            config_ct = extracted_ct or t.get('content_type')
                            if not (config_ct and str(config_ct).strip()):
                                if t.get('matched_content_type'):
                                    t['content_type'] = t['matched_content_type']
                            
                            logging.debug(f"通过已绑定关系找到节目 - 任务: '{task_name}', 节目: '{bound_show[1]}', tmdb_id: {bound_show[0]}")
                        else:
                            t['match_tmdb_id'] = None
                            t['matched_show_name'] = ''
                            t['matched_year'] = ''
                            t['matched_poster_local_path'] = ''
                            t['matched_content_type'] = t.get('content_type', '')
                            
                            logging.debug(f"任务未绑定到任何节目 - 任务: '{task_name}'")
                    except Exception as e:
                        logging.debug(f"搜索已绑定节目失败 - 任务: '{task_name}', 错误: {e}")
                        t['match_tmdb_id'] = None
                        t['matched_show_name'] = ''
                        t['matched_year'] = ''
                        t['matched_poster_local_path'] = ''
                        t['matched_content_type'] = t.get('content_type', '')
                enriched.append(t)
            tasks_info = enriched
        except Exception as _e:
            # 若补充失败，不影响主流程
            print(f"补充匹配结果失败: {_e}")
            pass

        return jsonify({
            'success': True,
            'data': {
                # 返回全部任务（包括未匹配/未提取剧名的），用于内容管理页显示
                'tasks': enrich_tasks_with_calendar_meta(tasks_info),
                'content_types': extractor.get_content_types_with_content([t for t in tasks_info if t.get('show_name')])
            }
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取追剧日历任务信息失败: {str(e)}',
            'data': {'tasks': [], 'content_types': []}
        })

@app.route("/api/calendar/episodes")
def get_calendar_episodes():
    """获取指定日期范围的剧集播出信息"""
    try:
        
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        content_type = request.args.get('content_type', '')
        show_name = request.args.get('show_name', '')
        
        if not start_date or not end_date:
            return jsonify({
                'success': False,
                'message': '请提供开始和结束日期',
                'data': {'episodes': []}
            })
        
        # 获取TMDB配置
        tmdb_api_key = config_data.get('tmdb_api_key', '')
        if not tmdb_api_key:
            return jsonify({
                'success': False,
                'message': 'TMDB API未配置',
                'data': {'episodes': []}
            })
        
        # 获取任务信息
        tasks = config_data.get('tasklist', [])
        extractor = TaskExtractor()
        tasks_info = extractor.extract_all_tasks_info(tasks, {})
        
        # 过滤任务
        if content_type:
            tasks_info = [task for task in tasks_info if task['content_type'] == content_type]
        if show_name:
            tasks_info = [task for task in tasks_info if show_name.lower() in task['show_name'].lower()]
        
        # 获取剧集信息
        poster_language = get_poster_language_setting()
        tmdb_service = TMDBService(tmdb_api_key, poster_language)
        all_episodes = []
        
        for task_info in tasks_info:
            if task_info['show_name'] and task_info['year']:
                show_data = tmdb_service.search_and_get_episodes(
                    task_info['show_name'], 
                    task_info['year']
                )
                if show_data:
                    # 获取剧集的海报信息
                    show_poster_path = show_data['show_info'].get('poster_path', '')
                    
                    # 过滤日期范围内的剧集
                    for episode in show_data['episodes']:
                        # 确保episode有air_date字段
                        if episode.get('air_date') and start_date <= episode['air_date'] <= end_date:
                            episode['task_info'] = {
                                'task_name': task_info['task_name'],
                                'content_type': task_info['content_type'],
                                'progress': task_info
                            }
                            # 添加海报路径信息
                            episode['poster_path'] = show_poster_path
                            all_episodes.append(episode)
        
        # 按播出日期排序
        all_episodes.sort(key=lambda x: x.get('air_date', ''))
        
        return jsonify({
            'success': True,
            'data': {
                'episodes': all_episodes,
                'total': len(all_episodes)
            }
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取剧集播出信息失败: {str(e)}',
            'data': {'episodes': [], 'total': 0}
        })


# 初始化：为任务写入 shows/seasons，下载海报本地化
def process_new_tasks_async():
    """异步处理新任务的元数据匹配和海报下载，不阻塞前端响应"""
    try:
        tmdb_api_key = config_data.get('tmdb_api_key', '')
        if not tmdb_api_key:
            return

        poster_language = get_poster_language_setting()
        tmdb_service = TMDBService(tmdb_api_key, poster_language)
        cal_db = CalendarDB()

        tasks = config_data.get('tasklist', [])
        # 收集需要处理的任务
        tasks_to_process = []
        for task in tasks:
            # 只处理没有完整元数据的任务
            cal = (task or {}).get('calendar_info') or {}
            match = cal.get('match') or {}
            extracted = cal.get('extracted') or {}
            tmdb_id = match.get('tmdb_id')
            
            # 如果已经有完整的元数据，跳过
            if tmdb_id and match.get('matched_show_name'):
                continue
                
            tasks_to_process.append(task)

        # 如果没有需要处理的任务，直接返回
        if not tasks_to_process:
            return

        # 并行处理任务
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = []
            for task in tasks_to_process:
                future = executor.submit(process_single_task_async, task, tmdb_service, cal_db)
                futures.append(future)
            
            # 等待所有任务完成
            for future in concurrent.futures.as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    logging.warning(f"处理任务失败: {e}")

    except Exception as e:
        logging.warning(f"异步处理新任务失败: {e}")


def process_single_task_async(task, tmdb_service, cal_db):
    """处理单个任务的元数据匹配和海报下载"""
    try:
        cal = (task or {}).get('calendar_info') or {}
        match = cal.get('match') or {}
        extracted = cal.get('extracted') or {}
        tmdb_id = match.get('tmdb_id')
        
        # 如果还没有提取信息，先提取
        if not extracted.get('show_name'):
            extractor = TaskExtractor()
            task_name = task.get('taskname') or task.get('task_name') or ''
            save_path = task.get('savepath', '')
            info = extractor.extract_show_info_from_path(save_path)
            extracted = {
                'show_name': info.get('show_name', ''),
                'year': info.get('year', ''),
                'content_type': info.get('type', 'other'),
            }
            cal['extracted'] = extracted
            task['calendar_info'] = cal

        # 如果还没有 TMDB 匹配，进行匹配
        if not tmdb_id and extracted.get('show_name'):
            try:
                search = tmdb_service.search_tv_show(extracted.get('show_name'), extracted.get('year') or None)
                if search and search.get('id'):
                    tmdb_id = search['id']
                    details = tmdb_service.get_tv_show_details(tmdb_id) or {}
                    seasons = details.get('seasons', [])
                    logging.debug(f"process_single_task_async - TMDB季数数据: {seasons}")
                    
                    # 选择已播出的季中最新的一季
                    current_date = datetime.now().date()
                    latest_season_number = 0
                    latest_air_date = None
                    
                    for s in seasons:
                        sn = s.get('season_number', 0)
                        air_date = s.get('air_date')
                        logging.debug(f"process_single_task_async - 季数: {sn}, 播出日期: {air_date}, 类型: {type(sn)}")
                        
                        # 只考虑已播出的季（有air_date且早于或等于当前日期）
                        if sn and sn > 0 and air_date:  # 排除第0季（特殊季）
                            try:
                                season_air_date = datetime.strptime(air_date, '%Y-%m-%d').date()
                                if season_air_date <= current_date:
                                    # 选择播出日期最新的季
                                    if latest_air_date is None or season_air_date > latest_air_date:
                                        latest_season_number = sn
                                        latest_air_date = season_air_date
                            except (ValueError, TypeError):
                                # 日期格式错误，跳过
                                continue
                    
                    logging.debug(f"process_single_task_async - 计算出的最新已播季数: {latest_season_number}")
                    
                    # 如果没有找到已播出的季，回退到第1季
                    if latest_season_number == 0:
                        latest_season_number = 1
                        logging.debug(f"process_single_task_async - 没有找到已播出的季，回退到第1季: {latest_season_number}")

                    chinese_title = tmdb_service.get_chinese_title_with_fallback(tmdb_id, extracted.get('show_name', ''))
                    
                    cal['match'] = {
                        'matched_show_name': chinese_title,
                        'matched_year': (details.get('first_air_date') or '')[:4],
                        'tmdb_id': tmdb_id,
                        'latest_season_number': latest_season_number,
                        'latest_season_fetch_url': f"/tv/{tmdb_id}/season/{latest_season_number}",
                    }
                    task['calendar_info'] = cal
                    
                    # 立即通知前端元数据匹配完成
                    try:
                        notify_calendar_changed('edit_metadata')
                    except Exception:
                        pass
                        
            except Exception as e:
                logging.warning(f"TMDB 匹配失败: task={task.get('taskname', '')}, err={e}")

        # 如果现在有 TMDB ID，下载海报并更新数据库
        if tmdb_id:
            try:
                # 重新获取更新后的match信息
                updated_match = cal.get('match') or {}
                logging.debug(f"process_single_task_async - 更新后的match信息: {updated_match}")
                details = tmdb_service.get_tv_show_details(tmdb_id) or {}
                name = tmdb_service.get_chinese_title_with_fallback(tmdb_id, extracted.get('show_name', ''))
                first_air = (details.get('first_air_date') or '')[:4]
                raw_status = (details.get('status') or '')
                try:
                    localized_status = tmdb_service.get_localized_show_status(int(tmdb_id), int(updated_match.get('latest_season_number') or 1), raw_status)
                    status = localized_status
                except Exception:
                    status = raw_status
                # 使用海报语言设置获取海报路径
                poster_path = tmdb_service.get_poster_path_with_language(int(tmdb_id)) if tmdb_service else (details.get('poster_path') or '')
                latest_season_number = updated_match.get('latest_season_number') or 1
                logging.debug(f"process_single_task_async - 最终使用的季数: {latest_season_number}")

                poster_local_path = ''
                if poster_path:
                    poster_local_path = download_poster_local(poster_path)

                # 获取现有的绑定关系和内容类型，避免被覆盖
                existing_show = cal_db.get_show(int(tmdb_id))
                existing_bound_tasks = existing_show.get('bound_task_names', '') if existing_show else ''
                existing_content_type = existing_show.get('content_type', '') if existing_show else ''
                cal_db.upsert_show(int(tmdb_id), name, first_air, status, poster_local_path, int(latest_season_number), 0, existing_bound_tasks, existing_content_type)
                
                # 更新 seasons 表
                refresh_url = f"/tv/{tmdb_id}/season/{latest_season_number}"
                # 处理季名称
                season_name_raw = ''
                try:
                    for s in (details.get('seasons') or []):
                        if int(s.get('season_number') or 0) == int(latest_season_number):
                            season_name_raw = s.get('name') or ''
                            break
                except Exception:
                    season_name_raw = ''
                try:
                    from sdk.tmdb_service import TMDBService as _T
                    season_name_processed = tmdb_service.process_season_name(season_name_raw) if isinstance(tmdb_service, _T) else season_name_raw
                except Exception:
                    season_name_processed = season_name_raw
                cal_db.upsert_season(
                    int(tmdb_id),
                    int(latest_season_number),
                    details_of_season_episode_count(tmdb_service, tmdb_id, latest_season_number),
                    refresh_url,
                    season_name_processed
                )

                # 立即拉取最新一季的所有集并写入本地，保证前端可立即读取
                try:
                    season = tmdb_service.get_tv_show_episodes(int(tmdb_id), int(latest_season_number)) or {}
                    episodes = season.get('episodes', []) or []
                    from time import time as _now
                    now_ts = int(_now())
                    for ep in episodes:
                        cal_db.upsert_episode(
                            tmdb_id=int(tmdb_id),
                            season_number=int(latest_season_number),
                            episode_number=int(ep.get('episode_number') or 0),
                            name=ep.get('name') or '',
                            overview=ep.get('overview') or '',
                            air_date=ep.get('air_date') or '',
                            runtime=ep.get('runtime'),
                            ep_type=(ep.get('episode_type') or ep.get('type')),
                            updated_at=now_ts,
                        )
                except Exception as _e:
                    logging.warning(f"写入剧集失败 tmdb_id={tmdb_id}: {_e}")
                
                # 绑定任务到节目
                task_final_name = task.get('taskname') or task.get('task_name') or ''
                ct = extracted.get('content_type', '')
                try:
                    cal_db.bind_task_and_content_type(tmdb_id, task_final_name, ct)
                except Exception as e:
                    logging.warning(f"绑定任务失败: {e}")
                
                # 立即通知前端海报下载完成
                try:
                    notify_calendar_changed('edit_metadata')
                except Exception:
                    pass
                    
            except Exception as e:
                logging.warning(f"处理任务海报失败: task={task.get('taskname', '')}, err={e}")

    except Exception as e:
        logging.warning(f"处理单个任务失败: task={task.get('taskname', '')}, err={e}")


def do_calendar_bootstrap() -> tuple:
    """内部方法：执行日历初始化。返回 (success: bool, message: str)"""
    try:
        ensure_calendar_info_for_tasks()

        tmdb_api_key = config_data.get('tmdb_api_key', '')
        if not tmdb_api_key:
            return False, 'TMDB API未配置'

        poster_language = get_poster_language_setting()
        tmdb_service = TMDBService(tmdb_api_key, poster_language)
        cal_db = CalendarDB()

        tasks = config_data.get('tasklist', [])
        any_written = False
        for task in tasks:
            cal = (task or {}).get('calendar_info') or {}
            match = cal.get('match') or {}
            extracted = cal.get('extracted') or {}
            tmdb_id = match.get('tmdb_id')
            if not tmdb_id:
                continue

            details = tmdb_service.get_tv_show_details(tmdb_id) or {}
            # 使用新的方法获取中文标题，支持从别名中获取中国地区的别名
            name = tmdb_service.get_chinese_title_with_fallback(tmdb_id, extracted.get('show_name', ''))
            first_air = (details.get('first_air_date') or '')[:4]
            # 将原始状态转换为本地化中文，且对 returning_series 场景做本季终/播出中判断
            raw_status = (details.get('status') or '')
            try:
                localized_status = tmdb_service.get_localized_show_status(int(tmdb_id), int(match.get('latest_season_number') or 1), raw_status)
                status = localized_status
            except Exception:
                status = raw_status
            # 使用海报语言设置获取海报路径
            poster_path = tmdb_service.get_poster_path_with_language(int(tmdb_id)) if tmdb_service else (details.get('poster_path') or '')
            latest_season_number = match.get('latest_season_number') or 1

            poster_local_path = ''
            if poster_path:
                poster_local_path = download_poster_local(poster_path)

            # 获取现有的绑定关系和内容类型，避免被覆盖
            existing_show = cal_db.get_show(int(tmdb_id))
            existing_bound_tasks = existing_show.get('bound_task_names', '') if existing_show else ''
            existing_content_type = existing_show.get('content_type', '') if existing_show else ''
            cal_db.upsert_show(int(tmdb_id), name, first_air, status, poster_local_path, int(latest_season_number), 0, existing_bound_tasks, existing_content_type)
            refresh_url = f"/tv/{tmdb_id}/season/{latest_season_number}"
            # 处理季名称
            season_name_raw = ''
            try:
                for s in (details.get('seasons') or []):
                    if int(s.get('season_number') or 0) == int(latest_season_number):
                        season_name_raw = s.get('name') or ''
                        break
            except Exception:
                season_name_raw = ''
            try:
                from sdk.tmdb_service import TMDBService as _T
                season_name_processed = tmdb_service.process_season_name(season_name_raw) if isinstance(tmdb_service, _T) else season_name_raw
            except Exception:
                season_name_processed = season_name_raw
            cal_db.upsert_season(
                int(tmdb_id),
                int(latest_season_number),
                details_of_season_episode_count(tmdb_service, tmdb_id, latest_season_number),
                refresh_url,
                season_name_processed
            )

            # 立即拉取最新一季的所有集并写入本地，保证前端可立即读取
            try:
                season = tmdb_service.get_tv_show_episodes(int(tmdb_id), int(latest_season_number)) or {}
                episodes = season.get('episodes', []) or []
                from time import time as _now
                now_ts = int(_now())
                for ep in episodes:
                    cal_db.upsert_episode(
                        tmdb_id=int(tmdb_id),
                        season_number=int(latest_season_number),
                        episode_number=int(ep.get('episode_number') or 0),
                        name=ep.get('name') or '',
                        overview=ep.get('overview') or '',
                        air_date=ep.get('air_date') or '',
                        runtime=ep.get('runtime'),
                        ep_type=(ep.get('episode_type') or ep.get('type')),
                        updated_at=now_ts,
                    )
                any_written = True
            except Exception as _e:
                logging.warning(f"bootstrap 写入剧集失败 tmdb_id={tmdb_id}: {_e}")
        try:
            if any_written:
                notify_calendar_changed('bootstrap')
        except Exception:
            pass
        return True, 'OK'
    except Exception as e:
        return False, f'bootstrap失败: {str(e)}'


@app.route("/api/calendar/bootstrap", methods=["POST"])
def calendar_bootstrap():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    success, msg = do_calendar_bootstrap()
    return jsonify({"success": success, "message": msg})


def details_of_season_episode_count(tmdb_service: TMDBService, tmdb_id: int, season_number: int) -> int:
    try:
        season = tmdb_service.get_tv_show_episodes(tmdb_id, season_number) or {}
        return len(season.get('episodes', []) or [])
    except Exception:
        return 0


def get_poster_language_setting():
    """获取海报语言设置"""
    return config_data.get('poster_language', 'zh-CN')

def clear_all_posters():
    """清空所有海报文件"""
    try:
        import shutil
        if os.path.exists(CACHE_IMAGES_DIR):
            shutil.rmtree(CACHE_IMAGES_DIR)
            os.makedirs(CACHE_IMAGES_DIR, exist_ok=True)
        return True
    except Exception as e:
        logging.error(f"清空海报文件失败: {e}")
        return False

def redownload_all_posters():
    """重新下载所有海报"""
    try:
        from app.sdk.db import CalendarDB
        cal_db = CalendarDB()
        
        # 获取所有节目
        shows = cal_db.get_all_shows()
        if not shows:
            return True
            
        tmdb_api_key = config_data.get('tmdb_api_key', '')
        if not tmdb_api_key:
            logging.warning("TMDB API未配置，无法重新下载海报")
            return False
            
        poster_language = get_poster_language_setting()
        tmdb_service = TMDBService(tmdb_api_key, poster_language)
        
        success_count = 0
        total_count = len(shows)
        
        for show in shows:
            try:
                tmdb_id = show.get('tmdb_id')
                if not tmdb_id:
                    continue
                    
                # 获取新的海报路径
                new_poster_path = tmdb_service.get_poster_path_with_language(int(tmdb_id))
                if new_poster_path:
                    # 下载新海报
                    poster_local_path = download_poster_local(new_poster_path)
                    if poster_local_path:
                        # 更新数据库中的海报路径
                        cal_db.update_show_poster(int(tmdb_id), poster_local_path)
                        success_count += 1
                        # 通知前端该节目海报已更新
                        try:
                            notify_calendar_changed(f'poster_updated:{int(tmdb_id)}')
                        except Exception:
                            pass
                        
            except Exception as e:
                logging.error(f"重新下载海报失败 (TMDB ID: {show.get('tmdb_id')}): {e}")
                continue
                
        return success_count > 0
        
    except Exception as e:
        logging.error(f"重新下载所有海报失败: {e}")
        return False

def download_poster_local(poster_path: str) -> str:
    """下载 TMDB 海报到本地 static/cache/images 下，等比缩放为宽400px，返回相对路径。"""
    try:
        if not poster_path:
            return ''
            
        folder = CACHE_IMAGES_DIR
        # 基于 poster_path 生成文件名
        safe_name = poster_path.strip('/').replace('/', '_') or 'poster.jpg'
        file_path = os.path.join(folder, safe_name)
        
        # 检查文件是否已存在，如果存在则直接返回路径
        if os.path.exists(file_path):
            return f"/cache/images/{safe_name}"
        
        # 文件不存在，需要下载
        base = "https://image.tmdb.org/t/p/w400"
        url = f"{base}{poster_path}"
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return ''
            
        # 确保目录存在
        os.makedirs(folder, exist_ok=True)
        
        with open(file_path, 'wb') as f:
            f.write(r.content)
        # 返回用于前端引用的相对路径（通过 /cache/images 暴露）
        return f"/cache/images/{safe_name}"
    except Exception:
        return ''


def download_custom_poster(poster_url: str, tmdb_id: int, target_safe_name: str = None) -> str:
    """下载自定义海报到本地，尽量覆盖原有海报文件名，返回相对路径。

    注意：不引入任何额外依赖，不进行等比缩放。如需缩放应在容器具备工具时再扩展。
    """
    try:
        if not poster_url or not tmdb_id:
            return ''

        folder = CACHE_IMAGES_DIR
        # 目标文件名：若提供则使用现有文件名覆盖；否则根据内容类型/URL推断扩展名
        default_ext = 'jpg'
        safe_name = (target_safe_name or '').strip()
        if not safe_name:
            # 尝试从 URL 推断扩展名
            guessed_ext = None
            try:
                from urllib.parse import urlparse
                path = urlparse(poster_url).path
                base = os.path.basename(path)
                if '.' in base:
                    guessed_ext = base.split('.')[-1].lower()
            except Exception:
                guessed_ext = None
            ext = guessed_ext or default_ext
            # 简单标准化
            if ext in ('jpeg',):
                ext = 'jpg'
            safe_name = f"poster_{tmdb_id}.{ext}"
        file_path = os.path.join(folder, safe_name)

        # 确保目录存在
        os.makedirs(folder, exist_ok=True)

        # 处理 file:// 前缀
        if poster_url.lower().startswith('file://'):
            poster_url = poster_url[7:]

        # 处理本地文件路径
        if poster_url.startswith('/') or poster_url.startswith('./'):
            # 本地文件路径
            if os.path.exists(poster_url):
                # 若提供了目标名但其扩展名与源文件明显不符（例如源为 .avif），则改用基于 tmdb_id 的标准文件名并匹配扩展
                try:
                    src_ext = os.path.splitext(poster_url)[1].lower().lstrip('.')
                except Exception:
                    src_ext = ''
                if src_ext in ('jpeg',):
                    src_ext = 'jpg'
                if src_ext in ('jpg', 'png', 'webp', 'avif'):
                    target_ext = os.path.splitext(safe_name)[1].lower().lstrip('.') if '.' in safe_name else ''
                    if target_ext != src_ext:
                        safe_name = f"poster_{tmdb_id}.{src_ext or default_ext}"
                        file_path = os.path.join(folder, safe_name)
                import shutil
                shutil.copy2(poster_url, file_path)
                return f"/cache/images/{safe_name}"
            else:
                logging.warning(f"本地海报文件不存在: {poster_url}")
                return ''
        else:
            # 网络URL
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            }
            r = requests.get(poster_url, timeout=15, headers=headers)
            if r.status_code != 200:
                logging.warning(f"下载自定义海报失败: {poster_url}, 状态码: {r.status_code}")
                return ''

            # 检查内容类型是否为图片（无法严格判断扩展名，这里仅基于响应头）
            content_type = (r.headers.get('content-type') or '').lower()
            if not any(img_type in content_type for img_type in ['image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/avif']):
                logging.warning(f"自定义海报URL不是有效的图片格式: {poster_url}, 内容类型: {content_type}")
                # 不强制失败，放行保存，部分服务不返回标准content-type
                # return ''

            # 若未指定目标文件名，或指定的扩展与内容类型不一致，则根据 content-type 修正扩展名
            need_fix_ext = False
            if (target_safe_name or '').strip():
                # 校验已有目标名的扩展
                current_ext = os.path.splitext(safe_name)[1].lower().lstrip('.') if '.' in safe_name else ''
                if ('image/avif' in content_type and current_ext != 'avif') or \
                   ('image/webp' in content_type and current_ext != 'webp') or \
                   (('image/jpeg' in content_type or 'image/jpg' in content_type) and current_ext not in ('jpg','jpeg')) or \
                   ('image/png' in content_type and current_ext != 'png'):
                    need_fix_ext = True
            if not (target_safe_name or '').strip() or need_fix_ext:
                ext_map = {
                    'image/jpeg': 'jpg',
                    'image/jpg': 'jpg',
                    'image/png': 'png',
                    'image/webp': 'webp',
                    'image/avif': 'avif',
                }
                chosen_ext = None
                for k, v in ext_map.items():
                    if k in content_type:
                        chosen_ext = v
                        break
                if chosen_ext:
                    base_no_ext = f"poster_{tmdb_id}"
                    safe_name = f"{base_no_ext}.{chosen_ext}"
                    file_path = os.path.join(folder, safe_name)

            with open(file_path, 'wb') as f:
                f.write(r.content)

            # 自定义海报保存成功（静默）
            return f"/cache/images/{safe_name}"

    except Exception as e:
        logging.error(f"下载自定义海报失败: {e}")
        return ''


# 刷新：拉取最新一季所有集，按有无 runtime 进行增量更新
@app.route("/api/calendar/refresh_latest_season")
def calendar_refresh_latest_season():
    try:
        if not is_login():
            return jsonify({"success": False, "message": "未登录"})

        tmdb_id = request.args.get('tmdb_id', type=int)
        if not tmdb_id:
            return jsonify({"success": False, "message": "缺少 tmdb_id"})

        tmdb_api_key = config_data.get('tmdb_api_key', '')
        if not tmdb_api_key:
            return jsonify({"success": False, "message": "TMDB API 未配置"})

        poster_language = get_poster_language_setting()
        tmdb_service = TMDBService(tmdb_api_key, poster_language)
        cal_db = CalendarDB()
        show = cal_db.get_show(int(tmdb_id))
        if not show:
            return jsonify({"success": False, "message": "未初始化该剧（请先 bootstrap）"})

        latest_season = int(show['latest_season_number'])
        season = tmdb_service.get_tv_show_episodes(tmdb_id, latest_season) or {}
        episodes = season.get('episodes', []) or []

        from time import time as _now
        now_ts = int(_now())

        any_written = False
        for ep in episodes:
            cal_db.upsert_episode(
                tmdb_id=int(tmdb_id),
                season_number=latest_season,
                episode_number=int(ep.get('episode_number') or 0),
                name=ep.get('name') or '',
                overview=ep.get('overview') or '',
                air_date=ep.get('air_date') or '',
                runtime=ep.get('runtime'),
                ep_type=(ep.get('episode_type') or ep.get('type')),
                updated_at=now_ts,
            )
            any_written = True

        # 同步更新 seasons 表的季名称与总集数
        try:
            season_name_raw = season.get('name') or ''
            season_name_processed = tmdb_service.process_season_name(season_name_raw)
        except Exception:
            season_name_processed = season.get('name') or ''
        try:
            cal_db.upsert_season(
                int(tmdb_id),
                int(latest_season),
                len(episodes),
                f"/tv/{tmdb_id}/season/{latest_season}",
                season_name_processed
            )
        except Exception:
            pass

        try:
            if any_written:
                notify_calendar_changed('refresh_latest_season')
        except Exception:
            pass
        return jsonify({"success": True, "updated": len(episodes)})
    except Exception as e:
        return jsonify({"success": False, "message": f"刷新失败: {str(e)}"})


# 强制刷新单集或合并集的元数据（海报视图使用）
@app.route("/api/calendar/refresh_episode")
def calendar_refresh_episode():
    try:
        if not is_login():
            return jsonify({"success": False, "message": "未登录"})

        tmdb_id = request.args.get('tmdb_id', type=int)
        season_number = request.args.get('season_number', type=int)
        episode_number = request.args.get('episode_number', type=int)
        
        if not tmdb_id or not season_number or not episode_number:
            return jsonify({"success": False, "message": "缺少必要参数"})

        tmdb_api_key = config_data.get('tmdb_api_key', '')
        if not tmdb_api_key:
            return jsonify({"success": False, "message": "TMDB API 未配置"})

        poster_language = get_poster_language_setting()
        tmdb_service = TMDBService(tmdb_api_key, poster_language)
        cal_db = CalendarDB()
        
        # 验证节目是否存在
        show = cal_db.get_show(int(tmdb_id))
        if not show:
            return jsonify({"success": False, "message": "未初始化该剧（请先 bootstrap）"})

        # 获取指定季的所有集数据
        season = tmdb_service.get_tv_show_episodes(tmdb_id, season_number) or {}
        episodes = season.get('episodes', []) or []
        
        # 查找指定集
        target_episode = None
        for ep in episodes:
            if int(ep.get('episode_number') or 0) == episode_number:
                target_episode = ep
                break
        
        if not target_episode:
            return jsonify({"success": False, "message": f"未找到第 {season_number} 季第 {episode_number} 集"})

        # 强制更新该集数据（无论是否有 runtime）
        from time import time as _now
        now_ts = int(_now())
        
        cal_db.upsert_episode(
            tmdb_id=int(tmdb_id),
            season_number=season_number,
            episode_number=episode_number,
            name=target_episode.get('name') or '',
            overview=target_episode.get('overview') or '',
            air_date=target_episode.get('air_date') or '',
            runtime=target_episode.get('runtime'),
            ep_type=(target_episode.get('episode_type') or target_episode.get('type')),
            updated_at=now_ts,
        )

        # 通知日历数据变更
        try:
            notify_calendar_changed('refresh_episode')
        except Exception:
            pass
            
        # 获取剧名用于通知
        show_name = show.get('name', '未知剧集')
        return jsonify({"success": True, "message": f"《{show_name}》第 {season_number} 季 · 第 {episode_number} 集刷新成功"})
    except Exception as e:
        return jsonify({"success": False, "message": f"刷新失败: {str(e)}"})


# 强制刷新整个季的元数据（内容管理视图使用）
@app.route("/api/calendar/refresh_season")
def calendar_refresh_season():
    try:
        if not is_login():
            return jsonify({"success": False, "message": "未登录"})

        tmdb_id = request.args.get('tmdb_id', type=int)
        season_number = request.args.get('season_number', type=int)
        
        if not tmdb_id or not season_number:
            return jsonify({"success": False, "message": "缺少必要参数"})

        tmdb_api_key = config_data.get('tmdb_api_key', '')
        if not tmdb_api_key:
            return jsonify({"success": False, "message": "TMDB API 未配置"})

        poster_language = get_poster_language_setting()
        tmdb_service = TMDBService(tmdb_api_key, poster_language)
        cal_db = CalendarDB()
        
        # 验证节目是否存在
        show = cal_db.get_show(int(tmdb_id))
        if not show:
            return jsonify({"success": False, "message": "未初始化该剧（请先 bootstrap）"})

        # 获取指定季的所有集数据
        season = tmdb_service.get_tv_show_episodes(tmdb_id, season_number) or {}
        episodes = season.get('episodes', []) or []
        
        if not episodes:
            return jsonify({"success": False, "message": f"第 {season_number} 季没有集数据"})

        # 强制更新该季所有集数据（无论是否有 runtime）
        from time import time as _now
        now_ts = int(_now())
        
        updated_count = 0
        for ep in episodes:
            cal_db.upsert_episode(
                tmdb_id=int(tmdb_id),
                season_number=season_number,
                episode_number=int(ep.get('episode_number') or 0),
                name=ep.get('name') or '',
                overview=ep.get('overview') or '',
                air_date=ep.get('air_date') or '',
                runtime=ep.get('runtime'),
                ep_type=(ep.get('episode_type') or ep.get('type')),
                updated_at=now_ts,
            )
            updated_count += 1

        # 同步更新 seasons 表的季名称与总集数
        try:
            season_name_raw = season.get('name') or ''
            season_name_processed = tmdb_service.process_season_name(season_name_raw)
        except Exception:
            season_name_processed = season.get('name') or ''
        try:
            cal_db.upsert_season(
                int(tmdb_id),
                int(season_number),
                len(episodes),
                f"/tv/{tmdb_id}/season/{season_number}",
                season_name_processed
            )
        except Exception:
            pass

        # 通知日历数据变更
        try:
            notify_calendar_changed('refresh_season')
        except Exception:
            pass
            
        # 获取剧名用于通知
        show_name = show.get('name', '未知剧集')
        return jsonify({"success": True, "message": f"《{show_name}》第 {season_number} 季刷新成功，共 {updated_count} 集"})
    except Exception as e:
        return jsonify({"success": False, "message": f"刷新失败: {str(e)}"})


# 刷新：拉取剧级别的最新详情并更新 shows 表（用于更新节目状态/中文名/首播年/海报/最新季）
@app.route("/api/calendar/refresh_show")
def calendar_refresh_show():
    try:
        if not is_login():
            return jsonify({"success": False, "message": "未登录"})

        tmdb_id = request.args.get('tmdb_id', type=int)
        if not tmdb_id:
            return jsonify({"success": False, "message": "缺少 tmdb_id"})

        tmdb_api_key = config_data.get('tmdb_api_key', '')
        if not tmdb_api_key:
            return jsonify({"success": False, "message": "TMDB API 未配置"})

        poster_language = get_poster_language_setting()
        tmdb_service = TMDBService(tmdb_api_key, poster_language)
        cal_db = CalendarDB()

        details = tmdb_service.get_tv_show_details(int(tmdb_id)) or {}
        if not details:
            return jsonify({"success": False, "message": "未获取到节目详情"})

        try:
            existing_show = cal_db.get_show(int(tmdb_id))
        except Exception:
            existing_show = None

        # 标题、状态与最新季
        try:
            name = tmdb_service.get_chinese_title_with_fallback(int(tmdb_id), (existing_show or {}).get('name') or '')
        except Exception:
            name = (details.get('name') or details.get('original_name') or (existing_show or {}).get('name') or '')

        first_air = (details.get('first_air_date') or '')[:4]
        raw_status = (details.get('status') or '')
        try:
            latest_sn_for_status = int((existing_show or {}).get('latest_season_number') or 1)
            localized_status = tmdb_service.get_localized_show_status(int(tmdb_id), latest_sn_for_status, raw_status)
        except Exception:
            localized_status = raw_status

        latest_season_number = 0
        try:
            for s in (details.get('seasons') or []):
                sn = int(s.get('season_number') or 0)
                if sn > latest_season_number:
                    latest_season_number = sn
        except Exception:
            latest_season_number = 0
        if latest_season_number <= 0:
            try:
                latest_season_number = int((existing_show or {}).get('latest_season_number') or 1)
            except Exception:
                latest_season_number = 1

        # 海报 - 使用海报语言设置获取海报路径
        poster_path = tmdb_service.get_poster_path_with_language(int(tmdb_id)) if tmdb_service else (details.get('poster_path') or '')
        poster_local_path = ''
        try:
            if poster_path:
                poster_local_path = download_poster_local(poster_path)
            elif existing_show and existing_show.get('poster_local_path'):
                poster_local_path = existing_show.get('poster_local_path')
        except Exception:
            poster_local_path = (existing_show or {}).get('poster_local_path') or ''

        bound_task_names = (existing_show or {}).get('bound_task_names', '')
        content_type = (existing_show or {}).get('content_type', '')

        cal_db.upsert_show(
            int(tmdb_id),
            name,
            first_air,
            localized_status,
            poster_local_path,
            int(latest_season_number),
            0,
            bound_task_names,
            content_type
        )

        try:
            notify_calendar_changed('refresh_show')
            # 通知前端指定节目海报可能已更新，用于触发按节目维度的缓存穿透
            try:
                if tmdb_id:
                    notify_calendar_changed(f'poster_updated:{int(tmdb_id)}')
            except Exception:
                pass
        except Exception:
            pass

        return jsonify({"success": True, "message": "剧目详情已刷新"})
    except Exception as e:
        return jsonify({"success": False, "message": f"刷新剧失败: {str(e)}"})

# 编辑追剧日历元数据：修改任务名、任务类型、重绑 TMDB
@app.route("/api/calendar/edit_metadata", methods=["POST"])
def calendar_edit_metadata():
    try:
        if not is_login():
            return jsonify({"success": False, "message": "未登录"})

        data = request.get_json(force=True) or {}
        task_name = (data.get('task_name') or '').strip()
        new_task_name = (data.get('new_task_name') or '').strip()
        new_content_type = (data.get('new_content_type') or '').strip()
        new_tmdb_id = data.get('new_tmdb_id')
        new_season_number = data.get('new_season_number')
        custom_poster_url = (data.get('custom_poster_url') or '').strip()

        if not task_name:
            return jsonify({"success": False, "message": "缺少任务名称"})

        global config_data
        tasks = config_data.get('tasklist', [])
        target = None
        for t in tasks:
            tn = t.get('taskname') or t.get('task_name') or ''
            if tn == task_name:
                target = t
                break
        if not target:
            return jsonify({"success": False, "message": "未找到任务"})

        cal = (target.get('calendar_info') or {})
        match = (cal.get('match') or {})
        old_tmdb_id = match.get('tmdb_id') or cal.get('tmdb_id')

        changed = False

        if new_task_name and new_task_name != task_name:
            target['taskname'] = new_task_name
            target['task_name'] = new_task_name
            changed = True

        valid_types = {'tv', 'anime', 'variety', 'documentary', 'other', ''}
        if new_content_type in valid_types:
            extracted = (target.setdefault('calendar_info', {}).setdefault('extracted', {}))
            if extracted.get('content_type') != new_content_type:
                # 同步到任务配置中的 extracted 与顶层 content_type，保证前端与其他逻辑可见
                extracted['content_type'] = new_content_type
                target['content_type'] = new_content_type
                # 未匹配任务：没有 old_tmdb_id 时，不访问数据库，仅更新配置
                # 已匹配任务：若已有绑定的 tmdb_id，则立即同步到数据库
                try:
                    if old_tmdb_id:
                        cal_db = CalendarDB()
                        cal_db.update_show_content_type(int(old_tmdb_id), new_content_type)
                        # 同步任务名与内容类型绑定关系
                        task_final_name = target.get('taskname') or target.get('task_name') or task_name
                        cal_db.bind_task_and_content_type(int(old_tmdb_id), task_final_name, new_content_type)
                except Exception as e:
                    logging.warning(f"同步内容类型到数据库失败: {e}")
                changed = True

        did_rematch = False
        cal_db = CalendarDB()
        tmdb_api_key = config_data.get('tmdb_api_key', '')
        poster_language = get_poster_language_setting()
        tmdb_service = TMDBService(tmdb_api_key, poster_language) if tmdb_api_key else None
        # 场景一：提供 new_tmdb_id（重绑节目，可同时指定季数）
        if new_tmdb_id:
            try:
                new_tid = int(str(new_tmdb_id).strip())
            except Exception:
                return jsonify({"success": False, "message": "TMDB ID 非法"})
            
            # 特殊处理：tmdb_id 为 0 表示取消匹配
            if new_tid == 0:
                if old_tmdb_id:
                    # 解绑旧节目的任务引用
                    try:
                        try:
                            _task_final_name = target.get('taskname') or target.get('task_name') or new_task_name or task_name
                        except Exception:
                            _task_final_name = task_name
                        cal_db.unbind_task_from_show(int(old_tmdb_id), _task_final_name)
                    except Exception as e:
                        logging.warning(f"解绑旧节目任务失败: {e}")
                    
                    # 清理旧节目的所有数据与海报（强制清理，因为我们已经解绑了任务）
                    try:
                        purge_calendar_by_tmdb_id_internal(int(old_tmdb_id), force=True)
                    except Exception as e:
                        logging.warning(f"清理旧节目数据失败: {e}")
                
                # 清空任务配置中的匹配信息
                if 'calendar_info' not in target:
                    target['calendar_info'] = {}
                if 'match' in target['calendar_info']:
                    target['calendar_info']['match'] = {}
                
                changed = True
                msg = '已取消匹配，并清除了原有的节目数据'
                return jsonify({"success": True, "message": msg})
            
            try:
                season_no = int(new_season_number or 1)
            except Exception:
                season_no = 1

            # 若 tmdb 发生变更，先解绑旧节目的任务引用；实际清理延后到配置写盘之后
            old_to_purge_tmdb_id = None
            if old_tmdb_id and int(old_tmdb_id) != new_tid:
                # 解绑旧节目的任务引用
                try:
                    try:
                        _task_final_name = target.get('taskname') or target.get('task_name') or new_task_name or task_name
                    except Exception:
                        _task_final_name = task_name
                    cal_db.unbind_task_from_show(int(old_tmdb_id), _task_final_name)
                except Exception as e:
                    logging.warning(f"解绑旧节目任务失败: {e}")
                # 记录待清理的旧 tmdb，在配置更新并落盘后再执行清理
                try:
                    old_to_purge_tmdb_id = int(old_tmdb_id)
                except Exception:
                    old_to_purge_tmdb_id = None

            show = cal_db.get_show(new_tid)
            if not show:
                if not tmdb_service:
                    return jsonify({"success": False, "message": "TMDB API 未配置，无法初始化新节目"})
                # 直接从 TMDB 获取详情并写入本地，而不是调用全量 bootstrap
                details = tmdb_service.get_tv_show_details(new_tid) or {}
                if not details:
                    return jsonify({"success": False, "message": "未找到指定 TMDB 节目"})
                try:
                    chinese_title = tmdb_service.get_chinese_title_with_fallback(new_tid, details.get('name') or details.get('original_name') or '')
                except Exception:
                    chinese_title = details.get('name') or details.get('original_name') or ''
                poster_local_path = ''
                try:
                    poster_local_path = download_poster_local(details.get('poster_path') or '')
                except Exception:
                    poster_local_path = ''
                first_air = (details.get('first_air_date') or '')[:4]
                status = details.get('status') or ''
                # 以 season_no 作为最新季写入 shows
                cal_db.upsert_show(int(new_tid), chinese_title, first_air, status, poster_local_path, int(season_no), 0, '', (target.get('content_type') or ''))
                show = cal_db.get_show(new_tid)
                if not show:
                    return jsonify({"success": False, "message": "未找到指定 TMDB 节目"})

            task_final_name = target.get('taskname') or target.get('task_name') or new_task_name or task_name
            ct = (target.get('calendar_info') or {}).get('extracted', {}).get('content_type', '')
            try:
                cal_db.bind_task_and_content_type(new_tid, task_final_name, ct)
            except Exception as e:
                logging.warning(f"绑定任务失败: {e}")

            if 'calendar_info' not in target:
                target['calendar_info'] = {}
            if 'match' not in target['calendar_info']:
                target['calendar_info']['match'] = {}
            target['calendar_info']['match'].update({
                'tmdb_id': new_tid,
                'matched_show_name': show.get('name', ''),
                'matched_year': show.get('year', '')
            })
            target['calendar_info']['match']['latest_season_number'] = season_no

            try:
                season = tmdb_service.get_tv_show_episodes(new_tid, season_no) if tmdb_service else None
                eps = (season or {}).get('episodes', []) or []
                from time import time as _now
                now_ts = int(_now())
                # 清理除当前季外的其他季数据，避免残留
                try:
                    cal_db.purge_other_seasons(int(new_tid), int(season_no))
                except Exception:
                    pass
                for ep in eps:
                    cal_db.upsert_episode(
                        tmdb_id=int(new_tid),
                        season_number=int(season_no),
                        episode_number=int(ep.get('episode_number') or 0),
                        name=ep.get('name') or '',
                        overview=ep.get('overview') or '',
                        air_date=ep.get('air_date') or '',
                        runtime=ep.get('runtime'),
                        ep_type=(ep.get('episode_type') or ep.get('type')),
                        updated_at=now_ts,
                    )
                try:
                    sname_raw = (season or {}).get('name') or ''
                    sname = tmdb_service.process_season_name(sname_raw) if tmdb_service else sname_raw
                    cal_db.upsert_season(int(new_tid), int(season_no), len(eps), f"/tv/{new_tid}/season/{season_no}", sname)
                    cal_db.update_show_latest_season_number(int(new_tid), int(season_no))
                except Exception:
                    pass
            except Exception as e:
                logging.warning(f"刷新新季失败: {e}")

            did_rematch = True
            changed = True
            # 如果需要，延后清理旧节目的数据（此时任务配置已指向新节目，避免被清理函数误判仍被引用）
            if old_to_purge_tmdb_id is not None:
                try:
                    purge_calendar_by_tmdb_id_internal(int(old_to_purge_tmdb_id))
                except Exception as e:
                    logging.warning(f"清理旧 tmdb 失败: {e}")
        # 场景二：未提供 new_tmdb_id，但提供了 new_season_number（仅修改季数）
        elif (new_season_number is not None) and (str(new_season_number).strip() != '') and old_tmdb_id:
            try:
                season_no = int(new_season_number)
            except Exception:
                return jsonify({"success": False, "message": "季数必须为数字"})

            # 检查是否与当前匹配的季数相同，如果相同则跳过处理
            current_match_season = match.get('latest_season_number')
            if current_match_season and int(current_match_season) == season_no:
                # 季数未变化，跳过处理
                pass
            else:
                if not tmdb_service:
                    return jsonify({"success": False, "message": "TMDB API 未配置"})

                # 更新 shows 表中的最新季，清理其他季，并拉取指定季数据
                try:
                    cal_db.update_show_latest_season_number(int(old_tmdb_id), int(season_no))
                except Exception:
                    pass

                # 拉取该季数据
                try:
                    season = tmdb_service.get_tv_show_episodes(int(old_tmdb_id), int(season_no)) or {}
                    eps = season.get('episodes', []) or []
                    from time import time as _now
                    now_ts = int(_now())
                    # 清理除当前季外其他季，避免残留
                    try:
                        cal_db.purge_other_seasons(int(old_tmdb_id), int(season_no))
                    except Exception:
                        pass
                    for ep in eps:
                        cal_db.upsert_episode(
                            tmdb_id=int(old_tmdb_id),
                            season_number=int(season_no),
                            episode_number=int(ep.get('episode_number') or 0),
                            name=ep.get('name') or '',
                            overview=ep.get('overview') or '',
                            air_date=ep.get('air_date') or '',
                            runtime=ep.get('runtime'),
                            ep_type=(ep.get('episode_type') or ep.get('type')),
                            updated_at=now_ts,
                        )
                    try:
                        sname_raw = (season or {}).get('name') or ''
                        sname = tmdb_service.process_season_name(sname_raw)
                        cal_db.upsert_season(int(old_tmdb_id), int(season_no), len(eps), f"/tv/{old_tmdb_id}/season/{season_no}", sname)
                    except Exception:
                        pass
                except Exception as e:
                    return jsonify({"success": False, "message": f"刷新季数据失败: {e}"})

                # 更新任务配置中的 latest_season_number 以便前端展示
                try:
                    if 'calendar_info' not in target:
                        target['calendar_info'] = {}
                    if 'match' not in target['calendar_info']:
                        target['calendar_info']['match'] = {}
                    target['calendar_info']['match']['latest_season_number'] = int(season_no)
                except Exception:
                    pass

                changed = True

        # 处理自定义海报
        if custom_poster_url:
            # 获取当前任务的TMDB ID
            current_tmdb_id = None
            if new_tmdb_id:
                current_tmdb_id = int(new_tmdb_id)
            elif old_tmdb_id:
                current_tmdb_id = int(old_tmdb_id)

            if current_tmdb_id:
                try:
                    # 尝试读取现有节目，若有已有海报，则覆盖同名文件以实现原位替换
                    show_row = None
                    try:
                        show_row = cal_db.get_show(int(current_tmdb_id))
                    except Exception:
                        show_row = None

                    target_safe_name = None
                    if show_row and (show_row.get('poster_local_path') or '').strip():
                        try:
                            existing_path = (show_row.get('poster_local_path') or '').strip()
                            # 期望格式：/cache/images/<filename>
                            target_safe_name = existing_path.split('/')[-1]
                        except Exception:
                            target_safe_name = None

                    # 覆盖保存（不进行缩放，不新增依赖）
                    saved_path = download_custom_poster(custom_poster_url, current_tmdb_id, target_safe_name)

                    if saved_path:
                        # 若文件名发生变化则需要同步数据库并清理旧文件
                        try:
                            existing_path = ''
                            if show_row:
                                existing_path = (show_row.get('poster_local_path') or '').strip()
                            if saved_path != existing_path:
                                # 删除旧文件（若存在且在缓存目录下）
                                try:
                                    if existing_path and existing_path.startswith('/cache/images/'):
                                        old_name = existing_path.replace('/cache/images/', '')
                                        old_path = os.path.join(CACHE_IMAGES_DIR, old_name)
                                        if os.path.exists(old_path):
                                            os.remove(old_path)
                                except Exception as e:
                                    logging.warning(f"删除旧海报失败: {e}")
                                # 更新数据库路径
                                try:
                                    cal_db.update_show_poster(int(current_tmdb_id), saved_path)
                                except Exception as e:
                                    logging.warning(f"更新海报路径失败: {e}")
                        except Exception:
                            # 即使对比失败也不影响功能
                            pass

                        # 成功更新自定义海报（静默）
                        changed = True
                        # 仅当自定义海报保存成功时，通知前端该节目海报已更新
                        try:
                            notify_calendar_changed(f'poster_updated:{int(current_tmdb_id)}')
                        except Exception:
                            pass
                    else:
                        logging.warning(f"自定义海报保存失败: {custom_poster_url}")
                except Exception as e:
                    logging.error(f"处理自定义海报失败: {e}")
            else:
                logging.warning("无法处理自定义海报：缺少TMDB ID")

        if changed:
            Config.write_json(CONFIG_PATH, config_data)

        try:
            sync_task_config_with_database_bindings()
        except Exception as e:
            logging.warning(f"同步绑定失败: {e}")

        try:
            notify_calendar_changed('edit_metadata')
        except Exception:
            pass

        msg = '元数据更新成功'
        if did_rematch:
            msg = '元数据更新成功，已重新匹配并刷新元数据'
        if custom_poster_url:
            if '已重新匹配' in msg:
                msg = '元数据更新成功，已重新匹配并刷新元数据，自定义海报已更新'
            else:
                msg = '元数据更新成功，自定义海报已更新'
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "message": f"保存失败: {str(e)}"})

# 获取节目信息（用于获取最新季数）
@app.route("/api/calendar/show_info")
def get_calendar_show_info():
    try:
        if not is_login():
            return jsonify({"success": False, "message": "未登录"})

        tmdb_id = request.args.get('tmdb_id', type=int)
        if not tmdb_id:
            return jsonify({"success": False, "message": "缺少 tmdb_id"})

        cal_db = CalendarDB()
        show = cal_db.get_show(int(tmdb_id))
        if not show:
            return jsonify({"success": False, "message": "未找到该节目"})

        return jsonify({
            "success": True,
            "data": {
                "tmdb_id": show['tmdb_id'],
                "name": show['name'],
                "year": show['year'],
                "status": show['status'],
                "latest_season_number": show['latest_season_number'],
                "poster_local_path": show['poster_local_path']
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": f"获取节目信息失败: {str(e)}"})


# --------- 日历刷新调度：按性能设置的周期自动刷新最新季 ---------
def restart_calendar_refresh_job():
    try:
        # 读取用户配置的刷新周期（秒），默认21600秒（6小时）
        perf = config_data.get('performance', {}) if isinstance(config_data, dict) else {}
        interval_seconds = int(perf.get('calendar_refresh_interval_seconds', 21600))
        if interval_seconds <= 0:
            # 非法或关闭则移除
            try:
                scheduler.remove_job('calendar_refresh_job')
            except Exception:
                pass
            logging.info("已关闭追剧日历元数据自动刷新")
            return

        # 重置任务
        try:
            scheduler.remove_job('calendar_refresh_job')
        except Exception:
            pass
        scheduler.add_job(run_calendar_refresh_all_internal, IntervalTrigger(seconds=interval_seconds), id='calendar_refresh_job', replace_existing=True)
        if scheduler.state == 0:
            scheduler.start()
        logging.info(f"已启动追剧日历自动刷新，周期 {interval_seconds} 秒")
    except Exception as e:
        logging.warning(f"配置自动刷新任务失败: {e}")


def run_calendar_refresh_all_internal():
    try:
        tmdb_api_key = config_data.get('tmdb_api_key', '')
        if not tmdb_api_key:
            return
        poster_language = get_poster_language_setting()
        tmdb_service = TMDBService(tmdb_api_key, poster_language)
        db = CalendarDB()
        shows = []
        # 简单读取所有已初始化的剧目
        try:
            cur = db.conn.cursor()
            cur.execute('SELECT tmdb_id FROM shows')
            shows = [r[0] for r in cur.fetchall()]
        except Exception:
            shows = []
        any_written = False
        for tmdb_id in shows:
            try:
                # 直接重用内部逻辑
                with app.app_context():
                    # 调用与 endpoint 相同的刷新流程
                    season = tmdb_service.get_tv_show_episodes(int(tmdb_id), int(db.get_show(int(tmdb_id))['latest_season_number'])) or {}
                    episodes = season.get('episodes', []) or []
                    from time import time as _now
                    now_ts = int(_now())
                    for ep in episodes:
                        db.upsert_episode(
                            tmdb_id=int(tmdb_id),
                            season_number=int(db.get_show(int(tmdb_id))['latest_season_number']),
                            episode_number=int(ep.get('episode_number') or 0),
                            name=ep.get('name') or '',
                            overview=ep.get('overview') or '',
                            air_date=ep.get('air_date') or '',
                            runtime=ep.get('runtime'),
                            ep_type=(ep.get('episode_type') or ep.get('type')),
                            updated_at=now_ts,
                        )
                        any_written = True
            except Exception as e:
                logging.warning(f"自动刷新失败 tmdb_id={tmdb_id}: {e}")
        try:
            if any_written:
                notify_calendar_changed('auto_refresh')
        except Exception:
            pass
    except Exception as e:
        logging.warning(f"自动刷新任务异常: {e}")
# 本地缓存读取：获取最新一季的本地剧集数据
@app.route("/api/calendar/episodes_local")
def get_calendar_episodes_local():
    try:
        tmdb_id = request.args.get('tmdb_id')
        db = CalendarDB()
        # 建立 tmdb_id -> 任务信息 映射，用于前端筛选（任务名/类型）
        tmdb_to_taskinfo = {}
        try:
            tasks = config_data.get('tasklist', [])
            for t in tasks:
                cal = (t.get('calendar_info') or {})
                match = (cal.get('match') or {})
                extracted = (cal.get('extracted') or {})
                tid = match.get('tmdb_id')
                if tid:
                    tmdb_to_taskinfo[int(tid)] = {
                        'task_name': t.get('taskname') or t.get('task_name') or '',
                        'content_type': extracted.get('content_type') or extracted.get('type') or 'other',
                        'progress': {}
                    }
            # 若未能建立完整映射，尝试基于 shows 表名称与任务 extracted.show_name 做一次兜底绑定
            if not tmdb_to_taskinfo:
                try:
                    cur = db.conn.cursor()
                    cur.execute('SELECT tmdb_id, name FROM shows')
                    rows = cur.fetchall() or []
                    for tid, name in rows:
                        name = name or ''
                        # 找到名称一致的任务
                        tgt = next((t for t in tasks if ((t.get('calendar_info') or {}).get('extracted') or {}).get('show_name') == name), None)
                        if tgt:
                            extracted = ((tgt.get('calendar_info') or {}).get('extracted') or {})
                            tmdb_to_taskinfo[int(tid)] = {
                                'task_name': tgt.get('taskname') or tgt.get('task_name') or '',
                                'content_type': extracted.get('content_type') or extracted.get('type') or 'other',
                                'progress': {}
                            }
                except Exception:
                    pass
        except Exception:
            tmdb_to_taskinfo = {}
        # 读取任务最新转存文件，构建 task_name -> 解析进度 映射
        progress_by_task = {}
        try:
            # 直接读取数据库，避免依赖登录校验的接口调用
            extractor = TaskExtractor()
            rdb = RecordDB()
            cursor = rdb.conn.cursor()
            cursor.execute("""
                SELECT task_name, MAX(transfer_time) as latest_transfer_time
                FROM transfer_records
                WHERE task_name NOT IN ('rename', 'undo_rename')
                GROUP BY task_name
            """)
            latest_times = cursor.fetchall() or []
            latest_files = {}
            for task_name, latest_time in latest_times:
                if latest_time:
                    time_window = 60000
                    cursor.execute(
                        """
                        SELECT renamed_to, original_name, transfer_time, modify_date
                        FROM transfer_records
                        WHERE task_name = ? AND transfer_time >= ? AND transfer_time <= ?
                        ORDER BY id DESC
                        """,
                        (task_name, latest_time - time_window, latest_time + time_window)
                    )
                    files = cursor.fetchall() or []
                    best_file = None
                    if files:
                        if len(files) == 1:
                            best_file = files[0][0]
                        else:
                            file_list = []
                            for renamed_to, original_name, transfer_time, modify_date in files:
                                file_list.append({'file_name': renamed_to, 'original_name': original_name, 'updated_at': transfer_time})
                            try:
                                sorted_files = sorted(file_list, key=sort_file_by_name)
                                best_file = sorted_files[-1]['file_name']
                            except Exception:
                                best_file = files[0][0]
                    if best_file:
                        file_name_without_ext = os.path.splitext(best_file)[0]
                        processed_name = process_season_episode_info(file_name_without_ext, task_name)
                        latest_files[task_name] = processed_name
            rdb.close()
            for tname, latest in (latest_files or {}).items():
                parsed = extractor.extract_progress_from_latest_file(latest)
                if parsed and (parsed.get('episode_number') or parsed.get('air_date')):
                    progress_by_task[tname] = parsed
        except Exception:
            progress_by_task = {}

        if tmdb_id:
            show = db.get_show(int(tmdb_id))
            if not show:
                return jsonify({'success': True, 'data': {'episodes': [], 'total': 0}})
            eps = db.list_latest_season_episodes(int(tmdb_id), int(show['latest_season_number']))
            # 注入任务信息与标准化进度
            info = tmdb_to_taskinfo.get(int(tmdb_id))
            if info:
                for e in eps:
                    e['task_info'] = info
                    # 合并标准化进度
                    tname = info.get('task_name') or ''
                    p = progress_by_task.get(tname)
                    if p:
                        e['task_info']['progress'] = p
            return jsonify({'success': True, 'data': {'episodes': eps, 'total': len(eps), 'show': show}})
        else:
            # 返回全部最新季汇总（供周视图合并展示）
            eps = db.list_all_latest_episodes()
            for e in eps:
                info = tmdb_to_taskinfo.get(int(e.get('tmdb_id')))
                if info:
                    e['task_info'] = info
                    tname = info.get('task_name') or ''
                    p = progress_by_task.get(tname)
                    if p:
                        e['task_info']['progress'] = p
            return jsonify({'success': True, 'data': {'episodes': eps, 'total': len(eps)}})
    except Exception as e:
        return jsonify({'success': False, 'message': f'读取本地剧集失败: {str(e)}', 'data': {'episodes': [], 'total': 0}})


# 清理缓存：按 tmdb_id 删除对应剧目的所有本地缓存
@app.route("/api/calendar/purge_tmdb", methods=["POST"])
def purge_calendar_tmdb():
    try:
        tmdb_id = request.args.get('tmdb_id') or (request.json or {}).get('tmdb_id')
        if not tmdb_id:
            return jsonify({'success': False, 'message': '缺少 tmdb_id'})
        force = False
        try:
            fv = request.args.get('force') or (request.json or {}).get('force')
            force = True if str(fv).lower() in ('1', 'true', 'yes') else False
        except Exception:
            force = False
        ok = purge_calendar_by_tmdb_id_internal(int(tmdb_id), force=force)
        try:
            if ok:
                notify_calendar_changed('purge_tmdb')
        except Exception:
            pass
        return jsonify({'success': ok})
    except Exception as e:
        return jsonify({'success': False, 'message': f'清理失败: {str(e)}'})


# 清理缓存：按任务名查找 config 中的 tmdb_id 并清理
@app.route("/api/calendar/purge_by_task", methods=["POST"])
def purge_calendar_by_task():
    try:
        task_name = request.args.get('task_name') or (request.json or {}).get('task_name')
        if not task_name:
            return jsonify({'success': False, 'message': '缺少 task_name'})
        force = False
        try:
            fv = request.args.get('force') or (request.json or {}).get('force')
            force = True if str(fv).lower() in ('1', 'true', 'yes') else False
        except Exception:
            force = False
        # 在配置中查找对应任务
        tasks = config_data.get('tasklist', [])
        target = next((t for t in tasks if t.get('taskname') == task_name or t.get('task_name') == task_name), None)
        if not target:
            # 若任务不存在，但用户希望强制清理：尝试按 tmdb_id 反查无引用 show 并清理（更安全）
            if force:
                # 扫描 shows 表，删除未被任何任务引用的记录（孤儿）
                purged = purge_orphan_calendar_shows_internal()
                try:
                    if purged > 0:
                        notify_calendar_changed('purge_orphans')
                except Exception:
                    pass
                return jsonify({'success': True, 'message': f'任务不存在，已强制清理孤儿记录 {purged} 条'})
            return jsonify({'success': True, 'message': '任务不存在，视为已清理'})
        cal = (target or {}).get('calendar_info') or {}
        tmdb_id = cal.get('match', {}).get('tmdb_id') or cal.get('tmdb_id')
        if not tmdb_id:
            return jsonify({'success': True, 'message': '任务未绑定 tmdb_id，无需清理'})
        ok = purge_calendar_by_tmdb_id_internal(int(tmdb_id), force=force)
        try:
            if ok:
                notify_calendar_changed('purge_by_task')
        except Exception:
            pass
        return jsonify({'success': ok})
    except Exception as e:
        return jsonify({'success': False, 'message': f'清理失败: {str(e)}'})
# 豆瓣API路由

# 通用电影接口
@app.route("/api/douban/movie/recent_hot")
def get_movie_recent_hot():
    """获取电影榜单 - 通用接口"""
    try:
        category = request.args.get('category', '热门')
        type_param = request.args.get('type', '全部')
        limit = int(request.args.get('limit', 20))
        start = int(request.args.get('start', 0))

        # 映射category到main_category
        category_mapping = {
            '热门': 'movie_hot',
            '最新': 'movie_latest',
            '豆瓣高分': 'movie_top',
            '冷门佳片': 'movie_underrated'
        }

        main_category = category_mapping.get(category, 'movie_hot')
        result = douban_service.get_list_data(main_category, type_param, limit, start)

        return jsonify(result)
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取电影榜单失败: {str(e)}',
            'data': {'items': []}
        })

@app.route("/api/douban/movie/<movie_type>/<sub_category>")
def get_movie_list(movie_type, sub_category):
    """获取电影榜单"""
    try:
        limit = int(request.args.get('limit', 20))
        start = int(request.args.get('start', 0))

        main_category = f"movie_{movie_type}"
        result = douban_service.get_list_data(main_category, sub_category, limit, start)

        return jsonify(result)
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取电影榜单失败: {str(e)}',
            'data': {'items': []}
        })


# 通用电视剧接口
@app.route("/api/douban/tv/recent_hot")
def get_tv_recent_hot():
    """获取电视剧榜单 - 通用接口"""
    try:
        category = request.args.get('category', 'tv')
        type_param = request.args.get('type', 'tv')
        limit = int(request.args.get('limit', 20))
        start = int(request.args.get('start', 0))

        # 映射category到main_category
        if category == 'tv':
            main_category = 'tv_drama'
        elif category == 'show':
            main_category = 'tv_variety'
        elif category == 'tv_drama':
            main_category = 'tv_drama'
        elif category == 'tv_variety':
            main_category = 'tv_variety'
        else:
            main_category = 'tv_drama'

        result = douban_service.get_list_data(main_category, type_param, limit, start)

        return jsonify(result)
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取电视剧榜单失败: {str(e)}',
            'data': {'items': []}
        })

@app.route("/api/douban/tv/<tv_type>/<sub_category>")
def get_tv_list(tv_type, sub_category):
    """获取电视剧/综艺榜单"""
    try:
        limit = int(request.args.get('limit', 20))
        start = int(request.args.get('start', 0))

        main_category = f"tv_{tv_type}"
        result = douban_service.get_list_data(main_category, sub_category, limit, start)

        return jsonify(result)
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取电视剧榜单失败: {str(e)}',
            'data': {'items': []}
        })

@app.route("/api/calendar/update_content_type", methods=["POST"])
def update_show_content_type():
    """更新节目的内容类型"""
    try:
        data = request.get_json()
        tmdb_id = data.get('tmdb_id')
        content_type = data.get('content_type')
        
        if not tmdb_id or not content_type:
            return jsonify({
                'success': False,
                'message': '缺少必要参数：tmdb_id 或 content_type'
            })
        
        from app.sdk.db import CalendarDB
        cal_db = CalendarDB()
        
        # 更新内容类型
        success = cal_db.update_show_content_type(int(tmdb_id), content_type)
        
        if success:
            return jsonify({
                'success': True,
                'message': '内容类型更新成功'
            })
        else:
            return jsonify({
                'success': False,
                'message': '节目不存在或更新失败'
            })
            
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'更新内容类型失败: {str(e)}'
        })

@app.route("/api/calendar/content_types")
def get_content_types():
    """获取所有节目内容类型"""
    try:
        from app.sdk.db import CalendarDB
        cal_db = CalendarDB()
        content_types = cal_db.get_all_content_types()
        return jsonify({
            'success': True,
            'data': content_types
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'获取节目内容类型失败: {str(e)}'
        })

if __name__ == "__main__":
    init()
    reload_tasks()
    # 初始化全局db对象，确保所有接口可用
    record_db = RecordDB()
    app.run(debug=DEBUG, host="0.0.0.0", port=PORT)
