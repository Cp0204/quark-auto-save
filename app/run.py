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
from apscheduler.triggers.date import DateTrigger
from queue import Queue
from collections import deque
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
from logging.handlers import RotatingFileHandler
import base64
import sys
import os
import re
import random
import time
import treelib
from functools import lru_cache
from threading import Lock, Thread

parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, parent_dir)
from quark_auto_save import Quark
from quark_auto_save import Config, format_bytes

# 添加导入全局extract_episode_number和sort_file_by_name函数
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quark_auto_save import extract_episode_number, sort_file_by_name, chinese_to_arabic, is_date_format, apply_subtitle_naming_rule

# 导入豆瓣服务
from sdk.douban_service import douban_service

# 导入追剧日历相关模块
from utils.task_extractor import TaskExtractor
from sdk.tmdb_service import TMDBService
from sdk.trakt_service import TraktService
from sdk.db import CalendarDB
from sdk.db import RecordDB
from utils.task_extractor import TaskExtractor

def _get_local_timezone() -> str:
    """
    获取本地时区配置，用于 Trakt 播出时间转换与本地时间判断。
    默认为 Asia/Shanghai（北京时间），保证向后兼容。
    """
    try:
        if isinstance(config_data, dict):
            tz = config_data.get("local_timezone") or "Asia/Shanghai"
            return str(tz) or "Asia/Shanghai"
    except Exception:
        pass
    return "Asia/Shanghai"

def is_episode_aired(tmdb_id: int, season_number: int, episode_number: int, now_local_dt) -> bool:
    """
    单集是否已播出判定：
    1. 始终使用 air_date_local（已考虑 air_date_offset）作为日期判断
    2. 如果 air_date_local 不存在，说明播出时间未知，返回 False
    3. 始终使用 local_air_time 作为时间判断，如果不存在则使用播出集数刷新时间
    """
    try:
        tmdb_id = int(tmdb_id)
        season_number = int(season_number)
        episode_number = int(episode_number)
    except Exception:
        return False

    # 从数据库获取集的播出时间信息
    try:
        cal_db = CalendarDB()
        cur = cal_db.conn.cursor()
        cur.execute(
            """
            SELECT air_date_local
            FROM episodes
            WHERE tmdb_id=? AND season_number=? AND episode_number=?
            """,
            (tmdb_id, season_number, episode_number),
        )
        row = cur.fetchone()
        if not row:
            return False
        air_date_local = row[0]
    except Exception:
        return False

    # 如果 air_date_local 不存在，说明播出时间未知，返回 False
    if not air_date_local:
        return False

    # 获取节目级播出时间配置
    schedule = {}
    try:
        schedule = CalendarDB().get_show_air_schedule(tmdb_id) or {}
    except Exception:
        schedule = {}
    
    # 优先使用 local_air_time，如果不存在（为空或 None）则使用全局播出集数刷新时间
    local_air_time = (schedule.get("local_air_time") or "").strip() or None
    perf = config_data.get("performance", {}) if isinstance(config_data, dict) else {}
    refresh_time_str = perf.get("aired_refresh_time", "00:00")
    
    # 始终使用 local_air_time（如果存在），否则使用播出集数刷新时间
    time_to_use = local_air_time if local_air_time else refresh_time_str
    
    # 解析时间（HH:MM）
    hh, mm = 0, 0
    try:
        parts = str(time_to_use).split(":")
        if len(parts) == 2:
            hh = int(parts[0])
            mm = int(parts[1])
    except Exception:
        hh, mm = 0, 0

    # 组合日期和时间
    try:
        dt_str = f"{air_date_local} {hh:02d}:{mm:02d}:00"
        dt_ep_local = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
        # 如果 now_local_dt 有时区信息，为 dt_ep_local 也添加相同的时区信息
        if getattr(now_local_dt, "tzinfo", None) is not None:
            dt_ep_local = dt_ep_local.replace(tzinfo=now_local_dt.tzinfo)
        result = dt_ep_local.timestamp() <= now_local_dt.timestamp()
        return result
    except Exception as _e:
        logging.debug(f"is_episode_aired 日期+时间判定异常: tmdb_id={tmdb_id}, season={season_number}, ep={episode_number}, error={_e}")
        import traceback
        logging.debug(f"异常堆栈: {traceback.format_exc()}")
        return False

def compute_aired_count_by_episode_check(tmdb_id: int, season_number: int, now_local_dt=None) -> int:
    """
    通过逐集调用 is_episode_aired 计算已播出集数（考虑播出时间）
    这是正确的计算方式，应该替代所有简单的日期比较逻辑
    """
    try:
        from zoneinfo import ZoneInfo as _ZoneInfo
        local_tz = _ZoneInfo(_get_local_timezone())
        if now_local_dt is None:
            now_local_dt = datetime.now(local_tz)
    except Exception:
        if now_local_dt is None:
            now_local_dt = datetime.now()
    
    try:
        cal_db = CalendarDB()
        cur = cal_db.conn.cursor()
        cur.execute(
            """
            SELECT episode_number FROM episodes
            WHERE tmdb_id=? AND season_number=?
            """,
            (int(tmdb_id), int(season_number)),
        )
        aired_count = 0
        for (ep_no,) in cur.fetchall() or []:
            try:
                if is_episode_aired(int(tmdb_id), int(season_number), int(ep_no), now_local_dt):
                    aired_count += 1
            except Exception:
                continue
        return aired_count
    except Exception:
        return 0

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

        # 统计"已播出集数"：使用 is_episode_aired 逐集判断（考虑播出时间）
        # 修复：不再使用简单的日期比较，而是逐集调用 is_episode_aired 判断是否已播出
        from datetime import datetime as _dt
        try:
            from zoneinfo import ZoneInfo as _ZoneInfo
            local_tz = _ZoneInfo(_get_local_timezone())
            now_local_dt = datetime.now(local_tz)
        except Exception:
            now_local_dt = datetime.now()
        
        aired_by_show_season = {}
        try:
            # 获取所有有剧集的节目和季
            cur.execute(
                """
                SELECT DISTINCT tmdb_id, season_number FROM episodes
                WHERE tmdb_id IS NOT NULL AND season_number IS NOT NULL
                """
            )
            for tid, sn in (cur.fetchall() or []):
                try:
                    aired_count = compute_aired_count_by_episode_check(int(tid), int(sn), now_local_dt)
                    aired_by_show_season[(int(tid), int(sn))] = aired_count
                except Exception:
                    continue
        except Exception:
            aired_by_show_season = {}

        # 读取 season_metrics 缓存（优先使用缓存回填展示；若缓存日期早于今天，则强制使用今日即时统计覆盖已播出集数，并在下方写回）
        season_metrics_map = {}
        try:
            cur.execute('SELECT tmdb_id, season_number, transferred_count, aired_count, total_count, updated_at FROM season_metrics')
            for tid, sn, tc, ac, tc2, ua in (cur.fetchall() or []):
                season_metrics_map[(int(tid), int(sn))] = {
                    'transferred_count': 0 if tc is None else int(tc),
                    'aired_count': 0 if ac is None else int(ac),
                    'total_count': 0 if tc2 is None else int(tc2),
                    'updated_at': 0 if ua is None else int(ua),
                }
        except Exception:
            season_metrics_map = {}

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
                    # 仅使用任务匹配到的季号；无匹配则不计算该任务的季级数据
                    season_no_to_use = None
                    try:
                        if t.get('matched_latest_season_number') is not None:
                            v = int(t.get('matched_latest_season_number'))
                            if v > 0:
                                season_no_to_use = v
                    except Exception:
                        season_no_to_use = None
                    if season_no_to_use is not None:
                        latest_sn = season_no_to_use
                        sm = season_meta.get((int(tmdb_id), latest_sn)) or {}
                        latest_season_name = sm.get('season_name') or ''
                        # 优先用 season_metrics 中缓存的 total/air/transferred（按匹配季）
                        _metrics = season_metrics_map.get((int(tmdb_id), latest_sn)) or {}
                        total_count = _metrics.get('total_count')
                        aired_count = _metrics.get('aired_count')
                        _cached_transferred = _metrics.get('transferred_count')
                        _updated_at = _metrics.get('updated_at')
                        # fallback：无缓存则用即时计算（按匹配季）
                        if total_count in (None, 0):
                            total_count = sm.get('episode_count')
                        # 一致性强化：在“未到刷新时间”时，始终使用按有效日期统计的值覆盖/校正缓存，
                        # 避免出现提前计入当日集数的展示偏差
                        try:
                            _aired_effective = aired_by_show_season.get((int(tmdb_id), latest_sn))
                            if _aired_effective is not None:
                                if not allow_refresh:
                                    # 刷新时间之前：无条件采用有效日期口径
                                    aired_count = _aired_effective
                                else:
                                    # 刷新时间之后：若缓存为非当日或缺失，则采用有效日期口径回填
                                    _ua_date = None
                                    if _updated_at:
                                        _ua_date = _dt.fromtimestamp(int(_updated_at)).strftime('%Y-%m-%d')
                                    if ((_ua_date is None) or (_ua_date != today)):
                                        aired_count = _aired_effective
                        except Exception:
                            pass
                        if aired_count in (None, 0):
                            aired_count = aired_by_show_season.get((int(tmdb_id), latest_sn))
                    else:
                        # 未匹配到季：不计算该任务的季级数据
                        latest_sn = None
                        latest_season_name = ''
                        total_count = 0
                        aired_count = 0
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

            # 后端兜底：当文件名无集序号但含日期时，尝试用 tmdb_id + season + air_date 推导已转存集数
            # 这样与前端展示口径一致，避免把 transferred_count 误写为 0
            try:
                _effective_transferred = int(transferred_count or 0)
            except Exception:
                _effective_transferred = 0

            # 保护：如果本次解析不到有效集数（_effective_transferred 为 0），
            # 且 metrics 中已经有缓存的 transferred_count > 0，则优先保留缓存值，
            # 避免实时刷新时短暂把已转存集数回落为 0。
            try:
                if (not _effective_transferred) and (locals().get('_cached_transferred') not in (None, 0)):
                    _effective_transferred = int(locals().get('_cached_transferred') or 0)
            except Exception:
                pass

            if (not _effective_transferred) and task_name and tmdb_id and latest_sn:
                try:
                    # 取最近一条转存记录的文件名，解析出日期
                    rdb2 = RecordDB()
                    cur2 = rdb2.conn.cursor()
                    cur2.execute(
                        """
                        SELECT MAX(transfer_time) as latest_transfer_time
                        FROM transfer_records
                        WHERE task_name = ? AND task_name NOT IN ('rename', 'undo_rename')
                        """,
                        (task_name,)
                    )
                    _row = cur2.fetchone()
                    _lt = _row[0] if _row else None
                    _best = None
                    if _lt:
                        cur2.execute(
                            """
                            SELECT renamed_to, original_name, transfer_time, modify_date
                            FROM transfer_records
                            WHERE task_name = ? AND transfer_time >= ? AND transfer_time <= ?
                            ORDER BY id DESC
                            """,
                            (task_name, _lt - 60000, _lt + 60000)
                        )
                        _files = cur2.fetchall() or []
                        if _files:
                            _best = _files[0][0]
                    rdb2.close()

                    if _best:
                        _name_wo_ext = os.path.splitext(_best)[0]
                        _processed = process_season_episode_info(_name_wo_ext, task_name)
                        _parsed = TaskExtractor().extract_progress_from_latest_file(_processed)
                        if _parsed and (not _parsed.get('episode_number')) and _parsed.get('air_date'):
                            _air_date = _parsed.get('air_date')
                            # 用 tmdb_id + season + air_date 查找当天对应的最大集号
                            cur.execute(
                                """
                                SELECT MAX(CAST(episode_number AS INTEGER))
                                FROM episodes
                                WHERE tmdb_id = ? AND season_number = ? AND air_date = ?
                                """,
                                (int(tmdb_id), int(latest_sn), _air_date)
                            )
                            _res = cur.fetchone()
                            if _res and _res[0] is not None:
                                _effective_transferred = int(_res[0])
                except Exception:
                    pass

            t['season_counts'] = {
                'transferred_count': _effective_transferred,
                'aired_count': aired_count or 0,
                'total_count': total_count or 0,
            }
            # 写回 season_metrics（以该任务自身匹配到的季号为键；无匹配则跳过）
            try:
                if tmdb_id and latest_sn:
                    from time import time as _now
                    # 计算进度百分比：以 min(aired, total) 为分母；分母<=0 则为 0
                    try:
                        _trans = int(_effective_transferred or 0)
                        _aired = int(aired_count or 0)
                        _total = int(total_count or 0)
                        denom = min(_aired, _total)
                        progress_pct = (100 * min(_trans, denom) // denom) if denom > 0 else 0
                    except Exception:
                        progress_pct = 0
                    CalendarDB().upsert_season_metrics(int(tmdb_id), int(latest_sn), int(_effective_transferred or 0), int(aired_count or 0), int(total_count or 0), int(progress_pct), int(_now()))
            except Exception:
                pass
            try:
                if task_name and tmdb_id and latest_sn:
                    from time import time as _now
                    # 同一套百分比口径
                    try:
                        _trans = int(_effective_transferred or 0)
                        _aired = int(aired_count or 0)
                        _total = int(total_count or 0)
                        denom = min(_aired, _total)
                        progress_pct = (100 * min(_trans, denom) // denom) if denom > 0 else 0
                    except Exception:
                        progress_pct = 0
                    CalendarDB().upsert_task_metrics(task_name, int(tmdb_id), int(latest_sn), int(_effective_transferred or 0), int(progress_pct), int(_now()))
            except Exception:
                pass
            
            # 补充最新季号给前端（用于下一集查找逻辑）
            if latest_sn:
                t['latest_season_number'] = int(latest_sn)
                
            enriched.append(t)
        return enriched
    except Exception:
        return tasks_info

# 内部：根据 task_name 重新计算转存进度，并写回 metrics 与推送变更
def recompute_task_metrics_and_notify(task_name: str) -> bool:
    try:
        if not task_name:
            return False
        rdb = RecordDB()
        cursor = rdb.conn.cursor()
        cursor.execute(
            """
            SELECT MAX(transfer_time) as latest_transfer_time
            FROM transfer_records
            WHERE task_name = ? AND task_name NOT IN ('rename', 'undo_rename')
            """,
            (task_name,)
        )
        row = cursor.fetchone()
        latest_time = row[0] if row else None
        latest_file = None
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
            if files:
                if len(files) == 1:
                    latest_file = files[0][0]
                else:
                    # 退化：按 id DESC 已近似最新，取第一条 renamed_to
                    latest_file = files[0][0]
        rdb.close()

        ep_no = None
        parsed_air_date = None
        if latest_file:
            try:
                extractor = TaskExtractor()
                parsed = extractor.extract_progress_from_latest_file((latest_file or '').split('.')[0])
                if parsed and parsed.get('episode_number') is not None:
                    ep_no = int(parsed.get('episode_number'))
                elif parsed and parsed.get('air_date'):
                    # 暂存日期，待解析出 tmdb_id 与 season 后据此推导集号
                    parsed_air_date = parsed.get('air_date')
            except Exception:
                ep_no = None

        cal_db = CalendarDB()
        cur = cal_db.conn.cursor()
        # 仅从任务配置解析 tmdb 与季号：强制要求 matched_latest_season_number 存在，否则视为未匹配
        tmdb_id = None
        season_no = None
        try:
            tasks = (config_data or {}).get('tasklist', [])
        except Exception:
            tasks = []
        try:
            tgt = next((t for t in tasks if (t.get('taskname') or t.get('task_name') or '') == task_name), None)
        except Exception:
            tgt = None
        if tgt:
            try:
                match = ((tgt.get('calendar_info') or {}).get('match') or {})
            except Exception:
                match = {}
            # tmdb_id 必须来自匹配信息
            try:
                if match.get('tmdb_id'):
                    tmdb_id = int(match.get('tmdb_id'))
            except Exception:
                tmdb_id = None
            # season 仅使用 matched_latest_season_number
            try:
                if tgt.get('matched_latest_season_number') is not None:
                    v = int(tgt.get('matched_latest_season_number'))
                    if v > 0:
                        season_no = v
            except Exception:
                season_no = None
        # 未匹配则跳过
        if tmdb_id is None or season_no is None or int(season_no) <= 0:
            return False

        from time import time as _now
        now_ts = int(_now())
        # 若仅有日期但无集号，尝试用 tmdb_id + season + air_date 推导集号
        if ep_no is None and parsed_air_date:
            try:
                cur.execute(
                    """
                    SELECT MAX(CAST(episode_number AS INTEGER))
                    FROM episodes
                    WHERE tmdb_id = ? AND season_number = ? AND air_date = ?
                    """,
                    (int(tmdb_id), int(season_no), parsed_air_date)
                )
                _row = cur.fetchone()
                if _row and _row[0] is not None:
                    ep_no = int(_row[0])
            except Exception:
                pass

        # 更新 task_metrics 的 transferred_count
        if ep_no is not None:
            cal_db.upsert_task_metrics(task_name, tmdb_id, season_no, ep_no, None, now_ts)

        # 读取 season_metrics/或动态计算，写回 progress_pct
        try:
            # 获取 aired/total
            metrics = cal_db.get_season_metrics(tmdb_id, season_no) or {}
            aired = metrics.get('aired_count')
            total = metrics.get('total_count')
            if aired in (None, 0) or total in (None, 0):
                # 动态回退：使用 is_episode_aired 逐集判断计算已播出集数（考虑播出时间）
                # 修复：不再使用简单的日期比较，而是逐集调用 is_episode_aired 判断是否已播出
                try:
                    from zoneinfo import ZoneInfo as _ZoneInfo
                    local_tz = _ZoneInfo(_get_local_timezone())
                    now_local_dt = datetime.now(local_tz)
                except Exception:
                    now_local_dt = datetime.now()
                aired = compute_aired_count_by_episode_check(tmdb_id, season_no, now_local_dt)
                cur.execute('SELECT episode_count FROM seasons WHERE tmdb_id=? AND season_number=?', (tmdb_id, season_no))
                row2 = cur.fetchone()
                total = int((row2 or [0])[0]) if row2 else 0
            trans = int(ep_no or 0)
            denom = min(int(aired or 0), int(total or 0))
            progress_pct = (100 * min(trans, denom) // denom) if denom > 0 else 0
            # 更新 season_metrics 时，需要传入 transferred_count（已转存集数），不能传 None
            cal_db.upsert_season_metrics(tmdb_id, season_no, trans, aired, total, progress_pct, now_ts)
            cal_db.upsert_task_metrics(task_name, tmdb_id, season_no, trans, progress_pct, now_ts)
        except Exception:
            pass

        try:
            notify_calendar_changed('transfer_update')
        except Exception:
            pass
        return True
    except Exception:
        return False


def register_metrics_sync_routes(app):
    @app.route('/api/calendar/metrics/sync_task', methods=['POST'])
    def api_sync_task_metrics():
        try:
            data = request.get_json(silent=True) or {}
            task_name = data.get('task_name') or request.args.get('task_name')
            ok = recompute_task_metrics_and_notify(task_name)
            return jsonify({'success': bool(ok)})
        except Exception as e:
            return jsonify({'success': False, 'message': str(e)})

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
LOG_DIR = os.path.join(parent_dir, "config", "logs")
LOG_FILE_PATH = os.path.join(LOG_DIR, "runtime.log")
MAX_RUNTIME_LOG_LINES = 2000
RUNTIME_LOG_PATTERN = re.compile(
    r'^\[(?P<timestamp>\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]\[(?P<level>[A-Z]+)\]\s?(?P<message>.*)$'
)
os.makedirs(LOG_DIR, exist_ok=True)

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
try:
    register_metrics_sync_routes(app)
except Exception:
    pass
app.config["SESSION_COOKIE_NAME"] = "QUARK_AUTO_SAVE_X_SESSION"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=31)
# 配置会话 cookie 以确保持久化
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# SESSION_COOKIE_SECURE 将通过 before_request 钩子动态设置，以同时支持 HTTP 和 HTTPS
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

# ---- Aired count safeguard: 极简按需校正 ----
def _aired_stamp_path():
    try:
        return os.path.abspath(os.path.join(app.root_path, '..', 'config', '.aired_done'))
    except Exception:
        return '.aired_done'

def _read_aired_effective_stamp():
    try:
        p = _aired_stamp_path()
        if os.path.exists(p):
            with open(p, 'r', encoding='utf-8') as f:
                return (f.read() or '').strip()
    except Exception:
        pass
    return ''

def _write_aired_effective_stamp(effective_date_str: str):
    try:
        p = _aired_stamp_path()
        with open(p, 'w', encoding='utf-8') as f:
            f.write(str(effective_date_str or ''))
    except Exception:
        pass

def ensure_today_aired_done_if_due_once(trigger_reason: str = ''):
    """仅在需要时、且至多每日一次按需补算。无定时轮询，无高频检查。"""
    global _aired_on_demand_checked_date
    try:
        # 直接使用今天的日期作为标记（现在集的播出日期已经是本地日期，不需要通过播出集数刷新时间限制）
        from datetime import datetime as _dt
        now = _dt.now()
        today = now.strftime('%Y-%m-%d')
        # 进程内已检查过今日，直接跳过
        if _aired_on_demand_checked_date == today:
            return
        # 仅当已过刷新时间时才允许补算（避免提前）
        perf = config_data.get('performance', {}) if isinstance(config_data, dict) else {}
        time_str = perf.get('aired_refresh_time', '00:00')
        try:
            hh, mm = [int(x) for x in time_str.split(':')]
            allow = (now.hour > hh) or (now.hour == hh and now.minute >= mm)
        except Exception:
            allow = True
        if not allow:
            # 未到刷新点，不做任何事
            return
        # 已记录的上次完成日期
        stamped = _read_aired_effective_stamp()
        if stamped != today:
            # 需要补算一次
            logging.debug(f"检测到当日尚未生效（触发:{trigger_reason or 'on_demand'}），执行一次补偿刷新…")
            recompute_all_seasons_aired_daily()
            # 成功后 recompute 内部会写 stamp；此处将进程内记为已检查，避免多次触发
            _aired_on_demand_checked_date = today
        else:
            # 已完成，标记今日已检查，避免重复
            _aired_on_demand_checked_date = today
    except Exception:
        pass


def _is_after_refresh_time(now_dt):
    try:
        perf = config_data.get('performance', {}) if isinstance(config_data, dict) else {}
        time_str = perf.get('aired_refresh_time', '00:00')
        hh, mm = [int(x) for x in time_str.split(':')]
        return (now_dt.hour > hh) or (now_dt.hour == hh and now_dt.minute >= mm)
    except Exception:
        return True

def _is_today_aired_done() -> bool:
    try:
        from datetime import datetime as _dt
        now = _dt.now()
        if not _is_after_refresh_time(now):
            return False
        # 直接使用今天的日期作为标记（现在集的播出日期已经是本地日期，不需要通过播出集数刷新时间限制）
        today = now.strftime('%Y-%m-%d')
        stamped = _read_aired_effective_stamp()
        return stamped == today
    except Exception:
        return False


def _schedule_aired_retry_in(minutes_delay: int = 10):
    try:
        from datetime import datetime as _dt, timedelta as _td
        # 移除已有的重试任务，避免重复堆积
        try:
            scheduler.remove_job('daily_aired_retry')
        except Exception:
            pass
        run_at = _dt.now() + _td(minutes=max(1, int(minutes_delay)))
        scheduler.add_job(
            func=lambda: recompute_all_seasons_aired_daily(),
            trigger=DateTrigger(run_date=run_at),
            id='daily_aired_retry',
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        if scheduler.state == 0:
            scheduler.start()
        logging.debug(f"已安排播出集数补偿重试任务，时间 {run_at.strftime('%Y-%m-%d %H:%M')}" )
    except Exception as e:
        logging.warning(f"安排播出集数补偿重试任务失败: {e}")

def _schedule_show_retry_in(tmdb_id: int, minutes_delay: int = 10):
    """为单个节目安排播出进度刷新重试任务"""
    try:
        from datetime import datetime as _dt, timedelta as _td
        job_id = f'show_aired_retry_{tmdb_id}'
        # 移除已有的重试任务，避免重复堆积
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass
        run_at = _dt.now() + _td(minutes=max(1, int(minutes_delay)))
        scheduler.add_job(
            func=lambda _tid=int(tmdb_id): recompute_show_aired_progress(_tid),
            trigger=DateTrigger(run_date=run_at),
            id=job_id,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        if scheduler.state == 0:
            scheduler.start()
        logging.debug(f"已安排节目播出进度补偿重试任务 tmdb_id={tmdb_id}，时间 {run_at.strftime('%Y-%m-%d %H:%M')}" )
    except Exception as e:
        logging.warning(f"安排节目播出进度补偿重试任务失败 tmdb_id={tmdb_id}: {e}")

# 是否为 Flask 主服务进程（仅该进程输出某些提示）
IS_FLASK_SERVER_PROCESS = False

scheduler = BackgroundScheduler(
    job_defaults={
        # 允许在系统唤醒后补偿执行全部错过的任务
        "misfire_grace_time": None,
        # 休眠期间即便错过多次触发，也仅补偿执行一次，避免堆积
        "coalesce": True,
    }
)

# 定时任务进程/线程跟踪：用于检测和清理未完成的任务
_crontab_task_process = None  # 定时运行全部任务的进程对象
_calendar_refresh_thread = None  # 追剧日历自动刷新的线程对象
# 记录每日任务上次生效的时间，避免重复日志
_daily_aired_last_time_str = None
_calendar_refresh_last_interval = None
_last_crontab = None
_last_crontab_delay = None
_aired_on_demand_checked_date = None  # 本进程内，当天是否已进行过一次按需检查
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="[%(asctime)s][%(levelname)s] %(message)s",
    datefmt="%m-%d %H:%M:%S",
)
# 降低第三方网络库的重试噪音：将 urllib3/requests 的日志调为 ERROR，并把"Retrying ..."消息降级为 DEBUG
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
# 添加过滤器，过滤HTTP访问日志（格式：IP - - [时间] "METHOD /path HTTP/1.1" 状态码）
class _HTTPAccessLogFilter(logging.Filter):
    """过滤HTTP访问日志，避免输出到日志文件"""
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = str(record.getMessage())
            # 匹配HTTP访问日志格式：IP - - [时间] "METHOD /path HTTP/1.1" 状态码
            if re.match(r'^\d+\.\d+\.\d+\.\d+\s+-\s+-\s+\[.*?\]\s+"(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+.*?\s+HTTP/1\.\d"\s+\d+', msg):
                return False  # 过滤掉HTTP访问日志
        except Exception:
            pass
        return True

# 过滤werkzeug日志输出（包括HTTP访问日志）
# 无论是否DEBUG模式，都禁用werkzeug的访问日志，只保留ERROR级别以上的日志
_werkzeug_logger = logging.getLogger("werkzeug")
_werkzeug_logger.setLevel(logging.ERROR)
# 将过滤器添加到werkzeug日志记录器，过滤HTTP访问日志
try:
    _werkzeug_logger.addFilter(_HTTPAccessLogFilter())
except Exception:
    pass

# 将过滤器也添加到根日志记录器，确保所有HTTP访问日志都被过滤
try:
    _root_logger = logging.getLogger()
    _root_logger.addFilter(_HTTPAccessLogFilter())
except Exception:
    pass

# 静音 APScheduler 的普通信息日志，避免 "Adding job tentatively" 等噪音
try:
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
except Exception:
    pass

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

# 写入运行日志文件，便于前端实时查看
try:
    _runtime_log_handler = RotatingFileHandler(
        LOG_FILE_PATH,
        maxBytes=5 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8"
    )
    _runtime_log_handler.setFormatter(_standard_formatter)
    _root_logger.addHandler(_runtime_log_handler)
except Exception as e:
    logging.warning(f"初始化运行日志文件处理器失败: {e}")


def _parse_runtime_log_line(line: str) -> dict:
    """解析单行日志文本，提取时间、级别与内容。"""
    text = (line or "").rstrip("\n")
    entry = {
        "raw": text,
        "timestamp": "",
        "level": "INFO",
        "message": text
    }
    if not text:
        return entry
    match = RUNTIME_LOG_PATTERN.match(text.strip())
    if match:
        entry["timestamp"] = match.group("timestamp")
        entry["level"] = match.group("level")
        entry["message"] = match.group("message")
    return entry


def get_recent_runtime_logs(limit: int = None, days: float = None) -> list:
    """获取最近的运行日志行。
    
    Args:
        limit: 按条数限制，默认 None（不使用条数限制）
        days: 按时间范围限制（天数），默认 None（不使用时间限制）
              如果同时指定 limit 和 days，优先使用 days 参数
    
    Returns:
        日志条目列表
    """
    if not os.path.exists(LOG_FILE_PATH):
        return []
    
    # 如果指定了 days 参数，按时间范围筛选
    if days is not None:
        try:
            days_float = float(days)
            if days_float <= 0:
                days_float = 1.0  # 默认1天
        except (ValueError, TypeError):
            days_float = 1.0
        
        # 计算时间阈值（当前时间往前推指定天数）
        time_threshold = datetime.now() - timedelta(days=days_float)
        current_year = datetime.now().year
        
        logs = []
        try:
            with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="replace") as fp:
                for raw_line in fp:
                    line = raw_line.rstrip("\n")
                    if not line:
                        continue
                    
                    entry = _parse_runtime_log_line(line)
                    timestamp_str = entry.get("timestamp", "")
                    
                    # 解析时间戳（格式：MM-DD HH:MM:SS）
                    if timestamp_str:
                        try:
                            # 尝试解析时间戳，需要加上当前年份
                            log_time = datetime.strptime(f"{current_year}-{timestamp_str}", "%Y-%m-%d %H:%M:%S")
                            
                            # 如果解析出的时间比当前时间还晚（跨年情况），则减一年
                            if log_time > datetime.now():
                                log_time = datetime.strptime(f"{current_year - 1}-{timestamp_str}", "%Y-%m-%d %H:%M:%S")
                            
                            # 只保留在时间范围内的日志
                            if log_time >= time_threshold:
                                entry["id"] = f"{len(logs)}-{hashlib.md5((line + str(len(logs))).encode('utf-8', 'ignore')).hexdigest()}"
                                logs.append(entry)
                        except (ValueError, TypeError):
                            # 如果时间戳解析失败，保留该日志（兼容没有时间戳的日志）
                            entry["id"] = f"{len(logs)}-{hashlib.md5((line + str(len(logs))).encode('utf-8', 'ignore')).hexdigest()}"
                            logs.append(entry)
                    else:
                        # 没有时间戳的日志也保留
                        entry["id"] = f"{len(logs)}-{hashlib.md5((line + str(len(logs))).encode('utf-8', 'ignore')).hexdigest()}"
                        logs.append(entry)
        except Exception as exc:
            logging.warning(f"读取运行日志失败: {exc}")
            return []
        
        return logs
    
    # 如果指定了 limit 参数，按条数限制（保持向后兼容）
    if limit is not None:
        try:
            limit_int = int(limit)
        except (ValueError, TypeError):
            limit_int = 600
        limit_int = max(100, min(limit_int, MAX_RUNTIME_LOG_LINES))
        
        lines = deque(maxlen=limit_int)
        try:
            with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="replace") as fp:
                for raw_line in fp:
                    lines.append(raw_line.rstrip("\n"))
        except Exception as exc:
            logging.warning(f"读取运行日志失败: {exc}")
            return []
        
        logs = []
        for idx, raw_line in enumerate(lines):
            entry = _parse_runtime_log_line(raw_line)
            entry["id"] = f"{idx}-{hashlib.md5((raw_line + str(idx)).encode('utf-8', 'ignore')).hexdigest()}"
            logs.append(entry)
        return logs
    
    # 如果两个参数都没有指定，返回所有日志（不推荐，但保持兼容性）
    logs = []
    try:
        with open(LOG_FILE_PATH, "r", encoding="utf-8", errors="replace") as fp:
            for raw_line in fp:
                line = raw_line.rstrip("\n")
                if not line:
                    continue
                entry = _parse_runtime_log_line(line)
                entry["id"] = f"{len(logs)}-{hashlib.md5((line + str(len(logs))).encode('utf-8', 'ignore')).hexdigest()}"
                logs.append(entry)
    except Exception as exc:
        logging.warning(f"读取运行日志失败: {exc}")
        return []
    
    return logs


# --------- 每日任务：在用户设置的刷新时间重算所有季的已播出集数并更新进度 ---------
def recompute_all_seasons_aired_daily():
    logging.info(f">>> 开始执行播出集数自动刷新")
    try:
        from time import time as _now
        now_ts = int(_now())

        # 当前本地时间（用于精确“是否已播出”判断）
        try:
            from zoneinfo import ZoneInfo as _ZoneInfo
            local_tz = _get_local_timezone()
            now_local_dt = datetime.now(_ZoneInfo(local_tz))
        except Exception:
            now_local_dt = datetime.now()

        cal_db = CalendarDB()
        cur = cal_db.conn.cursor()
        # 遍历本地所有季
        cur.execute('SELECT tmdb_id, season_number, episode_count FROM seasons')
        rows = cur.fetchall() or []
        for tmdb_id, season_no, total in rows:
            try:
                tmdb_id_i = int(tmdb_id)
                season_no_i = int(season_no)
                total_i = int(total or 0)

                # 逐集判断是否已播出（优先使用 Trakt 本地时间，其次节目级本地时间，最后 TMDB 日期）
                aired_i = 0
                cur.execute(
                    """
                    SELECT episode_number FROM episodes
                    WHERE tmdb_id=? AND season_number=?
                    """,
                    (tmdb_id_i, season_no_i),
                )
                for (ep_no,) in cur.fetchall() or []:
                    try:
                        if is_episode_aired(tmdb_id_i, season_no_i, int(ep_no), now_local_dt):
                            aired_i += 1
                    except Exception:
                        continue

                # 取已转存集数用于计算进度
                metrics = cal_db.get_season_metrics(tmdb_id_i, season_no_i) or {}
                transferred_i = int(metrics.get('transferred_count') or 0)
                denom = min(int(aired_i or 0), int(total_i or 0))
                progress_pct = (100 * min(transferred_i, denom) // denom) if denom > 0 else 0
                # 写回缓存：仅更新 aired/total/progress_pct 与时间戳
                cal_db.upsert_season_metrics(tmdb_id_i, season_no_i, None, aired_i, total_i, progress_pct, now_ts)
            except Exception:
                continue
        # 成功后写入当日完成标记（使用 stamp 文件，避免引入 DB 结构变更）
        try:
            # 直接使用今天的日期作为标记（现在集的播出日期已经是本地日期，不需要通过播出集数刷新时间限制）
            from datetime import datetime as _dt
            today = _dt.now().strftime('%Y-%m-%d')
            _write_aired_effective_stamp(today)
        except Exception:
            pass
        try:
            notify_calendar_changed('daily_aired_update')
        except Exception:
            pass
        # 验证当日是否已生效，成功则清理重试任务；否则安排下一次补偿重试
        try:
            if _is_today_aired_done():
                try:
                    scheduler.remove_job('daily_aired_retry')
                except Exception:
                    pass
            else:
                _schedule_aired_retry_in(10)
        except Exception:
            pass
        logging.info(f">>> 播出集数自动刷新执行成功")
    except Exception as e:
        logging.warning(f">>> 播出集数自动刷新执行异常: {e}")
        # 发生异常也安排一次补偿重试
        try:
            _schedule_aired_retry_in(10)
        except Exception:
            pass


def recompute_show_aired_progress(tmdb_id: int, now_local_dt=None, season_number: int = None, episode_number: int = None):
    """按节目维度重算播出/进度，供按播出时间触发与补偿使用
    
    Args:
        tmdb_id: 节目ID
        now_local_dt: 当前本地时间，可选
        season_number: 季号，可选。如果提供，只刷新指定的季
        episode_number: 集号，可选。如果提供，日志中会显示集号信息
    """
    try:
        from time import time as _now
        now_ts = int(_now())
        try:
            from zoneinfo import ZoneInfo as _ZoneInfo
            local_tz = _ZoneInfo(_get_local_timezone())
            if now_local_dt is None:
                now_local_dt = datetime.now(local_tz)
        except Exception:
            if now_local_dt is None:
                now_local_dt = datetime.now()

        cal_db = CalendarDB()
        cur = cal_db.conn.cursor()
        # 获取节目名称（用于日志，仅影响可读性）
        show_name = str(tmdb_id)
        try:
            cur.execute("SELECT name FROM shows WHERE tmdb_id=? LIMIT 1", (int(tmdb_id),))
            row = cur.fetchone()
            if row and row[0]:
                show_name = str(row[0])
        except Exception:
            pass
        
        # 如果指定了季号，只处理该季；否则处理所有季
        if season_number is not None:
            cur.execute(
                "SELECT season_number, episode_count FROM seasons WHERE tmdb_id=? AND season_number=?",
                (int(tmdb_id), int(season_number)),
            )
        else:
            cur.execute(
                "SELECT season_number, episode_count FROM seasons WHERE tmdb_id=?",
                (int(tmdb_id),),
            )
        rows = cur.fetchall() or []
        for season_no, total in rows:
            try:
                season_no_i = int(season_no)
                total_i = int(total or 0)
                
                # 构建日志显示名称：如果提供了集号，在日志中显示集号
                log_name = f"{show_name} · 第 {season_no_i} 季"
                if episode_number is not None:
                    log_name = f"{show_name} · 第 {season_no_i} 季 · 第 {int(episode_number)} 集"
                
                try:
                    logging.info(f">>> 开始执行 [{log_name}] 播出状态刷新任务")
                except Exception:
                    pass
                aired_i = 0
                cur.execute(
                    """
                    SELECT episode_number FROM episodes
                    WHERE tmdb_id=? AND season_number=?
                    """,
                    (int(tmdb_id), season_no_i),
                )
                for (ep_no,) in cur.fetchall() or []:
                    try:
                        if is_episode_aired(int(tmdb_id), season_no_i, int(ep_no), now_local_dt):
                            aired_i += 1
                    except Exception:
                        continue
                metrics = cal_db.get_season_metrics(int(tmdb_id), season_no_i) or {}
                transferred_i = int(metrics.get('transferred_count') or 0)
                denom = min(int(aired_i or 0), int(total_i or 0))
                progress_pct = (100 * min(transferred_i, denom) // denom) if denom > 0 else 0
                cal_db.upsert_season_metrics(int(tmdb_id), season_no_i, None, aired_i, total_i, progress_pct, now_ts)
                try:
                    logging.info(f">>> [{log_name}] 播出状态刷新任务执行成功")
                except Exception:
                    pass
            except Exception:
                continue
        try:
            notify_calendar_changed('aired_on_demand')
        except Exception:
            pass
        # 成功后清理该节目的重试任务
        try:
            job_id = f'show_aired_retry_{int(tmdb_id)}'
            scheduler.remove_job(job_id)
        except Exception:
            pass
    except Exception as e:
        logging.warning(f">>> 剧集播出状态刷新执行异常 tmdb_id={tmdb_id}: {e}")
        # 发生异常也安排一次补偿重试
        try:
            _schedule_show_retry_in(int(tmdb_id), minutes_delay=10)
        except Exception:
            pass


def _build_episode_local_air_dt(tmdb_id, air_datetime_local, air_date_local, air_date):
    """
    根据本地播出时间字段和兜底配置生成本地播出 datetime
    返回 (datetime_or_none, used_time_str_or_none)，用于上层判断是否与全局刷新时间重叠
    """
    try:
        from zoneinfo import ZoneInfo as _ZoneInfo
        local_tz = _ZoneInfo(_get_local_timezone())
    except Exception:
        local_tz = None

    try:
        if air_datetime_local:
            dt = datetime.fromisoformat(str(air_datetime_local))
            if dt.tzinfo is None and local_tz:
                dt = dt.replace(tzinfo=local_tz)
            return dt, None
    except Exception:
        pass

    date_str = (air_date_local or air_date or '').strip()
    if not date_str:
        return None, None
    try:
        date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    except Exception:
        return None, None

    schedule = {}
    try:
        schedule = CalendarDB().get_show_air_schedule(int(tmdb_id)) or {}
    except Exception:
        schedule = {}
    perf = config_data.get('performance', {}) if isinstance(config_data, dict) else {}
    fallback_time = (schedule.get("local_air_time") or perf.get("aired_refresh_time") or "00:00").strip()
    try:
        hh, mm = [int(x) for x in str(fallback_time).split(":")]
    except Exception:
        hh, mm = 0, 0
    try:
        dt = datetime.combine(date_obj, datetime.min.time()).replace(hour=hh, minute=mm, second=0)
        if local_tz:
            dt = dt.replace(tzinfo=local_tz)
        return dt, fallback_time
    except Exception:
        return None, fallback_time


def schedule_airtime_based_refresh_jobs(days_back: int = 2, days_forward: int = 14, tmdb_id: int = None):
    """
    为每个节目按本地播出时间调度单节目刷新，含错过补偿：
    - 未来的播出时间：注册 DateTrigger，确保到点立即重算
    - 已经过期但当天未刷新：立即重算一次补偿
    """
    try:
        try:
            from zoneinfo import ZoneInfo as _ZoneInfo
            local_tz = _ZoneInfo(_get_local_timezone())
            now_local = datetime.now(local_tz)
        except Exception:
            local_tz = None
            now_local = datetime.now()

        cal_db = CalendarDB()
        cur = cal_db.conn.cursor()
        base_sql = """
            SELECT tmdb_id, season_number, episode_number, air_datetime_local, air_date_local, air_date
            FROM episodes
            WHERE ((air_datetime_local IS NOT NULL AND air_datetime_local != '')
               OR (air_date_local IS NOT NULL AND air_date_local != '')
               OR (air_date IS NOT NULL AND air_date != ''))
        """
        params = []
        if tmdb_id is not None:
            base_sql += " AND tmdb_id = ?"
            params.append(int(tmdb_id))
        cur.execute(base_sql, params)
        rows = cur.fetchall() or []
        if not rows:
            return

        # 全局播出集数刷新时间，用于与节目级时间对比，避免重复调度
        perf = config_data.get('performance', {}) if isinstance(config_data, dict) else {}
        global_refresh_time = (perf.get('aired_refresh_time') or "00:00").strip()

        # 清理旧的播出时间调度，避免单一 job 覆盖多集
        try:
            _removed = 0
            prefix = f"aired_refresh_{int(tmdb_id)}_" if tmdb_id is not None else "aired_refresh_"
            for _job in scheduler.get_jobs():
                try:
                    if str(getattr(_job, "id", "")).startswith(prefix):
                        scheduler.remove_job(_job.id)
                        _removed += 1
                except Exception:
                    continue
            if _removed > 0:
                if tmdb_id is None:
                    logging.debug(f"已清理 {_removed} 个过期的剧集播出状态刷新任务")
                else:
                    logging.debug(f"已清理 {_removed} 个过期的剧集播出状态刷新任务 (tmdb_id={int(tmdb_id)})")
        except Exception:
            pass

        min_date = (now_local.date() - timedelta(days=max(0, days_back))).isoformat()
        max_date = (now_local.date() + timedelta(days=max(0, days_forward))).isoformat()
        # 用于记录需要补偿刷新的节目，key 为 tmdb_id，value 为最早需要补偿的播出时间
        shows_need_catch = {}
        scheduled = 0
        
        # 预加载所有季的刷新时间戳与当前已播集数，用于检查是否已经刷新过
        season_refresh_times = {}
        try:
            cur.execute('SELECT tmdb_id, season_number, updated_at, aired_count FROM season_metrics')
            for tid, sn, ua, ac in (cur.fetchall() or []):
                key = (int(tid), int(sn))
                if ua:
                    season_refresh_times[key] = {"updated_at": int(ua), "aired_count": 0 if ac is None else int(ac)}
        except Exception:
            pass
        
        for tmdb_id, season_number, episode_number, air_dt_local, air_date_local, air_date in rows:
            target_dt, used_time = _build_episode_local_air_dt(tmdb_id, air_dt_local, air_date_local, air_date)
            if not target_dt:
                continue
            # 若使用的播出时间与全局刷新时间相同，则交由全局任务处理，避免重复调度
            try:
                if used_time and str(used_time).strip() == global_refresh_time:
                    continue
            except Exception:
                pass
            try:
                d_str = target_dt.date().isoformat()
                if d_str < min_date or d_str > max_date:
                    continue
            except Exception:
                pass

            job_id = f"aired_refresh_{tmdb_id}_{season_number}_{episode_number}_{target_dt.strftime('%Y%m%d%H%M')}"

            # 对于已过期的播出时间，检查是否需要补偿刷新
            if target_dt <= now_local:
                tmdb_id_i = int(tmdb_id)
                season_no_i = int(season_number)
                
                # 检查该季是否已经刷新过（通过 season_metrics 的 updated_at 和已播集数）
                need_refresh = True
                try:
                    refresh_info = season_refresh_times.get((tmdb_id_i, season_no_i)) or {}
                    refresh_ts = refresh_info.get("updated_at")
                    aired_cached = refresh_info.get("aired_count")
                    # 将播出时间转换为时间戳进行比较
                    target_ts = int(target_dt.timestamp())
                    if refresh_ts:
                        # 如果刷新时间晚于播出时间，且已播集数已覆盖该集，则视为已刷新
                        if refresh_ts >= target_ts and aired_cached is not None:
                            try:
                                if int(aired_cached) >= int(episode_number):
                                    need_refresh = False
                            except Exception:
                                need_refresh = False
                except Exception:
                    # 检查失败，保守处理：执行补偿刷新
                    pass
                
                if need_refresh:
                    # 记录需要补偿的节目，并记录最早需要补偿的播出时间（用于日志）
                    if tmdb_id_i not in shows_need_catch:
                        shows_need_catch[tmdb_id_i] = target_dt
                    elif target_dt < shows_need_catch[tmdb_id_i]:
                        shows_need_catch[tmdb_id_i] = target_dt
                continue

            # 未来的播出时间：注册 DateTrigger
            try:
                _tmdb_id = int(tmdb_id)
                _season_no = int(season_number)
                _ep_no = int(episode_number)
                scheduler.add_job(
                    func=lambda _tid=_tmdb_id, _sn=_season_no, _ep=_ep_no: recompute_show_aired_progress(_tid, season_number=_sn, episode_number=_ep),
                    trigger=DateTrigger(run_date=target_dt),
                    id=job_id,
                    replace_existing=True,
                    coalesce=True,
                    max_instances=1,
                    misfire_grace_time=None,
                )
                scheduled += 1
            except Exception as _e:
                logging.debug(f"安排剧集播出状态刷新失败 tmdb_id={tmdb_id}: {_e}")
                continue

        # 执行补偿刷新：对于需要补偿的节目，立即刷新
        if shows_need_catch:
            for tid, earliest_dt in shows_need_catch.items():
                try:
                    logging.debug(f"补偿刷新节目 tmdb_id={tid}，最早错过播出时间: {earliest_dt.strftime('%Y-%m-%d %H:%M')}")
                    recompute_show_aired_progress(int(tid), now_local_dt=now_local)
                except Exception as _e:
                    logging.debug(f"补偿刷新失败 tmdb_id={tid}: {_e}")

        if scheduler.state == 0:
            scheduler.start()
        if scheduled > 0:
            logging.debug(f"已更新 {scheduled} 个剧集播出状态刷新任务")
    except Exception as e:
        logging.warning(f"按播出时间调度刷新失败: {e}")


def _trigger_airtime_reschedule(reason: str = "", tmdb_id: int = None):
    """统一包装，便于在数据更新后重新计算播出时间调度"""
    try:
        schedule_airtime_based_refresh_jobs(tmdb_id=tmdb_id)
        if reason:
            if tmdb_id is None:
                logging.debug(f"已重新安排剧集播出状态刷新任务，原因: {reason}")
            else:
                logging.debug(f"已重新安排剧集播出状态刷新任务，原因: {reason}, tmdb_id={int(tmdb_id)}")
    except Exception as e:
        logging.debug(f"重新安排剧集播出状态刷新任务失败({reason}): {e}")


def restart_daily_aired_update_job():
    try:
        global _daily_aired_last_time_str
        try:
            scheduler.remove_job('daily_aired_update')
        except Exception:
            pass
        # 从配置读取刷新时间，默认 00:00
        perf = config_data.get('performance', {}) if isinstance(config_data, dict) else {}
        time_str = perf.get('aired_refresh_time', '00:00')
        # 解析时间格式 HH:MM
        try:
            parts = time_str.split(':')
            if len(parts) == 2:
                hour = int(parts[0])
                minute = int(parts[1])
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    trigger = CronTrigger(hour=hour, minute=minute)
                else:
                    trigger = CronTrigger(hour=0, minute=0)
                    hour, minute = 0, 0
            else:
                trigger = CronTrigger(hour=0, minute=0)
                hour, minute = 0, 0
        except Exception:
            trigger = CronTrigger(hour=0, minute=0)
            hour, minute = 0, 0
        scheduler.add_job(
            recompute_all_seasons_aired_daily,
            trigger=trigger,
            id='daily_aired_update',
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=None
        )
        if scheduler.state == 0:
            scheduler.start()
        # 友好提示：每日任务已启动（仅在时间变化时输出，且仅在 Flask 主进程中打印，避免子进程噪音）
        try:
            if IS_FLASK_SERVER_PROCESS:
                _time_str = f"{hour:02d}:{minute:02d}"
                if _daily_aired_last_time_str != _time_str:
                    logging.info(f"已启动播出集数自动刷新，时间 {_time_str}")
                    _daily_aired_last_time_str = _time_str
        except Exception:
            pass
    except Exception as e:
        logging.warning(f"启动播出集数自动刷新失败: {e}")


# 应用启动时注册每日任务
try:
    restart_daily_aired_update_job()
    # 启动时做一次极简按需补偿检查（仅一次）
    ensure_today_aired_done_if_due_once('startup')
    # 启动时同步一次播出时间触发调度，确保新一集能按播出时间刷新
    _trigger_airtime_reschedule('startup')
    # 取消低频健康检查方案，改为失败后按需重试调度
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
        # 如果是会影响任务列表数据或剧集数据的变更，清除所有缓存，确保数据实时更新
        # 包括：转存相关、元数据编辑、任务配置变更、剧集数据刷新、记录删除等
        if reason in ('transfer_update', 'transfer_record_created', 'transfer_record_updated', 
                     'batch_rename_completed', 'crontab_task_completed', 'manual_task_completed',
                     'edit_metadata', 'daily_aired_update', 'aired_on_demand', 'bootstrap', 
                     'refresh_latest_season', 'refresh_episode', 'refresh_season', 'refresh_show', 
                     'auto_refresh', 'status_updated', 'aired_refresh_time_changed', 'update_airtime',
                     'trakt_airtime_synced', 'purge_tmdb', 'purge_by_task', 'purge_orphans',
                     'delete_records', 'reset_folder'):
            try:
                clear_all_calendar_cache()
            except Exception:
                pass
        
        for q in list(calendar_subscribers):
            try:
                q.put_nowait({'type': 'calendar_changed', 'reason': reason})
            except Exception:
                pass
    except Exception:
        pass

@app.route('/api/calendar/notify', methods=['POST'])
def api_calendar_notify():
    """供内部脚本调用的简易通知入口：通过 HTTP 触发日历/任务列表 SSE 事件"""
    try:
        data = request.get_json(silent=True) or {}
        reason = data.get('reason') or ''
        notify_calendar_changed(reason)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})

@app.route('/api/calendar/stream')
def calendar_stream():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    # 在首个建立 SSE 的入口处触发一次极简按需检查（无需全局每请求检查）
    try:
        ensure_today_aired_done_if_due_once('calendar_stream')
    except Exception:
        pass

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


# 确保已登录用户的会话保持永久状态，并动态设置 cookie secure 标志
@app.before_request
def make_session_permanent():
    """确保已登录用户的会话保持永久状态，并根据请求协议动态设置 secure 标志"""
    if session.get("token"):
        session.permanent = True
    
    # 动态检测是否使用 HTTPS（支持反向代理场景）
    is_secure = False
    if request.is_secure:
        # 直接使用 HTTPS
        is_secure = True
    elif request.headers.get('X-Forwarded-Proto') == 'https':
        # 通过反向代理使用 HTTPS（如 Nginx、Apache 等）
        is_secure = True
    elif request.headers.get('X-Forwarded-Scheme') == 'https':
        # 另一种常见的代理头
        is_secure = True
    
    # 动态设置 SESSION_COOKIE_SECURE，让 Flask 自动处理 cookie 的 secure 标志
    app.config["SESSION_COOKIE_SECURE"] = is_secure


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
        data['performance'] = {'calendar_refresh_interval_seconds': DEFAULT_REFRESH_SECONDS, 'aired_refresh_time': '00:00'}
    else:
        if 'calendar_refresh_interval_seconds' not in perf:
            data['performance']['calendar_refresh_interval_seconds'] = DEFAULT_REFRESH_SECONDS
        if 'aired_refresh_time' not in perf:
            data['performance']['aired_refresh_time'] = '00:00'
    
    # 确保海报语言有默认值
    if 'poster_language' not in data:
        data['poster_language'] = 'zh-CN'

    # 初始化追剧日历时区模式（原始时区 / 本地时区）
    if 'calendar_timezone_mode' not in data:
        data['calendar_timezone_mode'] = 'original'
    # 初始化本地时区（用于 Trakt 播出时间转换与本地时间判断）
    if 'local_timezone' not in data:
        # 默认按照北京时间处理，也支持用户后续在配置文件中手动修改
        data['local_timezone'] = 'Asia/Shanghai'

    # 初始化 Trakt 配置
    if 'trakt' not in data or not isinstance(data.get('trakt'), dict):
        data['trakt'] = {'client_id': ''}
    else:
        data['trakt'].setdefault('client_id', '')

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
    # 记录旧的播出集数刷新时间设置
    old_perf = config_data.get('performance', {}) if isinstance(config_data, dict) else {}
    old_aired_refresh_time = old_perf.get('aired_refresh_time', '00:00')
    # 记录旧的 Trakt Client ID，用于判断是否首次配置
    old_trakt_client_id = ''
    try:
        old_trakt_client_id = (config_data.get("trakt", {}) or {}).get("client_id", "") or ""
        old_trakt_client_id = str(old_trakt_client_id).strip()
    except Exception:
        old_trakt_client_id = ''
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
            elif key == "trakt":
                # 更新 Trakt 配置，仅接收 client_id 字段，其它字段忽略
                if not isinstance(value, dict):
                    value = {}
                trakt_cfg = config_data.get("trakt") if isinstance(config_data.get("trakt"), dict) else {}
                trakt_cfg["client_id"] = (value.get("client_id") or "").strip()
                config_data["trakt"] = trakt_cfg
            else:
                config_data.update({key: value})
    
    # 同步更新任务的插件配置
    sync_task_plugins_config()
    
    # 记录新的 Trakt Client ID
    try:
        new_trakt_client_id = (config_data.get("trakt", {}) or {}).get("client_id", "") or ""
        new_trakt_client_id = str(new_trakt_client_id).strip()
    except Exception:
        new_trakt_client_id = ''
    
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
    def _post_update_tasks(old_task_map_snapshot, prev_tmdb_value, new_tmdb_value, old_poster_lang, old_aired_refresh_time_value, prev_trakt_client_id_value, new_trakt_client_id_value):
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
        
        # 检查播出集数刷新时间是否改变
        new_perf = config_data.get('performance', {}) if isinstance(config_data, dict) else {}
        new_aired_refresh_time = new_perf.get('aired_refresh_time', '00:00')
        if old_aired_refresh_time_value != new_aired_refresh_time:
            try:
                # 立即重新计算所有已播出集数，使用新的刷新时间设置
                recompute_all_seasons_aired_daily()
                # 通知前端日历数据已更新
                notify_calendar_changed('aired_refresh_time_changed')
                logging.info(f"播出集数刷新时间已更改 ({old_aired_refresh_time_value} -> {new_aired_refresh_time})，已重新计算已播出集数")
            except Exception as e:
                logging.warning(f"重新计算已播出集数失败: {e}")
        
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

        # 基于最新任务映射清理孤儿 metrics/seasons（避免残留）
        try:
            tasks = config_data.get('tasklist', [])
            valid_names = []
            valid_pairs = []
            for t in tasks:
                name = t.get('taskname') or t.get('task_name') or ''
                cal = (t.get('calendar_info') or {})
                match = (cal.get('match') or {})
                tid = match.get('tmdb_id') or cal.get('tmdb_id')
                sn = match.get('latest_season_number') or cal.get('latest_season_number')
                if name:
                    valid_names.append(name)
                if tid and sn:
                    try:
                        valid_pairs.append((int(tid), int(sn)))
                    except Exception:
                        pass
            try:
                from app.sdk.db import CalendarDB as _CalDB
                _CalDB().cleanup_orphan_data(valid_pairs, valid_names)
                # 清理孤立数据后，也清理可能残留的孤立海报文件
                try:
                    cleanup_orphaned_posters()
                except Exception:
                    pass
            except Exception:
                pass
        except Exception as e:
            logging.warning(f"自动清理孤儿季与指标失败: {e}")


        # 清除所有追剧日历缓存，确保下次请求获取最新数据
        try:
            clear_all_calendar_cache()
        except Exception as e:
            logging.debug(f"清除缓存失败（不影响功能）: {e}")

        # 根据最新性能配置重启自动刷新任务
        try:
            restart_calendar_refresh_job()
        except Exception as e:
            logging.warning(f"重启追剧日历自动刷新任务失败: {e}")
        
        # 根据最新性能配置重启已播出集数更新任务
        try:
            restart_daily_aired_update_job()
        except Exception as e:
            logging.warning(f"重启已播出集数更新任务失败: {e}")

        # 若首次配置 Trakt Client ID，则为所有节目补齐播出时间
        try:
            if (not prev_trakt_client_id_value) and new_trakt_client_id_value:
                logging.info("首次配置 Trakt Client ID，开始全量同步节目播出时间")
                sync_trakt_airtime_for_all_shows()
        except Exception as e:
            logging.warning(f"首次配置 Trakt 播出时间同步失败: {e}")

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

    threading.Thread(target=_post_update_tasks, args=(old_task_map, prev_tmdb, new_tmdb, old_poster_language, old_aired_refresh_time, old_trakt_client_id, new_trakt_client_id), daemon=True).start()
    
    # 确保性能配置包含秒级字段
    if not isinstance(config_data.get('performance'), dict):
        config_data['performance'] = {'calendar_refresh_interval_seconds': DEFAULT_REFRESH_SECONDS, 'aired_refresh_time': '00:00'}
    else:
        config_data['performance'].setdefault('calendar_refresh_interval_seconds', DEFAULT_REFRESH_SECONDS)
        config_data['performance'].setdefault('aired_refresh_time', '00:00')
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
        f">>> 开始执行手动运行任务 [{tasklist[0].get('taskname') if len(tasklist)>0 else 'ALL'}]"
    )

    def generate_output():
        # 全局变量：跟踪上一次输出是否是空行
        last_was_empty = False
        
        # 设置环境变量
        process_env = os.environ.copy()
        process_env["PYTHONIOENCODING"] = "utf-8"
        if tasklist:
            process_env["TASKLIST"] = json.dumps(tasklist, ensure_ascii=False)
            # 添加原始任务索引的环境变量
            if len(tasklist) == 1:
                # 单任务手动运行：前端传入 original_index，用于日志展示
                if 'original_index' in request.json:
                    process_env["ORIGINAL_TASK_INDEX"] = str(request.json['original_index'])
                # 单任务手动运行应忽略执行周期和任务进度限制（包括自动模式下进度100%也要执行）
                process_env["IGNORE_EXECUTION_RULES"] = "1"
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
                stripped_line = line.strip()
                # 检查是否是空行
                is_empty = not stripped_line
                
                # 如果是空行且上一次也是空行，跳过本次输出
                if is_empty and last_was_empty:
                    continue
                
                # 输出日志（空行也输出，但避免连续空行）
                if is_empty:
                    logging.info("")
                    last_was_empty = True
                else:
                    logging.info(stripped_line)
                    last_was_empty = False
                
                yield f"data: {line}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            process.stdout.close()
            process.wait()
            # 结束通知：仅在正常退出时记录成功日志，非零退出码记录警告
            try:
                task_name = tasklist[0].get('taskname') if len(tasklist) > 0 else 'ALL'
            except Exception:
                task_name = 'ALL'
            if process.returncode == 0:
                logging.info(f">>> 手动运行任务 [{task_name}] 执行成功")
                # 手动运行任务完成后，清除缓存以确保前端能获取到最新的已转存数
                try:
                    notify_calendar_changed('manual_task_completed')
                except Exception:
                    pass
            else:
                logging.warning(f">>> 手动运行任务 [{task_name}] 执行完成但返回非零退出码: {process.returncode}")

    return Response(
        stream_with_context(generate_output()),
        content_type="text/event-stream;charset=utf-8",
    )


@app.route("/api/runtime_logs")
def api_runtime_logs():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"}), 401
    # 优先使用 days 参数（按时间范围），如果没有则使用 limit 参数（按条数，保持向后兼容）
    days = request.args.get("days")
    limit = request.args.get("limit")
    
    if days is not None:
        try:
            days_float = float(days)
            logs = get_recent_runtime_logs(days=days_float)
        except (ValueError, TypeError):
            # 如果 days 参数无效，回退到 limit 参数
            limit = limit or 600
            logs = get_recent_runtime_logs(limit=limit)
    else:
        # 如果没有 days 参数，使用 limit 参数（保持向后兼容）
        limit = limit or 600
        logs = get_recent_runtime_logs(limit=limit)
    
    return jsonify({"success": True, "logs": logs})


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
                        # 获取海报文件名用于日志提示
                        try:
                            db = CalendarDB()
                            show = db.get_show(int(tmdb_id))
                            poster_path = show.get('poster_local_path', '') if show else ''
                            if poster_path and poster_path.startswith('/cache/images/'):
                                poster_filename = poster_path.replace('/cache/images/', '')
                            else:
                                poster_filename = f"poster_{tmdb_id}"
                        except Exception:
                            poster_filename = f"poster_{tmdb_id}"
                        logging.debug(f"跳过清理海报文件 {poster_filename}（仍被其他任务引用）")
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
        local_air_time_changed = False
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
                            logging.debug(f"任务配置中的 TMDB ID 在数据库中不存在 - 任务: '{task_name}', tmdb_id: {tmdb_id}")
        
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
                    # 使用今天的日期判断季是否已播出（现在集的播出日期已经是本地日期，不需要通过播出集数刷新时间限制）
                    from datetime import datetime as _dt
                    today_date = _dt.now().date()
                    latest_season_number = 0
                    latest_air_date = None
                    
                    for s in seasons:
                        sn = s.get('season_number', 0)
                        air_date = s.get('air_date')
                        logging.debug(f"季数: {sn}, 播出日期: {air_date}, 类型: {type(sn)}")
                        
                        # 只考虑已播出的季（有air_date且早于或等于今天的日期）
                        if sn and sn > 0 and air_date:  # 排除第0季（特殊季）
                            try:
                                season_air_date = datetime.strptime(air_date, '%Y-%m-%d').date()
                                if season_air_date <= today_date:
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
                    # 为 CloudSaver 结果补齐来源字段
                    for it in search_results:
                        try:
                            if not it.get("source"):
                                it["source"] = "CloudSaver"
                        except Exception:
                            pass
                    merged.extend(search_results)
                    providers.append("CloudSaver")

        # PanSou
        if ps_data and ps_data.get("server") and PanSou is not None:
            try:
                ps = PanSou(ps_data.get("server"))
                result = ps.search(search_query)
                if result.get("success") and isinstance(result.get("data"), list):
                    # 为 PanSou 结果补齐来源字段
                    for it in result.get("data"):
                        try:
                            if not it.get("source"):
                                it["source"] = "PanSou"
                        except Exception:
                            pass
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
        dedup_map = {}          # 按 shareurl 归并，值为 {item, _sources:set}
        fingerprint_map = {}    # 兜底：完整指纹归并（仅当缺失链接时），同上
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
                if not existed or to_ts(existed["item"].get("publish_date")) < timestamp:
                    dedup_map[shareurl] = {"item": item, "_sources": set([src for src in [source] if src])}
                else:
                    # 更新时间较旧但来源需要合并
                    try:
                        if source:
                            existed["_sources"].add(source)
                    except Exception:
                        pass
            else:
                # 条件2（兜底）：完整指纹归并（极少发生），依然取最新
                fingerprint = f"{shareurl}|{title}|{pubdate}|{source}"
                existed = fingerprint_map.get(fingerprint)
                if not existed or to_ts(existed["item"].get("publish_date")) < timestamp:
                    fingerprint_map[fingerprint] = {"item": item, "_sources": set([src for src in [source] if src])}
                else:
                    try:
                        if source:
                            existed["_sources"].add(source)
                    except Exception:
                        pass

        # 第一轮：汇总归并后的候选结果
        candidates = list(dedup_map.values()) + list(fingerprint_map.values())

        # 第二轮：无论 shareurl 是否相同，再按 标题+发布时间 归并一次（使用时间戳作为键，兼容不同时间格式），保留最新
        final_map = {}
        for pack in candidates:
            try:
                item = pack.get("item") if isinstance(pack, dict) else pack
                src_set = pack.get("_sources") if isinstance(pack, dict) else set([ (item.get("source") or "").strip() ])
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
                    # 初次放入时带上来源集合
                    item_copy = dict(item)
                    item_copy["_sources"] = set([*list(src_set)])
                    final_map[key] = item_copy
                else:
                    existed_ts = to_ts(existed.get("publish_date"))
                    if current_ts > existed_ts:
                        item_copy = dict(item)
                        # 继承旧来源并合并新来源
                        merged_sources = set(list(existed.get("_sources") or set()))
                        for ssrc in list(src_set):
                            if ssrc:
                                merged_sources.add(ssrc)
                        item_copy["_sources"] = merged_sources
                        final_map[key] = item_copy
                    elif current_ts == existed_ts:
                        # 时间完全相同，使用确定性优先级打破平手
                        source_priority = {"CloudSaver": 2, "PanSou": 1}
                        existed_pri = source_priority.get((existed.get("source") or "").strip(), 0)
                        current_pri = source_priority.get(src, 0)
                        # 无论谁胜出，都要合并来源集合
                        if current_pri > existed_pri:
                            item_copy = dict(item)
                            merged_sources = set(list(existed.get("_sources") or set()))
                            for ssrc in list(src_set):
                                if ssrc:
                                    merged_sources.add(ssrc)
                            item_copy["_sources"] = merged_sources
                            final_map[key] = item_copy
                        elif current_pri == existed_pri:
                            # 进一步比较信息丰富度（content 长度）
                            if len(str(item.get("content") or "")) > len(str(existed.get("content") or "")):
                                item_copy = dict(item)
                                # 合并来源
                                merged_sources = set(list(existed.get("_sources") or set()))
                                for ssrc in list(src_set):
                                    if ssrc:
                                        merged_sources.add(ssrc)
                                item_copy["_sources"] = merged_sources
                                final_map[key] = item_copy
                            else:
                                # 即便不替换主体，也合并来源
                                try:
                                    merged_sources = set(list(existed.get("_sources") or set()))
                                    for ssrc in list(src_set):
                                        if ssrc:
                                            merged_sources.add(ssrc)
                                    existed["_sources"] = merged_sources
                                except Exception:
                                    pass
            except Exception:
                # 出现异常则跳过该项
                continue

        # 输出前：将来源集合压缩为展示字符串
        dedup = []
        for v in final_map.values():
            try:
                srcs = sorted([s for s in list(v.get("_sources") or []) if s])
                v.pop("_sources", None)
                if srcs:
                    v["source"] = " · ".join(srcs)
                dedup.append(v)
            except Exception:
                dedup.append(v)

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


def _resolve_qoark_redirect(url: str) -> str:
    """
    解析 pan.qoark.cn 链接的重定向地址
    如果链接是 pan.qoark.cn，则获取重定向后的真实地址
    如果不是，则直接返回原链接
    """
    if not isinstance(url, str) or "pan.qoark.cn" not in url:
        return url
    
    try:
        # 创建新的 session 用于重定向解析
        redirect_session = requests.Session()
        redirect_session.max_redirects = 10
        # 只获取头信息，不获取完整内容（HEAD 请求更快）
        resp = redirect_session.head(url, allow_redirects=True, timeout=10)
        
        # 如果成功重定向到 pan.quark.cn，返回重定向后的地址
        if resp.status_code == 200 and "pan.quark.cn" in resp.url:
            return resp.url
        # 如果 HEAD 请求失败，尝试 GET 请求（某些服务器可能不支持 HEAD）
        elif resp.status_code != 200:
            resp = redirect_session.get(url, allow_redirects=True, timeout=10)
            if resp.status_code == 200 and "pan.quark.cn" in resp.url:
                return resp.url
        
        # 如果重定向失败或没有重定向到 pan.quark.cn，返回原链接
        return url
    except Exception as e:
        # 如果解析失败，返回原链接（避免影响其他功能）
        logging.warning(f"解析 pan.qoark.cn 重定向失败: {str(e)}")
        return url


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
    
    # 解析 pan.qoark.cn 链接的重定向地址
    if shareurl and "pan.qoark.cn" in shareurl:
        shareurl = _resolve_qoark_redirect(shareurl)
        
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
                    
                    # 应用字幕命名规则
                    file["file_name_re"] = apply_subtitle_naming_rule(file["file_name_re"], config_data["task_settings"])
                    current_sequence += 1
            
            return share_detail
        elif regex.get("use_episode_naming") and regex.get("episode_naming"):
            # 剧集命名模式预览
            episode_pattern = regex.get("episode_naming")
            episode_patterns = regex.get("episode_patterns", [])
            
            # 获取用户补充的剧集模式（默认模式由后端内部提供，这里只处理用户补充）
            episode_patterns = []
            raw_patterns = config_data.get("episode_patterns", [])
            for p in raw_patterns:
                if isinstance(p, dict) and p.get("regex"):
                    episode_patterns.append(p)
                elif isinstance(p, str):
                    episode_patterns.append({"regex": p})
                
            
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
                        # 应用字幕命名规则
                        file["file_name_re"] = apply_subtitle_naming_rule(file["file_name_re"], config_data["task_settings"])
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
                    # 应用字幕命名规则
                    item["file_name_re"] = apply_subtitle_naming_rule(item["file_name_re"], config_data["task_settings"])
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
                # 热更新：按保存路径推断任务名集合并触发进度重算
                try:
                    if deleted_count > 0:
                        cursor.execute("SELECT DISTINCT task_name FROM transfer_records WHERE save_path = ?", (save_path,))
                        task_names = [row[0] for row in (cursor.fetchall() or []) if row and row[0]]
                        for tn in task_names:
                            recompute_task_metrics_and_notify(tn)
                except Exception:
                    pass
                
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
    global _crontab_task_process
    
    logging.info(f">>> 开始执行定时运行全部任务")
    
    # 检查上一次任务是否还在运行，如果是则强制终止
    if _crontab_task_process is not None:
        try:
            # 检查进程是否还在运行（poll() 返回 None 表示还在运行，返回退出码表示已结束）
            poll_result = _crontab_task_process.poll()
            if poll_result is None:  # 进程还在运行
                logging.warning(f">>> 检测到上一次定时运行全部任务仍在运行，正在强制终止...")
                _crontab_task_process.kill()
                _crontab_task_process.wait(timeout=10)
                logging.warning(f">>> 已强制终止上一次定时运行全部任务")
            else:  # 进程已结束（正常或异常退出）
                logging.debug(f">>> 上一次定时运行全部任务已结束（退出码: {poll_result}）")
        except ProcessLookupError:
            # 进程不存在（可能已被系统清理）
            logging.debug(f">>> 上一次定时运行全部任务进程已不存在/已自动清理")
        except Exception as e:
            logging.warning(f">>> 检查/终止上一次定时运行全部任务时出错: {e}")
        finally:
            # 无论什么情况，都清除进程对象，确保下次能正常运行
            _crontab_task_process = None

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
                logging.info(f">>> 随机延迟执行 {random_delay} 秒")
                time.sleep(random_delay)
        except (ValueError, TypeError):
            logging.warning(f">>> 延迟执行设置无效: {delay}")

    # 内部函数：执行一次任务
    def execute_task():
        """执行一次定时任务，返回退出码"""
        global _crontab_task_process
        
        process_env = os.environ.copy()
        process_env["PYTHONIOENCODING"] = "utf-8"
        
        # 构建命令
        if isinstance(args, str):
            import shlex
            args_list = shlex.split(args)
            command = [PYTHON_PATH] + args_list
        else:
            command = [PYTHON_PATH] + list(args)
        
        # 启动进程并记录，实时输出日志
        _crontab_task_process = subprocess.Popen(
            command,
            env=process_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,  # 行缓冲，确保实时输出
        )
        
        # 实时读取输出并写入日志，保留完整日志功能
        # 全局变量：跟踪上一次输出是否是空行
        last_was_empty = False
        
        try:
            for line in iter(_crontab_task_process.stdout.readline, ""):
                stripped_line = line.rstrip()
                # 检查是否是空行（去除换行符后是否为空）
                is_empty = not stripped_line
                
                # 如果是空行且上一次也是空行，跳过本次输出
                if is_empty and last_was_empty:
                    continue
                
                # 输出日志（空行也输出，但避免连续空行）
                if is_empty:
                    logging.info("")
                    last_was_empty = True
                else:
                    logging.info(stripped_line)
                    last_was_empty = False
        finally:
            _crontab_task_process.stdout.close()
        
        # 等待进程完成并返回退出码
        returncode = _crontab_task_process.wait()
        return returncode

    # 执行任务，支持重试机制
    max_retries = 1  # 最多重试1次（即总共执行2次）
    retry_count = 0
    returncode = None
    
    try:
        # 第一次执行
        returncode = execute_task()
        
        if returncode == 0:
            logging.info(f">>> 定时运行全部任务执行成功")
        else:
            logging.warning(f">>> 定时运行全部任务执行完成但返回非零退出码: {returncode}")
            
            # 如果返回非零退出码，进行重试
            if retry_count < max_retries:
                retry_count += 1
                logging.warning(f">>> 检测到非零退出码，将在 5 秒后自动重试一次定时运行全部任务...")
                time.sleep(5)  # 重试前等待5秒，避免立即重试
                logging.info(f">>> 开始重试定时运行全部任务")
                
                # 重试执行
                returncode = execute_task()
                
                if returncode == 0:
                    logging.info(f">>> 定时运行全部任务重试成功")
                else:
                    logging.warning(f">>> 定时运行全部任务重试完成但返回非零退出码: {returncode}")
        
        # 定时任务执行完毕后，通知前端更新（包括已转存集数和当日更新标识）
        # 注意：每个转存成功时已经通过 /api/calendar/metrics/sync_task 更新了数据库并发送了通知
        # 这里只需要发送一个总体的完成通知，让前端知道定时任务已完成，可以统一刷新数据
        try:
            notify_calendar_changed('crontab_task_completed')
            logging.debug(f">>> 已通知前端更新已转存集数和当日更新标识（定时任务执行完毕）")
        except Exception as e:
            logging.warning(f">>> 通知前端更新已转存集数和当日更新标识失败: {e}")
    except Exception as e:
        logging.error(f">>> 定时运行全部任务执行异常: {str(e)}")
        import traceback
        logging.error(f">>> 异常堆栈: {traceback.format_exc()}")
    finally:
        # 任务完成，清除进程对象
        _crontab_task_process = None


# 重新加载任务
def reload_tasks():
    global _last_crontab, _last_crontab_delay
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
            misfire_grace_time=None,
            coalesce=True,
            max_instances=1,
        )
        if scheduler.state == 0:
            scheduler.start()
        elif scheduler.state == 2:
            scheduler.resume()
        
        # 读取延迟执行设置
        delay = config_data.get("crontab_delay")
        delay_str = str(delay) if delay is not None else None
        
        # 仅在配置真正改变时输出日志（避免重复输出）
        crontab_changed = (_last_crontab != crontab)
        delay_changed = (_last_crontab_delay != delay_str)
        
        if crontab_changed or delay_changed:
            scheduler_state_map = {0: "停止", 1: "运行", 2: "暂停"}
            logging.info(">>> 重载调度器")
            logging.info(f"调度状态: {scheduler_state_map[scheduler.state]}")
            # 合并输出：定时规则 + 任务说明
            logging.info(f"已启动定时运行全部任务，定时规则 {crontab}")
            # 记录延迟执行设置
            if delay:
                logging.info(f"延迟执行: 0-{delay}秒")
            # 更新记录的值
            _last_crontab = crontab
            _last_crontab_delay = delay_str

        # 每次 reload_tasks() 会调用 scheduler.remove_all_jobs()，会把“按播出时间”的 DateTrigger 一起清掉。
        # 这里参考 __main__ 中的启动流程，在重载后立即重新注册：
        try:
            restart_calendar_refresh_job()
        except Exception:
            pass
        try:
            restart_daily_aired_update_job()
        except Exception:
            pass
        try:
            # 重新计算并注册所有节目/指定节目的“播出状态刷新任务”
            _trigger_airtime_reschedule('startup')
        except Exception:
            pass

        # 不再冗余输出现有任务列表
        return True
    else:
        # crontab 被移除的情况
        if _last_crontab is not None:
            logging.info(">>> no crontab")
            _last_crontab = None
            _last_crontab_delay = None
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
    
    # 自动清理剧集识别规则配置
    cleanup_episode_patterns_config(config_data)

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
    
    # 默认执行周期模式设置
    if "execution_mode" not in config_data:
        config_data["execution_mode"] = "manual"

    # 初始化追剧日历时区模式（原始时区 / 本地时区）
    if "calendar_timezone_mode" not in config_data:
        config_data["calendar_timezone_mode"] = "original"
    # 初始化本地时区（用于 Trakt 播出时间转换与本地时间判断）
    if "local_timezone" not in config_data:
        # 默认按照北京时间处理，也支持用户后续在配置文件中手动修改
        config_data["local_timezone"] = "Asia/Shanghai"

    # 初始化 Trakt 配置（仅存储 Client ID，用于调用节目播出时间 API）
    if "trakt" not in config_data or not isinstance(config_data.get("trakt"), dict):
        config_data["trakt"] = {"client_id": ""}
    else:
        config_data["trakt"].setdefault("client_id", "")

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
    task_names_raw = request.args.get("task_names", "")
    task_name_list = []
    if task_names_raw:
        try:
            decoded_names = json.loads(task_names_raw)
            if isinstance(decoded_names, list):
                task_name_list = [
                    str(name).strip() for name in decoded_names
                    if isinstance(name, (str, bytes)) and str(name).strip()
                ]
        except Exception:
            task_name_list = []
    
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
                task_name_list=task_name_list,
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
        task_name_list=task_name_list,
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

    # 热更新：按记录 id 反查任务名并重算进度
    try:
        if deleted_count > 0:
            cursor = db.conn.cursor()
            placeholders = ','.join(['?'] * len(record_ids)) if record_ids else ''
            if placeholders:
                cursor.execute(f"SELECT DISTINCT task_name FROM transfer_records WHERE id IN ({placeholders})", record_ids)
                task_names = [row[0] for row in (cursor.fetchall() or []) if row and row[0]]
                for tn in task_names:
                    recompute_task_metrics_and_notify(tn)
    except Exception:
        pass

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
    # 热更新：根据记录 id 反查任务名并重算进度
    try:
        if deleted:
            cursor = db.conn.cursor()
            cursor.execute('SELECT task_name FROM transfer_records WHERE id = ?', (record_id,))
            row = cursor.fetchone()
            if row and row[0]:
                recompute_task_metrics_and_notify(row[0])
    except Exception:
        pass

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
            # 热更新：按保存路径推断任务名集合并触发进度重算
            try:
                if deleted_records > 0:
                    cursor.execute("SELECT DISTINCT task_name FROM transfer_records WHERE save_path = ?", (save_path,))
                    task_names = [row[0] for row in (cursor.fetchall() or []) if row and row[0]]
                    for tn in task_names:
                        recompute_task_metrics_and_notify(tn)
            except Exception:
                pass
                
        except Exception as e:
            logging.error(f">>> 删除记录时出错: {str(e)}")
            # 即使删除记录失败，也返回文件删除成功
        
        # 成功后通过 SSE 通知前端进行热更新
        try:
            notify_calendar_changed('reset_folder')
        except Exception:
            pass

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
                # 应用字幕命名规则
                new_name = apply_subtitle_naming_rule(new_name, config_data["task_settings"])
                preview_results.append({
                    "original_name": file["file_name"],
                    "new_name": new_name,
                    "file_id": file["fid"]
                })
                sequence += 1
                
        elif naming_mode == "episode":
            # 剧集命名模式
            # 获取用户补充的剧集模式（默认模式由后端内部提供，这里只处理用户补充）
            episode_patterns = []
            raw_patterns = config_data.get("episode_patterns", [])
            for p in raw_patterns:
                if isinstance(p, dict) and p.get("regex"):
                    episode_patterns.append(p)
                elif isinstance(p, str):
                    episode_patterns.append({"regex": p})
                
            
            # 应用高级过滤词过滤（filterwords 已在函数开头获取）
            if filterwords:
                # 使用高级过滤函数
                filtered_files = advanced_filter_files(filtered_files, filterwords)
                # 标记被过滤的文件
                for item in filtered_files:
                    if item not in filtered_files:
                        item["filtered"] = True
            
            # 处理未被过滤的文件
            for file in filtered_files:
                if not file["dir"] and not file.get("filtered"):  # 只处理未被过滤的非目录文件
                    extension = os.path.splitext(file["file_name"])[1]
                    # 从文件名中提取集号
                    episode_num = extract_episode_number(file["file_name"], episode_patterns=episode_patterns)

                    if episode_num is not None:
                        new_name = pattern.replace("[]", f"{episode_num:02d}") + extension
                        # 应用字幕命名规则
                        new_name = apply_subtitle_naming_rule(new_name, config_data["task_settings"])
                        preview_results.append({
                            "original_name": file["file_name"],
                            "new_name": new_name,
                            "file_id": file["fid"]
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
                        # 应用字幕命名规则
                        new_name = apply_subtitle_naming_rule(new_name, config_data["task_settings"])
                    else:
                        # 如果没有提供替换表达式，则检查是否匹配
                        if re.search(pattern, file["file_name"]):
                            new_name = file["file_name"]  # 匹配但不替换
                            # 应用字幕命名规则
                            new_name = apply_subtitle_naming_rule(new_name, config_data["task_settings"])
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
        
        # 如果有成功重命名的文件，触发SSE通知
        if success_count > 0:
            notify_calendar_changed('batch_rename_completed')
        
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
# 清除任务列表缓存（在数据更新时调用）
def clear_calendar_tasks_cache():
    """清除任务列表缓存，强制下次请求重新加载数据"""
    global _calendar_tasks_cache
    with _calendar_tasks_cache_lock:
        _calendar_tasks_cache.clear()

# 追剧日历剧集数据缓存机制
_calendar_episodes_cache = {}
_calendar_episodes_cache_lock = Lock()
_calendar_episodes_cache_ttl = 30  # 缓存30秒

def clear_calendar_episodes_cache():
    """清除剧集数据缓存，强制下次请求重新加载数据"""
    global _calendar_episodes_cache
    with _calendar_episodes_cache_lock:
        _calendar_episodes_cache.clear()

def clear_all_calendar_cache():
    """清除所有追剧日历相关缓存（任务列表和剧集数据）"""
    clear_calendar_tasks_cache()
    clear_calendar_episodes_cache()

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

# 追剧日历API路由 - 缓存机制
_calendar_tasks_cache = {}
_calendar_tasks_cache_lock = Lock()
_calendar_tasks_cache_ttl = 30  # 缓存30秒
_last_sync_time = 0
_sync_interval = 300  # 同步操作每5分钟执行一次

@app.route("/api/calendar/tasks")
def get_calendar_tasks():
    """获取追剧日历任务信息（优化版：添加缓存和减少同步频率）"""
    global _calendar_tasks_cache, _last_sync_time
    
    try:
        # 检查缓存是否有效
        current_time = time.time()
        cache_key = 'tasks_data'
        
        with _calendar_tasks_cache_lock:
            # 检查缓存是否存在且未过期
            if cache_key in _calendar_tasks_cache:
                cache_data, cache_time = _calendar_tasks_cache[cache_key]
                if current_time - cache_time < _calendar_tasks_cache_ttl:
                    # 缓存有效，直接返回
                    return jsonify(cache_data)
        
        # 获取任务列表
        tasks = config_data.get('tasklist', [])
        
        # 获取任务的最近转存文件信息
        db = RecordDB()
        cursor = db.conn.cursor()
        
        # 获取所有任务的最新转存时间（优化：使用单个查询）
        query = """
        SELECT task_name, MAX(transfer_time) as latest_transfer_time
        FROM transfer_records
        WHERE task_name NOT IN ('rename', 'undo_rename')
        GROUP BY task_name
        """
        cursor.execute(query)
        latest_times = cursor.fetchall()
        
        task_latest_files = {}
        
        # 批量查询文件信息，减少数据库查询次数
        for task_name, latest_time in latest_times:
            if latest_time:
                # 获取该任务在最新转存时间附近的所有文件
                time_window = 60000  # 60秒的时间窗口（毫秒）
                query = """
                SELECT renamed_to, original_name, transfer_time, modify_date
                FROM transfer_records
                WHERE task_name = ? AND transfer_time >= ? AND transfer_time <= ?
                ORDER BY id DESC
                LIMIT 10
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
        
        try:
            extractor = TaskExtractor()
            tasks_info = extractor.extract_all_tasks_info(tasks, task_latest_files)
        except Exception as e:
            raise
        
        # 优化：减少同步操作频率，只在必要时执行（每5分钟最多执行一次）
        # 这样可以避免每次请求都执行耗时的同步操作
        should_sync = (current_time - _last_sync_time) >= _sync_interval
        
        if should_sync:
            try:
                # 首先同步数据库绑定关系到任务配置
                sync_task_config_with_database_bindings()
                
                # 同步内容类型数据
                sync_content_type_between_config_and_database()
                
                _last_sync_time = current_time
            except Exception as sync_error:
                # 同步失败不影响数据返回，只记录日志
                logging.debug(f"同步操作失败（不影响数据返回）: {sync_error}")
        
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

            # 计算有效“最新季”：若第一集未定档或在未来，则回退到已播季
            def determine_effective_latest_season(tmdb_id, fallback_latest):
                try:
                    cur = cal_db.conn.cursor()
                    cur.execute("""
                        SELECT season_number,
                               MIN(
                                   COALESCE(
                                       NULLIF(air_date_local, ''),
                                       NULLIF(air_date, '')
                                   )
                               ) as first_air
                        FROM episodes
                        WHERE tmdb_id=?
                        GROUP BY season_number
                        ORDER BY season_number DESC
                    """, (int(tmdb_id),))
                    rows = cur.fetchall() or []
                    from datetime import datetime as _dt
                    today = _dt.utcnow().strftime('%Y-%m-%d')
                    for sn, first_air in rows:
                        try:
                            if not sn:
                                continue
                            fa = (first_air or '').strip()
                            if not fa:
                                # 无首播日期视为未定档季，跳过
                                continue
                            if fa > today:
                                # 首集在未来，视为未开播季，跳过
                                continue
                            return int(sn)
                        except Exception:
                            continue
                except Exception:
                    pass
                try:
                    if fallback_latest and int(fallback_latest) > 0:
                        return int(fallback_latest)
                except Exception:
                    pass
                return None

            for idx, t in enumerate(tasks_info):
                # 1) 优先通过 calendar_info.match.tmdb_id 查找
                raw = tasks[idx] if idx < len(tasks) else None
                cal = (raw or {}).get('calendar_info') or {}
                match = cal.get('match') or {}
                tmdb_id = match.get('tmdb_id') or (cal.get('tmdb_id') if isinstance(cal, dict) else None)
                legacy_match = raw.get('match') if isinstance(raw.get('match'), dict) else {}
                
                task_name = t.get('task_name', '').strip()
                matched_show = None
                
                # 优先通过任务的 TMDB 匹配结果查找节目
                if tmdb_id:
                    try:
                        show = cal_db.get_show(int(tmdb_id)) or {}
                        if show:
                            matched_show = show
                    except Exception as e:
                        pass
                
                # 2) 如果找到匹配的节目，检查是否需要建立绑定关系
                if matched_show:
                    # 检查任务是否已经绑定到该节目，避免重复绑定
                    bound_tasks = cal_db.get_bound_tasks_for_show(matched_show['tmdb_id'])
                    # 清理空格，确保比较准确
                    bound_tasks_clean = [task.strip() for task in bound_tasks if task.strip()]
                    needs_binding = task_name not in bound_tasks_clean
                                        
                    if needs_binding:
                        # 建立任务与节目的绑定关系，同时设置内容类型
                        task_content_type = t.get('content_type', '')
                        cal_db.bind_task_and_content_type(matched_show['tmdb_id'], task_name, task_content_type)
                    
                    t['match_tmdb_id'] = matched_show['tmdb_id']
                    t['matched_show_name'] = matched_show['name']
                    t['matched_year'] = matched_show['year']
                    t['matched_poster_local_path'] = matched_show['poster_local_path']
                    # 提供最新季数用于前端展示：优先保留用户设定的匹配季，其次依据有效最新季规则
                    try:
                        manual_season = (
                            match.get('latest_season_number')
                            or cal.get('latest_season_number')
                            or legacy_match.get('latest_season_number')
                            or t.get('matched_latest_season_number')
                        )
                        if manual_season:
                            t['matched_latest_season_number'] = int(manual_season)
                        else:
                            effective_latest = determine_effective_latest_season(
                                matched_show['tmdb_id'],
                                matched_show.get('latest_season_number')
                            )
                            if effective_latest:
                                t['matched_latest_season_number'] = effective_latest
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
                    # 从数据库获取 local_air_time 和 air_date_offset 并添加到 calendar_info 中供前端读取
                    try:
                        schedule = cal_db.get_show_air_schedule(int(matched_show.get('tmdb_id'))) or {}
                        local_air_time = schedule.get('local_air_time') or ''
                        air_date_offset = schedule.get('air_date_offset') or 0
                        if not t.get('calendar_info'):
                            t['calendar_info'] = {}
                        t['calendar_info']['local_air_time'] = local_air_time
                        t['calendar_info']['air_date_offset'] = air_date_offset
                    except Exception:
                        pass
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
                                if full_show:
                                    manual_season = (
                                        match.get('latest_season_number')
                                        or cal.get('latest_season_number')
                                        or legacy_match.get('latest_season_number')
                                        or t.get('matched_latest_season_number')
                                    )
                                    if manual_season:
                                        t['matched_latest_season_number'] = int(manual_season)
                                    else:
                                        effective_latest = determine_effective_latest_season(
                                            int(bound_show[0]),
                                            full_show.get('latest_season_number')
                                        )
                                        if effective_latest:
                                            t['matched_latest_season_number'] = effective_latest
                                    # 从完整信息中获取 local_air_time 和 air_date_offset
                                    schedule = cal_db.get_show_air_schedule(int(bound_show[0])) or {}
                                    local_air_time = schedule.get('local_air_time') or ''
                                    air_date_offset = schedule.get('air_date_offset') or 0
                                    if not t.get('calendar_info'):
                                        t['calendar_info'] = {}
                                    t['calendar_info']['local_air_time'] = local_air_time
                                    t['calendar_info']['air_date_offset'] = air_date_offset
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
                            
                        else:
                            t['match_tmdb_id'] = None
                            t['matched_show_name'] = ''
                            t['matched_year'] = ''
                            t['matched_poster_local_path'] = ''
                            t['matched_content_type'] = t.get('content_type', '')
                    except Exception as e:
                        t['match_tmdb_id'] = None
                        t['matched_show_name'] = ''
                        t['matched_year'] = ''
                        t['matched_poster_local_path'] = ''
                        t['matched_content_type'] = t.get('content_type', '')
                enriched.append(t)
            tasks_info = enriched
        except Exception as _e:
            # 若补充失败，不影响主流程
            pass

        # 构建返回数据
        result_data = {
            'success': True,
            'data': {
                # 返回全部任务（包括未匹配/未提取剧名的），用于内容管理页显示
                'tasks': enrich_tasks_with_calendar_meta(tasks_info),
                'content_types': extractor.get_content_types_with_content([t for t in tasks_info if t.get('show_name')])
            }
        }
        
        # 更新缓存
        with _calendar_tasks_cache_lock:
            _calendar_tasks_cache[cache_key] = (result_data, current_time)
        
        return jsonify(result_data)
    except Exception as e:
        # 如果出错，尝试返回缓存数据（如果有）
        with _calendar_tasks_cache_lock:
            if cache_key in _calendar_tasks_cache:
                cache_data, _ = _calendar_tasks_cache[cache_key]
                logging.warning(f'获取追剧日历任务信息失败，返回缓存数据: {str(e)}')
                return jsonify(cache_data)
        
        # 没有缓存，返回错误
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
                show_name = extracted.get('show_name', '')
                year = extracted.get('year') or None
                search_result = None
                match_type = None  # 'exact' 或 'name_only'
                
                # 优先级匹配逻辑：先尝试精准匹配（名称+年份），无结果时再按名称匹配
                if year:
                    # 第一步：尝试精准匹配（名称+年份）
                    search_results = tmdb_service.search_tv_show_all(show_name, year)
                    if search_results:
                        # 从结果中找到年份完全匹配的
                        for result in search_results:
                            result_year = None
                            first_air_date = result.get('first_air_date', '')
                            if first_air_date:
                                try:
                                    result_year = first_air_date[:4] if len(first_air_date) >= 4 else None
                                except Exception:
                                    pass
                            
                            if result_year == year and result.get('id'):
                                search_result = result
                                match_type = 'exact'
                                logging.debug(f"TMDB 匹配成功: 任务名称={task.get('taskname', '')}, 节目名称={show_name}, 年份={year}")
                                break
                
                # 第二步：如果精准匹配没有结果，尝试仅名称匹配
                if not search_result:
                    search_results = tmdb_service.search_tv_show_all(show_name, None)
                    if search_results:
                        # 如果有年份信息，选择年份最接近的结果
                        if year:
                            try:
                                target_year = int(year)
                                best_match = None
                                min_year_diff = float('inf')
                                
                                for result in search_results:
                                    if not result.get('id'):
                                        continue
                                    result_year = None
                                    first_air_date = result.get('first_air_date', '')
                                    if first_air_date:
                                        try:
                                            result_year = int(first_air_date[:4]) if len(first_air_date) >= 4 else None
                                        except Exception:
                                            pass
                                    
                                    if result_year:
                                        year_diff = abs(result_year - target_year)
                                        if year_diff < min_year_diff:
                                            min_year_diff = year_diff
                                            best_match = result
                                
                                if best_match:
                                    search_result = best_match
                                    match_type = 'name_only'
                                    matched_year = best_match.get('first_air_date', '')[:4] if best_match.get('first_air_date') else '未知'
                                    logging.debug(f"TMDB 匹配成功（年份不一致）: 任务名称={task.get('taskname', '')}, 节目名称={show_name}, 期望年份={year}, 匹配年份={matched_year}, 年份差异={min_year_diff}")
                            except (ValueError, TypeError) as e:
                                # 年份解析失败，使用第一个结果
                                search_result = search_results[0] if search_results else None
                                match_type = 'name_only'
                                logging.debug(f"TMDB 匹配成功（年份解析失败）: 任务名称={task.get('taskname', '')}, 节目名称={show_name}, 错误={e}")
                        else:
                            # 没有年份信息，直接使用第一个结果
                            search_result = search_results[0] if search_results else None
                            match_type = 'name_only'
                            logging.debug(f"TMDB 匹配成功（无年份信息）: 任务名称={task.get('taskname', '')}, 节目名称={show_name}")
                
                # 如果找到了匹配结果，继续处理
                if search_result and search_result.get('id'):
                    tmdb_id = search_result['id']
                    details = tmdb_service.get_tv_show_details(tmdb_id) or {}
                    seasons = details.get('seasons', [])
                    
                    # 选择已播出的季中最新的一季
                    # 使用今天的日期判断季是否已播出（现在集的播出日期已经是本地日期，不需要通过播出集数刷新时间限制）
                    from datetime import datetime as _dt
                    today_date = _dt.now().date()
                    latest_season_number = 0
                    latest_air_date = None
                    
                    for s in seasons:
                        sn = s.get('season_number', 0)
                        air_date = s.get('air_date')
                        
                        # 只考虑已播出的季（有air_date且早于或等于今天的日期）
                        if sn and sn > 0 and air_date:  # 排除第0季（特殊季）
                            try:
                                season_air_date = datetime.strptime(air_date, '%Y-%m-%d').date()
                                if season_air_date <= today_date:
                                    # 选择播出日期最新的季
                                    if latest_air_date is None or season_air_date > latest_air_date:
                                        latest_season_number = sn
                                        latest_air_date = season_air_date
                            except (ValueError, TypeError):
                                # 日期格式错误，跳过
                                continue
                    
                    # 如果没有找到已播出的季，回退到第1季
                    if latest_season_number == 0:
                        latest_season_number = 1

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
                else:
                    # 没有找到任何匹配结果
                    year_info = f", 年份={year}" if year else ""
                    logging.warning(f"TMDB 匹配失败 - 任务名称: {task.get('taskname', '')}, 搜索的节目名称: {show_name}{year_info}, 失败原因: TMDB 搜索无结果，可能节目名称不匹配或 TMDB 中不存在该节目")
                        
            except Exception as e:
                # 捕获异常并输出详细的失败原因
                task_name = task.get('taskname') or task.get('task_name') or ''
                show_name = extracted.get('show_name', '')
                year = extracted.get('year') or None
                year_info = f", 年份={year}" if year else ""
                import traceback
                error_detail = str(e)
                error_type = type(e).__name__
                # 获取异常堆栈信息的前几行，用于诊断
                tb_lines = traceback.format_exc().split('\n')
                tb_summary = '; '.join([line.strip() for line in tb_lines[:3] if line.strip()][:2])
                logging.warning(f"TMDB 匹配失败 - 任务名称: {task_name}, 搜索的节目名称: {show_name}{year_info}, 失败原因: 异常类型={error_type}, 错误信息={error_detail}, 堆栈摘要={tb_summary}")

        # 如果现在有 TMDB ID，下载海报并更新数据库，并在可能的情况下同步 Trakt 播出时间信息
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

                # 获取现有的绑定关系和内容类型，避免被覆盖
                existing_show = cal_db.get_show(int(tmdb_id))
                existing_bound_tasks = existing_show.get('bound_task_names', '') if existing_show else ''
                existing_content_type = existing_show.get('content_type', '') if existing_show else ''
                existing_poster_path = existing_show.get('poster_local_path', '') if existing_show else ''
                is_custom_poster = bool(existing_show.get('is_custom_poster', 0)) if existing_show else False
                
                poster_local_path = ''
                if poster_path:
                    poster_local_path = download_poster_local(poster_path, int(tmdb_id), existing_poster_path, is_custom_poster)
                cal_db.upsert_show(int(tmdb_id), name, first_air, status, poster_local_path, int(latest_season_number), 0, existing_bound_tasks, existing_content_type)
                
                # 如果海报路径发生变化，触发孤立文件清理
                if poster_local_path and poster_local_path != existing_poster_path:
                    try:
                        cleanup_orphaned_posters()
                    except Exception as e:
                        logging.warning(f"清理孤立海报失败: {e}")
                
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
                    # 根因修复：清理本地该季中 TMDB 不存在的多余集
                    try:
                        valid_eps = []
                        for _ep in episodes:
                            try:
                                n = int(_ep.get('episode_number') or 0)
                                if n > 0:
                                    valid_eps.append(n)
                            except Exception:
                                pass
                        cal_db.prune_season_episodes_not_in(int(tmdb_id), int(latest_season_number), valid_eps)
                    except Exception:
                        pass
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

                    # 使用 Trakt 同步节目级播出时间（如已配置 Client ID）
                    try:
                        trakt_cfg = config_data.get("trakt", {}) if isinstance(config_data, dict) else {}
                        client_id = (trakt_cfg.get("client_id") or "").strip()
                        if client_id:
                            local_tz = config_data.get("local_timezone", "Asia/Shanghai")
                            tsvc = TraktService(client_id=client_id)
                            if tsvc.is_configured():
                                logging.debug(f"开始同步 Trakt 播出时间 tmdb_id={tmdb_id}")
                                show_info = tsvc.get_show_by_tmdb_id(int(tmdb_id))
                                if show_info and show_info.get("trakt_id"):
                                    trakt_id = show_info.get("trakt_id")
                                    logging.debug(f"找到 Trakt 节目 tmdb_id={tmdb_id}, trakt_id={trakt_id}")
                                    # 1) 节目级播出时间 -> 本地时区 HH:MM，写入 shows.local_air_time
                                    airtime = tsvc.get_show_airtime(trakt_id)
                                    if airtime and airtime.get("aired_time") and airtime.get("timezone"):
                                        local_air_time = tsvc.convert_show_airtime_to_local(
                                            airtime.get("aired_time"), airtime.get("timezone"), local_tz
                                        ) or airtime.get("aired_time")
                                        logging.debug(f"获取到播出时间 tmdb_id={tmdb_id}, aired_time={airtime.get('aired_time')}, timezone={airtime.get('timezone')}, local_air_time={local_air_time}")
                                        try:
                                            from sdk.db import CalendarDB as _CalDB
                                            _CalDB().update_show_air_schedule(
                                                int(tmdb_id),
                                                local_air_time=local_air_time or "",
                                                air_time_source=airtime.get("aired_time") or "",
                                                air_timezone=airtime.get("timezone") or "",
                                            )
                                            logging.debug(f"成功写入播出时间到数据库 tmdb_id={tmdb_id}")
                                        except Exception as _db_err:
                                            logging.warning(f"写入播出时间到数据库失败 tmdb_id={tmdb_id}: {_db_err}")
                                    else:
                                        logging.warning(f"未获取到完整的播出时间信息 tmdb_id={tmdb_id}, airtime={airtime}")
                                else:
                                    logging.debug(f"未找到 Trakt 节目映射 tmdb_id={tmdb_id}, show_info={show_info}")
                            else:
                                logging.debug(f"TraktService 未配置 tmdb_id={tmdb_id}")
                        else:
                            logging.debug(f"Trakt Client ID 未配置，跳过同步 tmdb_id={tmdb_id}")
                    except Exception as _te_outer:
                        logging.warning(f"同步 Trakt 播出时间失败 tmdb_id={tmdb_id}: {_te_outer}")
                        import traceback
                        logging.debug(f"异常堆栈: {traceback.format_exc()}")
                    
                    # 2) 如果有 Trakt 的源时间/时区，计算每集的本地播出日期并更新 air_date_local
                    update_episodes_air_date_local(cal_db, int(tmdb_id), int(latest_season_number), episodes)
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

            # 获取现有的绑定关系和内容类型，避免被覆盖
            existing_show = cal_db.get_show(int(tmdb_id))
            existing_bound_tasks = existing_show.get('bound_task_names', '') if existing_show else ''
            existing_content_type = existing_show.get('content_type', '') if existing_show else ''
            existing_poster_path = existing_show.get('poster_local_path', '') if existing_show else ''
            is_custom_poster = bool(existing_show.get('is_custom_poster', 0)) if existing_show else False
            
            poster_local_path = ''
            if poster_path:
                poster_local_path = download_poster_local(poster_path, int(tmdb_id), existing_poster_path, is_custom_poster)
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
                # 根因修复：清理本地该季中 TMDB 不存在的多余集
                try:
                    valid_eps = []
                    for _ep in episodes:
                        try:
                            n = int(_ep.get('episode_number') or 0)
                            if n > 0:
                                valid_eps.append(n)
                        except Exception:
                            pass
                    cal_db.prune_season_episodes_not_in(int(tmdb_id), int(latest_season_number), valid_eps)
                except Exception:
                    pass
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
                # 如果有 Trakt 的源时间/时区，计算每集的本地播出日期并更新 air_date_local
                update_episodes_air_date_local(cal_db, int(tmdb_id), int(latest_season_number), episodes)
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


def update_episodes_air_date_local(cal_db: CalendarDB, tmdb_id: int, season_number: int, episodes: list):
    """
    辅助函数：如果有 Trakt 的源时间/时区，计算每集的本地播出日期并更新 air_date_local
    如果用户手动设置了 air_date_offset，优先使用该偏移值而不是重新计算
    
    Args:
        cal_db: CalendarDB 实例
        tmdb_id: 节目 TMDB ID
        season_number: 季号
        episodes: 集列表（包含 air_date 字段）
    """
    try:
        # 从 shows 表读取 Trakt 的源时间/时区和用户手动设置的偏移值
        show = cal_db.get_show(int(tmdb_id))
        if not show:
            return
        air_time_source = show.get('air_time_source') or ''
        air_timezone = show.get('air_timezone') or ''
        # 获取用户手动设置的日期偏移值（如果存在）
        schedule = cal_db.get_show_air_schedule(int(tmdb_id)) or {}
        manual_air_date_offset = schedule.get('air_date_offset')
        local_air_time = schedule.get('local_air_time') or ''
        # 获取是否手动设置的标记（只有用户在 WebUI 编辑元数据页面修改并保存后才为 True）
        air_date_offset_manually_set = schedule.get('air_date_offset_manually_set', False)
        
        # 重要：判断是否应该自动计算 air_date_offset
        # 判断标准：
        # 1. 默认 is_manually_set 为 False
        # 2. 只有当 air_date_offset_manually_set 为 True 时（用户在 WebUI 编辑元数据页面修改并保存过），才认为是手动设置的
        # 3. 这样初始化时即使 air_date_offset 是 0，也会自动计算正确的偏移值
        is_manually_set = bool(air_date_offset_manually_set)
        
        # 如果 manual_air_date_offset 是 None，设置为 0 用于计算
        if manual_air_date_offset is None:
            manual_air_date_offset = 0
        
        # 如果用户手动设置了偏移值（且没有时区信息），直接使用偏移值计算
        if not air_time_source or not air_timezone:
            if is_manually_set:
                # 用户手动设置了偏移值，使用偏移值计算 air_date_local
                for ep in episodes:
                    ep_air_date = ep.get('air_date') or ''
                    ep_number = ep.get('episode_number') or 0
                    if not ep_number:
                        continue
                    if ep_air_date:
                        try:
                            from datetime import datetime as _dt, timedelta as _td
                            src_date = _dt.strptime(ep_air_date, '%Y-%m-%d').date()
                            adjusted_date = src_date + _td(days=manual_air_date_offset)
                            cal_db.update_episode_air_date_local(
                                int(tmdb_id),
                                int(season_number),
                                int(ep_number),
                                adjusted_date.strftime('%Y-%m-%d')
                            )
                        except Exception as _e_sync:
                            logging.debug(f"使用手动偏移值更新 air_date_local 失败 tmdb_id={tmdb_id}, season={season_number}, ep={ep_number}: {_e_sync}")
                    else:
                        # 如果 air_date 为空，清空 air_date_local
                        try:
                            cal_db.update_episode_air_date_local(
                                int(tmdb_id),
                                int(season_number),
                                int(ep_number),
                                ''
                            )
                        except Exception as _e_clear:
                            logging.debug(f"清空 air_date_local 失败 tmdb_id={tmdb_id}, season={season_number}, ep={ep_number}: {_e_clear}")
                return
            else:
                # 没有时区信息且没有手动偏移值，将 air_date_local 设置为与 air_date 相同的值
                for ep in episodes:
                    ep_air_date = ep.get('air_date') or ''
                    ep_number = ep.get('episode_number') or 0
                    if not ep_number:
                        continue
                    if ep_air_date:
                        try:
                            cal_db.update_episode_air_date_local(
                                int(tmdb_id),
                                int(season_number),
                                int(ep_number),
                                ep_air_date
                            )
                        except Exception as _e_sync:
                            logging.debug(f"同步 air_date 到 air_date_local 失败 tmdb_id={tmdb_id}, season={season_number}, ep={ep_number}: {_e_sync}")
                    else:
                        # 如果 air_date 为空，清空 air_date_local
                        try:
                            cal_db.update_episode_air_date_local(
                                int(tmdb_id),
                                int(season_number),
                                int(ep_number),
                                ''
                            )
                        except Exception as _e_clear:
                            logging.debug(f"清空 air_date_local 失败 tmdb_id={tmdb_id}, season={season_number}, ep={ep_number}: {_e_clear}")
                return
        
        # 获取本地时区配置
        local_tz = config_data.get("local_timezone", "Asia/Shanghai")
        
        try:
            from zoneinfo import ZoneInfo as _ZoneInfo
            from datetime import datetime as _dt, time as _dtime, timedelta as _td
            date_offset = None  # 用于存储计算出的日期偏移
            for ep in episodes:
                ep_air_date = ep.get('air_date') or ''
                ep_number = ep.get('episode_number') or 0
                if not ep_number:
                    continue
                if ep_air_date:
                    try:
                        # 解析 TMDB air_date 作为源时区日期
                        src_date = _dt.strptime(ep_air_date, '%Y-%m-%d').date()
                        
                        # 如果用户手动设置了偏移值（包括手动设置为0），优先使用手动偏移值
                        if is_manually_set:
                            adjusted_date = src_date + _td(days=manual_air_date_offset)
                            air_date_local = adjusted_date.strftime('%Y-%m-%d')
                            date_offset = manual_air_date_offset
                        else:
                            # 否则通过时区转换计算
                            # 解析源时区时间
                            hh, mm = [int(x) for x in air_time_source.split(':')]
                            src_time = _dtime(hour=hh, minute=mm)
                            # 组合成源时区 datetime
                            src_dt = _dt.combine(src_date, src_time)
                            src_dt = src_dt.replace(tzinfo=_ZoneInfo(air_timezone))
                            # 转换到本地时区
                            local_dt = src_dt.astimezone(_ZoneInfo(local_tz))
                            air_date_local = local_dt.strftime('%Y-%m-%d')
                            # 计算日期偏移（如果还未计算）
                            if date_offset is None:
                                local_date = _dt.strptime(air_date_local, '%Y-%m-%d').date()
                                date_offset = (local_date - src_date).days
                        
                        # 更新数据库
                        cal_db.update_episode_air_date_local(
                            int(tmdb_id),
                            int(season_number),
                            int(ep_number),
                            air_date_local
                        )
                    except Exception as _e_local:
                        logging.debug(f"计算本地播出日期失败 tmdb_id={tmdb_id}, season={season_number}, ep={ep_number}: {_e_local}")
                else:
                    # 如果 air_date 为空，清空 air_date_local
                    try:
                        cal_db.update_episode_air_date_local(
                            int(tmdb_id),
                            int(season_number),
                            int(ep_number),
                            ''
                        )
                    except Exception as _e_clear:
                        logging.debug(f"清空 air_date_local 失败 tmdb_id={tmdb_id}, season={season_number}, ep={ep_number}: {_e_clear}")
            
            # 如果计算出了日期偏移且用户没有手动设置，存储到数据库
            # 这里的 date_offset 是通过时区转换计算出的偏移值（例如：美东周日 21:00 -> 北京时间周一 10:00，偏移 +1）
            if date_offset is not None and not is_manually_set:
                try:
                    cal_db.update_show_air_schedule(
                        int(tmdb_id),
                        air_date_offset=date_offset
                    )
                except Exception as _offset_err:
                    logging.debug(f"存储日期偏移失败 tmdb_id={tmdb_id}: {_offset_err}")
        except Exception as _e_batch:
            logging.debug(f"批量更新本地播出日期失败 tmdb_id={tmdb_id}, season={season_number}: {_e_batch}")
    except Exception as _e:
        logging.debug(f"更新本地播出日期失败 tmdb_id={tmdb_id}: {_e}")


def sync_trakt_airtime_for_all_shows():
    """为所有已有节目同步一次 Trakt 播出时间（首次配置 Client ID 时使用）"""
    try:
        trakt_cfg = config_data.get("trakt", {}) if isinstance(config_data, dict) else {}
        client_id = (trakt_cfg.get("client_id") or "").strip()
        if not client_id:
            logging.debug("Trakt Client ID 未配置，跳过全量播出时间同步")
            return
        local_tz = config_data.get("local_timezone", "Asia/Shanghai")
        tsvc = TraktService(client_id=client_id)
        if not tsvc.is_configured():
            logging.debug("TraktService 未配置，跳过全量播出时间同步")
            return

        cal_db = CalendarDB()
        shows = cal_db.get_all_shows() or []
        if not shows:
            logging.debug("当前无节目可同步 Trakt 播出时间")
            return

        total = len(shows)
        synced = 0
        for show in shows:
            tmdb_id = show.get("tmdb_id")
            if tmdb_id is None or tmdb_id == "":
                continue
            try:
                tmdb_id_int = int(tmdb_id)
            except Exception:
                continue

            try:
                show_info = tsvc.get_show_by_tmdb_id(tmdb_id_int)
                if not show_info or not show_info.get("trakt_id"):
                    logging.debug(f"未找到 Trakt 节目映射 tmdb_id={tmdb_id_int}")
                    continue
                trakt_id = show_info.get("trakt_id")
                airtime = tsvc.get_show_airtime(trakt_id)
                if not airtime or not airtime.get("aired_time") or not airtime.get("timezone"):
                    logging.debug(f"Trakt 未返回播出时间 tmdb_id={tmdb_id_int}")
                    continue

                local_air_time = tsvc.convert_show_airtime_to_local(
                    airtime.get("aired_time"), airtime.get("timezone"), local_tz
                ) or airtime.get("aired_time")

                cal_db.update_show_air_schedule(
                    tmdb_id_int,
                    local_air_time=local_air_time or "",
                    air_time_source=airtime.get("aired_time") or "",
                    air_timezone=airtime.get("timezone") or "",
                )

                # 同步已有集的本地播出日期
                try:
                    cursor = cal_db.conn.cursor()
                    cursor.execute(
                        "SELECT season_number, episode_number, air_date FROM episodes WHERE tmdb_id=?",
                        (tmdb_id_int,),
                    )
                    rows = cursor.fetchall()
                    episodes_by_season = {}
                    for season_number, ep_number, air_date in rows:
                        episodes_by_season.setdefault(season_number, []).append(
                            {"episode_number": ep_number, "air_date": air_date}
                        )
                    for season_number, eps in episodes_by_season.items():
                        update_episodes_air_date_local(cal_db, tmdb_id_int, int(season_number), eps)
                except Exception as _ep_err:
                    logging.debug(f"同步本地播出日期失败 tmdb_id={tmdb_id_int}: {_ep_err}")

                synced += 1
            except Exception as _single_err:
                logging.debug(f"同步单个节目 Trakt 播出时间失败 tmdb_id={tmdb_id_int}: {_single_err}")

        if synced > 0:
            try:
                notify_calendar_changed('trakt_airtime_synced')
            except Exception:
                pass
            try:
                _trigger_airtime_reschedule('trakt_airtime_synced')
            except Exception:
                pass
            # 首次全量同步后立即重算播出/转存进度并热更新
            try:
                recompute_all_seasons_aired_daily()
            except Exception as _recalc_err:
                logging.debug(f"Trakt 全量同步后重算进度失败: {_recalc_err}")

        logging.info(f"Trakt 播出时间全量同步完成，成功 {synced}/{total}")
    except Exception as e:
        logging.warning(f"Trakt 播出时间全量同步失败: {e}")


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

def cleanup_orphaned_posters():
    """清理孤立的旧海报文件（不在数据库中的海报文件）"""
    try:
        from app.sdk.db import CalendarDB
        cal_db = CalendarDB()
        
        # 获取数据库中所有正在使用的海报路径
        shows = cal_db.get_all_shows()
        used_posters = set()
        for show in shows:
            poster_path = show.get('poster_local_path', '')
            if poster_path and poster_path.startswith('/cache/images/'):
                poster_name = poster_path.replace('/cache/images/', '')
                used_posters.add(poster_name)
        
        # 扫描缓存目录中的所有文件
        if os.path.exists(CACHE_IMAGES_DIR):
            orphaned_files = []
            for filename in os.listdir(CACHE_IMAGES_DIR):
                if filename not in used_posters:
                    file_path = os.path.join(CACHE_IMAGES_DIR, filename)
                    if os.path.isfile(file_path):
                        orphaned_files.append(file_path)
            
            # 删除孤立文件
            deleted_count = 0
            for file_path in orphaned_files:
                try:
                    os.remove(file_path)
                    deleted_count += 1
                except Exception as e:
                    logging.warning(f"删除孤立海报文件失败: {e}")
            
            # 只在删除了文件时才输出日志
            if deleted_count > 0:
                logging.info(f"海报清理完成，删除了 {deleted_count} 个孤立文件")
            return deleted_count > 0
        
        return False
    except Exception as e:
        logging.error(f"清理孤立海报失败: {e}")
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
                    # 获取现有海报信息
                    existing_poster_path = show.get('poster_local_path', '')
                    is_custom_poster = bool(show.get('is_custom_poster', 0))
                    # 下载新海报
                    poster_local_path = download_poster_local(new_poster_path, int(tmdb_id), existing_poster_path, is_custom_poster)
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
                
        # 重新下载完成后，清理孤立的旧海报文件
        try:
            cleanup_orphaned_posters()
        except Exception as e:
            logging.warning(f"清理孤立海报失败: {e}")
        
        return success_count > 0
        
    except Exception as e:
        logging.error(f"重新下载所有海报失败: {e}")
        return False

def download_poster_local(poster_path: str, tmdb_id: int = None, existing_poster_path: str = None, is_custom_poster: bool = False) -> str:
    """下载 TMDB 海报到本地 static/cache/images 下，等比缩放为宽400px，返回相对路径。
    
    Args:
        poster_path: TMDB海报路径
        tmdb_id: TMDB ID，用于检查自定义海报标记
        existing_poster_path: 现有的海报路径，用于检查是否需要更新
        is_custom_poster: 是否为自定义海报标记
    """
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
        
        # 如果提供了现有海报路径，检查是否需要更新
        if existing_poster_path and tmdb_id is not None:
            existing_file_name = existing_poster_path.replace('/cache/images/', '')
            new_file_name = safe_name
            
            # 如果文件名不同，说明TMDB海报有变更
            if existing_file_name != new_file_name:
                # 检查是否有自定义海报标记
                if is_custom_poster:
                    return existing_poster_path
                else:
                    # 删除旧文件
                    try:
                        old_file_path = os.path.join(folder, existing_file_name)
                        if os.path.exists(old_file_path):
                            os.remove(old_file_path)
                    except Exception as e:
                        logging.warning(f"删除旧海报文件失败: {e}")
        
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

        try:
            show_name_for_log = show.get('name') or f"tmdb:{tmdb_id}"
            logging.info(f">>> 开始执行刷新最新季元数据 [{show_name_for_log}]")
        except Exception:
            pass

        latest_season = int(show['latest_season_number'])
        season = tmdb_service.get_tv_show_episodes(tmdb_id, latest_season) or {}
        episodes = season.get('episodes', []) or []

        from time import time as _now
        now_ts = int(_now())

        any_written = False
        # 根因修复：清理本地该季中 TMDB 不存在的多余集
        try:
            valid_eps = []
            for _ep in episodes:
                try:
                    n = int(_ep.get('episode_number') or 0)
                    if n > 0:
                        valid_eps.append(n)
                except Exception:
                    pass
            cal_db.prune_season_episodes_not_in(int(tmdb_id), int(latest_season), valid_eps)
        except Exception:
            pass
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
        # 如果有 Trakt 的源时间/时区，计算每集的本地播出日期并更新 air_date_local
        update_episodes_air_date_local(cal_db, int(tmdb_id), latest_season, episodes)
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
            # 写回 season_metrics（air/total），transferred 留待聚合更新
            try:
                # 使用 is_episode_aired 逐集判断计算已播出集数（考虑播出时间）
                # 修复：不再使用简单的日期比较，而是逐集调用 is_episode_aired 判断是否已播出
                try:
                    from zoneinfo import ZoneInfo as _ZoneInfo
                    local_tz = _ZoneInfo(_get_local_timezone())
                    now_local_dt = datetime.now(local_tz)
                except Exception:
                    now_local_dt = datetime.now()
                aired_cnt = compute_aired_count_by_episode_check(int(tmdb_id), int(latest_season), now_local_dt)
                progress_pct = 0
                from time import time as _now
                cal_db.upsert_season_metrics(int(tmdb_id), int(latest_season), None, aired_cnt, int(len(episodes)), int(progress_pct), int(_now()))
            except Exception:
                pass
        except Exception:
            pass
        # 单节目手动刷新完成后，立即重算已播出/进度并同步前后端
        try:
            recompute_show_aired_progress(int(tmdb_id))
        except Exception as _recalc_err:
            logging.debug(f"刷新后重算播出进度失败 tmdb_id={tmdb_id}: {_recalc_err}")

        try:
            if any_written:
                notify_calendar_changed('refresh_latest_season')
        except Exception:
            pass
        try:
            _trigger_airtime_reschedule('refresh_latest_season', tmdb_id)
        except Exception:
            pass
        try:
            logging.info(f">>> 刷新最新季元数据 [{show_name_for_log}] 执行成功")
        except Exception:
            pass
        return jsonify({"success": True, "updated": len(episodes)})
    except Exception as e:
        return jsonify({"success": False, "message": f"刷新失败: {str(e)}"})


# 工具函数：格式化集范围用于日志
def format_episode_range_for_log(episodes):
    try:
        eps = sorted(set(int(x) for x in episodes if x is not None))
    except Exception:
        return ''
    if not eps:
        return ''
    if len(eps) == 1:
        return f"第 {eps[0]} 集"
    # 检测连续
    is_consecutive = all(eps[i] + 1 == eps[i + 1] for i in range(len(eps) - 1))
    if is_consecutive:
        return f"第 {eps[0]} 至 {eps[-1]} 集"
    return "第 " + "、".join(str(e) for e in eps) + " 集"


def _refresh_single_episode(cal_db, tmdb_id: int, season_number: int, episode_number: int, tmdb_service):
    season = tmdb_service.get_tv_show_episodes(tmdb_id, season_number) or {}
    episodes = season.get('episodes', []) or []
    target_episode = None
    for ep in episodes:
        if int(ep.get('episode_number') or 0) == episode_number:
            target_episode = ep
            break
    if not target_episode:
        raise ValueError(f"未找到第 {season_number} 季第 {episode_number} 集")

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
    update_episodes_air_date_local(cal_db, int(tmdb_id), season_number, [target_episode])
    return target_episode


# 强制刷新单集元数据
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
        
        show = cal_db.get_show(int(tmdb_id))
        if not show:
            return jsonify({"success": False, "message": "未初始化该剧（请先 bootstrap）"})

        show_name_for_log = show.get('name') or f"tmdb:{tmdb_id}"
        try:
            logging.info(f">>> 开始执行刷新元数据 [{show_name_for_log} · 第 {season_number} 季 · 第 {episode_number} 集]")
        except Exception:
            pass

        target_episode = _refresh_single_episode(cal_db, int(tmdb_id), int(season_number), int(episode_number), tmdb_service)

        try:
            notify_calendar_changed('refresh_episode')
        except Exception:
            pass
        try:
            _trigger_airtime_reschedule('refresh_episode', tmdb_id)
        except Exception:
            pass
            
        show_name = show.get('name', '未知剧集')
        try:
            logging.info(f">>> 刷新元数据 [{show_name_for_log} · 第 {season_number} 季 · 第 {episode_number} 集] 执行成功")
        except Exception:
            pass
        return jsonify({"success": True, "message": f"《{show_name}》第 {season_number} 季 · 第 {episode_number} 集刷新成功"})
    except Exception as e:
        return jsonify({"success": False, "message": f"刷新失败: {str(e)}"})


# 批量刷新多集（合并集）
@app.route("/api/calendar/refresh_episodes_batch")
def calendar_refresh_episodes_batch():
    try:
        if not is_login():
            return jsonify({"success": False, "message": "未登录"})

        tmdb_id = request.args.get('tmdb_id', type=int)
        season_number = request.args.get('season_number', type=int)
        episode_numbers_raw = request.args.get('episode_numbers', '')

        if not tmdb_id or not season_number or not episode_numbers_raw:
            return jsonify({"success": False, "message": "缺少必要参数"})

        try:
            episode_numbers = [int(x) for x in str(episode_numbers_raw).split(',') if str(x).strip()]
        except Exception:
            return jsonify({"success": False, "message": "episode_numbers 参数非法"})
        if not episode_numbers:
            return jsonify({"success": False, "message": "缺少有效的集号列表"})

        tmdb_api_key = config_data.get('tmdb_api_key', '')
        if not tmdb_api_key:
            return jsonify({"success": False, "message": "TMDB API 未配置"})

        poster_language = get_poster_language_setting()
        tmdb_service = TMDBService(tmdb_api_key, poster_language)
        cal_db = CalendarDB()

        show = cal_db.get_show(int(tmdb_id))
        if not show:
            return jsonify({"success": False, "message": "未初始化该剧（请先 bootstrap）"})

        show_name_for_log = show.get('name') or f"tmdb:{tmdb_id}"
        ep_range = format_episode_range_for_log(episode_numbers)
        try:
            logging.info(f">>> 开始执行刷新元数据 [{show_name_for_log} · 第 {season_number} 季 · {ep_range}]")
        except Exception:
            pass

        failed = []
        for ep_num in episode_numbers:
            try:
                _refresh_single_episode(cal_db, int(tmdb_id), int(season_number), int(ep_num), tmdb_service)
            except Exception as e:
                failed.append((ep_num, str(e)))

        try:
            notify_calendar_changed('refresh_episode')
        except Exception:
            pass
        try:
            _trigger_airtime_reschedule('refresh_episodes_batch', tmdb_id)
        except Exception:
            pass

        show_name = show.get('name', '未知剧集')
        if failed:
            msg = f"《{show_name}》第 {season_number} 季 · {ep_range}部分刷新失败: {failed}"
            return jsonify({"success": False, "message": msg})
        try:
            logging.info(f">>> 刷新元数据 [{show_name_for_log} · 第 {season_number} 季 · {ep_range}] 执行成功")
        except Exception:
            pass
        return jsonify({"success": True, "message": f"《{show_name}》第 {season_number} 季 · {ep_range}刷新成功"})
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
        is_auto_refresh = request.args.get('is_auto_refresh', 'false').lower() == 'true'
        is_batch_refresh = request.args.get('is_batch_refresh', 'false').lower() == 'true'
        is_edit_metadata_refresh = request.args.get('is_edit_metadata_refresh', 'false').lower() == 'true'
        
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

        # 日志：季级刷新（批量刷新时不输出单个任务日志，手动刷新用INFO，自动刷新和编辑元数据触发的刷新用DEBUG）
        if not is_batch_refresh:
            try:
                show_name_for_log = show.get('name') or f"tmdb:{tmdb_id}"
                log_msg = f">>> 开始执行刷新元数据 [{show_name_for_log} · 第 {season_number} 季]"
                if is_auto_refresh or is_edit_metadata_refresh:
                    logging.debug(log_msg)
                else:
                    logging.info(log_msg)
            except Exception:
                pass

        # 获取指定季的所有集数据
        season = tmdb_service.get_tv_show_episodes(tmdb_id, season_number) or {}
        episodes = season.get('episodes', []) or []
        
        if not episodes:
            return jsonify({"success": False, "message": f"第 {season_number} 季没有集数据"})

        # 强制更新该季所有集数据（无论是否有 runtime）
        from time import time as _now
        now_ts = int(_now())
        
        # 根因修复：清理本地该季中 TMDB 不存在的多余集
        try:
            valid_eps = []
            for _ep in episodes:
                try:
                    n = int(_ep.get('episode_number') or 0)
                    if n > 0:
                        valid_eps.append(n)
                except Exception:
                    pass
            cal_db.prune_season_episodes_not_in(int(tmdb_id), int(season_number), valid_eps)
        except Exception:
            pass

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
        
        # 如果有 Trakt 的源时间/时区，计算每集的本地播出日期并更新 air_date_local
        update_episodes_air_date_local(cal_db, int(tmdb_id), season_number, episodes)

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
            # 写回 season_metrics（使用时区感知的已播出判定）
            try:
                try:
                    from zoneinfo import ZoneInfo as _ZoneInfo
                    _local_tz = _ZoneInfo(_get_local_timezone())
                    _now_local_dt = datetime.now(_local_tz)
                except Exception:
                    _now_local_dt = datetime.now()
                aired_cnt = 0
                cur = cal_db.conn.cursor()
                cur.execute(
                    """
                    SELECT episode_number FROM episodes
                    WHERE tmdb_id=? AND season_number=?
                    """,
                    (int(tmdb_id), int(season_number)),
                )
                ep_list = cur.fetchall() or []
                for (_ep_no,) in ep_list:
                    try:
                        result = is_episode_aired(int(tmdb_id), int(season_number), int(_ep_no), _now_local_dt)
                        if result:
                            aired_cnt += 1
                    except Exception as _ep_err:
                        logging.debug(f"calendar_refresh_season 计算已播出集数异常: tmdb_id={tmdb_id}, season={season_number}, ep={_ep_no}, error={_ep_err}")
                        import traceback
                        logging.debug(f"异常堆栈: {traceback.format_exc()}")
                        continue
                from time import time as _now
                logging.debug(f"calendar_refresh_season 计算完成: tmdb_id={tmdb_id}, season={season_number}, aired_cnt={aired_cnt}, total={len(episodes)}")
                cal_db.upsert_season_metrics(int(tmdb_id), int(season_number), None, aired_cnt, int(len(episodes)), 0, int(_now()))
            except Exception as _metrics_err:
                logging.debug(f"calendar_refresh_season 更新 season_metrics 异常: tmdb_id={tmdb_id}, season={season_number}, error={_metrics_err}")
                import traceback
                logging.debug(f"异常堆栈: {traceback.format_exc()}")
                pass
        except Exception:
            pass

        # 通知日历数据变更
        try:
            notify_calendar_changed('refresh_season')
        except Exception:
            pass
        try:
            _trigger_airtime_reschedule('refresh_season', tmdb_id)
        except Exception:
            pass

        # 获取剧名用于通知
        show_name = show.get('name', '未知剧集')
        if not is_batch_refresh:
            try:
                log_msg = f">>> 刷新元数据 [{show_name_for_log} · 第 {season_number} 季] 执行成功"
                if is_auto_refresh or is_edit_metadata_refresh:
                    logging.debug(log_msg)
                else:
                    logging.info(log_msg)
            except Exception:
                pass
        # 刷新后回填任务进度（若有绑定且匹配到同季）
        try:
            tasks = (config_data or {}).get('tasklist', [])
            bound_tasks = []
            for _t in tasks:
                _name = _t.get('taskname') or _t.get('task_name') or ''
                _cal = (_t.get('calendar_info') or {})
                _match = (_cal.get('match') or {})
                _tid = _match.get('tmdb_id') or _cal.get('tmdb_id')
                _matched_sn = _t.get('matched_latest_season_number')
                if _name and _tid and int(_tid) == int(tmdb_id):
                    # 仅对匹配到同一季的任务执行回填
                    if _matched_sn is None or int(_matched_sn) == int(season_number):
                        bound_tasks.append(_name)
            for _task_name in bound_tasks:
                try:
                    recompute_task_metrics_and_notify(_task_name)
                except Exception:
                    continue
        except Exception:
            pass

        return jsonify({"success": True, "message": f"《{show_name}》第 {season_number} 季刷新成功，共 {updated_count} 集"})
    except Exception as e:
        return jsonify({"success": False, "message": f"刷新失败: {str(e)}"})


# 记录批量刷新开始日志
@app.route("/api/calendar/log_batch_refresh_start", methods=["POST"])
def log_batch_refresh_start():
    try:
        if not is_login():
            return jsonify({"success": False, "message": "未登录"})
        logging.info(">>> 开始执行追剧日历手动刷新")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": f"记录日志失败: {str(e)}"})


# 记录批量刷新结束日志
@app.route("/api/calendar/log_batch_refresh_end", methods=["POST"])
def log_batch_refresh_end():
    try:
        if not is_login():
            return jsonify({"success": False, "message": "未登录"})
        logging.info(">>> 追剧日历手动刷新执行成功")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": f"记录日志失败: {str(e)}"})


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
                existing_poster_path = existing_show.get('poster_local_path', '') if existing_show else ''
                is_custom_poster = bool(existing_show.get('is_custom_poster', 0)) if existing_show else False
                poster_local_path = download_poster_local(poster_path, int(tmdb_id), existing_poster_path, is_custom_poster)
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

        # 如果海报路径发生变化，触发孤立文件清理
        existing_poster_path = existing_show.get('poster_local_path', '') if existing_show else ''
        if poster_local_path and poster_local_path != existing_poster_path:
            try:
                cleanup_orphaned_posters()
            except Exception as e:
                logging.warning(f"清理孤立海报失败: {e}")

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
        local_air_time = (data.get('local_air_time') or '').strip()

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
        # 兼容旧字段，避免因结构差异无法读取已匹配的节目
        legacy_match = target.get('match') if isinstance(target.get('match'), dict) else {}
        old_tmdb_id = (
            match.get('tmdb_id')
            or cal.get('tmdb_id')
            or legacy_match.get('tmdb_id')
            or target.get('match_tmdb_id')
            or target.get('tmdb_id')
        )

        changed = False
        local_air_time_changed = False

        if new_task_name and new_task_name != task_name:
            target['taskname'] = new_task_name
            target['task_name'] = new_task_name
            changed = True

        # 处理节目级本地播出时间：写入任务配置，并在存在 TMDB ID 时同步到数据库
        if 'calendar_info' not in target:
            target['calendar_info'] = {}
        if local_air_time or cal.get('local_air_time'):
            # 允许用户清空播出时间（回退到 Trakt 自动推断或全局刷新时间）
            # 从数据库获取当前的播出时间和偏移值，而不是从 cal 中获取（可能不准确）
            old_offset = 0
            old_air_time_only = ''
            if old_tmdb_id:
                try:
                    cal_db_for_offset = CalendarDB()
                    schedule_for_offset = cal_db_for_offset.get_show_air_schedule(int(old_tmdb_id)) or {}
                    old_air_time_only = schedule_for_offset.get('local_air_time') or ''
                    old_offset = schedule_for_offset.get('air_date_offset') or 0
                except Exception:
                    old_air_time_only = str(cal.get('local_air_time') or '').strip()
                    old_offset = cal.get('air_date_offset') or 0
            else:
                old_air_time_only = str(cal.get('local_air_time') or '').strip()
                old_offset = cal.get('air_date_offset') or 0
            
            norm_old = old_air_time_only  # 只使用时间部分，不包含偏移值
            norm_new = str(local_air_time or '').strip()
            
            # 解析新的播出时间字符串，提取时间和日期偏移
            air_time_only_new = norm_new
            air_date_offset_new = 0
            offset_match = re.match(r'^([01]?[0-9]|2[0-3]):([0-5][0-9])\s+([+-])(\d+)d$', norm_new)
            if offset_match:
                air_time_only_new = offset_match.group(1) + ':' + offset_match.group(2)
                sign = offset_match.group(3)
                days = int(offset_match.group(4))
                air_date_offset_new = days if sign == '+' else -days
            
            # 比较时间部分和偏移值，只要有一个不同就需要更新
            time_changed = (air_time_only_new != norm_old) or (air_date_offset_new != old_offset)
            if time_changed:
                # 使用已解析的值
                air_time_only = air_time_only_new
                air_date_offset = air_date_offset_new
                
                target['calendar_info']['local_air_time'] = air_time_only
                target['calendar_info']['air_date_offset'] = air_date_offset
                changed = True
                local_air_time_changed = True
                # 用户手动设置播出时间：仅以本地时间为准，同时清空节目级源时区设置，
                # 后续不再使用 Trakt 的 airs.time / timezone 做日期偏移推导
                try:
                    if old_tmdb_id:
                        cal_db_for_air = CalendarDB()
                        # 保留原有的 air_time_source 和 air_timezone（不清空），但使用手动设置的偏移值
                        # 这样 update_episodes_air_date_local 可以优先使用手动偏移值
                        # 如果日期偏移发生变化，或者时间部分发生变化，都标记为手动设置
                        # 因为用户通过 WebUI 编辑元数据页面修改了播出时间，无论是否改变了偏移值，都应该标记为手动设置
                        should_mark_manually_set = (air_date_offset != old_offset) or (air_time_only_new != norm_old)
                        
                        cal_db_for_air.update_show_air_schedule(
                            int(old_tmdb_id),
                            local_air_time=air_time_only,
                            air_date_offset=air_date_offset,
                            air_date_offset_manually_set=True if should_mark_manually_set else None,
                        )
                        # 如果日期偏移发生变化，重新计算所有集的 air_date_local
                        if air_date_offset != old_offset:
                            try:
                                cursor = cal_db_for_air.conn.cursor()
                                cursor.execute(
                                    "SELECT season_number, episode_number, air_date FROM episodes WHERE tmdb_id=? AND air_date IS NOT NULL AND air_date != ''",
                                    (int(old_tmdb_id),),
                                )
                                rows = cursor.fetchall()
                                episodes_by_season = {}
                                for season_number, ep_number, air_date in rows:
                                    episodes_by_season.setdefault(season_number, []).append(
                                        {"episode_number": ep_number, "air_date": air_date}
                                    )
                                updated_count = 0
                                for season_number, eps in episodes_by_season.items():
                                    # 使用新的偏移值重新计算 air_date_local
                                    for ep in eps:
                                        try:
                                            from datetime import datetime as _dt, timedelta as _td
                                            src_date = _dt.strptime(ep['air_date'], '%Y-%m-%d').date()
                                            adjusted_date = src_date + _td(days=air_date_offset)
                                            cal_db_for_air.update_episode_air_date_local(
                                                int(old_tmdb_id),
                                                int(season_number),
                                                int(ep['episode_number']),
                                                adjusted_date.strftime('%Y-%m-%d')
                                            )
                                            updated_count += 1
                                        except Exception as _ep_err:
                                            logging.debug(f"更新集日期失败 tmdb_id={old_tmdb_id}, season={season_number}, ep={ep['episode_number']}: {_ep_err}")
                            except Exception as _recalc_date_err:
                                logging.debug(f"重新计算集日期失败 tmdb_id={old_tmdb_id}: {_recalc_date_err}")
                        # 变更播出时间后，立即重算该节目的已播/进度并通知前端，确保热更新
                        try:
                            recompute_show_aired_progress(int(old_tmdb_id))
                        except Exception as _recalc_err:
                            logging.debug(f"重算节目进度失败 tmdb_id={old_tmdb_id}: {_recalc_err}")
                        try:
                            notify_calendar_changed('update_airtime')
                        except Exception:
                            pass
                        try:
                            _trigger_airtime_reschedule('update_airtime', old_tmdb_id)
                        except Exception as _sched_err:
                            logging.debug(f"重建播出时间调度失败 tmdb_id={old_tmdb_id}: {_sched_err}")
                except Exception as e:
                    logging.warning(f"同步本地播出时间到数据库失败: {e}")

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
        new_tid = None
        season_no = None
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
                # 根因修复：清理本地该季中 TMDB 不存在的多余集
                try:
                    valid_eps = []
                    for _ep in eps:
                        try:
                            n = int(_ep.get('episode_number') or 0)
                            if n > 0:
                                valid_eps.append(n)
                        except Exception:
                            pass
                    cal_db.prune_season_episodes_not_in(int(new_tid), int(season_no), valid_eps)
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
                # 如果有 Trakt 的源时间/时区，计算每集的本地播出日期并更新 air_date_local
                update_episodes_air_date_local(cal_db, int(new_tid), int(season_no), eps)
                try:
                    sname_raw = (season or {}).get('name') or ''
                    sname = tmdb_service.process_season_name(sname_raw) if tmdb_service else sname_raw
                    cal_db.upsert_season(int(new_tid), int(season_no), len(eps), f"/tv/{new_tid}/season/{season_no}", sname)
                    cal_db.update_show_latest_season_number(int(new_tid), int(season_no))
                    # 写回 season_metrics（air/total），transferred 留给聚合环节补齐
                    try:
                        # 使用 is_episode_aired 逐集判断计算已播出集数（考虑播出时间）
                        # 修复：不再使用简单的日期比较，而是逐集调用 is_episode_aired 判断是否已播出
                        try:
                            from zoneinfo import ZoneInfo as _ZoneInfo
                            local_tz = _ZoneInfo(_get_local_timezone())
                            now_local_dt = datetime.now(local_tz)
                        except Exception:
                            now_local_dt = datetime.now()
                        aired_cnt = compute_aired_count_by_episode_check(int(new_tid), int(season_no), now_local_dt)
                        progress_pct = 0  # 此处无 transferred_count，百分比暂置 0，由聚合环节补齐
                        from time import time as _now
                        cal_db.upsert_season_metrics(int(new_tid), int(season_no), None, aired_cnt, int(len(eps)), int(progress_pct), int(_now()))
                    except Exception:
                        pass
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
            # 基于最新任务映射清理孤儿季与指标
            try:
                _tasks = config_data.get('tasklist', [])
                _valid_names = []
                _valid_pairs = []
                for _t in _tasks:
                    _n = _t.get('taskname') or _t.get('task_name') or ''
                    _cal = (_t.get('calendar_info') or {})
                    _match = (_cal.get('match') or {})
                    _tid = _match.get('tmdb_id') or _cal.get('tmdb_id')
                    _sn = _match.get('latest_season_number') or _cal.get('latest_season_number')
                    if _n:
                        _valid_names.append(_n)
                    if _tid and _sn:
                        try:
                            _valid_pairs.append((int(_tid), int(_sn)))
                        except Exception:
                            pass
                CalendarDB().cleanup_orphan_data(_valid_pairs, _valid_names)
                # 清理孤立数据后，也清理可能残留的孤立海报文件
                try:
                    cleanup_orphaned_posters()
                except Exception:
                    pass
            except Exception:
                pass
        # 场景二：未提供 new_tmdb_id，但提供了 new_season_number（仅修改季数）
        elif (new_season_number is not None) and (str(new_season_number).strip() != '') and old_tmdb_id:
            try:
                season_no = int(new_season_number)
            except Exception:
                return jsonify({"success": False, "message": "季数必须为数字"})

            # 检查是否与当前匹配的季数相同，如果相同则跳过处理
            current_match_season = (
                match.get('latest_season_number')
                or legacy_match.get('latest_season_number')
                or target.get('matched_latest_season_number')
                or cal.get('latest_season_number')
            )
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
                    # 根因修复：清理本地该季中 TMDB 不存在的多余集
                    try:
                        valid_eps = []
                        for _ep in eps:
                            try:
                                n = int(_ep.get('episode_number') or 0)
                                if n > 0:
                                    valid_eps.append(n)
                            except Exception:
                                pass
                        cal_db.prune_season_episodes_not_in(int(old_tmdb_id), int(season_no), valid_eps)
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
                    # 如果有 Trakt 的源时间/时区，计算每集的本地播出日期并更新 air_date_local
                    update_episodes_air_date_local(cal_db, int(old_tmdb_id), int(season_no), eps)
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
                # 基于最新任务映射清理孤儿季与指标
                try:
                    _tasks = config_data.get('tasklist', [])
                    _valid_names = []
                    _valid_pairs = []
                    for _t in _tasks:
                        _n = _t.get('taskname') or _t.get('task_name') or ''
                        _cal = (_t.get('calendar_info') or {})
                        _match = (_cal.get('match') or {})
                        _tid = _match.get('tmdb_id') or _cal.get('tmdb_id')
                        _sn = _match.get('latest_season_number') or _cal.get('latest_season_number')
                        if _n:
                            _valid_names.append(_n)
                        if _tid and _sn:
                            try:
                                _valid_pairs.append((int(_tid), int(_sn)))
                            except Exception:
                                pass
                    CalendarDB().cleanup_orphan_data(_valid_pairs, _valid_names)
                    # 清理孤立数据后，也清理可能残留的孤立海报文件
                    try:
                        cleanup_orphaned_posters()
                    except Exception:
                        pass
                except Exception:
                    pass

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
                        # 更新数据库路径，标记为自定义海报
                        try:
                            cal_db.update_show_poster(int(current_tmdb_id), saved_path, is_custom_poster=1)
                        except Exception as e:
                            logging.warning(f"更新海报路径失败: {e}")
                        
                        # 若文件名发生变化则需要删除旧文件
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
                        except Exception:
                            # 即使对比失败也不影响功能
                            pass
                        
                        # 用户上传自定义海报后，总是触发孤立文件清理
                        try:
                            cleanup_orphaned_posters()
                        except Exception as e:
                            logging.warning(f"清理孤立海报失败: {e}")

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

        if local_air_time_changed and old_tmdb_id:
            try:
                _trigger_airtime_reschedule('edit_metadata_local_air_time', old_tmdb_id)
            except Exception:
                pass

        # 确定最终的 tmdb_id 和 season_number，用于前端刷新
        # 只有在需要刷新元数据的情况下才返回有效的 tmdb_id 和 season_number：
        # 1. 重新匹配了 TMDB（did_rematch）
        # 2. 修改了季数（new_season_number）
        # 3. 修改了自定义海报（custom_poster_url）- 需要刷新以更新海报
        # 其他情况（如只修改任务名、内容类型、本地播出时间）不应该触发刷新
        final_tmdb_id = None
        final_season_number = None
        if did_rematch and new_tid:
            # 如果重新匹配，使用新的 tmdb_id 和 season_number
            final_tmdb_id = new_tid
            final_season_number = season_no
        elif (new_season_number is not None) and (str(new_season_number).strip() != '') and old_tmdb_id:
            # 如果只修改了季数，使用当前的 tmdb_id 和新的季数
            try:
                final_tmdb_id = old_tmdb_id
                final_season_number = int(new_season_number)
            except Exception:
                pass
        elif custom_poster_url and old_tmdb_id:
            # 如果修改了自定义海报，需要刷新以更新海报（但不需要刷新元数据）
            # 这里不返回 tmdb_id 和 season_number，因为海报更新不需要刷新元数据
            # 海报更新会通过 notify_calendar_changed('poster_updated:...') 通知前端
            pass

        msg = '元数据更新成功'
        if did_rematch:
            msg = '元数据更新成功，已重新匹配并刷新元数据'
        if custom_poster_url:
            if '已重新匹配' in msg:
                msg = '元数据更新成功，已重新匹配并刷新元数据，自定义海报已更新'
            else:
                msg = '元数据更新成功，自定义海报已更新'
        
        # 返回修改状态和需要刷新的节目信息
        result = {
            "success": True,
            "message": msg,
            "changed": changed,
            "tmdb_id": final_tmdb_id,
            "season_number": final_season_number
        }
        return jsonify(result)
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
        global _calendar_refresh_last_interval
        # 读取用户配置的刷新周期（秒），默认21600秒（6小时）
        perf = config_data.get('performance', {}) if isinstance(config_data, dict) else {}
        interval_seconds = int(perf.get('calendar_refresh_interval_seconds', 21600))
        if interval_seconds <= 0:
            # 非法或关闭则移除
            try:
                scheduler.remove_job('calendar_refresh_job')
            except Exception:
                pass
            # 只在状态改变时输出日志
            if _calendar_refresh_last_interval is not None:
                logging.info("已关闭追剧日历元数据自动刷新")
                _calendar_refresh_last_interval = None
            return

        # 重置任务
        try:
            scheduler.remove_job('calendar_refresh_job')
        except Exception:
            pass
        scheduler.add_job(
            run_calendar_refresh_all_internal_wrapper,
            IntervalTrigger(seconds=interval_seconds),
            id='calendar_refresh_job',
            replace_existing=True,
            misfire_grace_time=None,
            coalesce=True,
            max_instances=1,
        )
        if scheduler.state == 0:
            scheduler.start()
        # 友好提示：仅在周期变化时输出日志（避免重复输出）
        try:
            if IS_FLASK_SERVER_PROCESS:
                if _calendar_refresh_last_interval != interval_seconds:
                    logging.info(f"已启动追剧日历自动刷新，周期 {interval_seconds} 秒")
                    _calendar_refresh_last_interval = interval_seconds
        except Exception:
            pass
    except Exception as e:
        logging.warning(f"配置自动刷新任务失败: {e}")


def run_calendar_refresh_all_internal_wrapper():
    """
    追剧日历自动刷新的包装函数，用于检查上一次任务是否还在运行
    """
    global _calendar_refresh_thread
    
    logging.info(f">>> 开始执行追剧日历自动刷新")
    
    # 检查上一次任务是否还在运行
    if _calendar_refresh_thread is not None:
        try:
            if _calendar_refresh_thread.is_alive():  # 线程还在运行
                logging.warning(f">>> 检测到上一次追剧日历自动刷新仍在运行，将忽略并继续执行新任务")
                # 注意：Python 线程无法强制终止，只能记录警告
                # 但由于调度器会继续执行，不会阻塞后续任务
            else:  # 线程已结束（正常或异常退出）
                logging.debug(f">>> 上一次追剧日历自动刷新已结束")
        except Exception as e:
            logging.warning(f">>> 检查上一次追剧日历自动刷新任务状态时出错: {e}")
        finally:
            # 无论什么情况，都清除线程对象，确保下次能正常运行
            _calendar_refresh_thread = None
    
    # 在新线程中执行任务，记录线程对象以便下次检查
    def run_in_thread():
        global _calendar_refresh_thread
        try:
            run_calendar_refresh_all_internal()
            logging.info(f">>> 追剧日历自动刷新执行成功")
        except Exception as e:
            logging.error(f">>> 追剧日历自动刷新执行异常: {str(e)}")
            import traceback
            logging.error(f">>> 异常堆栈: {traceback.format_exc()}")
        finally:
            # 任务完成，清除线程对象
            _calendar_refresh_thread = None
    
    _calendar_refresh_thread = Thread(target=run_in_thread, daemon=True)
    _calendar_refresh_thread.start()
    # 不等待线程完成，让调度器认为任务已启动
    # 这样即使任务运行时间很长，也不会阻塞后续任务


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
        status_changed_any = False
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
                    
                    # 如果有 Trakt 的源时间/时区，计算每集的本地播出日期并更新 air_date_local
                    # 如果没有时区信息，将 air_date_local 设置为与 air_date 相同的值
                    update_episodes_air_date_local(db, int(tmdb_id), int(db.get_show(int(tmdb_id))['latest_season_number']), episodes)

                    # 新增：节目状态变更检测与更新（如 Returning → 本季终/已完结 等）
                    try:
                        existing_show = db.get_show(int(tmdb_id)) or {}
                        raw_details = tmdb_service.get_tv_show_details(int(tmdb_id)) or {}
                        raw_status = (raw_details.get('status') or '')
                        latest_sn_for_status = int((existing_show or {}).get('latest_season_number') or 1)
                        try:
                            localized_status = tmdb_service.get_localized_show_status(int(tmdb_id), latest_sn_for_status, raw_status)
                        except Exception:
                            localized_status = raw_status

                        old_status = (existing_show or {}).get('status') or ''
                        if localized_status and localized_status != old_status:
                            # 仅更新 status 等必要字段，其他沿用原值
                            db.upsert_show(
                                tmdb_id=int(tmdb_id),
                                name=(existing_show or {}).get('name') or '',
                                year=(existing_show or {}).get('year') or '',
                                status=localized_status,
                                poster_local_path=(existing_show or {}).get('poster_local_path') or '',
                                latest_season_number=int((existing_show or {}).get('latest_season_number') or 1),
                                last_refreshed_at=now_ts,
                                bound_task_names=(existing_show or {}).get('bound_task_names') or '',
                                content_type=(existing_show or {}).get('content_type') or '',
                                is_custom_poster=int((existing_show or {}).get('is_custom_poster') or 0),
                            )
                            status_changed_any = True
                    except Exception:
                        # 状态更新失败不影响整体刷新
                        pass
            except Exception as e:
                logging.warning(f"自动刷新失败 tmdb_id={tmdb_id}: {e}")
        try:
            if any_written:
                notify_calendar_changed('auto_refresh')
            if status_changed_any:
                notify_calendar_changed('status_updated')
        except Exception:
            pass
        try:
            _trigger_airtime_reschedule('auto_refresh')
        except Exception:
            pass
    except Exception as e:
        logging.warning(f"自动刷新任务异常: {e}")
# 本地缓存读取：获取最新一季的本地剧集数据
@app.route("/api/calendar/episodes_local")
def get_calendar_episodes_local():
    """获取本地剧集数据（优化版：添加缓存机制）"""
    global _calendar_episodes_cache
    
    try:
        # 检查缓存是否有效
        current_time = time.time()
        tmdb_id = request.args.get('tmdb_id')
        cache_key = f'episodes_data_{tmdb_id or "all"}'
        
        with _calendar_episodes_cache_lock:
            # 检查缓存是否存在且未过期
            if cache_key in _calendar_episodes_cache:
                cache_data, cache_time = _calendar_episodes_cache[cache_key]
                if current_time - cache_time < _calendar_episodes_cache_ttl:
                    # 缓存有效，直接返回
                    return jsonify(cache_data)
        
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

        # 获取日历时区模式配置
        calendar_timezone_mode = config_data.get('calendar_timezone_mode', 'original')

        # 统一获取当前本地时间，用于批量计算 is_aired 保持与后端统计逻辑一致
        try:
            from zoneinfo import ZoneInfo as _ZoneInfo
            local_tz = _ZoneInfo(_get_local_timezone())
            now_local_dt = datetime.now(local_tz)
        except Exception:
            now_local_dt = datetime.now()
        
        # 批量计算 is_aired 的辅助函数（避免重复数据库查询）
        def batch_compute_is_aired(episodes_list, db_instance):
            """批量计算所有集的 is_aired，复用数据库连接和批量查询优化性能"""
            if not episodes_list:
                return
            try:
                # 收集所有需要查询的 (tmdb_id, season_number, episode_number) 三元组
                episode_keys = []
                for e in episodes_list:
                    try:
                        tmdb_id_i = int(e.get('tmdb_id'))
                        season_no_i = int(e.get('season_number'))
                        ep_no = int(e.get('episode_number'))
                        episode_keys.append((tmdb_id_i, season_no_i, ep_no, e))
                    except Exception:
                        continue
                
                if not episode_keys:
                    return
                
                # 批量查询所有集的 air_date_local（一次性查询）
                cur = db_instance.conn.cursor()
                air_dates_map = {}
                try:
                    # SQLite 不支持元组 IN，使用 OR 条件组合（性能仍然比逐集查询好很多）
                    if len(episode_keys) <= 500:  # 限制批量大小，避免 SQL 过长
                        conditions = []
                        params = []
                        for tmdb_id_i, season_no_i, ep_no, _ in episode_keys:
                            conditions.append("(tmdb_id=? AND season_number=? AND episode_number=?)")
                            params.extend([tmdb_id_i, season_no_i, ep_no])
                        sql = f"""
                            SELECT tmdb_id, season_number, episode_number, air_date_local
                            FROM episodes
                            WHERE {' OR '.join(conditions)}
                        """
                        cur.execute(sql, params)
                        for row in cur.fetchall():
                            tmdb_id_i, season_no_i, ep_no, air_date_local = row
                            air_dates_map[(int(tmdb_id_i), int(season_no_i), int(ep_no))] = air_date_local
                    else:
                        # 如果集数太多，分批查询（每批500个）
                        batch_size = 500
                        for i in range(0, len(episode_keys), batch_size):
                            batch = episode_keys[i:i + batch_size]
                            conditions = []
                            params = []
                            for tmdb_id_i, season_no_i, ep_no, _ in batch:
                                conditions.append("(tmdb_id=? AND season_number=? AND episode_number=?)")
                                params.extend([tmdb_id_i, season_no_i, ep_no])
                            sql = f"""
                                SELECT tmdb_id, season_number, episode_number, air_date_local
                                FROM episodes
                                WHERE {' OR '.join(conditions)}
                            """
                            cur.execute(sql, params)
                            for row in cur.fetchall():
                                tmdb_id_i, season_no_i, ep_no, air_date_local = row
                                air_dates_map[(int(tmdb_id_i), int(season_no_i), int(ep_no))] = air_date_local
                except Exception:
                    # 如果批量查询失败，回退到逐集查询（兼容性）
                    for tmdb_id_i, season_no_i, ep_no, _ in episode_keys:
                        try:
                            cur.execute(
                                "SELECT air_date_local FROM episodes WHERE tmdb_id=? AND season_number=? AND episode_number=?",
                                (tmdb_id_i, season_no_i, ep_no)
                            )
                            row = cur.fetchone()
                            if row:
                                air_dates_map[(tmdb_id_i, season_no_i, ep_no)] = row[0]
                        except Exception:
                            pass
                
                # 批量查询所有节目的播出时间配置（一次性查询，避免重复）
                unique_tmdb_ids = list(set([k[0] for k in episode_keys]))
                schedules_map = {}
                try:
                    if unique_tmdb_ids:
                        placeholders = ','.join(['?' for _ in unique_tmdb_ids])
                        cur.execute(
                            f"SELECT tmdb_id, local_air_time FROM shows WHERE tmdb_id IN ({placeholders})",
                            unique_tmdb_ids
                        )
                        for row in cur.fetchall():
                            schedules_map[int(row[0])] = (row[1] or '').strip() or None
                except Exception:
                    # 回退到逐节目查询（使用 get_show_air_schedule 获取完整配置）
                    for tmdb_id_i in unique_tmdb_ids:
                        try:
                            schedule = db_instance.get_show_air_schedule(tmdb_id_i) or {}
                            schedules_map[tmdb_id_i] = (schedule.get('local_air_time') or '').strip() or None
                        except Exception:
                            schedules_map[tmdb_id_i] = None
                
                # 获取全局播出集数刷新时间（只需获取一次）
                perf = config_data.get("performance", {}) if isinstance(config_data, dict) else {}
                refresh_time_str = perf.get("aired_refresh_time", "00:00")
                
                # 在内存中批量计算 is_aired，并同时为前端注入节目级播出时间（local_air_time）
                for tmdb_id_i, season_no_i, ep_no, episode_obj in episode_keys:
                    try:
                        air_date_local = air_dates_map.get((tmdb_id_i, season_no_i, ep_no))
                        if not air_date_local:
                            episode_obj['is_aired'] = False
                            continue
                        
                        # 获取节目级播出时间
                        local_air_time = schedules_map.get(tmdb_id_i)
                        # 将节目级播出时间原样注入到每一集对象中，供前端悬停提示展示
                        # 仅在存在节目级配置时注入；若配置为空则保持该字段缺失，前端仍按原逻辑不显示时间
                        if local_air_time:
                            episode_obj['local_air_time'] = local_air_time
                        time_to_use = local_air_time if local_air_time else refresh_time_str
                        
                        # 解析时间（HH:MM）
                        hh, mm = 0, 0
                        try:
                            parts = str(time_to_use).split(":")
                            if len(parts) == 2:
                                hh = int(parts[0])
                                mm = int(parts[1])
                        except Exception:
                            hh, mm = 0, 0
                        
                        # 组合日期和时间，与当前时间比较
                        dt_str = f"{air_date_local} {hh:02d}:{mm:02d}:00"
                        dt_ep_local = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                        if getattr(now_local_dt, "tzinfo", None) is not None:
                            dt_ep_local = dt_ep_local.replace(tzinfo=now_local_dt.tzinfo)
                        episode_obj['is_aired'] = dt_ep_local.timestamp() <= now_local_dt.timestamp()
                    except Exception:
                        episode_obj['is_aired'] = False
            except Exception:
                # 如果批量计算失败，回退到逐集调用 is_episode_aired（兼容性保证）
                for e in episodes_list:
                    try:
                        tmdb_id_i = int(e.get('tmdb_id'))
                        season_no_i = int(e.get('season_number'))
                        ep_no = int(e.get('episode_number'))
                        e['is_aired'] = bool(is_episode_aired(tmdb_id_i, season_no_i, ep_no, now_local_dt))
                    except Exception:
                        e['is_aired'] = False
        
        if tmdb_id:
            show = db.get_show(int(tmdb_id))
            if not show:
                return jsonify({'success': True, 'data': {'episodes': [], 'total': 0}})
            eps = db.list_latest_season_episodes(int(tmdb_id), int(show['latest_season_number']))
            # 根据 calendar_timezone_mode 选择使用 air_date 还是 air_date_local
            # air_date_local 应该已经包含了偏移值，直接使用即可
            for e in eps:
                if calendar_timezone_mode == 'local' and e.get('air_date_local'):
                    e['display_air_date'] = e['air_date_local']
                else:
                    e['display_air_date'] = e.get('air_date') or ''
                # 补充 tmdb_id 和 season_number（批量计算需要）
                e['tmdb_id'] = int(tmdb_id)
                e['season_number'] = int(show['latest_season_number'])
            # 批量计算 is_aired（优化性能）
            batch_compute_is_aired(eps, db)
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
            result_data = {'success': True, 'data': {'episodes': eps, 'total': len(eps), 'show': show}}
            
            # 更新缓存
            with _calendar_episodes_cache_lock:
                _calendar_episodes_cache[cache_key] = (result_data, current_time)
            
            return jsonify(result_data)
        else:
            # 返回全部最新季汇总（供周视图合并展示）
            eps = db.list_all_latest_episodes()
            # 根据 calendar_timezone_mode 选择使用 air_date 还是 air_date_local
            # air_date_local 应该已经包含了偏移值，直接使用即可
            for e in eps:
                if calendar_timezone_mode == 'local' and e.get('air_date_local'):
                    e['display_air_date'] = e['air_date_local']
                else:
                    e['display_air_date'] = e.get('air_date') or ''
            # 批量计算 is_aired（优化性能）
            batch_compute_is_aired(eps, db)
            for e in eps:
                info = tmdb_to_taskinfo.get(int(e.get('tmdb_id')))
                if info:
                    e['task_info'] = info
                    tname = info.get('task_name') or ''
                    p = progress_by_task.get(tname)
                    if p:
                        e['task_info']['progress'] = p
            result_data = {'success': True, 'data': {'episodes': eps, 'total': len(eps)}}
            
            # 更新缓存
            with _calendar_episodes_cache_lock:
                _calendar_episodes_cache[cache_key] = (result_data, current_time)
            
            return jsonify(result_data)
    except Exception as e:
        # 如果出错，尝试返回缓存数据（如果有）
        with _calendar_episodes_cache_lock:
            if cache_key in _calendar_episodes_cache:
                cache_data, _ = _calendar_episodes_cache[cache_key]
                logging.warning(f'获取本地剧集数据失败，返回缓存数据: {str(e)}')
                return jsonify(cache_data)
        
        # 没有缓存，返回错误
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


@app.route("/api/proxy/douban-image")
def proxy_douban_image():
    """代理豆瓣图片请求，设置正确的 Referer 头以绕过防盗链限制"""
    try:
        image_url = request.args.get('url')
        if not image_url:
            return Response('缺少图片URL参数', status=400, mimetype='text/plain')
        
        # 验证URL是否为豆瓣图片地址
        if not (image_url.startswith('http://') or image_url.startswith('https://')):
            return Response('无效的图片URL', status=400, mimetype='text/plain')
        
        # 检查是否为豆瓣图片域名（douban.com, doubanio.com等）
        douban_domains = ['douban.com', 'doubanio.com']
        is_douban_image = any(domain in image_url for domain in douban_domains)
        
        if not is_douban_image:
            # 如果不是豆瓣图片，可以选择直接重定向或拒绝
            # 这里我们选择直接代理，但不设置Referer
            headers = {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1'
            }
        else:
            # 豆瓣图片需要设置Referer为豆瓣域名
            headers = {
                'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Mobile/15E148 Safari/604.1',
                'Referer': 'https://movie.douban.com/'
            }
        
        # 请求图片
        response = requests.get(image_url, headers=headers, timeout=10, stream=True)
        
        if response.status_code != 200:
            return Response(f'图片加载失败: {response.status_code}', 
                          status=response.status_code, 
                          mimetype='text/plain')
        
        # 获取图片的Content-Type
        content_type = response.headers.get('Content-Type', 'image/jpeg')
        
        # 设置响应头，允许跨域（如果需要）
        resp = Response(
            stream_with_context(response.iter_content(chunk_size=8192)),
            content_type=content_type
        )
        resp.headers['Cache-Control'] = 'public, max-age=3600'
        
        return resp
        
    except Exception as e:
        logging.error(f"代理豆瓣图片失败: {str(e)}")
        return Response(f'代理图片失败: {str(e)}', status=500, mimetype='text/plain')

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

def cleanup_episode_patterns_config(config_data):
    """清理剧集识别规则配置"""
    try:
        # 需要清理的默认剧集识别规则（按部分规则匹配）
        default_pattern_parts = [
            "第(\\d+)集",
            "第(\\d+)期", 
            "第(\\d+)话",
            "(\\d+)集",
            "(\\d+)期",
            "(\\d+)话",
            "[Ee][Pp]?(\\d+)",
            "(\\d+)[-_\\s]*4[Kk]",
            "(\\d+)[-_\\\\s]*4[Kk]",
            "\\[(\\d+)\\]",
            "【(\\d+)】",
            "_?(\\d+)_?"
        ]
        
        cleaned_tasks = 0
        cleaned_global = False
        
        # 1. 清理任务级别的 config_data.episode_patterns
        if 'tasklist' in config_data:
            for task in config_data['tasklist']:
                if 'config_data' in task and 'episode_patterns' in task['config_data']:
                    del task['config_data']['episode_patterns']
                    cleaned_tasks += 1
                    # 如果 config_data 为空，删除整个 config_data
                    if not task['config_data']:
                        del task['config_data']
        
        # 2. 清理全局配置中的默认规则
        if 'episode_patterns' in config_data:
            current_patterns = config_data['episode_patterns']
            if isinstance(current_patterns, list):
                # 过滤掉包含默认规则的配置
                filtered_patterns = []
                for pattern in current_patterns:
                    if isinstance(pattern, dict) and 'regex' in pattern:
                        pattern_regex = pattern['regex']
                        # 用竖线分割规则
                        pattern_parts = pattern_regex.split('|')
                        # 过滤掉默认规则部分，保留自定义规则
                        custom_parts = [part.strip() for part in pattern_parts if part.strip() not in default_pattern_parts]
                        
                        if custom_parts:
                            # 如果有自定义规则，保留并重新组合
                            filtered_patterns.append({
                                'regex': '|'.join(custom_parts)
                            })
                    elif isinstance(pattern, str):
                        pattern_regex = pattern
                        # 用竖线分割规则
                        pattern_parts = pattern_regex.split('|')
                        # 过滤掉默认规则部分，保留自定义规则
                        custom_parts = [part.strip() for part in pattern_parts if part.strip() not in default_pattern_parts]
                        
                        if custom_parts:
                            # 如果有自定义规则，保留并重新组合
                            filtered_patterns.append('|'.join(custom_parts))
                
                # 更新配置
                if filtered_patterns:
                    config_data['episode_patterns'] = filtered_patterns
                else:
                    # 如果没有剩余规则，清空配置
                    config_data['episode_patterns'] = []
                    cleaned_global = True
        
        # 静默执行清理操作，不输出日志
        
        return True
        
    except Exception as e:
        logging.error(f"清理剧集识别规则配置失败: {str(e)}")
        return False

@app.route("/api/calendar/cleanup_orphaned_posters", methods=["POST"])
def cleanup_orphaned_posters_api():
    """手动清理孤立的旧海报文件"""
    try:
        if not is_login():
            return jsonify({"success": False, "message": "未登录"})
        
        success = cleanup_orphaned_posters()
        if success:
            return jsonify({"success": True, "message": "孤立海报文件清理完成"})
        else:
            return jsonify({"success": True, "message": "没有发现需要清理的孤立文件"})
    except Exception as e:
        return jsonify({"success": False, "message": f"清理孤立海报失败: {str(e)}"})

if __name__ == "__main__":
    IS_FLASK_SERVER_PROCESS = True
    init()
    reload_tasks()
    # 在 reload_tasks() 之后重新注册会被清空的后台任务（避免 remove_all_jobs 的影响）
    try:
        restart_calendar_refresh_job()
    except Exception:
        pass
    try:
        restart_daily_aired_update_job()
    except Exception:
        pass
    # 初始化全局db对象，确保所有接口可用
    record_db = RecordDB()
    app.run(debug=DEBUG, host="0.0.0.0", port=PORT)
