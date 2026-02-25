# !/usr/bin/env python3
# -*- coding: utf-8 -*-
# ConfigFile: quark_config.json
"""
new Env('夸克自动追更');
0 8,18,20 * * * quark_auto_save.py
"""
import os
import re
import sys
import json
import time
import random
import requests
import importlib
import urllib.parse
from datetime import datetime

# 添加数据库导入
try:
    from app.sdk.db import RecordDB, CalendarDB
except ImportError:
    # 如果直接运行脚本，路径可能不同
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        from app.sdk.db import RecordDB, CalendarDB
    except ImportError:
        # 定义一个空的RecordDB类，以防止导入失败
        class RecordDB:
            def __init__(self, *args, **kwargs):
                self.enabled = False
            
            def add_record(self, *args, **kwargs):
                pass
            
            def close(self):
                pass
        
        # 定义一个空的CalendarDB类，以防止导入失败
        class CalendarDB:
            def __init__(self, *args, **kwargs):
                self.enabled = False
            
            def get_task_metrics(self, *args, **kwargs):
                return None

def notify_calendar_changed_safe(reason):
    """安全地触发SSE通知，避免导入错误
    
    修复：不再通过直接导入 run.py 调用函数（该方式仅作用于当前进程，无法通知运行中的 Flask 服务），
    而是通过 HTTP 调用后端提供的 /api/calendar/notify 接口，由实际的 Web 进程统一负责广播 SSE 事件。
    """
    try:
        # 优先使用与 Flask 相同的 PORT 环境变量；允许通过 CALENDAR_SERVER_BASE 自定义服务基址
        port = os.environ.get("PORT", "5005")
        base_url = os.environ.get("CALENDAR_SERVER_BASE", f"http://127.0.0.1:{port}")
        requests.post(
            f"{base_url}/api/calendar/notify",
            json={"reason": reason},
            timeout=1
        )
    except Exception as e:
        # 仅输出提示，不影响主流程
        print(f"触发SSE通知失败: {e}")

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

# 全局的文件排序函数
def sort_file_by_name(file):
    """
    通用的文件排序函数，用于根据文件名智能排序
    支持多种格式的日期、期数、集数等提取和排序
    使用多级排序键，按日期、期数、上中下顺序排序
    如果以上均无法提取，则使用文件更新时间和拼音排序作为最后排序依据
    """
    if isinstance(file, dict) and file.get("dir", False):  # 跳过文件夹
        return (float('inf'), float('inf'), float('inf'), 0, "")

    # 获取文件名，支持字符串或文件对象
    if isinstance(file, dict):
        filename = file.get("file_name", "")
        # 获取更新时间作为第四级排序依据
        update_time = file.get("updated_at", 0)
    else:
        filename = file
        update_time = 0

    # 导入拼音排序工具用于第五级排序
    try:
        from app.utils.pinyin_sort import get_filename_pinyin_sort_key
        pinyin_sort_key = get_filename_pinyin_sort_key(filename)
    except ImportError:
        # 如果导入失败，使用简单的小写排序作为备用
        pinyin_sort_key = filename.lower()
    
    # 提取文件名，不含扩展名
    file_name_without_ext = os.path.splitext(filename)[0]
    
    # 初始化排序值
    date_value = float('inf')  # 日期键（第一级）
    episode_value = float('inf')  # 期数/集数键（第二级）
    segment_value = 0  # 上中下/其他细分键（第三级）
    
    # 1. 提取日期 - 第一级排序键
    
    # 1.1 YYYY-MM-DD 或 YYYY.MM.DD 或 YYYY/MM/DD 或 YYYY MM DD格式（四位年份）
    match_date_full = re.search(r'((?:19|20)\d{2})[-./\s](\d{1,2})[-./\s](\d{1,2})', filename)
    if match_date_full:
        year = int(match_date_full.group(1))
        month = int(match_date_full.group(2))
        day = int(match_date_full.group(3))
        date_value = year * 10000 + month * 100 + day
    
    # 1.2 YY-MM-DD 或 YY.MM.DD 或 YY/MM/DD 或 YY MM DD格式（两位年份）
    if date_value == float('inf'):
        match_yy_date = re.search(r'((?:19|20)?\d{2})[-./\s](\d{1,2})[-./\s](\d{1,2})', filename)
        if match_yy_date and len(match_yy_date.group(1)) == 2:
            year_str = match_yy_date.group(1)
            # 如果是两位年份，假设20xx年
            year = int("20" + year_str)
            month = int(match_yy_date.group(2))
            day = int(match_yy_date.group(3))
            date_value = year * 10000 + month * 100 + day
    
    # 1.3 完整的YYYYMMDD格式（无分隔符）
    if date_value == float('inf'):
        match_date_compact = re.search(r'((?:19|20)\d{2})(\d{2})(\d{2})', filename)
        if match_date_compact:
            year = int(match_date_compact.group(1))
            month = int(match_date_compact.group(2))
            day = int(match_date_compact.group(3))
            date_value = year * 10000 + month * 100 + day
    
    # 1.4 YYMMDD格式（两位年份，无分隔符）
    if date_value == float('inf'):
        match_yy_compact = re.search(r'(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)', filename)
        if match_yy_compact:
            year_str = match_yy_compact.group(1)
            # 检查月份和日期的有效性
            month = int(match_yy_compact.group(2))
            day = int(match_yy_compact.group(3))
            if 1 <= month <= 12 and 1 <= day <= 31:
                # 合理的月份和日期，假设为YY-MM-DD
                year = int("20" + year_str)
                date_value = year * 10000 + month * 100 + day
    
    # 1.5 MM/DD/YYYY 或 DD/MM/YYYY 格式
    if date_value == float('inf'):
        match_date_alt = re.search(r'(\d{1,2})[-./\s](\d{1,2})[-./\s]((?:19|20)\d{2})', filename)
        if match_date_alt:
            # 假设第一个是月，第二个是日（美式日期）
            month = int(match_date_alt.group(1))
            day = int(match_date_alt.group(2))
            year = int(match_date_alt.group(3))
            # 检查月份值，如果大于12可能是欧式日期格式（DD/MM/YYYY）
            if month > 12:
                month, day = day, month
            date_value = year * 10000 + month * 100 + day
    
    # 1.6 MM-DD 或 MM.DD 或 MM/DD格式（无年份，不包括空格分隔）
    if date_value == float('inf'):
        match_date_short = re.search(r'(?<!\d)(\d{1,2})[-./](\d{1,2})(?!\d)', filename)
        if match_date_short:
            # 假设第一个是月，第二个是日
            month = int(match_date_short.group(1))
            day = int(match_date_short.group(2))
            # 验证是否为有效的月日组合
            if ((month >= 1 and month <= 12 and day >= 1 and day <= 31) or
                (day >= 1 and day <= 12 and month >= 1 and month <= 31)):
                # 检查月份值，如果大于12可能是欧式日期格式（DD/MM）
                if month > 12:
                    month, day = day, month
                # 由于没有年份，使用一个较低的基数，确保任何有年份的日期都排在前面
                # 使用20000000作为基准，所以无年份日期都会排在有年份日期之后
                date_value = 20000000 + month * 100 + day
    
    # 2. 提取期数/集数 - 第二级排序键
    
    # 2.1 "第X期/集/话/部/篇" 格式（支持空格）
    match_chinese = re.search(r'第\s*(\d+)\s*[期集话部篇]', filename)
    if match_chinese:
        episode_value = int(match_chinese.group(1))

    # 2.1.1 "第[中文数字]期/集/话/部/篇" 格式（支持空格）
    if episode_value == float('inf'):
        match_chinese_num = re.search(r'第\s*([一二三四五六七八九十百千万零两]+)\s*[期集话部篇]', filename)
        if match_chinese_num:
            chinese_num = match_chinese_num.group(1)
            arabic_num = chinese_to_arabic(chinese_num)
            if arabic_num is not None:
                episode_value = arabic_num
    
    # 2.2 "X集/期/话" 格式
    if episode_value == float('inf'):
        match_chinese_simple = re.search(r'(\d+)[期集话]', filename)
        if match_chinese_simple:
            episode_value = int(match_chinese_simple.group(1))
    
    # 2.2.1 "[中文数字]集/期/话" 格式
    if episode_value == float('inf'):
        match_chinese_simple_num = re.search(r'([一二三四五六七八九十百千万零两]+)[期集话]', filename)
        if match_chinese_simple_num:
            chinese_num = match_chinese_simple_num.group(1)
            arabic_num = chinese_to_arabic(chinese_num)
            if arabic_num is not None:
                episode_value = arabic_num
    
    # 2.3 S01E01格式
    if episode_value == float('inf'):
        match_s_e = re.search(r'[Ss](\d+)[Ee](\d+)', filename)
        if match_s_e:
            season = int(match_s_e.group(1))
            episode = int(match_s_e.group(2))
            # 使用季*1000+集作为期数值
            episode_value = episode  # 只用集数作为排序键
    
    # 2.4 E01/EP01格式
    if episode_value == float('inf'):
        match_e = re.search(r'[Ee][Pp]?(\d+)', filename)
        if match_e:
            # 若数字位于含字母的中括号内部，跳过该匹配
            if not _in_alpha_brackets(filename, match_e.start(1), match_e.end(1)):
                episode_value = int(match_e.group(1))
    
    # 2.5 1x01格式
    if episode_value == float('inf'):
        match_x = re.search(r'(\d+)[Xx](\d+)', filename)
        if match_x:
            episode = int(match_x.group(2))
            episode_value = episode
    
    # 2.6 方括号/中括号包围的数字
    if episode_value == float('inf'):
        match_bracket = re.search(r'\[(\d+)\]|【(\d+)】', filename)
        if match_bracket:
            episode_value = int(match_bracket.group(1) if match_bracket.group(1) else match_bracket.group(2))
    
    # 2.7 其他数字格式（如果没有明确的期数）
    if episode_value == float('inf'):
        # 优先尝试纯数字文件名
        if file_name_without_ext.isdigit():
            episode_value = int(file_name_without_ext)
        else:
            # 预处理：移除分辨率标识（如 720p, 1080P, 2160p 等）
            filename_without_resolution = filename
            resolution_patterns = [
                r'\b\d+[pP]\b',  # 匹配 720p, 1080P, 2160p 等
                r'\b\d+x\d+\b',  # 匹配 1920x1080 等
                r'(?<!\d)[248]\s*[Kk](?!\d)',  # 匹配 2K/4K/8K
            ]

            for pattern in resolution_patterns:
                filename_without_resolution = re.sub(pattern, ' ', filename_without_resolution)

            # 否则尝试提取任何数字
            candidates = []
            for m in re.finditer(r'\d+', filename_without_resolution):
                num_str = m.group(0)
                # 过滤日期模式
                if is_date_format(num_str):
                    continue
                # 过滤中括号内且含字母的片段
                span_l, span_r = m.start(), m.end()
                if _in_alpha_brackets(filename_without_resolution, span_l, span_r):
                    continue
                try:
                    value = int(num_str)
                except ValueError:
                    continue
                if value > 9999:
                    continue
                candidates.append((m.start(), value))
            if candidates:
                candidates.sort(key=lambda x: x[0])
                episode_value = candidates[0][1]
    
    # 3. 提取上中下标记或其他细分 - 第三级排序键
    segment_base = 0  # 基础值：上=1, 中=2, 下=3
    sequence_number = 0  # 序号值：用于处理上中下后的数字或中文数字序号

    # 严格匹配上中下标记：支持多种格式
    # 1. 直接相邻：上集、期上
    # 2. 括号分隔：期（上）、集（中）
    # 3. 其他分隔符：期-上、集_中
    if re.search(r'上[集期话部篇]|[集期话部篇]上|[集期话部篇]\s*[（(]\s*上\s*[）)]|[集期话部篇]\s*[-_·丨]\s*上', filename):
        segment_base = 1
    elif re.search(r'中[集期话部篇]|[集期话部篇]中|[集期话部篇]\s*[（(]\s*中\s*[）)]|[集期话部篇]\s*[-_·丨]\s*中', filename):
        segment_base = 2
    elif re.search(r'下[集期话部篇]|[集期话部篇]下|[集期话部篇]\s*[（(]\s*下\s*[）)]|[集期话部篇]\s*[-_·丨]\s*下', filename):
        segment_base = 3

    # 统一的序号提取逻辑，支持多种分隔符和格式
    # 无论是否有上中下标记，都使用相同的序号提取逻辑

    # 定义序号提取的模式，使用正向匹配组合的方式
    # 这样可以精准匹配，避免误判"星期六"等内容
    sequence_patterns = [
        # 第+中文数字+期集话部篇+序号：第一期（一）、第五十六期-二、第 一 期 三
        (r'第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇]\s*[（(]\s*([一二三四五六七八九十百千万零两]+)\s*[）)]', 'chinese'),
        (r'第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇]\s*[（(]\s*(\d+)\s*[）)]', 'arabic'),
        (r'第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇]\s*[-_·丨]\s*([一二三四五六七八九十百千万零两]+)', 'chinese'),
        (r'第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇]\s*[-_·丨]\s*(\d+)', 'arabic'),
        (r'第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇]\s+([一二三四五六七八九十百千万零两]+)(?![一二三四五六七八九十])', 'chinese'),
        (r'第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇]\s+(\d+)(?!\d)', 'arabic'),
        (r'第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇]([一二三四五六七八九十])(?![一二三四五六七八九十])', 'chinese'),
        (r'第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇](\d+)(?!\d)', 'arabic'),

        # 第+阿拉伯数字+期集话部篇+序号：第1期（一）、第100期-二、第 1 期 三
        (r'第\s*\d+\s*[期集话部篇]\s*[（(]\s*([一二三四五六七八九十百千万零两]+)\s*[）)]', 'chinese'),
        (r'第\s*\d+\s*[期集话部篇]\s*[（(]\s*(\d+)\s*[）)]', 'arabic'),
        (r'第\s*\d+\s*[期集话部篇]\s*[-_·丨]\s*([一二三四五六七八九十百千万零两]+)', 'chinese'),
        (r'第\s*\d+\s*[期集话部篇]\s*[-_·丨]\s*(\d+)', 'arabic'),
        (r'第\s*\d+\s*[期集话部篇]\s+([一二三四五六七八九十百千万零两]+)(?![一二三四五六七八九十])', 'chinese'),
        (r'第\s*\d+\s*[期集话部篇]\s+(\d+)(?!\d)', 'arabic'),
        (r'第\s*\d+\s*[期集话部篇]([一二三四五六七八九十])(?![一二三四五六七八九十])', 'chinese'),
        (r'第\s*\d+\s*[期集话部篇](\d+)(?!\d)', 'arabic'),

        # 上中下+集期话部篇+序号：上集（一）、中部-二、下篇 三
        (r'[上中下][集期话部篇]\s*[（(]\s*([一二三四五六七八九十百千万零两]+)\s*[）)]', 'chinese'),
        (r'[上中下][集期话部篇]\s*[（(]\s*(\d+)\s*[）)]', 'arabic'),
        (r'[上中下][集期话部篇]\s*[-_·丨]\s*([一二三四五六七八九十百千万零两]+)', 'chinese'),
        (r'[上中下][集期话部篇]\s*[-_·丨]\s*(\d+)', 'arabic'),
        (r'[上中下][集期话部篇]\s+([一二三四五六七八九十百千万零两]+)(?![一二三四五六七八九十])', 'chinese'),
        (r'[上中下][集期话部篇]\s+(\d+)(?!\d)', 'arabic'),
        (r'[上中下][集期话部篇]([一二三四五六七八九十])(?![一二三四五六七八九十])', 'chinese'),
        (r'[上中下][集期话部篇](\d+)(?!\d)', 'arabic'),

        # 集期话部篇+上中下+序号：集上（一）、部中-二、篇下 三
        (r'[集期话部篇][上中下]\s*[（(]\s*([一二三四五六七八九十百千万零两]+)\s*[）)]', 'chinese'),
        (r'[集期话部篇][上中下]\s*[（(]\s*(\d+)\s*[）)]', 'arabic'),
        (r'[集期话部篇][上中下]\s*[-_·丨]\s*([一二三四五六七八九十百千万零两]+)', 'chinese'),
        (r'[集期话部篇][上中下]\s*[-_·丨]\s*(\d+)', 'arabic'),
        (r'[集期话部篇][上中下]\s+([一二三四五六七八九十百千万零两]+)(?![一二三四五六七八九十])', 'chinese'),
        (r'[集期话部篇][上中下]\s+(\d+)(?!\d)', 'arabic'),
        (r'[集期话部篇][上中下]([一二三四五六七八九十])(?![一二三四五六七八九十])', 'chinese'),
        (r'[集期话部篇][上中下](\d+)(?!\d)', 'arabic'),
    ]

    # 尝试匹配序号
    for pattern, num_type in sequence_patterns:
        match = re.search(pattern, filename)
        if match:
            if num_type == 'chinese':
                arabic_num = chinese_to_arabic(match.group(1))
                if arabic_num is not None:
                    sequence_number = arabic_num
                    # 如果之前没有检测到上中下标记，给一个基础值
                    if segment_base == 0:
                        segment_base = 1
                    break
            else:  # arabic
                sequence_number = int(match.group(1))
                # 如果之前没有检测到上中下标记，给一个基础值
                if segment_base == 0:
                    segment_base = 1
                break

    # 组合segment_value：基础值*1000 + 序号值，确保排序正确
    segment_value = segment_base * 1000 + sequence_number
    
    # 返回多级排序元组，加入更新时间作为第四级排序键，拼音排序作为第五级排序键
    return (date_value, episode_value, segment_value, update_time, pinyin_sort_key)


# 全局的剧集编号提取函数
def _in_alpha_brackets(text, start, end):
    """
    判断 [start,end) 范围内的数字是否位于"含字母的中括号对"内部。
    支持英文方括号 [] 和中文方括号 【】。
    要求：数字左侧最近的未闭合括号与右侧最近的对应闭合括号形成对，且括号内容包含字母。
    但是允许 E/e 和 EP/ep/Ep 这样的集数格式。
    """
    if start < 0 or end > len(text):
        return False
    
    # 检查英文方括号 []
    last_open_en = text.rfind('[', 0, start)
    if last_open_en != -1:
        close_before_en = text.rfind(']', 0, start)
        if close_before_en == -1 or close_before_en < last_open_en:
            close_after_en = text.find(']', end)
            if close_after_en != -1:
                content = text[last_open_en + 1:close_after_en]
                if _check_bracket_content(content):
                    return True
    
    # 检查中文方括号 【】
    last_open_cn = text.rfind('【', 0, start)
    if last_open_cn != -1:
        close_before_cn = text.rfind('】', 0, start)
        if close_before_cn == -1 or close_before_cn < last_open_cn:
            close_after_cn = text.find('】', end)
            if close_after_cn != -1:
                content = text[last_open_cn + 1:close_after_cn]
                if _check_bracket_content(content):
                    return True
    
    return False

def _check_bracket_content(content):
    """
    检查括号内容是否应该被排除
    """
    # 检查是否包含字母
    has_letters = bool(re.search(r'[A-Za-z]', content))
    if not has_letters:
        return False
    
    # 如果是 E/e 或 EP/ep/Ep 格式，则允许通过
    if re.match(r'^[Ee][Pp]?\d+$', content):
        return False
    
    return True
def extract_episode_number(filename, episode_patterns=None, config_data=None):
    """
    从文件名中提取剧集编号
    
    Args:
        filename: 文件名
        episode_patterns: 可选的自定义匹配模式列表
        config_data: 可选的任务配置数据
        
    Returns:
        int: 提取到的剧集号，如果无法提取则返回None
    """
    # 首先去除文件扩展名
    file_name_without_ext = os.path.splitext(filename)[0]
    
    # 特判：SxxEyy.zz 模式（例如 S01E11.11），在日期清洗前优先识别
    m_spec = re.search(r'[Ss](\d+)[Ee](\d{1,2})[._\-/]\d{1,2}', file_name_without_ext)
    if m_spec:
        return int(m_spec.group(2))
    
    # 预处理顺序调整：先移除技术规格，再移除日期，降低误判
    # 预处理：先移除技术规格信息，避免误提取技术参数中的数字为集编号
    filename_without_dates = file_name_without_ext
    tech_spec_patterns = [
        # 分辨率相关（限定常见p档）
        r'(?:240|360|480|540|720|900|960|1080|1440|2160|4320)[pP]',
        # 常见分辨率 WxH（白名单）
        r'\b(?:640x360|640x480|720x480|720x576|854x480|960x540|1024x576|1280x720|1280x800|1280x960|1366x768|1440x900|1600x900|1920x1080|2560x1080|2560x1440|3440x1440|3840x1600|3840x2160|4096x2160|7680x4320)\b',
        r'(?<!\d)[248]\s*[Kk](?!\d)',  # 匹配 2K/4K/8K
        
        # 视频编码相关（包含数字的编码）
        r'\b[Hh]\.?(?:264|265)\b',  # 匹配 h264/h265, h.264/h.265 等
        r'\b[Xx](?:264|265)\b',     # 匹配 x264/x265, X264/X265
        
        # 音频采样率（限定常见采样率）
        r'\b(?:44\.1|48|96)\s*[Kk][Hh][Zz]\b',
        r'\b(?:44100|48000|96000)\s*[Hh][Zz]\b',
        
        # 比特率
        # 常见码率（白名单）
        r'\b(?:64|96|128|160|192|256|320)\s*[Kk][Bb][Pp][Ss]\b',
        r'\b(?:1|1\.5|2|2\.5|3|4|5|6|8|10|12|15|20|25|30|35|40|50|80|100)\s*[Mm][Bb][Pp][Ss]\b',
        
        # 视频相关
        # 位深（白名单）
        r'\b(?:8|10|12)\s*[Bb][Ii][Tt]s?\b',
        # 严格限定常见帧率，避免将 "07.30FPS" 视为帧率从而连带清除集数
        r'\b(?:23\.976|29\.97|59\.94|24|25|30|50|60|90|120)\s*[Ff][Pp][Ss]\b',
        # 无空格的帧率格式（如 60fps, 30fps 等）
        r'(?:23\.976|29\.97|59\.94|24|25|30|50|60|90|120)[Ff][Pp][Ss]',
        # 中文帧率：带空格的如 "25 帧"、"30 帧"
        r'\b(?:23\.976|29\.97|59\.94|24|25|30|50|60|90|120)\s*帧\b',
        # 中文帧率：无空格的如 "25帧"、"30帧"、"60帧"
        r'(?:23\.976|29\.97|59\.94|24|25|30|50|60|90|120)帧',
        # 中文“帧率”：带空格与无空格（如 "60帧率"、"60 帧率"）
        r'\b(?:23\.976|29\.97|59\.94|24|25|30|50|60|90|120)\s*帧率\b',
        r'(?:23\.976|29\.97|59\.94|24|25|30|50|60|90|120)帧率',
        
        # 频率相关
        # 频率相关（白名单，含空格/无空格）
        r'\b(?:100|144|200|240|400|800)\s*[Mm][Hh][Zz]\b',
        r'\b(?:1|1\.4|2|2\.4|5|5\.8)\s*[Gg][Hh][Zz]\b',
        r'\b(?:100|144|200|240|400|800)[Mm][Hh][Zz]\b',
        r'\b(?:1|1\.4|2|2\.4|5|5\.8)[Gg][Hh][Zz]\b',
        
        # 声道相关（限定常见声道）
        r'\b(?:1\.0|2\.0|5\.1|7\.1)\s*[Cc][Hh]\b',
        r'\b(?:1\.0|2\.0|5\.1|7\.1)[Cc][Hh]\b',
        r'\b(?:1\.0|2\.0|5\.1|7\.1)\s*[Cc][Hh][Aa][Nn][Nn][Ee][Ll]\b',
        
        # 位深相关
        r'\b\d+\.?\d*\s*[Bb][Ii][Tt][Ss]\b',  # 匹配 128bits, 256bits 等
        
        # 其他技术参数
        # 其他技术参数（白名单）
        r'\b(?:8|12|16|24|32|48|50|64|108)\s*[Mm][Pp]\b',
        r'\b(?:720|1080|1440|1600|1920|2160|4320)\s*[Pp][Ii][Xx][Ee][Ll]\b',
        r'\b(?:5400|7200)\s*[Rr][Pp][Mm]\b',
        r'\b(?:720|1080|1440|1600|1920|2160|4320)[Pp][Ii][Xx][Ee][Ll]\b',
        r'\b(?:5400|7200)[Rr][Pp][Mm]\b',
    ]
    for pattern in tech_spec_patterns:
        filename_without_dates = re.sub(pattern, ' ', filename_without_dates, flags=re.IGNORECASE)

    # 预处理：移除季编号标识，避免误提取季编号为集编号（放在日期清洗之前）
    season_patterns = [
        r'[Ss]\d+(?![Ee])',  # S1, S01 (但不包括S01E01中的S01)
        r'[Ss]\s+\d+',  # S 1, S 01
        r'Season\s*\d+',  # Season1, Season 1
        r'第\s*\d+\s*季',  # 第1季, 第 1 季
        r'第\s*[一二三四五六七八九十百千万零两]+\s*季',  # 第一季, 第 二 季
    ]

    for pattern in season_patterns:
        filename_without_dates = re.sub(pattern, ' ', filename_without_dates, flags=re.IGNORECASE)

    # 预处理：再排除文件名中可能是日期的部分，避免误识别
    date_patterns = [
        # YYYY-MM-DD 或 YYYY.MM.DD 或 YYYY/MM/DD 或 YYYY MM DD格式（四位年份）
        r'((?:19|20)\d{2})[-./\s](\d{1,2})[-./\s](\d{1,2})',
        # YY-MM-DD 或 YY.MM.DD 或 YY/MM/DD 或 YY MM DD格式（两位年份），但排除E/EP后面的数字
        r'(?<![Ee])(?<![Ee][Pp])((?:19|20)?\d{2})[-./\s](\d{1,2})[-./\s](\d{1,2})',
        # 完整的YYYYMMDD格式（无分隔符）
        r'((?:19|20)\d{2})(\d{2})(\d{2})',
        # YYMMDD格式（两位年份，无分隔符）
        r'(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)',
        # MM/DD/YYYY 或 DD/MM/YYYY 格式
        r'(\d{1,2})[-./\s](\d{1,2})[-./\s]((?:19|20)\d{2})',
        # MM-DD 或 MM.DD 或 MM/DD格式（无年份，不包括空格分隔），但排除E/EP后面的数字和分辨率标识
        r'(?<![Ee])(?<![Ee][Pp])(?<!\d)(\d{1,2})[-./](\d{1,2})(?!\d)(?![KkPp])',
    ]
    
    # 从已清除技术规格的信息中移除日期部分
    for pattern in date_patterns:
        matches = re.finditer(pattern, filename_without_dates)
        for match in matches:
            # 检查匹配的内容是否确实是日期
            date_str = match.group(0)
            # 针对短日期 x.x 或 xx.xx：前一字符为 E/e/EP/Ep 时不清洗（保护 E11.11, EP08.7 场景）
            if re.match(r'^\d{1,2}[./-]\d{1,2}$', date_str):
                prev_chars = filename_without_dates[max(0, match.start()-2):match.start()]
                if prev_chars.endswith(('E', 'e', 'EP', 'Ep', 'ep')):
                    continue
            # 保护 E/EP 格式的集编号，如 E07, E14, EP08 等
            if re.match(r'^\d+$', date_str) and len(date_str) <= 3:
                prev_chars = filename_without_dates[max(0, match.start()-2):match.start()]
                if prev_chars.endswith(('E', 'e', 'EP', 'Ep', 'ep')):
                    continue
            month = None
            day = None
            
            # 根据不同模式提取月和日
            if len(match.groups()) >= 3:
                if re.match(r'(?:19|20)\d{2}', match.group(1)):  # 首个分组是年份
                    month = int(match.group(2))
                    day = int(match.group(3))
                elif re.match(r'(?:19|20)\d{2}', match.group(3)):  # 末尾分组是年份
                    month = int(match.group(1))
                    day = int(match.group(2))
                else:
                    # 处理两位数年份的情况（如25.03.21）
                    try:
                        # 假设第一个是年份，第二个是月，第三个是日
                        year = int(match.group(1))
                        month = int(match.group(2))
                        day = int(match.group(3))
                        
                        # 如果月和日在有效范围内，则这可能是一个日期
                        if 1 <= month <= 12 and 1 <= day <= 31:
                            pass  # 保持month和day的值
                        else:
                            # 尝试另一种解释：月.日.年
                            month = int(match.group(1))
                            day = int(match.group(2))
                            # 检查月和日的有效性
                            if not (1 <= month <= 12 and 1 <= day <= 31):
                                # 仍然无效，重置month和day
                                month = None
                                day = None
                    except ValueError:
                        # 转换失败，保持month和day为None
                        pass
            elif len(match.groups()) == 2:
                # 处理两个分组的模式（如MM-DD格式）
                try:
                    month = int(match.group(1))
                    day = int(match.group(2))
                    
                    # 检查月和日的有效性
                    if not (1 <= month <= 12 and 1 <= day <= 31):
                        # 尝试另一种解释：日-月
                        month = int(match.group(2))
                        day = int(match.group(1))
                        if not (1 <= month <= 12 and 1 <= day <= 31):
                            # 仍然无效，重置month和day
                            month = None
                            day = None
                except ValueError:
                    # 转换失败，保持month和day为None
                    pass
            
            # 如果能确定月日且是有效的日期，则从文件名中删除该日期
            if month and day and 1 <= month <= 12 and 1 <= day <= 31:
                filename_without_dates = filename_without_dates.replace(date_str, " ")

    # 优先匹配SxxExx格式
    match_s_e = re.search(r'[Ss](\d+)[Ee](\d+)', filename_without_dates)
    if match_s_e:
        # 直接返回E后面的集数
        return int(match_s_e.group(2))
    
    # 其次匹配E01格式
    match_e = re.search(r'[Ee][Pp]?(\d+)', filename_without_dates)
    if match_e:
        # 若数字位于含字母的中括号内部，跳过该匹配
        if not _in_alpha_brackets(filename_without_dates, match_e.start(1), match_e.end(1)):
            return int(match_e.group(1))
    
    # 添加中文数字匹配模式（优先匹配）
    chinese_patterns = [
        r'第([一二三四五六七八九十百千万零两]+)集',
        r'第([一二三四五六七八九十百千万零两]+)期',
        r'第([一二三四五六七八九十百千万零两]+)话',
        r'([一二三四五六七八九十百千万零两]+)集',
        r'([一二三四五六七八九十百千万零两]+)期',
        r'([一二三四五六七八九十百千万零两]+)话'
    ]
    
    # 优先匹配中文数字模式
    for pattern_regex in chinese_patterns:
        try:
            match = re.search(pattern_regex, filename_without_dates)
            if match:
                chinese_num = match.group(1)
                arabic_num = chinese_to_arabic(chinese_num)
                if arabic_num is not None:
                    return arabic_num
        except:
            continue
    
    # 尝试匹配更多格式（注意：避免匹配季数）
    default_patterns = [
        r'第(\d+)集',
        r'第(\d+)期',
        r'第(\d+)话',
        r'(?<!第\d+季\s*)(\d+)集',  # 避免匹配"第X季 Y集"中的季数
        r'(?<!第\d+季\s*)(\d+)期',  # 避免匹配"第X季 Y期"中的季数
        r'(?<!第\d+季\s*)(\d+)话',  # 避免匹配"第X季 Y话"中的季数
        r'[Ee][Pp]?(\d+)',
        r'\[(\d+)\]',
        r'【(\d+)】',
        # 中文数字匹配模式
        r'第([一二三四五六七八九十百千万零两]+)集',
        r'第([一二三四五六七八九十百千万零两]+)期',
        r'第([一二三四五六七八九十百千万零两]+)话',
        r'([一二三四五六七八九十百千万零两]+)集',
        r'([一二三四五六七八九十百千万零两]+)期',
        r'([一二三四五六七八九十百千万零两]+)话',
        # 先匹配"前方有分隔符"的数字，避免后一个规则优先命中单字符
        r'[- _\s\.]([0-9]+)',
        r'(?:^|[^0-9])(\d+)(?=[- _\s\.])',
        # 新增：文件名起始纯数字后接非数字（如 1094(1).mp4）
        r'^(\d+)(?=\D)'
    ]
    
    # 构建最终的patterns：默认模式 + 用户补充模式
    patterns = []
    
    # 1. 首先添加默认模式（除了最后的纯数字模式）
    default_non_numeric = [p for p in default_patterns if not re.match(r'^[- _\\s\\.]\([0-9]+\)', p) and not re.match(r'^\([^)]*\)\([0-9]+\)', p)]
    patterns.extend(default_non_numeric)
    
    # 2. 添加用户补充的模式
    user_patterns = []
    
    # 检查传入的episode_patterns参数
    if episode_patterns:
        user_patterns = [p.get("regex", "(\\d+)") for p in episode_patterns if p.get("regex", "").strip()]
    # 如果配置了task的自定义规则
    elif config_data and isinstance(config_data.get("episode_patterns"), list) and config_data["episode_patterns"]:
        user_patterns = [p.get("regex", "(\\d+)") for p in config_data["episode_patterns"] if p.get("regex", "").strip()]
    # 尝试从全局配置获取
    elif 'CONFIG_DATA' in globals() and isinstance(globals()['CONFIG_DATA'].get("episode_patterns"), list) and globals()['CONFIG_DATA']["episode_patterns"]:
        user_patterns = [p.get("regex", "(\\d+)") for p in globals()['CONFIG_DATA']["episode_patterns"] if p.get("regex", "").strip()]
    
    # 添加用户补充的模式
    patterns.extend(user_patterns)
    
    # 3. 最后添加默认的纯数字模式
    default_numeric = [p for p in default_patterns if re.match(r'^[- _\\s\\.]\([0-9]+\)', p) or re.match(r'^\([^)]*\)\([0-9]+\)', p)]
    patterns.extend(default_numeric)
    
    # 尝试使用每个正则表达式匹配文件名（使用不含日期的文件名）
    for pattern_regex in patterns:
        try:
            # 特殊处理：如果是包含多个捕获组的复合正则表达式
            if '|' in pattern_regex and '(' in pattern_regex:
                # 先尝试匹配集/期/话相关的模式，避免误匹配季数
                episode_specific_patterns = [
                    r'第(\d+)集', r'第(\d+)期', r'第(\d+)话',
                    r'(\d+)集', r'(\d+)期', r'(\d+)话'
                ]

                for ep_pattern in episode_specific_patterns:
                    ep_match = re.search(ep_pattern, filename_without_dates)
                    if ep_match:
                        # 检查这个匹配是否紧跟在"第X季"后面，如果是则跳过
                        match_start = ep_match.start()
                        prefix = filename_without_dates[:match_start]
                        if re.search(r'第\d+季\s*$', prefix):
                            continue  # 跳过紧跟在季数后的匹配

                        episode_num = int(ep_match.group(1))

                        # 检查提取的数字是否可能是日期的一部分
                        if str(episode_num).isdigit() and is_date_format(str(episode_num)):
                            continue

                        return episode_num

                # 如果集/期/话模式都没匹配到，再尝试原始的复合正则表达式
                match = re.search(pattern_regex, filename_without_dates)
                if match:
                    # 遍历所有捕获组，找到第一个非空的
                    for group_num in range(1, len(match.groups()) + 1):
                        if match.group(group_num):
                            # 若数字位于含字母的中括号内部，跳过
                            span_l, span_r = match.start(group_num), match.end(group_num)
                            if _in_alpha_brackets(filename_without_dates, span_l, span_r):
                                continue
                            episode_num = int(match.group(group_num))

                            # 检查提取的数字是否可能是日期的一部分
                            if str(episode_num).isdigit() and is_date_format(str(episode_num)):
                                continue

                            # 额外检查：如果匹配的数字来自"第X季"格式，跳过
                            match_start = match.start()
                            match_end = match.end()

                            # 检查匹配前后的上下文，看是否是"第X季"格式
                            context_start = max(0, match_start - 2)
                            context_end = min(len(filename_without_dates), match_end + 1)
                            context = filename_without_dates[context_start:context_end]

                            if re.search(r'第\d+季', context):
                                continue

                            return episode_num
            else:
                # 单一模式的正则表达式
                match = re.search(pattern_regex, filename_without_dates)
                if match:
                    # 若数字位于含字母的中括号内部，跳过
                    span_l, span_r = match.start(1), match.end(1)
                    if _in_alpha_brackets(filename_without_dates, span_l, span_r):
                        continue
                    episode_num = int(match.group(1))

                    # 检查提取的数字是否可能是日期的一部分
                    if str(episode_num).isdigit() and is_date_format(str(episode_num)):
                        continue

                    return episode_num
        except:
            continue
    
    # 如果文件名是纯数字，且不是日期格式，则可能是剧集号
    if filename_without_dates.isdigit() and not is_date_format(filename_without_dates):
        return int(filename_without_dates)
    
    # 最后尝试提取任何数字，但要排除日期可能性
    candidates = []
    for m in re.finditer(r'\d+', filename_without_dates):
        num_str = m.group(0)
        # 过滤日期模式
        if is_date_format(num_str):
            continue
        # 过滤中括号内且含字母的片段
        span_l, span_r = m.start(), m.end()
        if _in_alpha_brackets(filename_without_dates, span_l, span_r):
            continue
        try:
            value = int(num_str)
        except ValueError:
            continue
        if value > 9999:
            continue
        candidates.append((m.start(), value))
    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]
 
    return None

# 全局变量
VERSION = "2.9.0"
CONFIG_PATH = "quark_config.json"
COOKIE_PATH = "quark_cookie.txt"
CONFIG_DATA = {}
LOG_LIST = []
NOTIFYS = []

def is_date_format(number_str):
    """
    判断一个纯数字字符串是否可能是日期格式
    支持的格式：YYYYMMDD, YYMMDD
    """
    # 判断YYYYMMDD格式 (8位数字)
    if len(number_str) == 8 and number_str.startswith('20'):
        year = int(number_str[:4])
        month = int(number_str[4:6])
        day = int(number_str[6:8])
        
        # 简单检查月份和日期是否有效
        if 1 <= month <= 12 and 1 <= day <= 31:
            # 可能是日期格式
            return True
    
    # 判断YYMMDD格式 (6位数字)
    elif len(number_str) == 6:
        year_str = number_str[:2]
        month = int(number_str[2:4])
        day = int(number_str[4:6])
        
        # 检查月份和日期是否有效
        if 1 <= month <= 12 and 1 <= day <= 31:
            # 可能是日期格式
            return True
    
    # 不再将4位纯数字按MMDD视为日期，避免误伤集号（如1124）
    
    # 其他格式不视为日期格式
    return False

def chinese_to_arabic(chinese):
    """
    将中文数字转换为阿拉伯数字
    支持格式：一、二、三、四、五、六、七、八、九、十、百、千、万
    以及：零、两（特殊处理为2）
    
    Args:
        chinese: 中文数字字符串
        
    Returns:
        int: 转换后的阿拉伯数字，如果无法转换则返回None
    """
    if not chinese:
        return None
        
    # 数字映射
    digit_map = {
        '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, 
        '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, 
        '两': 2
    }
    
    # 单位映射
    unit_map = {
        '十': 10, 
        '百': 100, 
        '千': 1000, 
        '万': 10000
    }
    
    # 如果是单个字符，直接返回对应数字
    if len(chinese) == 1:
        if chinese == '十':
            return 10
        return digit_map.get(chinese)
    
    result = 0
    section = 0
    number = 0
    
    # 从左向右处理
    for i in range(len(chinese)):
        char = chinese[i]
        
        if char in digit_map:
            number = digit_map[char]
        elif char in unit_map:
            unit = unit_map[char]
            # 如果前面没有数字，默认为1，例如"十"表示1*10=10
            section += (number or 1) * unit
            number = 0
            
            # 如果是万级单位，累加到结果并重置section
            if unit == 10000:
                result += section
                section = 0
        else:
            # 非法字符
            return None
    
    # 加上最后的数字和小节
    result += section + number
    
    return result

# 兼容青龙
try:
    from treelib import Tree
except:
    print("正在尝试自动安装依赖...")
    os.system("pip3 install treelib &> /dev/null")
    from treelib import Tree


MAGIC_REGEX = {
    "$TV": {
        "pattern": r".*?([Ss]\d{1,2})?(?:[第EePpXx\.\-\_\( ]{1,2}|^)(\d{1,3})(?!\d).*?\.(mp4|mkv)",
        "replace": r"\1E\2.\3",
    },
    "$BLACK_WORD": {
        "pattern": r"^(?!.*纯享)(?!.*加更)(?!.*超前企划)(?!.*训练室)(?!.*蒸蒸日上).*",
        "replace": "",
    },
}


# 发送通知消息
def send_ql_notify(title, body):
    try:
        # 导入通知模块
        import notify

        # 如未配置 push_config 则使用青龙环境通知设置
        if CONFIG_DATA.get("push_config"):
            notify.push_config = CONFIG_DATA["push_config"].copy()
            notify.push_config["CONSOLE"] = notify.push_config.get("CONSOLE", True)
        notify.send(title, body)
    except Exception as e:
        if e:
            print("发送通知消息失败！")


# 添加消息
def add_notify(text):
    global NOTIFYS
    # 防止重复添加相同的通知
    # 但是对于文件树显示，允许相同文本（因为可能是不同目录下的同名文件）
    # 通过检查文本是否包含文件树前缀（├── 或 └──）来判断
    is_file_tree_line = "├── " in text or "└── " in text
    if text in NOTIFYS and not is_file_tree_line:
        return text
    
    # 检查推送通知类型配置
    push_notify_type = CONFIG_DATA.get("push_notify_type", "full")
    
    # 定义关键字（复用于过滤与分隔）
    failure_keywords = ["❌", "❗", "失败", "失效", "错误", "异常", "无效", "登录失败"]
    invalid_keywords = ["分享资源已失效", "分享详情获取失败", "分享为空", "文件已被分享者删除"]
    
    # 如果设置为仅推送成功信息，则过滤掉失败和错误信息
    if push_notify_type == "success_only":
        # 检查是否包含失败或错误相关的关键词
        if any(keyword in text for keyword in failure_keywords):
            # 只打印到控制台，不添加到通知列表
            print(text)
            return text
    
    # 如果设置为排除失效信息，则过滤掉资源失效信息，但保留转存失败信息
    elif push_notify_type == "exclude_invalid":
        # 检查是否包含资源失效相关的关键词（主要是分享资源失效）
        if any(keyword in text for keyword in invalid_keywords):
            # 只打印到控制台，不添加到通知列表
            print(text)
            return text
    
    # 在每个任务块之间插入一个空行，增强可读性
    # 成功块：以“✅《”开头；失败/错误块：以“❌”“❗”开头或包含失败相关关键词
    is_success_block = text.startswith("✅《")
    is_failure_block = text.startswith("❌") or text.startswith("❗") or any(k in text for k in failure_keywords if k not in ["✅"])
    if is_success_block or is_failure_block:
        if NOTIFYS and NOTIFYS[-1] != "":
            NOTIFYS.append("")
            # 仅在通知体中添加空行，不额外打印控制台空行
    
    NOTIFYS.append(text)
    print(text)
    return text


# 格式化文件显示，统一图标和文件名之间的空格
def format_file_display(prefix, icon, name):
    """
    格式化文件/文件夹的显示，确保图标和名称之间只有一个空格
    
    Args:
        prefix: 树形结构的前缀（如"├── "）
        icon: 文件/文件夹图标
        name: 文件/文件夹名称
        
    Returns:
        格式化后的显示字符串
    """
    # 去除图标和名称中可能存在的空格
    clean_icon = icon.strip() if icon else ""
    clean_name = name.strip() if name else ""
    
    # 如果有图标，确保图标和名称之间只有一个空格
    if clean_icon:
        return f"{prefix}{clean_icon} {clean_name}"
    else:
        return f"{prefix}{clean_name}"

# 定义一个通用的文件类型图标选择函数
def get_file_icon(file_name, is_dir=False):
    """根据文件扩展名返回对应的图标"""
    # 如果是文件夹，直接返回文件夹图标
    if is_dir:
        return "📁"

    # 文件名转小写便于匹配
    lower_name = file_name.lower()

    # 视频文件
    if any(lower_name.endswith(ext) for ext in ['.mp4', '.mkv', '.avi', '.mov', '.rmvb', '.flv', '.wmv', '.m4v', '.ts', '.webm', '.3gp', '.f4v']):
        return "🎞️"

    # 图片文件
    if any(lower_name.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tiff', '.svg']):
        return "🖼️"

    # 音频文件
    if any(lower_name.endswith(ext) for ext in ['.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma']):
        return "🎵"

    # 文档文件
    if any(lower_name.endswith(ext) for ext in ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.md', '.csv']):
        return "📄"

    # 压缩文件
    if any(lower_name.endswith(ext) for ext in ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2']):
        return "📦"

    # 代码文件
    if any(lower_name.endswith(ext) for ext in ['.py', '.js', '.html', '.css', '.java', '.c', '.cpp', '.php', '.go', '.json']):
        return "📝"

    # 字幕文件
    if any(lower_name.endswith(ext) for ext in ['.srt', '.ass', '.ssa', '.vtt', '.sup']):
        return "💬"

    # 歌词文件
    if any(lower_name.endswith(ext) for ext in ['.lrc']):
        return "💬"

    # 默认图标（其他文件类型）
    return ""

# 定义一个函数来去除文件名中的所有图标
def remove_file_icons(filename):
    """去除文件名开头的所有图标"""
    # 定义所有可能的文件图标
    icons = ["🎞️", "🖼️", "🎵", "📄", "📦", "📝", "💬", "📁"]

    # 去除开头的图标和空格
    clean_name = filename
    for icon in icons:
        if clean_name.startswith(icon):
            clean_name = clean_name[len(icon):].lstrip()
            break

    return clean_name

# 定义一个函数来检测字幕文件并应用语言代码后缀
def apply_subtitle_naming_rule(filename, task_settings):
    """
    检测字幕文件并应用语言代码后缀
    
    Args:
        filename: 原始文件名
        task_settings: 任务设置，包含字幕命名规则配置
    
    Returns:
        处理后的文件名
    """
    # 从任务设置中获取字幕命名规则配置
    subtitle_add_language_code = task_settings.get("subtitle_add_language_code", False)
    subtitle_naming_rule = task_settings.get("subtitle_naming_rule", "zh")
    
    # 如果任务设置中没有配置，尝试从全局配置中获取
    if not subtitle_add_language_code:
        try:
            if 'CONFIG_DATA' in globals() and CONFIG_DATA:
                global_task_settings = CONFIG_DATA.get("task_settings", {})
                subtitle_add_language_code = global_task_settings.get("subtitle_add_language_code", False)
                subtitle_naming_rule = global_task_settings.get("subtitle_naming_rule", "zh")
        except (KeyError, AttributeError, TypeError):
            # 配置获取失败，使用默认值
            pass
    
    # 检查是否启用了字幕命名规则
    if not subtitle_add_language_code or not subtitle_naming_rule:
        return filename
    
    # 检测是否为字幕文件
    lower_name = filename.lower()
    subtitle_extensions = ['.srt', '.ass', '.ssa', '.vtt', '.sup']
    
    if not any(lower_name.endswith(ext) for ext in subtitle_extensions):
        return filename
    
    # 分离文件名和扩展名
    name_without_ext, ext = os.path.splitext(filename)
    
    # 检查是否已经包含语言代码后缀，避免重复添加
    if f".{subtitle_naming_rule}" in name_without_ext:
        return filename
    
    # 添加语言代码后缀
    return f"{name_without_ext}.{subtitle_naming_rule}{ext}"


class Config:
    # 下载配置
    def download_file(url, save_path):
        response = requests.get(url)
        if response.status_code == 200:
            with open(save_path, "wb") as file:
                file.write(response.content)
            return True
        else:
            return False

    # 读取 JSON 文件内容
    def read_json(config_path):
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data

    # 将数据写入 JSON 文件
    def write_json(config_path, data):
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, sort_keys=False, indent=2)

    # 读取CK
    def get_cookies(cookie_val):
        if isinstance(cookie_val, list):
            return cookie_val
        elif cookie_val:
            if "\n" in cookie_val:
                return cookie_val.split("\n")
            else:
                return [cookie_val]
        else:
            return False

    def load_plugins(plugins_config={}, plugins_dir="plugins"):
        PLUGIN_FLAGS = os.environ.get("PLUGIN_FLAGS", "").split(",")
        plugins_available = {}
        task_plugins_config = {}
        all_modules = [
            f.replace(".py", "") for f in os.listdir(plugins_dir) if f.endswith(".py")
        ]
        # 调整模块优先级
        priority_path = os.path.join(plugins_dir, "_priority.json")
        try:
            with open(priority_path, encoding="utf-8") as f:
                priority_modules = json.load(f)
            if priority_modules:
                all_modules = [
                    module for module in priority_modules if module in all_modules
                ] + [module for module in all_modules if module not in priority_modules]
        except (FileNotFoundError, json.JSONDecodeError):
            priority_modules = []
        for module_name in all_modules:
            if f"-{module_name}" in PLUGIN_FLAGS:
                continue
            try:
                module = importlib.import_module(f"{plugins_dir}.{module_name}")
                ServerClass = getattr(module, module_name.capitalize())
                # 检查配置中是否存在该模块的配置
                if module_name in plugins_config:
                    plugin = ServerClass(**plugins_config[module_name])
                    plugins_available[module_name] = plugin
                else:
                    plugin = ServerClass()
                    plugins_config[module_name] = plugin.default_config
                # 检查插件是否支持单独任务配置
                if hasattr(plugin, "default_task_config"):
                    task_plugins_config[module_name] = plugin.default_task_config
            except (ImportError, AttributeError) as e:
                print(f"载入模块 {module_name} 失败: {e}")
        print()
        return plugins_available, plugins_config, task_plugins_config

    def breaking_change_update(config_data):
        if config_data.get("emby"):
            print("🔼 Update config v0.3.6.1 to 0.3.7")
            config_data.setdefault("media_servers", {})["emby"] = {
                "url": config_data["emby"]["url"],
                "token": config_data["emby"]["apikey"],
            }
            del config_data["emby"]
            for task in config_data.get("tasklist", {}):
                task["media_id"] = task.get("emby_id", "")
                if task.get("emby_id"):
                    del task["emby_id"]
        if config_data.get("media_servers"):
            print("🔼 Update config v0.3.8 to 0.3.9")
            config_data["plugins"] = config_data.get("media_servers")
            del config_data["media_servers"]
            for task in config_data.get("tasklist", {}):
                task["addition"] = {
                    "emby": {
                        "media_id": task.get("media_id", ""),
                    }
                }
                if task.get("media_id"):
                    del task["media_id"]
                    


class Quark:
    BASE_URL = "https://drive-pc.quark.cn"
    BASE_URL_APP = "https://drive-m.quark.cn"
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) quark-cloud-drive/3.14.2 Chrome/112.0.5615.165 Electron/24.1.3.8 Safari/537.36 Channel/pckk_other_ch"

    def __init__(self, cookie, index=None):
        self.cookie = cookie.strip()
        self.index = index + 1
        self.is_active = False
        self.nickname = ""
        self.mparam = self._match_mparam_form_cookie(cookie)
        self.savepath_fid = {"/": "0"}

    def _match_mparam_form_cookie(self, cookie):
        mparam = {}
        kps_match = re.search(r"(?<!\w)kps=([a-zA-Z0-9%+/=]+)[;&]?", cookie)
        sign_match = re.search(r"(?<!\w)sign=([a-zA-Z0-9%+/=]+)[;&]?", cookie)
        vcode_match = re.search(r"(?<!\w)vcode=([a-zA-Z0-9%+/=]+)[;&]?", cookie)
        if kps_match and sign_match and vcode_match:
            mparam = {
                "kps": kps_match.group(1).replace("%25", "%"),
                "sign": sign_match.group(1).replace("%25", "%"),
                "vcode": vcode_match.group(1).replace("%25", "%"),
            }
        return mparam

    def _send_request(self, method, url, **kwargs):
        headers = {
            "cookie": self.cookie,
            "content-type": "application/json",
            "user-agent": self.USER_AGENT,
        }
        if "headers" in kwargs:
            headers = kwargs["headers"]
            del kwargs["headers"]
        if self.mparam and "share" in url and self.BASE_URL in url:
            url = url.replace(self.BASE_URL, self.BASE_URL_APP)
            kwargs["params"].update(
                {
                    "device_model": "M2011K2C",
                    "entry": "default_clouddrive",
                    "_t_group": "0%3A_s_vp%3A1",
                    "dmn": "Mi%2B11",
                    "fr": "android",
                    "pf": "3300",
                    "bi": "35937",
                    "ve": "7.4.5.680",
                    "ss": "411x875",
                    "mi": "M2011K2C",
                    "nt": "5",
                    "nw": "0",
                    "kt": "4",
                    "pr": "ucpro",
                    "sv": "release",
                    "dt": "phone",
                    "data_from": "ucapi",
                    "kps": self.mparam.get("kps"),
                    "sign": self.mparam.get("sign"),
                    "vcode": self.mparam.get("vcode"),
                    "app": "clouddrive",
                    "kkkk": "1",
                }
            )
            del headers["cookie"]
        try:
            response = requests.request(method, url, headers=headers, **kwargs)
            # print(f"{response.text}")
            # response.raise_for_status()  # 检查请求是否成功，但返回非200也会抛出异常
            
            # 检查响应内容中是否包含inner error，将其转换为request error
            try:
                response_json = response.json()
                if isinstance(response_json, dict) and response_json.get("message"):
                    error_message = response_json.get("message", "")
                    if "inner error" in error_message.lower():
                        # 将inner error转换为request error，避免误判为资源失效
                        response_json["message"] = "request error"
                        response._content = json.dumps(response_json).encode('utf-8')
            except:
                pass  # 如果JSON解析失败，保持原响应不变
                
            return response
        except Exception as e:
            print(f"_send_request error:\n{e}")
            fake_response = requests.Response()
            fake_response.status_code = 500
            fake_response._content = b'{"status": 500, "message": "request error"}'
            return fake_response

    def init(self):
        account_info = self.get_account_info()
        if account_info:
            self.is_active = True
            self.nickname = account_info["nickname"]
            return account_info
        else:
            return False

    def get_account_info(self):
        url = "https://pan.quark.cn/account/info"
        querystring = {"fr": "pc", "platform": "pc"}
        response = self._send_request("GET", url, params=querystring).json()
        if response.get("data"):
            return response["data"]
        else:
            return False

    def get_growth_info(self):
        url = f"{self.BASE_URL_APP}/1/clouddrive/capacity/growth/info"
        querystring = {
            "pr": "ucpro",
            "fr": "android",
            "kps": self.mparam.get("kps"),
            "sign": self.mparam.get("sign"),
            "vcode": self.mparam.get("vcode"),
        }
        headers = {
            "content-type": "application/json",
        }
        response = self._send_request(
            "GET", url, headers=headers, params=querystring
        ).json()
        if response.get("data"):
            return response["data"]
        else:
            return False

    def get_growth_sign(self):
        url = f"{self.BASE_URL_APP}/1/clouddrive/capacity/growth/sign"
        querystring = {
            "pr": "ucpro",
            "fr": "android",
            "kps": self.mparam.get("kps"),
            "sign": self.mparam.get("sign"),
            "vcode": self.mparam.get("vcode"),
        }
        payload = {
            "sign_cyclic": True,
        }
        headers = {
            "content-type": "application/json",
        }
        response = self._send_request(
            "POST", url, json=payload, headers=headers, params=querystring
        ).json()
        if response.get("data"):
            return True, response["data"]["sign_daily_reward"]
        else:
            return False, response["message"]

    # 可验证资源是否失效
    def get_stoken(self, pwd_id, passcode=""):
        url = f"{self.BASE_URL}/1/clouddrive/share/sharepage/token"
        querystring = {"pr": "ucpro", "fr": "pc"}
        payload = {"pwd_id": pwd_id, "passcode": passcode}
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        if response.get("status") == 200:
            return True, response["data"]["stoken"]
        else:
            return False, response["message"]

    def is_recoverable_error(self, error_message):
        """
        检查错误是否为可恢复的错误（网络错误、服务端临时错误等）
        
        Args:
            error_message: 错误消息
            
        Returns:
            bool: 是否为可恢复错误
        """
        if not error_message:
            return False
            
        error_message = error_message.lower()
        recoverable_errors = [
            "inner error",
            "request error", 
            "网络错误",
            "服务端错误",
            "临时错误",
            "timeout",
            "connection error",
            "server error"
        ]
        
        return any(error in error_message for error in recoverable_errors)

    def get_detail(self, pwd_id, stoken, pdir_fid, _fetch_share=0, max_retries=3):
        list_merge = []
        page = 1
        retry_count = 0
        
        while True:
            url = f"{self.BASE_URL}/1/clouddrive/share/sharepage/detail"
            querystring = {
                "pr": "ucpro",
                "fr": "pc",
                "pwd_id": pwd_id,
                "stoken": stoken,
                "pdir_fid": pdir_fid,
                "force": "0",
                "_page": page,
                "_size": "50",
                "_fetch_banner": "0",
                "_fetch_share": _fetch_share,
                "_fetch_total": "1",
                "_sort": "file_type:asc,updated_at:desc",
            }
            # 兼容网络错误或服务端异常
            try:
                response = self._send_request("GET", url, params=querystring).json()
            except Exception:
                return {"error": "request error"}

            # 检查响应中是否包含inner error或其他可恢复错误
            if isinstance(response, dict) and response.get("message"):
                error_message = response.get("message", "")
                if "inner error" in error_message.lower():
                    # 对于inner error，尝试重试
                    if retry_count < max_retries:
                        retry_count += 1
                        # 静默重试，不输出重试信息，避免日志污染
                        # 只有在调试模式下才记录详细重试信息
                        try:
                            import os
                            if os.environ.get("DEBUG", "false").lower() == "true":
                                print(f"[DEBUG] 遇到inner error，进行第{retry_count}次重试...")
                        except:
                            pass  # 如果无法获取DEBUG环境变量，静默处理
                        time.sleep(1)  # 等待1秒后重试
                        continue
                    else:
                        return {"error": "request error"}  # 重试次数用尽，返回request error

            # 统一判错：某些情况下返回没有 code 字段
            code = response.get("code")
            status = response.get("status")
            if code not in (0, None):
                return {"error": response.get("message", "unknown error")}
            if status not in (None, 200):
                return {"error": response.get("message", "request error")}

            data = response.get("data") or {}
            metadata = response.get("metadata") or {}

            if data.get("list"):
                list_merge += data["list"]
                page += 1
            else:
                break
            # 防御性：metadata 或 _total 缺失时不再访问嵌套键
            total = metadata.get("_total") if isinstance(metadata, dict) else None
            if isinstance(total, int) and len(list_merge) >= total:
                break
        # 统一输出结构，缺失字段时提供默认值
        if not isinstance(data, dict):
            return {"error": response.get("message", "request error")}
        data["list"] = list_merge
        if "paths" not in data:
            data["paths"] = []
        return data

    def get_fids(self, file_paths):
        fids = []
        while True:
            url = f"{self.BASE_URL}/1/clouddrive/file/info/path_list"
            querystring = {"pr": "ucpro", "fr": "pc"}
            payload = {"file_path": file_paths[:50], "namespace": "0"}
            response = self._send_request(
                "POST", url, json=payload, params=querystring
            ).json()
            if response["code"] == 0:
                fids += response["data"]
                file_paths = file_paths[50:]
            else:
                print(f"获取目录ID: 失败, {response['message']}")
                break
            if len(file_paths) == 0:
                break
        return fids

    def ls_dir(self, pdir_fid, **kwargs):
        file_list = []
        page = 1
        # 优化：增加每页大小，减少API调用次数
        page_size = kwargs.get("page_size", 200)  # 从50增加到200

        while True:
            url = f"{self.BASE_URL}/1/clouddrive/file/sort"
            querystring = {
                "pr": "ucpro",
                "fr": "pc",
                "uc_param_str": "",
                "pdir_fid": pdir_fid,
                "_page": page,
                "_size": str(page_size),
                "_fetch_total": "1",
                "_fetch_sub_dirs": "0",
                "_sort": "file_type:asc,updated_at:desc",
                "_fetch_full_path": kwargs.get("fetch_full_path", 0),
            }
            response = self._send_request("GET", url, params=querystring).json()
            if response["code"] != 0:
                return {"error": response["message"]}
            if response["data"]["list"]:
                file_list += response["data"]["list"]
                page += 1
            else:
                break
            if len(file_list) >= response["metadata"]["_total"]:
                break
        
        # 修复文件夹大小显示问题：当include_items字段不存在时，通过额外API调用获取
        file_list = self._fix_folder_sizes(file_list)
        
        return file_list

    def _fix_folder_sizes(self, file_list):
        """
        修复文件夹大小显示问题
        当include_items字段不存在时，通过额外API调用获取文件夹项目数量
        """
        if not isinstance(file_list, list):
            return file_list
            
        for item in file_list:
            if item.get("dir", False) and "include_items" not in item:
                folder_id = item.get("fid")
                if folder_id:
                    # 获取文件夹项目数量
                    item_count = self._get_folder_item_count(folder_id)
                    item["include_items"] = item_count
                    
        return file_list
    
    def _get_folder_item_count(self, folder_id):
        """
        获取文件夹项目数量
        """
        try:
            url = f"{self.BASE_URL}/1/clouddrive/file/sort"
            querystring = {
                "pr": "ucpro",
                "fr": "pc",
                "uc_param_str": "",
                "pdir_fid": folder_id,
                "_page": 1,
                "_size": 1,  # 只获取第一页，用于获取总数
                "_fetch_total": "1",
                "_fetch_sub_dirs": "0",
                "_sort": "file_type:asc,updated_at:desc",
                "_fetch_full_path": 0,
            }
            
            response = self._send_request("GET", url, params=querystring).json()
            if response.get("code") == 0:
                metadata = response.get("metadata", {})
                return metadata.get("_total", 0)
            else:
                return 0
        except Exception:
            return 0

    def get_paths(self, folder_id):
        """
        获取指定文件夹ID的完整路径信息
        
        Args:
            folder_id: 文件夹ID
            
        Returns:
            list: 路径信息列表，每个元素包含fid和name
        """
        if folder_id == "0" or folder_id == 0:
            return []
            
        url = f"{self.BASE_URL}/1/clouddrive/file/sort"
        querystring = {
            "pr": "ucpro",
            "fr": "pc",
            "uc_param_str": "",
            "pdir_fid": folder_id,
            "_page": 1,
            "_size": "50",
            "_fetch_total": "1",
            "_fetch_sub_dirs": "0",
            "_sort": "file_type:asc,updated_at:desc",
            "_fetch_full_path": 1,
        }
        
        try:
            response = self._send_request("GET", url, params=querystring).json()
            if response["code"] == 0 and "full_path" in response["data"]:
                paths = []
                for item in response["data"]["full_path"]:
                    paths.append({
                        "fid": item["fid"],
                        "name": item["file_name"]
                    })
                return paths
        except Exception as e:
            print(f"获取文件夹路径出错: {str(e)}")
            
        return []

    def save_file(self, fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken):
        url = f"{self.BASE_URL}/1/clouddrive/share/sharepage/save"
        querystring = {
            "pr": "ucpro",
            "fr": "pc",
            "uc_param_str": "",
            "app": "clouddrive",
            "__dt": int(random.uniform(1, 5) * 60 * 1000),
            "__t": datetime.now().timestamp(),
        }
        payload = {
            "fid_list": fid_list,
            "fid_token_list": fid_token_list,
            "to_pdir_fid": to_pdir_fid,
            "pwd_id": pwd_id,
            "stoken": stoken,
            "pdir_fid": "0",
            "scene": "link",
        }
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def query_task(self, task_id):
        retry_index = 0
        while True:
            url = f"{self.BASE_URL}/1/clouddrive/task"
            querystring = {
                "pr": "ucpro",
                "fr": "pc",
                "uc_param_str": "",
                "task_id": task_id,
                "retry_index": retry_index,
                "__dt": int(random.uniform(1, 5) * 60 * 1000),
                "__t": datetime.now().timestamp(),
            }
            response = self._send_request("GET", url, params=querystring).json()
            if response["data"]["status"] != 0:
                if retry_index > 0:
                    print()
                break
            else:
                if retry_index == 0:
                    print(
                        f"正在等待「{response['data']['task_title']}」执行结果",
                        end="",
                        flush=True,
                    )
                else:
                    print(".", end="", flush=True)
                retry_index += 1
                time.sleep(0.500)
        return response

    def cloud_unarchive(self, fid, to_pdir_fid="0"):
        """
        云解压压缩文件（需要夸克高级会员）
        
        参数:
            fid: 压缩文件的 fid
            to_pdir_fid: 解压目标目录 fid，'0' 表示根目录
        
        返回:
            解压结果，包含 task_id 和解压后的文件信息
        """
        url = f"{self.BASE_URL}/1/clouddrive/archive/unarchive"
        querystring = {
            "pr": "ucpro",
            "fr": "pc",
            "uc_param_str": "",
        }
        payload = {
            "fid": fid,
            "select_mode": 1,  # 1 表示全部解压
            "to_pdir_fid": to_pdir_fid,
        }
        response = self._send_request("POST", url, json=payload, params=querystring).json()
        
        if response.get("code") == 0:
            return response
        
        # 错误处理
        error_code = response.get("code")
        error_msg = response.get("message", "")
        
        if error_code == 23008 and "doloading" in error_msg:
            # 文件正在处理中
            return {"code": 23008, "message": "文件正在处理中，请稍后重试"}
        elif error_code == 31001:
            # 非 SVIP 会员
            return {"code": 31001, "message": "云解压需要夸克 88VIP、SVIP、SVIP+ 等高级会员"}
        else:
            return response

    def query_unarchive_task(self, task_id, timeout=300):
        """
        查询解压任务状态
        
        参数:
            task_id: 解压任务 ID
            timeout: 超时时间（秒）
        
        返回:
            解压任务结果，包含解压后的文件信息
        """
        start_time = time.time()
        retry_index = 0
        
        while time.time() - start_time < timeout:
            url = f"{self.BASE_URL}/1/clouddrive/task"
            querystring = {
                "pr": "ucpro",
                "fr": "pc",
                "uc_param_str": "",
                "task_id": task_id,
                "retry_index": retry_index,
            }
            response = self._send_request("GET", url, params=querystring).json()
            
            if response.get("code") == 0:
                data = response.get("data", {})
                status = data.get("status")
                
                if status == 2:  # 完成
                    return {"code": 0, "data": data}
                elif status == 4:  # 失败
                    # 尝试从任务结果中提取更具体的失败原因
                    unarchive_result = data.get("unarchive_result", {})
                    task_msg = unarchive_result.get("message") or data.get("message") or response.get("message", "")
                    error_msg = task_msg if task_msg else "解压任务失败"
                    return {"code": -1, "message": error_msg, "data": data}
                else:
                    # 任务进行中
                    retry_index += 1
                    time.sleep(1)
            else:
                return response
        
        return {"code": -1, "message": "解压任务超时"}

    def _is_file_size_extract_error(self, error_msg):
        """判断是否为文件大小/限制导致的解压失败，此类情况建议使用夸克客户端解压"""
        if not error_msg:
            return False
        msg_lower = error_msg.lower()
        size_keywords = ("大小", "超限", "超过", "限制", "过大", "size", "limit", "mb", "gb", "不支持")
        return any(kw in msg_lower for kw in size_keywords)

    def is_archive_file(self, filename):
        """判断是否为压缩文件"""
        lower_name = filename.lower()
        archive_extensions = ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.tar.gz', '.tar.bz2']
        return any(lower_name.endswith(ext) for ext in archive_extensions)

    def process_auto_extract(self, archive_fid, archive_name, target_pdir_fid, extract_mode, task=None):
        """
        处理压缩文件的自动解压
        
        参数:
            archive_fid: 压缩文件的 fid
            archive_name: 压缩文件名
            target_pdir_fid: 目标目录的 fid
            extract_mode: 解压模式 (keep_structure_keep_archive, flat_files_keep_archive, 
                          keep_structure_delete_archive, flat_files_delete_archive)
            task: 任务信息（用于日志）
        
        返回:
            dict: 包含解压结果信息
        """
        task_name = task.get("taskname", "未知任务") if task else "未知任务"
        
        # 解析解压模式
        keep_structure = "keep_structure" in extract_mode
        delete_archive = "delete_archive" in extract_mode
        
        # 第一步：执行云解压（解压到根目录）
        unarchive_result = self.cloud_unarchive(archive_fid, "0")
        
        if unarchive_result.get("code") == 23008:
            # 文件正在处理中，等待后重试
            print(f"  ⏳ 文件正在处理中，等待 15 秒后重试...")
            time.sleep(15)
            unarchive_result = self.cloud_unarchive(archive_fid, "0")
        
        if unarchive_result.get("code") != 0:
            error_msg = unarchive_result.get("message", "未知错误")
            print(f"  ❌ 云解压失败: {error_msg}")
            suggest_client = self._is_file_size_extract_error(error_msg)
            return {"success": False, "message": error_msg, "suggest_client": suggest_client}
        
        # 获取解压任务 ID
        unarchive_task_id = unarchive_result.get("data", {}).get("task_id")
        if not unarchive_task_id:
            print(f"  ❌ 未获取到解压任务 ID")
            return {"success": False, "message": "未获取到解压任务 ID"}
        
        # 第二步：等待解压任务完成（30 秒超时，超时则放弃解压，按无法解压处理）
        task_result = self.query_unarchive_task(unarchive_task_id, timeout=30)
        
        if task_result.get("code") != 0:
            error_msg = task_result.get("message", "解压任务失败")
            if "超时" in error_msg:
                print(f"  ⏱️ 云解压异常超时，放弃解压: {archive_name}")
                print()
            else:
                print(f"  ❌ {error_msg}")
            suggest_client = self._is_file_size_extract_error(error_msg)
            return {"success": False, "message": error_msg, "suggest_client": suggest_client}
        
        # 获取解压后的文件夹信息
        unarchive_data = task_result.get("data", {})
        unarchive_result_info = unarchive_data.get("unarchive_result", {})
        extracted_list = unarchive_result_info.get("list", [])
        
        if not extracted_list:
            print(f"  ⚠️ 未获取到解压结果")
            return {"success": False, "message": "未获取到解压结果"}
        
        # 解压后的文件夹（在根目录）
        extracted_folder = extracted_list[0]
        extracted_folder_fid = extracted_folder.get("fid")
        extracted_folder_name = extracted_folder.get("file_name")
        
        # 第三步：获取解压后的所有文件
        extracted_files = self.ls_dir(extracted_folder_fid)
        
        # 收集所有需要处理的文件（递归获取）
        all_files = []
        folders_to_delete = [extracted_folder_fid]  # 需要删除的文件夹列表
        
        def collect_files_recursive(folder_fid, relative_path=""):
            """递归收集文件夹内的所有文件"""
            files = self.ls_dir(folder_fid)
            for f in files:
                file_relative_path = f"{relative_path}/{f['file_name']}" if relative_path else f['file_name']
                if f.get("dir"):
                    folders_to_delete.append(f["fid"])
                    collect_files_recursive(f["fid"], file_relative_path)
                else:
                    all_files.append({
                        "fid": f["fid"],
                        "file_name": f["file_name"],
                        "relative_path": file_relative_path,
                        "size": f.get("size", 0),
                        "parent_fid": folder_fid,
                    })
        
        collect_files_recursive(extracted_folder_fid)
        
        # 第四步：应用过滤规则并删除被过滤掉的文件
        if task and task.get("filterwords"):
            # 应用过滤规则，获取通过过滤的文件列表
            filtered_files = advanced_filter_files(all_files, task["filterwords"])
            
            # 找出被过滤掉的文件（不在过滤后的列表中）
            filtered_file_fids = {f["fid"] for f in filtered_files}
            files_to_delete = [f for f in all_files if f["fid"] not in filtered_file_fids]
            
            # 删除被过滤掉的文件
            if files_to_delete:
                for file_to_delete in files_to_delete:
                    delete_result = self.delete([file_to_delete["fid"]])
                    if delete_result.get("code") != 0:
                        print(f"    ⚠️ 删除被过滤文件失败: {file_to_delete['file_name']} - {delete_result.get('message', '未知错误')}")
                
                # 更新 all_files 列表，只保留通过过滤的文件
                all_files = filtered_files
        
        # 第五步：根据模式移动文件
        moved_files = []
        
        if keep_structure:
            # 保持原目录结构：将整个解压文件夹移动到目标目录
            move_result = self.move([extracted_folder_fid], target_pdir_fid)
            if move_result.get("code") == 0:
                moved_files = all_files
                folders_to_delete = []  # 不需要删除，因为已经移动了
                # 记录需要后续重命名的子目录（与顶层相同规则）
                if task is not None:
                    subdir_path = f"/{extracted_folder_name}"
                    auto_subdirs = task.get("_auto_extract_subdirs") or []
                    if subdir_path not in auto_subdirs:
                        auto_subdirs.append(subdir_path)
                        task["_auto_extract_subdirs"] = auto_subdirs
            else:
                print(f"  ❌ 移动文件夹失败: {move_result.get('message')}")
        else:
            # 仅保留文件：将所有文件移动到目标目录（扁平化）
            for f in all_files:
                move_result = self.move([f["fid"]], target_pdir_fid)
                if move_result.get("code") == 0:
                    moved_files.append(f)
                else:
                    print(f"    ❌ {f['file_name']}: {move_result.get('message')}")
            
            # 删除解压后的空文件夹（从最深层开始删除）
            for folder_fid in reversed(folders_to_delete):
                self.delete([folder_fid])
        
        # 第六步：根据模式处理原压缩文件
        archive_deleted = False
        if delete_archive:
            # 等待一小段时间确保之前的操作完成
            time.sleep(0.5)
            fid_to_delete = str(archive_fid) if archive_fid is not None else None
            if fid_to_delete:
                delete_result = self.delete([fid_to_delete])
                if delete_result.get("code") == 0:
                    archive_deleted = True
                else:
                    error_msg = delete_result.get("message", "未知错误")
                    print(f"  ⚠️ 删除压缩文件失败: {error_msg}")
                    # 重试：在目标目录中按文件名重新查找压缩包并删除（解压/移动后 fid 可能变化）
                    time.sleep(1)
                    try:
                        current_list = self.ls_dir(target_pdir_fid)
                        for item in current_list:
                            if not item.get("dir", False) and item.get("file_name") == archive_name:
                                retry_result = self.delete([str(item["fid"])])
                                if retry_result.get("code") == 0:
                                    archive_deleted = True
                                break
                        if not archive_deleted and keep_structure and extracted_folder_name:
                            subdir_list = self.ls_dir(target_pdir_fid)
                            for d in subdir_list:
                                if d.get("dir") and d.get("file_name") == extracted_folder_name:
                                    inside = self.ls_dir(d["fid"])
                                    for item in inside:
                                        if not item.get("dir", False) and item.get("file_name") == archive_name:
                                            retry_result = self.delete([str(item["fid"])])
                                            if retry_result.get("code") == 0:
                                                archive_deleted = True
                                            break
                                    break
                    except Exception:
                        pass
                    if not archive_deleted:
                        print(f"  ❌ 重试删除仍然失败")

        # 记录本次已解压的压缩包文件名，供 do_rename_task 根目录跳过再次转存（扁平化不写 _auto_extract_subdirs，需单独标记）
        if task is not None:
            extracted_names = task.get("_extracted_archive_names") or []
            if archive_name not in extracted_names:
                extracted_names = list(extracted_names) + [archive_name]
                task["_extracted_archive_names"] = extracted_names

        return {
            "success": True,
            "extracted_folder_name": extracted_folder_name,
            "extracted_folder_fid": extracted_folder_fid if keep_structure else None,
            "moved_files": moved_files,
            "total_files": len(all_files),
            "archive_deleted": archive_deleted,
            "delete_requested": delete_archive,
        }

    def download(self, fids):
        url = f"{self.BASE_URL}/1/clouddrive/file/download"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {"fids": fids}
        response = self._send_request("POST", url, json=payload, params=querystring)
        set_cookie = response.cookies.get_dict()
        cookie_str = "; ".join([f"{key}={value}" for key, value in set_cookie.items()])
        return response.json(), cookie_str

    def mkdir(self, dir_path):
        url = f"{self.BASE_URL}/1/clouddrive/file"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {
            "pdir_fid": "0",
            "file_name": "",
            "dir_path": dir_path,
            "dir_init_lock": False,
        }
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def mkdir_in_folder(self, parent_fid, folder_name):
        """在指定父目录下创建新文件夹"""
        url = f"{self.BASE_URL}/1/clouddrive/file"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {
            "pdir_fid": parent_fid,
            "file_name": folder_name,
            "dir_path": "",
            "dir_init_lock": False,
        }
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def rename(self, fid, file_name):
        url = f"{self.BASE_URL}/1/clouddrive/file/rename"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {"fid": fid, "file_name": file_name}
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def delete(self, filelist):
        url = f"{self.BASE_URL}/1/clouddrive/file/delete"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {"action_type": 2, "filelist": filelist, "exclude_fids": []}
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def move(self, filelist, to_pdir_fid):
        """移动文件到指定目录"""
        url = f"{self.BASE_URL}/1/clouddrive/file/move"
        querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        payload = {
            "action_type": 2,
            "filelist": filelist,
            "to_pdir_fid": to_pdir_fid,
            "exclude_fids": []
        }
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    def recycle_list(self, page=1, size=30):
        url = f"{self.BASE_URL}/1/clouddrive/file/recycle/list"
        querystring = {
            "_page": page,
            "_size": size,
            "pr": "ucpro",
            "fr": "pc",
            "uc_param_str": "",
        }
        response = self._send_request("GET", url, params=querystring).json()
        return response["data"]["list"]

    def recycle_remove(self, record_list):
        url = f"{self.BASE_URL}/1/clouddrive/file/recycle/remove"
        querystring = {"uc_param_str": "", "fr": "pc", "pr": "ucpro"}
        payload = {
            "select_mode": 2,
            "record_list": record_list,
        }
        response = self._send_request(
            "POST", url, json=payload, params=querystring
        ).json()
        return response

    # ↑ 请求函数
    # ↓ 操作函数

    # 魔法正则匹配
    def magic_regex_func(self, pattern, replace, taskname=None, magic_regex={}):
        magic_regex = magic_regex or CONFIG_DATA.get("magic_regex") or MAGIC_REGEX
        keyword = pattern
        if keyword in magic_regex:
            pattern = magic_regex[keyword]["pattern"]
            if replace == "":
                replace = magic_regex[keyword]["replace"]
        if taskname:
            replace = replace.replace("$TASKNAME", taskname)
        return pattern, replace

    # def get_id_from_url(self, url):
    #     url = url.replace("https://pan.quark.cn/s/", "")
    #     pattern = r"(\w+)(\?pwd=(\w+))?(#/list/share.*/(\w+))?"
    #     match = re.search(pattern, url)
    #     if match:
    #         pwd_id = match.group(1)
    #         passcode = match.group(3) if match.group(3) else ""
    #         pdir_fid = match.group(5) if match.group(5) else 0
    #         return pwd_id, passcode, pdir_fid
    #     else:
    #         return None

    def extract_url(self, url):
        # pwd_id
        match_id = re.search(r"/s/(\w+)", url)
        pwd_id = match_id.group(1) if match_id else None
        # passcode
        match_pwd = re.search(r"pwd=(\w+)", url)
        passcode = match_pwd.group(1) if match_pwd else ""
        # path: fid-name
        paths = []
        matches = re.findall(r"/(\w{32})-?([^/]+)?", url)
        for match in matches:
            fid = match[0]
            name = urllib.parse.unquote(match[1])
            paths.append({"fid": fid, "name": name})
        pdir_fid = paths[-1]["fid"] if matches else 0
        return pwd_id, passcode, pdir_fid, paths

    def update_savepath_fid(self, tasklist):
        dir_paths = [
            re.sub(r"/{2,}", "/", f"/{item['savepath']}")
            for item in tasklist
            if not item.get("enddate")
            or (
                datetime.now().date()
                <= datetime.strptime(item["enddate"], "%Y-%m-%d").date()
            )
        ]
        # 去掉每个路径开头的斜杠，确保格式一致
        dir_paths = [path.lstrip('/') for path in dir_paths]
        
        if not dir_paths:
            return False
            
        # 重新添加斜杠前缀，确保格式一致
        dir_paths = [f"/{path}" for path in dir_paths]
        
        dir_paths_exist_arr = self.get_fids(dir_paths)
        dir_paths_exist = [item["file_path"] for item in dir_paths_exist_arr]
        # 比较创建不存在的
        dir_paths_unexist = list(set(dir_paths) - set(dir_paths_exist) - set(["/"]))
        for dir_path in dir_paths_unexist:
            mkdir_return = self.mkdir(dir_path)
            if mkdir_return["code"] == 0:
                new_dir = mkdir_return["data"]
                dir_paths_exist_arr.append(
                    {"file_path": dir_path, "fid": new_dir["fid"]}
                )
                # print(f"创建文件夹：{dir_path}")
            else:
                # print(f"创建文件夹：{dir_path} 失败, {mkdir_return['message']}")
                pass
        # 储存目标目录的fid
        for dir_path in dir_paths_exist_arr:
            self.savepath_fid[dir_path["file_path"]] = dir_path["fid"]
        # print(dir_paths_exist_arr)

    def do_save_check(self, shareurl, savepath):
        try:
            pwd_id, passcode, pdir_fid, _ = self.extract_url(shareurl)
            _, stoken = self.get_stoken(pwd_id, passcode)
            share_file_list = self.get_detail(pwd_id, stoken, pdir_fid)["list"]
            fid_list = [item["fid"] for item in share_file_list]
            fid_token_list = [item["share_fid_token"] for item in share_file_list]
            file_name_list = [item["file_name"] for item in share_file_list]
            if not fid_list:
                return
            get_fids = self.get_fids([savepath])
            to_pdir_fid = (
                get_fids[0]["fid"] if get_fids else self.mkdir(savepath)["data"]["fid"]
            )
            save_file = self.save_file(
                fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken
            )
            if save_file["code"] == 41017:
                return
            elif save_file["code"] == 0:
                dir_file_list = self.ls_dir(to_pdir_fid)
                del_list = [
                    item["fid"]
                    for item in dir_file_list
                    if (item["file_name"] in file_name_list)
                    and ((datetime.now().timestamp() - item["created_at"]) < 60)
                ]
                if del_list:
                    self.delete(del_list)
                    recycle_list = self.recycle_list()
                    record_id_list = [
                        item["record_id"]
                        for item in recycle_list
                        if item["fid"] in del_list
                    ]
                    self.recycle_remove(record_id_list)
                return save_file
            else:
                return False
        except Exception as e:
            print(f"转存测试失败: {str(e)}")

    def save_transfer_record(self, task, file_info, renamed_to=""):
        """保存转存记录到数据库
        
        Args:
            task: 任务信息
            file_info: 文件信息
            renamed_to: 重命名后的名称
        """
        try:
            # 初始化数据库
            db = RecordDB()
            
            # 提取文件信息
            original_name = file_info.get("file_name", "")
            file_size = file_info.get("size", 0)
            
            # 处理修改日期
            # 检查updated_at是否为未来日期
            current_time = int(time.time())
            modify_date = file_info.get("updated_at", current_time)
            
            # 如果修改日期是毫秒级时间戳，转换为秒级
            if isinstance(modify_date, int) and modify_date > 9999999999:
                modify_date = int(modify_date / 1000)
                
            # 确保修改日期是合理的值（不是未来日期）
            if modify_date > current_time:
                # 使用当前时间作为备用值
                modify_date = current_time
                
            file_id = file_info.get("fid", "")
            file_type = os.path.splitext(original_name)[1].lower().lstrip(".") if original_name else ""
            
            # 如果没有重命名信息，使用原始名称
            if not renamed_to:
                renamed_to = original_name
            
            # 提取视频信息（时长和分辨率）
            duration = ""
            resolution = ""
            # 对常见视频格式添加时长和分辨率信息
            video_exts = ["mp4", "mkv", "avi", "mov", "wmv", "flv", "m4v", "webm"]
            if file_type in video_exts:
                # 在实际应用中，这里可以通过媒体处理库提取时长和分辨率
                # 目前只是添加占位符，未来可以扩展功能
                pass
            
            # 获取保存路径
            save_path = task.get("savepath", "")
            # 如果file_info中有子目录路径信息，则拼接完整路径
            subdir_path = file_info.get("subdir_path", "")
            if subdir_path:
                # 确保路径格式正确，避免双斜杠
                if save_path.endswith('/') and subdir_path.startswith('/'):
                    save_path = save_path + subdir_path[1:]
                elif not save_path.endswith('/') and not subdir_path.startswith('/'):
                    save_path = save_path + '/' + subdir_path
                else:
                    save_path = save_path + subdir_path
            
            # 添加记录到数据库
            db.add_record(
                task_name=task.get("taskname", ""),
                original_name=original_name,
                renamed_to=renamed_to,
                file_size=file_size,
                modify_date=modify_date,
                duration=duration,
                resolution=resolution,
                file_id=file_id,
                file_type=file_type,
                save_path=save_path
            )
            # 调用后端接口触发该任务的指标实时同步（进度热更新）
            # 修复：不再写死端口 9898，而是优先使用与 Flask 相同的 PORT 环境变量，
            # 同时支持通过 CALENDAR_SERVER_BASE 覆盖完整服务地址，避免端口/地址不一致导致热更新失效
            try:
                _tname = task.get("taskname", "")
                if _tname:
                    port = os.environ.get("PORT", "5005")
                    base_url = os.environ.get("CALENDAR_SERVER_BASE", f"http://127.0.0.1:{port}")
                    requests.post(
                        f"{base_url}/api/calendar/metrics/sync_task",
                        json={"task_name": _tname},
                        timeout=1
                    )
            except Exception:
                # 任何错误都不影响主流程，只影响热更新及时性
                pass
            
            # 关闭数据库连接
            db.close()
            
            # 触发SSE通知，让前端实时感知转存记录变化
            notify_calendar_changed_safe('transfer_record_created')
                
        except Exception as e:
            print(f"保存转存记录失败: {e}")
    
    # 添加一个新的函数，功能与save_transfer_record相同，但名称更清晰表示其用途
    def create_transfer_record(self, task, file_info, renamed_to=""):
        """创建新的转存记录
        
        此函数与save_transfer_record功能完全相同，但名称更明确地表达了其目的
        - 用于在文件初次转存时创建记录
        
        Args:
            task: 任务信息
            file_info: 文件信息
            renamed_to: 重命名后的名称（如果有）
        """
        self.save_transfer_record(task, file_info, renamed_to)

    def update_transfer_record(self, task, file_info, renamed_to):
        """更新转存记录的重命名信息
        
        Args:
            task: 任务信息
            file_info: 文件信息
            renamed_to: 重命名后的名称
        """
        try:
            # 初始化数据库
            db = RecordDB()
            
            # 提取信息用于查找记录
            original_name = file_info.get("file_name", "")
            file_id = file_info.get("fid", "")
            task_name = task.get("taskname", "")
            
            # 获取保存路径
            save_path = task.get("savepath", "")
            # 如果file_info中有子目录路径信息，则拼接完整路径
            subdir_path = file_info.get("subdir_path", "")
            if subdir_path:
                # 确保路径格式正确，避免双斜杠
                if save_path.endswith('/') and subdir_path.startswith('/'):
                    save_path = save_path + subdir_path[1:]
                elif not save_path.endswith('/') and not subdir_path.startswith('/'):
                    save_path = save_path + '/' + subdir_path
                else:
                    save_path = save_path + subdir_path
            
            # 更新记录
            updated = db.update_renamed_to(
                file_id=file_id,
                original_name=original_name,
                renamed_to=renamed_to,
                task_name=task_name,
                save_path=save_path
            )
            
            # 关闭数据库连接
            db.close()
            
            # 如果更新成功，触发SSE通知
            if updated > 0:
                notify_calendar_changed_safe('transfer_record_updated')
            
            return updated > 0
        except Exception as e:
            print(f"更新转存记录失败: {e}")
            return False
    
    # 添加一个专门从重命名日志更新记录的方法
    def update_transfer_record_from_log(self, task, rename_log, actual_file_names=None):
        """从重命名日志中提取信息并更新记录

        Args:
            task: 任务信息
            rename_log: 重命名日志，格式为 "重命名: 旧名 → 新名"
            actual_file_names: 实际文件名映射字典，用于修正显示的文件名
        """
        try:
            # 使用字符串分割方法提取文件名，更可靠地获取完整文件名
            if "重命名:" not in rename_log or " → " not in rename_log:
                return False

            # 先分割出"重命名:"后面的部分
            parts = rename_log.split("重命名:", 1)[1].strip()
            # 再按箭头分割
            if " → " not in parts:
                return False

            old_name, expected_new_name = parts.split(" → ", 1)

            # 如果新名称包含"失败"，则是失败的重命名，跳过
            if "失败" in expected_new_name:
                return False

            # 处理可能的截断标记，只保留实际文件名部分
            # 注意：只有明确是失败消息才应该截断
            if " 失败，" in expected_new_name:
                expected_new_name = expected_new_name.split(" 失败，")[0]

            # 去除首尾空格
            old_name = old_name.strip()
            expected_new_name = expected_new_name.strip()

            # 确保提取到的是完整文件名
            if not old_name or not expected_new_name:
                return False

            # 获取实际的文件名（如果提供了映射）
            actual_new_name = expected_new_name
            if actual_file_names and old_name in actual_file_names:
                actual_new_name = actual_file_names[old_name]

            # 初始化数据库
            db = RecordDB()

            # 使用原文件名和任务名查找记录
            task_name = task.get("taskname", "")

            # 获取保存路径
            save_path = task.get("savepath", "")
            # 注意：从日志中无法获取子目录信息，只能使用任务的主保存路径

            # 检查文件是否已存在于记录中
            # 先查询是否有匹配的记录
            cursor = db.conn.cursor()
            query = "SELECT file_id FROM transfer_records WHERE original_name = ? AND task_name = ? AND save_path = ?"
            cursor.execute(query, (old_name, task_name, save_path))
            result = cursor.fetchone()

            # 如果找到了匹配的记录，使用file_id进行更新
            file_id = result[0] if result else ""

            # 更新记录，使用实际的文件名
            if file_id:
                # 使用file_id更新
                updated = db.update_renamed_to(
                    file_id=file_id,
                    original_name="",  # 不使用原文件名，因为已有file_id
                    renamed_to=actual_new_name,  # 使用实际的文件名
                    task_name=task_name,
                    save_path=save_path
                )
            else:
                # 使用原文件名更新
                updated = db.update_renamed_to(
                    file_id="",  # 不使用file_id查询，因为在日志中无法获取
                    original_name=old_name,
                    renamed_to=actual_new_name,  # 使用实际的文件名
                    task_name=task_name,
                    save_path=save_path
                )

            # 关闭数据库连接
            db.close()

            return updated > 0
        except Exception as e:
            print(f"根据日志更新转存记录失败: {e}")
            return False
    
    # 批量处理重命名日志
    def process_rename_logs(self, task, rename_logs):
        """处理重命名日志列表，更新数据库记录

        Args:
            task: 任务信息
            rename_logs: 重命名日志列表
        """
        # 获取实际的文件名映射
        actual_file_names = self.get_actual_file_names_from_directory(task, rename_logs)

        for log in rename_logs:
            if "重命名:" in log and "→" in log and "失败" not in log:
                self.update_transfer_record_from_log(task, log, actual_file_names)

    def get_actual_file_names_from_directory(self, task, rename_logs):
        """从目录中获取实际的文件名，用于修正转存记录和日志显示

        Args:
            task: 任务信息
            rename_logs: 重命名日志列表

        Returns:
            dict: 原文件名到实际文件名的映射
        """
        try:
            # 获取保存路径
            savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}")

            # 获取当前目录的文件列表
            if hasattr(self, 'savepath_fid') and self.savepath_fid.get(savepath):
                dir_file_list = self.ls_dir(self.savepath_fid[savepath])
            else:
                return {}

            # 从重命名日志中提取预期的重命名映射（包括失败的重命名）
            expected_renames = {}
            for log in rename_logs:
                if "重命名:" in log and " → " in log:
                    parts = log.split("重命名:", 1)[1].strip()
                    if " → " in parts:
                        old_name, new_name = parts.split(" → ", 1)
                        # 处理可能的失败信息
                        if " 失败，" in new_name:
                            new_name = new_name.split(" 失败，")[0]
                        old_name = old_name.strip()
                        new_name = new_name.strip()
                        expected_renames[old_name] = new_name

            # 获取目录中实际存在的文件名
            actual_files = [f["file_name"] for f in dir_file_list if not f["dir"]]

            # 创建实际文件名映射
            actual_renames = {}

            # 对于每个预期的重命名，检查实际文件是否存在
            for old_name, expected_new_name in expected_renames.items():
                if expected_new_name in actual_files:
                    # 预期的重命名成功了
                    actual_renames[old_name] = expected_new_name
                elif old_name in actual_files:
                    # 重命名失败，文件保持原名
                    actual_renames[old_name] = old_name
                else:
                    # 尝试模糊匹配，可能文件名有细微差异
                    for actual_file in actual_files:
                        # 简单的相似度检查：如果文件名包含预期名称的主要部分
                        if (len(expected_new_name) > 10 and
                            expected_new_name[:10] in actual_file and
                            actual_file not in actual_renames.values()):
                            actual_renames[old_name] = actual_file
                            break
                    else:
                        # 如果找不到匹配，使用预期名称（可能文件已被删除或移动）
                        actual_renames[old_name] = expected_new_name

            return actual_renames

        except Exception as e:
            print(f"获取实际文件名失败: {e}")
            return {}
    
    def check_file_exists_in_records(self, file_id, task=None):
        """检查文件ID是否存在于转存记录中
        
        Args:
            file_id: 要检查的文件ID
            task: 可选的任务信息，用于进一步筛选
            
        Returns:
            bool: 文件是否已存在于记录中
        """
        if not file_id:
            return False
            
        try:
            # 初始化数据库
            db = RecordDB()
            
            # 构建查询条件
            conditions = ["file_id = ?"]
            params = [file_id]
            
            # 如果提供了任务信息，添加任务名称条件
            if task and task.get("taskname"):
                conditions.append("task_name = ?")
                params.append(task.get("taskname"))
            
            # 构建WHERE子句
            where_clause = " AND ".join(conditions)
            
            # 查询是否存在匹配的记录
            cursor = db.conn.cursor()
            query = f"SELECT COUNT(*) FROM transfer_records WHERE {where_clause}"
            cursor.execute(query, params)
            count = cursor.fetchone()[0]
            
            # 关闭数据库连接
            db.close()
            
            return count > 0
        except Exception as e:
            print(f"检查文件记录时出错: {e}")
            return False

    def do_save_task(self, task):
        # 判断资源失效记录
        if task.get("shareurl_ban"):
            add_notify(f"❗《{task['taskname']}》分享资源已失效: {task['shareurl_ban']}\n")
            return
            
        # 标准化保存路径，去掉可能存在的首位斜杠，然后重新添加
        savepath = task["savepath"].lstrip('/')
        task["savepath"] = savepath  # 更新任务中的路径，确保后续处理一致
            
        # 提取链接参数
        pwd_id, passcode, pdir_fid, paths = self.extract_url(task["shareurl"])
        if not pwd_id:
            task["shareurl_ban"] = f"提取链接参数失败，请检查分享链接是否有效"
            print(f"提取链接参数失败，请检查分享链接是否有效")
            return
        # 获取分享详情
        is_sharing, stoken = self.get_stoken(pwd_id, passcode)
        if not is_sharing:
            # 如果是可恢复错误（网络/临时），不要设置为失效资源
            try:
                error_text = str(stoken or "")
                if self.is_recoverable_error(error_text):
                    print(f"分享详情获取失败（网络异常）: {error_text}")
                    return  # 直接返回，不设置 shareurl_ban
            except Exception:
                pass
            # 非可恢复错误，按失效处理
            task["shareurl_ban"] = stoken
            add_notify(f"❗《{task['taskname']}》分享详情获取失败: {stoken}\n")
            return
        share_detail = self.get_detail(pwd_id, stoken, pdir_fid, _fetch_share=1)
        # 如果获取详情返回错误，按可恢复性判断
        if isinstance(share_detail, dict) and share_detail.get("error"):
            error_text = str(share_detail.get("error") or "")
            if self.is_recoverable_error(error_text):
                print(f"获取分享详情失败（网络异常）: {error_text}")
                return  # 直接返回，不设置 shareurl_ban
            else:
                task["shareurl_ban"] = self.format_unrecoverable_error(error_text) if hasattr(self, 'format_unrecoverable_error') else error_text
                add_notify(f"❗《{task['taskname']}》获取分享详情失败: {task['shareurl_ban']}\n")
                return
        # 获取保存路径fid
        savepath = task["savepath"]
        if not self.savepath_fid.get(savepath):
            # 检查规范化路径是否已在字典中
            norm_savepath = re.sub(r"/{2,}", "/", f"/{savepath}")
            if norm_savepath != savepath and self.savepath_fid.get(norm_savepath):
                self.savepath_fid[savepath] = self.savepath_fid[norm_savepath]
            else:
                savepath_fids = self.get_fids([savepath])
                if not savepath_fids:
                    # print(f"保存路径不存在，准备新建：{savepath}")
                    mkdir_result = self.mkdir(savepath)
                    if mkdir_result["code"] == 0:
                        self.savepath_fid[savepath] = mkdir_result["data"]["fid"]
                        # print(f"保存路径新建成功：{savepath}")
                    else:
                        # print(f"保存路径新建失败：{mkdir_result['message']}")
                        return
                else:
                    # 路径已存在，直接设置fid
                    self.savepath_fid[savepath] = savepath_fids[0]["fid"]

        # 支持顺序命名模式
        if task.get("use_sequence_naming") and task.get("sequence_naming"):
            # 顺序命名模式下已经在do_save中打印了顺序命名信息，这里不再重复打印
            # 设置正则模式为空
            task["regex_pattern"] = None
            # 构建顺序命名的正则表达式
            sequence_pattern = task["sequence_naming"]
            # 将{}替换为(\d+)用于匹配
            if sequence_pattern == "{}":
                # 对于单独的{}，使用特殊匹配
                regex_pattern = "(\\d+)"
            else:
                regex_pattern = re.escape(sequence_pattern).replace('\\{\\}', '(\\d+)')
            task["regex_pattern"] = regex_pattern
        # 支持剧集命名模式
        elif task.get("use_episode_naming") and task.get("episode_naming"):
            # 剧集命名模式下已经在do_save中打印了剧集命名信息，这里不再重复打印
            # 构建剧集命名的正则表达式
            episode_pattern = task["episode_naming"]
            # 先检查是否包含合法的[]字符
            if "[]" in episode_pattern:
                # 对于所有包含[]的模式，使用完整的剧集号识别规则
                regex_pattern = "SPECIAL_EPISODE_PATTERN"  # 这个标记后续用于特殊处理
                task["use_complex_episode_extraction"] = True  # 添加一个标记
                # 保存原始模式，用于生成新文件名
                task["original_episode_pattern"] = episode_pattern
            else:
                # 如果输入模式不包含[]，则使用简单匹配模式，避免正则表达式错误
                regex_pattern = "^" + re.escape(episode_pattern) + "(\\d+)$"

            task["regex_pattern"] = regex_pattern
        else:
            # 正则命名模式
            pattern, replace = self.magic_regex_func(
                task.get("pattern", ""), task.get("replace", ""), task["taskname"]
            )
            # 注释掉这里的正则表达式打印，因为在do_save函数中已经打印了
            # 只有在非魔法变量情况下才显示展开后的正则表达式
            # 对于魔法变量($TV等)，显示原始输入
            # if pattern and task.get("pattern") and task.get("pattern") not in CONFIG_DATA.get("magic_regex", MAGIC_REGEX):
            #     print(f"正则匹配: {pattern}")
            #     print(f"正则替换: {replace}")
            
        # 保存文件
        tree = self.dir_check_and_save(task, pwd_id, stoken, pdir_fid)
        
        # 检查是否有新文件转存
        if tree and tree.size() <= 1:  # 只有根节点意味着没有新文件
            return False
            
        return tree

    def dir_check_and_save(self, task, pwd_id, stoken, pdir_fid="", subdir_path="", parent_dir_info=None):
        tree = Tree()
        # 获取分享文件列表
        share_file_list = self.get_detail(pwd_id, stoken, pdir_fid)["list"]
        # print("share_file_list: ", share_file_list)

        if not share_file_list:
            if subdir_path == "":
                task["shareurl_ban"] = "分享为空，文件已被分享者删除"
                add_notify(f"❌《{task['taskname']}》: {task['shareurl_ban']}\n")
            return tree
        elif (
            len(share_file_list) == 1
            and share_file_list[0]["dir"]
            and subdir_path == ""
        ):  # 仅有一个文件夹
            print("🧠 该分享是一个文件夹，读取文件夹内列表")
            share_file_list = self.get_detail(
                pwd_id, stoken, share_file_list[0]["fid"]
            )["list"]
            
        # 添加父目录信息传递，用于确定是否在更新目录内
        if parent_dir_info is None:
            parent_dir_info = {
                "in_update_dir": False,  # 标记是否在更新目录内
                "update_dir_pattern": task.get("update_subdir", ""),  # 保存更新目录的正则表达式
                "dir_path": []  # 保存目录路径
            }
            
        # 对文件列表进行排序，使用全局文件排序函数的倒序排序
        # 这样可以确保起始文件过滤逻辑正确工作
        share_file_list.sort(key=sort_file_by_name, reverse=True)

        # 应用过滤词过滤
        if task.get("filterwords"):
            # 记录过滤前的文件总数（包括文件夹）
            original_total_count = len(share_file_list)

            # 使用高级过滤函数处理保留词和过滤词
            share_file_list = advanced_filter_files(share_file_list, task["filterwords"])
            
            # 打印过滤信息（格式保持不变）
            # 计算剩余文件数
            remaining_count = len(share_file_list)
            
            # 区分不同模式的显示逻辑：
            # 顺序命名和剧集命名模式不处理文件夹，应该排除文件夹计数
            # 正则命名模式会处理文件夹，但只处理符合正则表达式的文件夹
            if task.get("use_sequence_naming") or task.get("use_episode_naming"):
                # 计算剩余的实际可用文件数（排除文件夹）
                remaining_usable_count = len([f for f in share_file_list if not f.get("dir", False)])
                print(f"📑 应用过滤词: {task['filterwords']}，剩余 {remaining_usable_count} 个项目")
            else:
                # 正则模式下，需要先检查哪些文件/文件夹会被实际转存
                pattern, replace = "", ""
                # 检查是否是剧集命名模式
                if task.get("use_episode_naming") and task.get("regex_pattern"):
                    # 使用预先准备好的正则表达式
                    pattern = task["regex_pattern"]
                else:
                    # 普通正则命名模式
                    pattern, replace = self.magic_regex_func(
                        task.get("pattern", ""), task.get("replace", ""), task["taskname"]
                    )
                
                # 确保pattern不为空，避免正则表达式错误
                if not pattern:
                    pattern = ".*"
                
                # 计算真正会被转存的项目数量，使用简化的逻辑
                try:
                    # 简化的计算逻辑：只检查正则表达式匹配
                    processable_items = []
                    for share_file in share_file_list:
                        # 检查是否符合正则表达式
                        if not re.search(pattern, share_file["file_name"]):
                            continue
                        processable_items.append(share_file)
                    
                    remaining_count = len(processable_items)
                except Exception as e:
                    # 出错时回退到简单计数方式
                    print(f"⚠️ 计算可处理项目时出错: {str(e)}")
                    remaining_count = len([f for f in share_file_list if re.search(pattern, f["file_name"])])
                
                print(f"📑 应用过滤词: {task['filterwords']}，剩余 {remaining_count} 个项目")
            print()

        # 获取目标目录文件列表
        savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}{subdir_path}")
        if not self.savepath_fid.get(savepath):
            # 检查规范化路径是否已在字典中
            norm_savepath = re.sub(r"/{2,}", "/", f"/{savepath}")
            if norm_savepath != savepath and self.savepath_fid.get(norm_savepath):
                self.savepath_fid[savepath] = self.savepath_fid[norm_savepath]
            else:
                savepath_fids = self.get_fids([savepath])
                if not savepath_fids:
                    # print(f"保存路径不存在，准备新建：{savepath}")
                    mkdir_result = self.mkdir(savepath)
                    if mkdir_result["code"] == 0:
                        self.savepath_fid[savepath] = mkdir_result["data"]["fid"]
                        # print(f"保存路径新建成功：{savepath}")
                    else:
                        # print(f"保存路径新建失败：{mkdir_result['message']}")
                        return
                else:
                    # 路径已存在，直接设置fid
                    self.savepath_fid[savepath] = savepath_fids[0]["fid"]
        to_pdir_fid = self.savepath_fid[savepath]
        dir_file_list = self.ls_dir(to_pdir_fid)
        
        # 检查根节点是否已存在
        if not tree.contains(pdir_fid):
            tree.create_node(
                savepath,
                pdir_fid,
                data={
                    "is_dir": True,
                },
            )

        # 处理顺序命名模式
        if task.get("use_sequence_naming") and task.get("sequence_naming"):
            # 顺序命名模式
            current_sequence = 1
            sequence_pattern = task["sequence_naming"]
            regex_pattern = task.get("regex_pattern")
            
            # 查找目录中现有的最大序号
            for dir_file in dir_file_list:
                if not dir_file["dir"]:  # 只检查文件
                    if sequence_pattern == "{}":
                        # 对于单独的{}，直接尝试匹配整个文件名是否为数字
                        file_name_without_ext = os.path.splitext(dir_file["file_name"])[0]
                        if file_name_without_ext.isdigit():
                            try:
                                seq_num = int(file_name_without_ext)
                                current_sequence = max(current_sequence, seq_num + 1)
                            except (ValueError, IndexError):
                                pass
                    elif matches := re.match(regex_pattern, dir_file["file_name"]):
                        try:
                            seq_num = int(matches.group(1))
                            current_sequence = max(current_sequence, seq_num + 1)
                        except (ValueError, IndexError):
                            pass
            
            # 构建目标目录中所有文件的查重索引（按大小和修改时间）
            dir_files_map = {}
            for dir_file in dir_file_list:
                if not dir_file["dir"]:  # 仅处理文件
                    file_size = dir_file.get("size", 0)
                    file_ext = os.path.splitext(dir_file["file_name"])[1].lower()
                    update_time = dir_file.get("updated_at", 0)
                    
                    # 创建大小+扩展名的索引，用于快速查重
                    key = f"{file_size}_{file_ext}"
                    if key not in dir_files_map:
                        dir_files_map[key] = []
                    dir_files_map[key].append({
                        "file_name": dir_file["file_name"],
                        "updated_at": update_time,
                    })
            
            # 预先过滤掉已经存在的文件（按大小和扩展名比对）
            # 只保留文件，不保留文件夹
            filtered_share_files = []
            start_fid = task.get("startfid", "")
            start_file_found = False



            for share_file in share_file_list:
                if share_file["dir"]:
                    # 顺序命名模式下，未设置update_subdir时不处理文件夹
                    continue

                # 改进的起始文件过滤逻辑 - 优先执行，在数据库查重之前
                if start_fid:
                    if share_file["fid"] == start_fid:
                        start_file_found = True
                        # 找到起始文件，但不包含起始文件本身，只处理比它更新的文件
                        continue
                    elif start_file_found:
                        # 已经找到起始文件，跳过后续（更旧的）文件
                        continue
                    # 如果还没找到起始文件，说明当前文件比起始文件更新，需要处理
                else:
                    # 没有设置起始文件，处理所有文件
                    pass

                # 检查文件ID是否存在于转存记录中
                file_id = share_file.get("fid", "")
                if file_id and self.check_file_exists_in_records(file_id, task):
                    # 文件ID已存在于记录中，跳过处理
                    continue

                file_size = share_file.get("size", 0)
                file_ext = os.path.splitext(share_file["file_name"])[1].lower()
                share_update_time = share_file.get("last_update_at", 0) or share_file.get("updated_at", 0)

                # 检查是否已存在相同大小和扩展名的文件
                key = f"{file_size}_{file_ext}"
                is_duplicate = False
                if key in dir_files_map:
                    for existing_file in dir_files_map[key]:
                        existing_update_time = existing_file.get("updated_at", 0)
                        # 防止除零错误
                        if existing_update_time == 0:
                            continue
                        # 如果修改时间相近（30天内）或者差距不大（10%以内），认为是同一个文件
                        time_diff = abs(share_update_time - existing_update_time)
                        time_ratio = abs(1 - (share_update_time / existing_update_time)) if existing_update_time else 1
                        if time_diff < 2592000 or time_ratio < 0.1:
                            # 文件已存在，跳过处理
                            is_duplicate = True
                            break

                # 只有非重复文件才进行处理
                if not is_duplicate:
                    filtered_share_files.append(share_file)
            
            # 实现高级排序算法
            def extract_sorting_value(file):
                # 使用全局排序函数
                sort_tuple = sort_file_by_name(file)
                # 返回排序元组，实现多级排序
                return sort_tuple

            # 对过滤后的文件进行排序（正序，确保顺序命名按正确顺序进行）
            # 注意：这里使用正序排序，因为顺序命名需要按照正确的顺序分配序号
            filtered_share_files.sort(key=extract_sorting_value)

            # 判断是否使用单独的{}模式

            # 需保存的文件清单
            need_save_list = []
            
            # 为每个文件分配序号
            for share_file in filtered_share_files:
                # 获取文件扩展名
                file_ext = os.path.splitext(share_file["file_name"])[1]
                # 生成新文件名
                save_name = sequence_pattern.replace("{}", f"{current_sequence:02d}") + file_ext
                
                # 应用字幕命名规则
                save_name = apply_subtitle_naming_rule(save_name, task)
                
                # 检查目标目录是否已存在此文件，支持忽略后缀选项
                if task.get("ignore_extension", False):
                    # 忽略后缀模式：只比较文件名部分，不比较扩展名
                    save_name_base = os.path.splitext(save_name)[0]
                    file_exists = any(
                        os.path.splitext(dir_file["file_name"])[0] == save_name_base for dir_file in dir_file_list
                    )
                else:
                    # 不忽略后缀模式：完整文件名必须匹配
                    file_exists = any(
                        dir_file["file_name"] == save_name for dir_file in dir_file_list
                    )
                
                if not file_exists:
                    # 设置保存文件名（单独的{}不在这里重命名，而是在do_rename_task中处理）
                    if sequence_pattern == "{}":
                        share_file["save_name"] = share_file["file_name"]  # 保持原文件名，稍后在do_rename_task中处理
                    else:
                        share_file["save_name"] = save_name
                    share_file["original_name"] = share_file["file_name"]  # 保存原文件名，用于排序
                    need_save_list.append(share_file)
                    current_sequence += 1
                else:
                    # print(f"跳过已存在的文件: {save_name}")
                    pass
                
                # 这里不需要再次检查起始文件，因为在前面的过滤中已经处理了
                    
            # 处理子文件夹
            for share_file in share_file_list:
                if share_file["dir"] and task.get("update_subdir", False):
                    # 确定是否处理此目录：
                    # 1. 如果当前在更新目录内，则处理所有子目录
                    # 2. 如果不在更新目录内，只处理符合更新目录规则的子目录
                    if (parent_dir_info and parent_dir_info.get("in_update_dir", False)) or re.search(task["update_subdir"], share_file["file_name"]):
                        # print(f"检查子目录: {savepath}/{share_file['file_name']}")
                        
                        # 创建一个子任务对象，保留原任务的属性，但专门用于子目录处理
                        subdir_task = task.copy()
                        # 确保子目录也可以使用忽略后缀功能
                        
                        # 将子目录任务设置为正则命名模式
                        # 如果原任务没有设置pattern，或者使用的是顺序/剧集命名，确保有基本的pattern
                        if (not subdir_task.get("pattern") or 
                            subdir_task.get("use_sequence_naming") or 
                            subdir_task.get("use_episode_naming")):
                            subdir_task["pattern"] = ".*"
                            subdir_task["replace"] = ""
                            # 取消顺序命名和剧集命名模式，强制使用正则模式
                            subdir_task["use_sequence_naming"] = False
                            subdir_task["use_episode_naming"] = False
                            
                        # 更新子目录的parent_dir_info，跟踪目录路径和更新状态
                        current_parent_info = parent_dir_info.copy() if parent_dir_info else {
                            "in_update_dir": False,
                            "update_dir_pattern": task.get("update_subdir", ""),
                            "dir_path": []
                        }
                        
                        # 如果当前文件夹符合更新目录规则，标记为在更新目录内
                        if re.search(task["update_subdir"], share_file["file_name"]):
                            current_parent_info["in_update_dir"] = True
                            
                        # 添加当前目录到路径
                        current_parent_info["dir_path"] = current_parent_info["dir_path"].copy() if "dir_path" in current_parent_info else []
                        current_parent_info["dir_path"].append(share_file["file_name"])
                        
                        subdir_tree = self.dir_check_and_save(
                            subdir_task,
                            pwd_id,
                            stoken,
                            share_file["fid"],
                            f"{subdir_path}/{share_file['file_name']}",
                            current_parent_info
                        )
                        # 只有当子目录树有实际内容（大于1表示不只有根节点）时才处理
                        if subdir_tree.size(1) > 0:
                            # 检查子目录树是否只包含文件夹而没有文件
                            has_files = False
                            for node in subdir_tree.all_nodes_itr():
                                # 检查是否有非目录节点（即文件节点）
                                if node.data and not node.data.get("is_dir", False):
                                    has_files = True
                                    break
                                    
                            # 只有当子目录包含文件时才将其合并到主树中
                            if has_files:
                                # 获取保存路径的最后一部分目录名
                                save_path_basename = os.path.basename(task.get("savepath", "").rstrip("/"))
                                
                                # 跳过与保存路径同名的目录
                                if share_file["file_name"] == save_path_basename:
                                    continue
                                
                                # 合并子目录树
                                tree.create_node(
                                    f"📁{share_file['file_name']}",
                                    share_file["fid"],
                                    parent=pdir_fid,
                                    data={
                                        "is_dir": share_file["dir"],
                                    },
                                )
                                tree.merge(share_file["fid"], subdir_tree, deep=False)
                                
                                # 标记此文件夹有更新
                                # 检查文件夹是否已添加到need_save_list
                                folder_in_list = False
                                for item in need_save_list:
                                    if item.get("fid") == share_file["fid"]:
                                        item["has_updates"] = True
                                        folder_in_list = True
                                        break
                                
                                # 如果文件夹未添加到need_save_list，需要添加
                                if not folder_in_list and has_files:
                                    # 检查目标目录中是否已存在同名文件夹
                                    dir_exists = False
                                    for dir_file in dir_file_list:
                                        if dir_file["dir"] and dir_file["file_name"] == share_file["file_name"]:
                                            dir_exists = True
                                            break
                                    
                                    # 如果存在同名文件夹，检查文件夹内是否有更新
                                    # 如果不存在同名文件夹，或者文件夹内有更新，则添加到保存列表
                                    if not dir_exists or has_files:
                                        share_file["save_name"] = share_file["file_name"]
                                        share_file["original_name"] = share_file["file_name"]
                                        share_file["has_updates"] = True
                                        need_save_list.append(share_file)
                
        elif task.get("use_episode_naming") and task.get("episode_naming"):
            # 剧集命名模式
            need_save_list = []

            # 构建目标目录中所有文件的查重索引（按大小和修改时间）
            dir_files_map = {}
            for dir_file in dir_file_list:
                if not dir_file["dir"]:  # 仅处理文件
                    file_size = dir_file.get("size", 0)
                    file_ext = os.path.splitext(dir_file["file_name"])[1].lower()
                    update_time = dir_file.get("updated_at", 0)

                    # 创建大小+扩展名的索引，用于快速查重
                    key = f"{file_size}_{file_ext}"
                    if key not in dir_files_map:
                        dir_files_map[key] = []
                    dir_files_map[key].append({
                        "file_name": dir_file["file_name"],
                        "updated_at": update_time,
                    })

            # 预先过滤分享文件列表，去除已存在的文件
            filtered_share_files = []
            start_fid = task.get("startfid", "")
            start_file_found = False

            for share_file in share_file_list:
                if share_file["dir"]:
                    # 处理子目录
                    if task.get("update_subdir") and re.search(task["update_subdir"], share_file["file_name"]):
                        filtered_share_files.append(share_file)
                    continue

                # 改进的起始文件过滤逻辑 - 优先执行，在数据库查重之前
                if start_fid:
                    if share_file["fid"] == start_fid:
                        start_file_found = True
                        # 找到起始文件，但不包含起始文件本身，只处理比它更新的文件
                        continue
                    elif start_file_found:
                        # 已经找到起始文件，跳过后续（更旧的）文件
                        continue
                    # 如果还没找到起始文件，说明当前文件比起始文件更新，需要处理
                else:
                    # 没有设置起始文件，处理所有文件
                    pass

                # 检查文件ID是否存在于转存记录中
                file_id = share_file.get("fid", "")
                if file_id and self.check_file_exists_in_records(file_id, task):
                    # 文件ID已存在于记录中，跳过处理
                    continue

                # 从共享文件中提取剧集号
                episode_num = extract_episode_number(share_file["file_name"])
                is_duplicate = False

                # 通过文件名判断是否已存在（新的查重逻辑）
                if not is_duplicate:
                    # 如果没有重命名，判断原文件名是否已存在
                    original_name = share_file["file_name"]
                    # 如果有剧集号，判断重命名后的文件名是否已存在
                    file_ext = os.path.splitext(original_name)[1]

                    if episode_num is not None:
                        # 根据剧集命名模式生成目标文件名
                        episode_pattern = task["episode_naming"]
                        if episode_pattern == "[]":
                            target_name = f"{episode_num:02d}{file_ext}"
                        else:
                            target_name = episode_pattern.replace("[]", f"{episode_num:02d}") + file_ext
                        
                        # 应用字幕命名规则
                        target_name = apply_subtitle_naming_rule(target_name, task)

                        # 检查目标文件名是否已存在，支持忽略后缀选项
                        if task.get("ignore_extension", False):
                            # 忽略后缀模式：只比较文件名部分，不比较扩展名
                            target_name_base = os.path.splitext(target_name)[0]
                            target_exists = any(
                                os.path.splitext(dir_file["file_name"])[0] == target_name_base for dir_file in dir_file_list
                            )
                        else:
                            # 不忽略后缀模式：完整文件名必须匹配
                            target_exists = any(dir_file["file_name"] == target_name for dir_file in dir_file_list)

                        if target_exists:
                            is_duplicate = True

                    # 如果没有重复，检查文件大小和扩展名是否重复
                    if not is_duplicate:
                        file_size = share_file.get("size", 0)
                        file_ext_lower = file_ext.lower()
                        share_update_time = share_file.get("last_update_at", 0) or share_file.get("updated_at", 0)

                        # 检查是否已存在相同大小和扩展名的文件
                        key = f"{file_size}_{file_ext_lower}"
                        if key in dir_files_map:
                            for existing_file in dir_files_map[key]:
                                existing_update_time = existing_file.get("updated_at", 0)
                                # 防止除零错误
                                if existing_update_time == 0:
                                    continue
                                # 如果修改时间相近（30天内）或者差距不大（10%以内），认为是同一个文件
                                time_diff = abs(share_update_time - existing_update_time)
                                time_ratio = abs(1 - (share_update_time / existing_update_time)) if existing_update_time else 1
                                if time_diff < 2592000 or time_ratio < 0.1:
                                    # 文件已存在，跳过处理
                                    is_duplicate = True
                                    break

                if not is_duplicate:
                    share_file["save_name"] = share_file["file_name"]  # 剧集命名模式下保持原文件名，重命名在后续步骤进行
                    share_file["original_name"] = share_file["file_name"]
                    filtered_share_files.append(share_file)

            # 实现高级排序算法
            def sort_by_episode(file):
                if file["dir"]:
                    return (float('inf'), 0)

                filename = file["file_name"]

                # 优先匹配S01E01格式
                match_s_e = re.search(r'[Ss](\d+)[Ee](\d+)', filename)
                if match_s_e:
                    season = int(match_s_e.group(1))
                    episode = int(match_s_e.group(2))
                    return (season * 1000 + episode, 0)

                # 使用统一的剧集提取函数
                episode_num = extract_episode_number(filename)
                if episode_num is not None:
                    return (episode_num, 0)

                # 无法识别，回退到修改时间排序
                return (float('inf'), file.get("last_update_at", 0))

            # 过滤出文件并排序
            files_to_process = [f for f in filtered_share_files if not f["dir"]]
            sorted_files = sorted(files_to_process, key=sort_by_episode)

            # 要保存的文件列表
            need_save_list = []

            # 添加排序后的文件到保存列表
            for share_file in sorted_files:
                need_save_list.append(share_file)

            # 处理文件夹
            for share_file in filtered_share_files:
                if share_file["dir"]:
                    need_save_list.append(share_file)

        else:
            # 正则命名模式（普通正则命名模式）
            need_save_list = []

            # 构建目标目录中所有文件的查重索引（按大小和修改时间）- 加入文件查重机制
            dir_files_map = {}
            for dir_file in dir_file_list:
                if not dir_file["dir"]:  # 仅处理文件
                    file_size = dir_file.get("size", 0)
                    file_ext = os.path.splitext(dir_file["file_name"])[1].lower()
                    update_time = dir_file.get("updated_at", 0)

                    # 创建大小+扩展名的索引，用于快速查重
                    key = f"{file_size}_{file_ext}"
                    if key not in dir_files_map:
                        dir_files_map[key] = []
                    dir_files_map[key].append({
                        "file_name": dir_file["file_name"],
                        "updated_at": update_time,
                    })

            # 应用起始文件过滤逻辑
            start_fid = task.get("startfid", "")
            if start_fid:
                # 找到起始文件的索引
                start_index = -1
                for i, share_file in enumerate(share_file_list):
                    if share_file["fid"] == start_fid:
                        start_index = i
                        break

                if start_index >= 0:
                    # 只处理起始文件之前的文件（比起始文件更新的文件，不包括起始文件本身）
                    share_file_list = share_file_list[:start_index]

            # 添加符合的
            for share_file in share_file_list:
                # 检查文件ID是否存在于转存记录中
                file_id = share_file.get("fid", "")
                if file_id and self.check_file_exists_in_records(file_id, task):
                    # 文件ID已存在于记录中，跳过处理
                    continue
                    
                # 检查文件是否已存在（通过大小和扩展名）- 新增的文件查重逻辑
                is_duplicate = False
                if not share_file["dir"]:  # 文件夹不进行内容查重
                    # 新的查重逻辑：优先使用文件名查重，支持忽略后缀
                    original_file_name = share_file["file_name"]
                    original_name_base = os.path.splitext(original_file_name)[0]
                    
                    # 判断是否用正则替换后的文件名
                    if task.get("pattern") and task.get("replace") is not None:
                        pattern, replace = self.magic_regex_func(
                            task.get("pattern", ""), task.get("replace", ""), task.get("taskname", "")
                        )
                        # 确保pattern不为空，避免正则表达式错误
                        if pattern:
                            try:
                                # 尝试应用正则替换
                                if re.search(pattern, original_file_name):
                                    renamed_file = re.sub(pattern, replace, original_file_name)
                                    # 应用字幕命名规则
                                    renamed_file = apply_subtitle_naming_rule(renamed_file, task)
                                    renamed_base = os.path.splitext(renamed_file)[0]
                                else:
                                    renamed_file = None
                                    renamed_base = None
                            except Exception:
                                # 正则出错时使用原文件名
                                renamed_file = None
                                renamed_base = None
                        else:
                            renamed_file = None
                            renamed_base = None
                    else:
                        renamed_file = None
                        renamed_base = None
                    
                    # 查重逻辑，同时考虑原文件名和重命名后的文件名
                    for dir_file in dir_file_list:
                        if dir_file["dir"]:
                            continue
                        
                        if task.get("ignore_extension", False):
                            # 忽略后缀：只比较文件名部分，不管扩展名
                            existing_name_base = os.path.splitext(dir_file["file_name"])[0]
                            
                            # 如果原文件名或重命名后文件名与目标目录中文件名相同（忽略后缀），则视为已存在
                            if (existing_name_base == original_name_base or 
                                (renamed_file and existing_name_base == renamed_base)):
                                is_duplicate = True
                                break
                        else:
                            # 不忽略后缀：文件名和扩展名都要一致才视为同一个文件
                            if (dir_file["file_name"] == original_file_name or 
                                (renamed_file and dir_file["file_name"] == renamed_file)):
                                is_duplicate = True
                                break
                    

                # 如果文件已经存在并且不是目录，跳过后续处理
                if is_duplicate and not share_file["dir"]:
                    continue
                    
                # 设置匹配模式：目录使用update_subdir，文件使用普通正则
                if share_file["dir"] and task.get("update_subdir", False):
                    # 确定是否处理此目录：
                    # 1. 如果当前在更新目录内，则处理所有子目录
                    # 2. 如果不在更新目录内，只处理符合更新目录规则的子目录
                    if parent_dir_info and parent_dir_info.get("in_update_dir", False):
                        # 已经在更新目录内，处理所有子目录
                        pass
                    elif not re.search(task["update_subdir"], share_file["file_name"]):
                        # 不在更新目录内，且不符合更新目录规则，跳过处理
                        continue
                    
                    # 先检查目标目录中是否已存在这个子目录
                    dir_exists = False
                    for dir_file in dir_file_list:
                        if dir_file["dir"] and dir_file["file_name"] == share_file["file_name"]:
                            dir_exists = True
                            break
                    
                    # 如果目标中已经存在此子目录，则直接检查子目录的内容更新，不要重复转存
                    if dir_exists:
                        # 子目录存在，直接递归处理其中的文件，不在主目录的处理中再转存一次
                        # print(f"检查子目录: {savepath}/{share_file['file_name']} (已存在)")
                        
                        # 创建一个子任务对象，专门用于子目录处理
                        subdir_task = task.copy()
                        if (not subdir_task.get("pattern") or 
                            subdir_task.get("use_sequence_naming") or 
                            subdir_task.get("use_episode_naming")):
                            subdir_task["pattern"] = ".*"
                            subdir_task["replace"] = ""
                            # 取消顺序命名和剧集命名模式，强制使用正则模式
                            subdir_task["use_sequence_naming"] = False
                            subdir_task["use_episode_naming"] = False
                        
                        # 更新子目录的parent_dir_info，跟踪目录路径和更新状态
                        current_parent_info = parent_dir_info.copy() if parent_dir_info else {
                            "in_update_dir": False,
                            "update_dir_pattern": task.get("update_subdir", ""),
                            "dir_path": []
                        }
                        
                        # 如果当前文件夹符合更新目录规则，标记为在更新目录内
                        if re.search(task["update_subdir"], share_file["file_name"]):
                            current_parent_info["in_update_dir"] = True
                            
                        # 添加当前目录到路径
                        current_parent_info["dir_path"] = current_parent_info["dir_path"].copy() if "dir_path" in current_parent_info else []
                        current_parent_info["dir_path"].append(share_file["file_name"])
                        
                        # 递归处理子目录但不在need_save_list中添加目录本身
                        subdir_tree = self.dir_check_and_save(
                            subdir_task,
                            pwd_id,
                            stoken,
                            share_file["fid"],
                            f"{subdir_path}/{share_file['file_name']}",
                            current_parent_info
                        )
                        
                        # 如果子目录有新内容，合并到主树中
                        if subdir_tree and subdir_tree.size() > 1:
                            has_files = False
                            for node in subdir_tree.all_nodes_itr():
                                if node.data and not node.data.get("is_dir", False):
                                    has_files = True
                                    break
                            
                            if has_files:
                                # 添加目录到树中但不添加到保存列表
                                if not tree.contains(share_file["fid"]):
                                    tree.create_node(
                                        f"📁{share_file['file_name']}",
                                        share_file["fid"],
                                        parent=pdir_fid,
                                        data={
                                            "is_dir": share_file["dir"],
                                        },
                                    )
                                # 合并子目录树
                                tree.merge(share_file["fid"], subdir_tree, deep=False)
                        
                        # 跳过后续处理，不对已存在的子目录再做转存处理
                        continue
                    
                    # 目录不存在，继续正常流程
                    pattern, replace = task["update_subdir"], ""
                else:
                    # 检查是否是剧集命名模式
                    if task.get("use_episode_naming") and task.get("regex_pattern"):
                        # 使用预先准备好的正则表达式
                        pattern = task["regex_pattern"]
                        replace = ""
                    else:
                        # 普通正则命名模式
                        pattern, replace = self.magic_regex_func(
                            task.get("pattern", ""), task.get("replace", ""), task["taskname"]
                        )
                
                # 确保pattern不为空，避免正则表达式错误
                if not pattern:
                    pattern = ".*"
                    
                # 正则文件名匹配
                try:
                    if re.search(pattern, share_file["file_name"]):
                        # 替换后的文件名
                        save_name = (
                            re.sub(pattern, replace, share_file["file_name"])
                            if replace != ""
                            else share_file["file_name"]
                        )
                        
                        # 应用字幕命名规则
                        save_name = apply_subtitle_naming_rule(save_name, task)
                        
                        # 检查新名称是否存在重复的前缀
                        if replace and " - " in save_name:
                            parts = save_name.split(" - ")
                            if len(parts) >= 2 and parts[0] == parts[1]:
                                # 如果新名称包含重复前缀，使用原文件名
                                save_name = share_file["file_name"]
                            
                            # 检查是否任务名已经存在于原文件名中
                            taskname = task.get("taskname", "")
                            if taskname and taskname in share_file["file_name"] and share_file["file_name"].startswith(taskname):
                                # 如果原文件名已包含任务名作为前缀，保持原样
                                save_name = share_file["file_name"]
                        
                        # 为正则模式实现基于文件名的查重逻辑，支持忽略后缀选项
                        # 判断目标目录文件是否存在
                        file_exists = False
                        for dir_file in dir_file_list:
                            if dir_file["dir"] and share_file["dir"]:
                                # 如果都是目录，只要名称相同就视为已存在
                                if dir_file["file_name"] == share_file["file_name"]:
                                    file_exists = True
                                    break
                            elif not dir_file["dir"] and not share_file["dir"]:
                                # 如果都是文件
                                if task.get("ignore_extension", False):
                                    # 忽略后缀：只比较文件名部分，不管扩展名
                                    original_name_base = os.path.splitext(share_file["file_name"])[0]
                                    renamed_name_base = os.path.splitext(save_name)[0]
                                    existing_name_base = os.path.splitext(dir_file["file_name"])[0]
                                    
                                    # 如果原文件名或重命名后文件名与目标目录中文件名相同（忽略后缀），则视为已存在
                                    if existing_name_base == original_name_base or existing_name_base == renamed_name_base:
                                        file_exists = True
                                        break
                                else:
                                    # 不忽略后缀：文件名和扩展名都要一致才视为同一个文件
                                    if dir_file["file_name"] == share_file["file_name"] or dir_file["file_name"] == save_name:
                                        file_exists = True
                                        break
                                
                        if not file_exists:
                            # 不打印保存信息
                            share_file["save_name"] = save_name
                            share_file["original_name"] = share_file["file_name"]  # 保存原文件名，用于排序
                            
                            # 文件夹需要特殊处理，标记为has_updates=False，等待后续检查
                            # 只有在文件夹匹配update_subdir时才设置
                            if share_file["dir"]:
                                share_file["has_updates"] = False
                                
                            # 将文件添加到保存列表
                            need_save_list.append(share_file)
                        elif share_file["dir"]:
                            # 文件夹已存在，根据是否递归处理子目录决定操作
                            
                            # 如果开启了子目录递归，处理子目录结构
                            if task.get("update_subdir", False):
                                # print(f"检查子目录: {savepath}/{share_file['file_name']}")
                                
                                # 创建一个子任务对象，保留原任务的属性，但专门用于子目录处理
                                subdir_task = task.copy()
                                # 确保子目录也可以使用忽略后缀功能
                                
                                # 如果原任务没有设置pattern，确保有基本的pattern
                                if not subdir_task.get("pattern"):
                                    subdir_task["pattern"] = ".*"  # 在子目录中匹配所有文件
                                    subdir_task["replace"] = ""
                                
                                # 更新子目录的parent_dir_info，跟踪目录路径和更新状态
                                current_parent_info = parent_dir_info.copy() if parent_dir_info else {
                                    "in_update_dir": False,
                                    "update_dir_pattern": task.get("update_subdir", ""),
                                    "dir_path": []
                                }
                                
                                # 如果当前文件夹符合更新目录规则，标记为在更新目录内
                                if re.search(task["update_subdir"], share_file["file_name"]):
                                    current_parent_info["in_update_dir"] = True
                                    
                                # 添加当前目录到路径
                                current_parent_info["dir_path"] = current_parent_info["dir_path"].copy() if "dir_path" in current_parent_info else []
                                current_parent_info["dir_path"].append(share_file["file_name"])
                                
                                # 递归处理子目录
                                subdir_tree = self.dir_check_and_save(
                                    subdir_task,
                                    pwd_id,
                                    stoken,
                                    share_file["fid"],
                                    f"{subdir_path}/{share_file['file_name']}",
                                    current_parent_info
                                )
                                
                                # 只有当子目录树有实际内容（大于1表示不只有根节点）时才处理
                                if subdir_tree and subdir_tree.size() > 1:
                                    # 检查子目录树是否只包含文件夹而没有文件
                                    has_files = False
                                    for node in subdir_tree.all_nodes_itr():
                                        # 检查是否有非目录节点（即文件节点）
                                        if node.data and not node.data.get("is_dir", False):
                                            has_files = True
                                            break
                                    
                                    # 只有当子目录包含文件时才将其合并到主树中
                                    if has_files:
                                        # 获取保存路径的最后一部分目录名
                                        save_path_basename = os.path.basename(task.get("savepath", "").rstrip("/"))
                                        
                                        # 跳过与保存路径同名的目录
                                        if share_file["file_name"] == save_path_basename:
                                            continue
                                        
                                        # 添加目录到树中
                                        # 检查节点是否已存在于树中，避免重复添加
                                        if not tree.contains(share_file["fid"]):
                                            tree.create_node(
                                                f"📁{share_file['file_name']}",
                                                share_file["fid"],
                                                parent=pdir_fid,
                                                data={
                                                    "is_dir": share_file["dir"],
                                                },
                                            )
                                        # 合并子目录树
                                        tree.merge(share_file["fid"], subdir_tree, deep=False)
                                        
                                        # 检查文件夹是否已添加到need_save_list
                                        folder_in_list = False
                                        for item in need_save_list:
                                            if item.get("fid") == share_file["fid"]:
                                                # 文件夹已在列表中，设置为有更新
                                                item["has_updates"] = True
                                                folder_in_list = True
                                                break
                                        
                                        # 如果文件夹未添加到need_save_list且有文件更新，则添加
                                        if not folder_in_list:
                                            # 检查目标目录中是否已存在同名子目录
                                            dir_exists = False
                                            for dir_file in dir_file_list:
                                                if dir_file["dir"] and dir_file["file_name"] == share_file["file_name"]:
                                                    dir_exists = True
                                                    break
                                            
                                            # 只有当目录不存在于目标位置时，才将其添加到转存列表
                                            if not dir_exists:
                                                # 将父文件夹添加到保存列表，确保子目录的变化能被处理
                                                share_file["save_name"] = share_file["file_name"]
                                                share_file["original_name"] = share_file["file_name"]
                                                share_file["has_updates"] = True  # 标记为有更新
                                                need_save_list.append(share_file)
                                                print(f"发现子目录 {share_file['file_name']} 有更新，将包含到转存列表")
                                            else:
                                                # 如果子目录已存在，只显示提示消息，不添加到转存列表
                                                print(f"发现子目录 {share_file['file_name']} 有更新，将更新到已存在的文件夹中")
                except Exception as e:
                    print(f"⚠️ 正则表达式错误: {str(e)}, pattern: {pattern}")
                    # 使用安全的默认值
                    share_file["save_name"] = share_file["file_name"]
                    share_file["original_name"] = share_file["file_name"]
                    need_save_list.append(share_file)

        fid_list = [item["fid"] for item in need_save_list]
        fid_token_list = [item["share_fid_token"] for item in need_save_list]
        
        # 过滤掉没有真正内容更新的文件夹（仅在正则命名模式下）
        if not task.get("use_sequence_naming") and not task.get("use_episode_naming") and need_save_list:
            # 计算非目录文件数量
            non_dir_files = [item for item in need_save_list if not item.get("dir", False)]
            
            # 如果有常规文件，代表有真正的更新
            has_file_updates = len(non_dir_files) > 0
            
            # 检查文件夹是否标记为有更新
            folders_with_updates = [item for item in need_save_list if item.get("dir", False) and item.get("has_updates", False) == True]
            has_folder_updates = len(folders_with_updates) > 0
            
            # 获取保存路径的最后一部分目录名
            save_path_basename = os.path.basename(task.get("savepath", "").rstrip("/"))
            
            # 从列表中移除没有真正更新的文件夹和与保存路径同名的目录
            filtered_need_save_list = []
            for item in need_save_list:
                # 跳过与保存路径同名的目录
                if item.get("dir", False) and item.get("save_name") == save_path_basename:
                    continue
                    
                # 跳过没有更新的文件夹
                if item.get("dir", False) and item.get("has_updates", False) == False and not has_file_updates and not has_folder_updates:
                    continue
                    
                # 保留其他所有项目
                filtered_need_save_list.append(item)
                
            need_save_list = filtered_need_save_list
            
            # 如果过滤后列表为空，直接返回树对象
            if not need_save_list:
                return tree
            
            # 更新fid列表
            fid_list = [item["fid"] for item in need_save_list]
            fid_token_list = [item["share_fid_token"] for item in need_save_list]
        
        if fid_list:
            # 只在有新文件需要转存时才处理
            save_file_return = self.save_file(
                fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken
            )
            err_msg = None
            if save_file_return["code"] == 0:
                task_id = save_file_return["data"]["task_id"]
                query_task_return = self.query_task(task_id)
                if query_task_return["code"] == 0:
                    # 等待文件保存完成
                    time.sleep(1)
                    
                    # 检查是否需要自动解压压缩文件
                    auto_extract_mode = task.get("auto_extract_archive", "disabled")
                    if auto_extract_mode != "disabled":
                        # 获取刚转存的文件列表
                        fresh_files = self.ls_dir(to_pdir_fid)

                        # 检查是否有压缩文件需要解压
                        items_to_remove = []
                        items_to_add = []
                        
                        for saved_item in need_save_list:
                            if not saved_item.get("dir", False) and self.is_archive_file(saved_item["file_name"]):
                                # 在目标目录中找到对应的压缩文件
                                archive_file = None
                                for f in fresh_files:
                                    if f["file_name"] == saved_item["file_name"]:
                                        archive_file = f
                                        break
                                
                                if archive_file:
                                    # 执行自动解压
                                    extract_result = self.process_auto_extract(
                                        archive_fid=archive_file["fid"],
                                        archive_name=archive_file["file_name"],
                                        target_pdir_fid=to_pdir_fid,
                                        extract_mode=auto_extract_mode,
                                        task=task
                                    )
                                    
                                    if extract_result.get("success"):
                                        # 只有在压缩文件确实被删除时，才从列表中移除它
                                        # 这样可以避免后续重命名操作处理不存在的文件
                                        if extract_result.get("archive_deleted", False):
                                            items_to_remove.append(saved_item)
                                        # 添加解压出的文件到列表
                                        for moved_file in extract_result.get("moved_files", []):
                                            items_to_add.append({
                                                "fid": moved_file["fid"],
                                                "file_name": moved_file["file_name"],
                                                "save_name": moved_file["file_name"],
                                                "original_name": moved_file["file_name"],
                                                "size": moved_file["size"],
                                                "dir": False,
                                                "obj_category": "default",
                                                "share_fid_token": "",
                                                "_from_extraction": True,  # 标记来自解压
                                            })
                        
                        # 不论何种命名模式，删除压缩包时都保留其转存记录，便于后续查重、避免重复转存
                        for item in items_to_remove:
                            self.create_transfer_record(
                                task=task,
                                file_info=item,
                                renamed_to=item.get("file_name", item.get("save_name", ""))
                            )
                        # 更新 need_save_list
                        for item in items_to_remove:
                            if item in need_save_list:
                                need_save_list.remove(item)
                        need_save_list.extend(items_to_add)
                    
                    # 建立目录树
                    saved_files = []

                    for index, item in enumerate(need_save_list):
                        icon = (
                            "📁"
                            if item["dir"] == True
                            else "🎞️" if item["obj_category"] == "video" else get_file_icon(item["save_name"], False)
                        )
                        
                        # 修复文件树显示问题 - 防止文件名重复重复显示
                        # 对于顺序命名和剧集命名模式，转存时使用原文件名显示
                        # 因为实际文件是以原名保存的，重命名在后续步骤进行
                        if task.get("use_sequence_naming") or task.get("use_episode_naming"):
                            display_name = item['file_name']  # 使用原文件名
                        else:
                            # 其他模式使用save_name
                            display_name = item['save_name']
                        
                        # 确保只显示文件/文件夹名，而不是完整路径
                        if "/" in display_name:
                            # 只取路径的最后一部分作为显示名
                            display_name = display_name.split("/")[-1]
                        
                        # 不再自动添加任务名称前缀，尊重用户选择
                        
                        # 保存到树中
                        saved_files.append(format_file_display("", icon, display_name))
                        # 检查节点是否已存在于树中，避免重复添加
                        if not tree.contains(item["fid"]):
                            # 安全地获取save_as_top_fids中的fid，防止索引越界
                            save_as_top_fids = query_task_return.get('data', {}).get('save_as', {}).get('save_as_top_fids', [])
                            saved_fid = save_as_top_fids[index] if index < len(save_as_top_fids) else item["fid"]

                            # 标记该节点是否来自自动解压（仅对文件生效）
                            from_extraction = item.get("_from_extraction", False) if not item["dir"] else False

                            tree.create_node(
                                display_name,  # 只存储文件名，不包含图标
                                item["fid"],
                                parent=pdir_fid,
                                data={
                                    "fid": f"{saved_fid}",
                                    "path": f"{savepath}/{item['save_name']}",
                                    "is_dir": item["dir"],
                                    "icon": icon,  # 将图标存储在data中
                                    "from_extraction": from_extraction,
                                },
                            )
                            
                            # 保存转存记录到数据库
                            if not item["dir"]:  # 只记录文件，不记录文件夹
                                # 转存时先用原文件名记录，重命名后再更新
                                self.create_transfer_record(
                                    task=task,
                                    file_info=item,
                                    renamed_to=item["file_name"]  # 转存时使用原文件名
                                )
                    
                    # 移除通知生成，由do_save函数统一处理
                    # 顺序命名模式和剧集命名模式都不在此处生成通知
                else:
                    err_msg = query_task_return["message"]
            else:
                err_msg = save_file_return["message"]
            if err_msg:
                add_notify(f"❌《{task['taskname']}》转存失败: {err_msg}\n")
        else:
            # 没有新文件需要转存
            if not subdir_path:  # 只在顶层（非子目录）打印一次消息
                pass
        return tree

    def do_rename_task(self, task, subdir_path=""):
        # 检查是否为顺序命名模式
        if task.get("use_sequence_naming") and task.get("sequence_naming"):
            # 使用顺序命名模式
            sequence_pattern = task["sequence_naming"]
            # 替换占位符为正则表达式捕获组
            if sequence_pattern == "{}":
                # 对于单独的{}，使用特殊匹配
                regex_pattern = "(\\d+)"
            else:
                regex_pattern = re.escape(sequence_pattern).replace('\\{\\}', '(\\d+)')
            
            savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}{subdir_path}")
            if not self.savepath_fid.get(savepath):
                # 路径已存在，直接设置fid
                self.savepath_fid[savepath] = self.get_fids([savepath])[0]["fid"]
            dir_file_list = self.ls_dir(self.savepath_fid[savepath])
            dir_file_name_list = [item["file_name"] for item in dir_file_list]
            
            # 判断目录是否为空（只包含非目录文件）
            non_dir_files = [f for f in dir_file_list if not f.get("dir", False)]
            is_empty_dir = len(non_dir_files) == 0

            # 应用过滤词过滤（修复bug：为本地文件重命名添加过滤规则）
            if task.get("filterwords"):
                # 记录过滤前的文件总数
                original_total_count = len(dir_file_list)
                
                # 使用高级过滤函数处理保留词和过滤词
                dir_file_list = advanced_filter_files(dir_file_list, task["filterwords"])
                dir_file_name_list = [item["file_name"] for item in dir_file_list]

            # 找出当前最大序号
            max_sequence = 0
            # 先检查目录中是否有符合命名规则的文件
            has_matching_files = False

            for dir_file in dir_file_list:
                if dir_file.get("dir", False):
                    continue  # 跳过文件夹

                if sequence_pattern == "{}":
                    # 对于单独的{}，直接尝试匹配整个文件名是否为数字
                    file_name_without_ext = os.path.splitext(dir_file["file_name"])[0]
                    if file_name_without_ext.isdigit():
                        # 增加判断：如果是日期格式的纯数字，不应被视为序号
                        if not is_date_format(file_name_without_ext):
                            try:
                                current_seq = int(file_name_without_ext)
                                max_sequence = max(max_sequence, current_seq)
                                has_matching_files = True
                            except (ValueError, IndexError):
                                pass
                elif matches := re.match(regex_pattern, dir_file["file_name"]):
                    try:
                        current_seq = int(matches.group(1))
                        max_sequence = max(max_sequence, current_seq)
                        has_matching_files = True
                    except (IndexError, ValueError):
                        pass

            if not has_matching_files:
                # 没有符合命名规则的文件时，检查数据库中的转存记录
                try:
                    from app.sdk.db import RecordDB
                    db = RecordDB()

                    # 获取当前保存路径
                    current_save_path = task.get("savepath", "")
                    if subdir_path:
                        current_save_path = f"{current_save_path}/{subdir_path}"

                    # 查询该目录的转存记录
                    records = db.get_records_by_save_path(current_save_path, include_subpaths=False)

                    # 从转存记录中提取最大序号
                    max_sequence_from_records = 0
                    for record in records:
                        renamed_to = record.get("renamed_to", "")
                        if renamed_to:
                            if sequence_pattern == "{}":
                                # 对于单独的{}，直接尝试匹配整个文件名是否为数字
                                file_name_without_ext = os.path.splitext(renamed_to)[0]
                                if file_name_without_ext.isdigit():
                                    try:
                                        seq_num = int(file_name_without_ext)
                                        max_sequence_from_records = max(max_sequence_from_records, seq_num)
                                    except (ValueError, IndexError):
                                        pass
                            elif matches := re.match(regex_pattern, renamed_to):
                                try:
                                    seq_num = int(matches.group(1))
                                    max_sequence_from_records = max(max_sequence_from_records, seq_num)
                                except (ValueError, IndexError):
                                    pass

                    # 使用记录中的最大序号
                    max_sequence = max_sequence_from_records
                    db.close()
                except Exception as e:
                    max_sequence = 0
            
            # 实现高级排序算法
            def extract_sorting_value(file):
                # 使用全局排序函数
                sort_tuple = sort_file_by_name(file)
                # 返回排序元组，实现多级排序
                return sort_tuple
            
            # 判断是否使用单独的{}模式
            
            # 初始化sorted_files列表，用于收集需要重命名的文件
            sorted_files = []
            
            # 对于单独的{}模式，增加额外检查
            if sequence_pattern == "{}":
                # 收集所有不是纯数字命名的文件
                for dir_file in dir_file_list:
                    if dir_file["dir"]:
                        continue  # 跳过文件夹
                    
                    file_name_without_ext = os.path.splitext(dir_file["file_name"])[0]
                    # 检查文件名是否为纯数字，如果是则跳过（已经命名好的）
                    if file_name_without_ext.isdigit():
                        # 增加判断：如果是日期格式的纯数字，不视为已命名
                        if not is_date_format(file_name_without_ext):
                            # 不是日期格式，是纯数字序号，跳过
                            continue
                        # 是日期格式，需要重命名，所以不跳过
                    
                    # 添加到需要处理的文件列表
                    sorted_files.append(dir_file)
            else:
                # 对于非单独{}的模式，收集所有不符合模式的文件
                for dir_file in dir_file_list:
                    if dir_file["dir"]:
                        continue  # 跳过文件夹
                    
                    # 检查是否已符合命名模式
                    if re.match(regex_pattern, dir_file["file_name"]):
                        continue  # 跳过已经符合命名规则的文件
                    
                    # 添加到需要处理的文件列表
                    sorted_files.append(dir_file)
            
            # 使用extract_sorting_value函数对所有需要处理的文件进行排序
            sorted_files = sorted(sorted_files, key=extract_sorting_value)
            
            # 收集所有需要重命名的文件，并按顺序处理
            renamed_pairs = []
            
            # 如果没有找到有效的最大序号，从1开始命名
            if max_sequence == 0:
                current_sequence = 0  # 会立即加1，所以从0开始
            else:
                current_sequence = max_sequence
            
            # 对排序好的文件应用顺序命名
            for dir_file in sorted_files:
                current_sequence += 1
                file_ext = os.path.splitext(dir_file["file_name"])[1]
                # 根据顺序命名模式生成新的文件名
                if sequence_pattern == "{}":
                    # 对于单独的{}，直接使用数字序号作为文件名，不再使用日期格式
                    save_name = f"{current_sequence:02d}{file_ext}"
                else:
                    save_name = sequence_pattern.replace("{}", f"{current_sequence:02d}") + file_ext
                
                # 应用字幕命名规则
                save_name = apply_subtitle_naming_rule(save_name, task)
                
                # 检查是否需要重命名，支持忽略后缀选项
                name_conflict = False
                if task.get("ignore_extension", False):
                    # 忽略后缀模式：只比较文件名部分，不比较扩展名
                    save_name_base = os.path.splitext(save_name)[0]
                    name_conflict = any(
                        os.path.splitext(existing_name)[0] == save_name_base for existing_name in dir_file_name_list
                    )
                else:
                    # 不忽略后缀模式：完整文件名必须匹配
                    name_conflict = save_name in dir_file_name_list

                if save_name != dir_file["file_name"] and not name_conflict:
                    # 收集重命名对，包含原始文件信息以便排序
                    renamed_pairs.append((dir_file, save_name, current_sequence))
                    dir_file_name_list.append(save_name)
            
            # 确保按照序号顺序执行重命名操作
            renamed_pairs.sort(key=lambda x: x[2])
            
            is_rename_count = 0
            rename_logs = []  # 初始化重命名日志列表
            # 执行重命名，并按顺序打印
            for dir_file, save_name, _ in renamed_pairs:
                try:
                    rename_return = self.rename(dir_file["fid"], save_name)
                    # 防止网络问题导致的错误
                    if isinstance(rename_return, dict) and rename_return.get("code") == 0:
                        rename_log = f"重命名: {dir_file['file_name']} → {save_name}"
                        rename_logs.append(rename_log)
                        # 移除直接打印的部分，由do_save负责打印
                        # print(rename_log)
                        is_rename_count += 1
                        
                        # 更新重命名记录到数据库（只更新renamed_to字段）
                        self.update_transfer_record(
                            task=task,
                            file_info=dir_file,
                            renamed_to=save_name
                        )
                    else:
                        error_msg = rename_return.get("message", "未知错误")
                        rename_log = f"重命名: {dir_file['file_name']} → {save_name} 失败，{error_msg}"
                        rename_logs.append(rename_log)
                        # 移除直接打印的部分，由do_save负责打印
                        # print(rename_log)
                except Exception as e:
                    rename_log = f"重命名出错: {dir_file['file_name']} → {save_name}，错误: {str(e)}"
                    rename_logs.append(rename_log)
                    # 移除直接打印的部分，由do_save负责打印
                    # print(rename_log)
            
            return is_rename_count > 0, rename_logs

        # 检查是否为剧集命名模式
        elif task.get("use_episode_naming") and task.get("episode_naming"):
            # 使用剧集命名模式
            episode_pattern = task["episode_naming"]
            regex_pattern = task.get("regex_pattern")
            
            # 初始化变量
            already_renamed_files = set()  # 用于防止重复重命名
            
            # 获取目录文件列表 - 添加这行代码初始化dir_file_list
            savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}{subdir_path}")
            if not self.savepath_fid.get(savepath):
                # 路径已存在，直接设置fid
                savepath_fids = self.get_fids([savepath])
                if not savepath_fids:
                    # print(f"保存路径不存在，准备新建：{savepath}")
                    mkdir_result = self.mkdir(savepath)
                    if mkdir_result["code"] == 0:
                        self.savepath_fid[savepath] = mkdir_result["data"]["fid"]
                        # print(f"保存路径新建成功：{savepath}")
                    else:
                        # print(f"保存路径新建失败：{mkdir_result['message']}")
                        return False, []
                else:
                    self.savepath_fid[savepath] = savepath_fids[0]["fid"]
            
            dir_file_list = self.ls_dir(self.savepath_fid[savepath])
            rename_logs = []  # 全剧集分支共用：转存/解压后重命名与本地文件重命名的日志
            is_rename_count = 0  # 全分支共用，供最终 return (is_rename_count > 0, rename_logs)

            # 构建目标目录中所有文件的查重索引（按大小和修改时间）
            dir_files_map = {}
            for dir_file in dir_file_list:
                if not dir_file["dir"]:  # 仅处理文件
                    file_size = dir_file.get("size", 0)
                    file_ext = os.path.splitext(dir_file["file_name"])[1].lower()
                    update_time = dir_file.get("updated_at", 0)
                    
                    # 创建大小+扩展名的索引，用于快速查重
                    key = f"{file_size}_{file_ext}"
                    if key not in dir_files_map:
                        dir_files_map[key] = []
                    dir_files_map[key].append({
                        "file_name": dir_file["file_name"],
                        "updated_at": update_time,
                    })
            
            # 实现序号提取函数
            def extract_episode_number_local(filename):
                # 使用全局的统一提取函数，直接使用全局CONFIG_DATA
                if 'CONFIG_DATA' not in globals() or not CONFIG_DATA:
                    return extract_episode_number(filename)
                return extract_episode_number(filename, config_data=CONFIG_DATA)
                
            # 找出已命名的文件列表，避免重复转存
            existing_episode_numbers = set()
            for dir_file in dir_file_list:
                if not dir_file["dir"]:
                    try:
                        # 对于剧集命名模式，直接使用extract_episode_number函数提取剧集号
                        episode_num = extract_episode_number_local(dir_file["file_name"])
                        if episode_num is not None:
                            existing_episode_numbers.add(episode_num)
                    except:
                        pass
            
            # 检查是否需要从分享链接获取数据
            if task.get("shareurl"):
                # 如果任务已经有 shareurl_ban，说明分享已失效，不需要再尝试获取分享详情
                if task.get("shareurl_ban"):
                    return False, []
                try:
                    # 提取链接参数
                    pwd_id, passcode, pdir_fid, paths = self.extract_url(task["shareurl"])
                    if not pwd_id:
                        print(f"提取链接参数失败，请检查分享链接是否有效")
                        return False, []
                    
                    # 获取分享详情
                    is_sharing, stoken = self.get_stoken(pwd_id, passcode)
                    if not is_sharing:
                        # 如果任务已经有 shareurl_ban，说明已经在 do_save_task 中处理过了，不需要重复输出
                        if not task.get("shareurl_ban"):
                            print(f"分享详情获取失败: {stoken}")
                        return False, []
                    
                    # 获取分享文件列表
                    share_file_list = self.get_detail(pwd_id, stoken, pdir_fid)["list"]
                    if not share_file_list:
                        # 如果任务已经有 shareurl_ban，说明已经在 do_save_task 中处理过了，不需要重复输出
                        if not task.get("shareurl_ban"):
                            print("分享为空，文件已被分享者删除")
                        return False, []

                    # 在剧集命名模式中，需要先对文件列表进行排序，然后再应用起始文件过滤
                    # 使用全局排序函数进行排序（倒序，最新的在前）
                    share_file_list = sorted(share_file_list, key=sort_file_by_name, reverse=True)

                    # 预先过滤分享文件列表，去除已存在的文件
                    filtered_share_files = []
                    start_fid = task.get("startfid", "")
                    start_file_found = False

                    for share_file in share_file_list:
                        if share_file["dir"]:
                            # 处理子目录
                            if task.get("update_subdir") and re.search(task["update_subdir"], share_file["file_name"]):
                                filtered_share_files.append(share_file)
                            continue

                        # 检查文件ID是否存在于转存记录中
                        file_id = share_file.get("fid", "")
                        if file_id and self.check_file_exists_in_records(file_id, task):
                            # 文件ID已存在于记录中，跳过处理
                            continue

                        # 改进的起始文件过滤逻辑
                        if start_fid:
                            if share_file["fid"] == start_fid:
                                start_file_found = True
                                # 找到起始文件，但不包含起始文件本身，只处理比它更新的文件
                                continue
                            elif start_file_found:
                                # 已经找到起始文件，跳过后续（更旧的）文件
                                continue
                            # 如果还没找到起始文件，说明当前文件比起始文件更新，需要处理
                        else:
                            # 没有设置起始文件，处理所有文件
                            pass

                        # 根目录下已由 do_save_task 解压过的压缩包不再转存，避免重新保存后只被重命名而残留
                        if not subdir_path and self.is_archive_file(share_file["file_name"]):
                            # 保持原目录结构：用 _auto_extract_subdirs（如 /26）判断
                            auto_subs = task.get("_auto_extract_subdirs") or []
                            base = os.path.splitext(share_file["file_name"])[0]
                            if f"/{base}" in auto_subs:
                                continue
                            # 扁平化或其它：用本次任务已解压文件名列表判断
                            if share_file["file_name"] in (task.get("_extracted_archive_names") or []):
                                continue
                            
                        # 从共享文件中提取剧集号
                        episode_num = extract_episode_number_local(share_file["file_name"])
                        is_duplicate = False
                        
                        # 通过文件名判断是否已存在（新的查重逻辑）
                        if not is_duplicate:
                            # 如果没有重命名，判断原文件名是否已存在
                            original_name = share_file["file_name"]
                            # 如果有剧集号，判断重命名后的文件名是否已存在
                            file_ext = os.path.splitext(original_name)[1]
                            
                            # 构建可能的新文件名
                            if episode_num is not None:
                                if episode_pattern == "[]":
                                    new_name = f"{episode_num:02d}{file_ext}"
                                else:
                                    new_name = episode_pattern.replace("[]", f"{episode_num:02d}") + file_ext
                                # 应用字幕命名规则
                                new_name = apply_subtitle_naming_rule(new_name, task)
                            else:
                                new_name = None
                            
                            # 根据是否忽略后缀进行检查
                            if task.get("ignore_extension", False):
                                # 忽略后缀模式：只比较文件名，不比较扩展名
                                original_name_base = os.path.splitext(original_name)[0]
                                
                                # 检查原文件名是否存在
                                exists_original = any(os.path.splitext(dir_file["file_name"])[0] == original_name_base for dir_file in dir_file_list)
                                
                                # 如果有新文件名，检查新文件名是否存在
                                exists_new = False
                                if new_name:
                                    new_name_base = os.path.splitext(new_name)[0]
                                    exists_new = any(os.path.splitext(dir_file["file_name"])[0] == new_name_base for dir_file in dir_file_list)
                                
                                is_duplicate = exists_original or exists_new
                            else:
                                # 不忽略后缀模式：文件名和扩展名都要一致
                                exists_original = any(dir_file["file_name"] == original_name for dir_file in dir_file_list)
                                
                                # 如果有新文件名，检查新文件名是否存在
                                exists_new = False
                                if new_name:
                                    exists_new = any(dir_file["file_name"] == new_name for dir_file in dir_file_list)
                                
                                is_duplicate = exists_original or exists_new
                        
                        # 只处理非重复文件
                        if not is_duplicate:
                            filtered_share_files.append(share_file)
                    
                    # 实现高级排序算法
                    def sort_by_episode(file):
                        if file["dir"]:
                            return (float('inf'), 0)
                        
                        filename = file["file_name"]
                        
                        # 优先匹配S01E01格式
                        match_s_e = re.search(r'[Ss](\d+)[Ee](\d+)', filename)
                        if match_s_e:
                            season = int(match_s_e.group(1))
                            episode = int(match_s_e.group(2))
                            return (season * 1000 + episode, 0)
                        
                        # 使用统一的剧集提取函数
                        episode_num = extract_episode_number_local(filename)
                        if episode_num is not None:
                            return (episode_num, 0)
                        
                        # 无法识别，回退到修改时间排序
                        return (float('inf'), file.get("last_update_at", 0))
                        
                    # 过滤出文件并排序
                    files_to_process = [f for f in filtered_share_files if not f["dir"]]
                    sorted_files = sorted(files_to_process, key=sort_by_episode)
                    
                    # 要保存的文件列表
                    need_save_list = []
                    
                    # 生成文件名并添加到列表
                    for share_file in sorted_files:
                        episode_num = extract_episode_number_local(share_file["file_name"])
                        if episode_num is not None:
                            # 生成新文件名
                            file_ext = os.path.splitext(share_file["file_name"])[1]
                            if episode_pattern == "[]":
                                # 对于单独的[]，直接使用数字序号作为文件名
                                save_name = f"{episode_num:02d}{file_ext}"
                            else:
                                save_name = episode_pattern.replace("[]", f"{episode_num:02d}") + file_ext
                            
                            # 应用字幕命名规则
                            save_name = apply_subtitle_naming_rule(save_name, task)
                            
                            # 检查过滤词
                            should_filter = False
                            if task.get("filterwords"):
                                # 使用高级过滤函数检查文件名
                                temp_file_list = [{"file_name": share_file["file_name"]}]
                                if advanced_filter_files(temp_file_list, task["filterwords"]):
                                    # 检查目标文件名
                                    temp_save_list = [{"file_name": save_name}]
                                    if not advanced_filter_files(temp_save_list, task["filterwords"]):
                                        should_filter = True
                                else:
                                    should_filter = True
                            
                            # 只处理不需要过滤的文件
                            if not should_filter:
                                # 添加到保存列表
                                share_file["save_name"] = save_name
                                share_file["original_name"] = share_file["file_name"]
                                need_save_list.append(share_file)
                        else:
                            # 无法提取集号，使用原文件名（仍然检查过滤词）
                            # 检查过滤词
                            should_filter = False
                            if task.get("filterwords"):
                                # 使用高级过滤函数检查文件名
                                temp_file_list = [{"file_name": share_file["file_name"]}]
                                if not advanced_filter_files(temp_file_list, task["filterwords"]):
                                    should_filter = True
                            
                            # 只处理不需要过滤的文件
                            if not should_filter:
                                share_file["save_name"] = share_file["file_name"]
                                share_file["original_name"] = share_file["file_name"]
                                need_save_list.append(share_file)
                    
                    # 若当前子目录来自自动解压（_auto_extract_subdirs），则不再保存与解压，仅做重命名
                    auto_extract_subdirs_ep = task.get("_auto_extract_subdirs") or []
                    is_auto_extracted_subdir_ep = subdir_path and subdir_path in auto_extract_subdirs_ep
                    
                    if is_auto_extracted_subdir_ep:
                        # 自动解压子目录：基于现有文件构建 need_save_list，仅做重命名
                        need_save_list = []
                        for f in dir_file_list:
                            if not f.get("dir", False):
                                fname = f["file_name"]
                                ep_num = extract_episode_number_local(fname)
                                if ep_num is not None:
                                    ext = os.path.splitext(fname)[1]
                                    save_name = episode_pattern.replace("[]", f"{ep_num:02d}") + ext if episode_pattern != "[]" else f"{ep_num:02d}{ext}"
                                    save_name = apply_subtitle_naming_rule(save_name, task)
                                else:
                                    save_name = fname
                                need_save_list.append({"file_name": fname, "original_name": fname, "save_name": save_name})
                    
                    # 保存文件（自动解压产生的子目录跳过保存与解压），或仅执行重命名
                    run_rename_block = False
                    if need_save_list:
                        if is_auto_extracted_subdir_ep:
                            run_rename_block = True
                        else:
                            fid_list = [item["fid"] for item in need_save_list]
                            fid_token_list = [item["share_fid_token"] for item in need_save_list]
                            save_file_return = self.save_file(
                                fid_list, fid_token_list, self.savepath_fid[savepath], pwd_id, stoken
                            )
                            if save_file_return["code"] == 0:
                                task_id = save_file_return["data"]["task_id"]
                                query_task_return = self.query_task(task_id)
                                
                                if query_task_return["code"] == 0:
                                    # 进行重命名操作，确保文件按照预览名称保存
                                    time.sleep(1)  # 等待文件保存完成
                                    
                                    # 检查是否需要自动解压压缩文件
                                    # 根目录（subdir_path 为空）的解压已在 do_save_task 中执行，此处仅处理子目录，避免同一压缩包解压两次
                                    # 若当前子目录来自自动解压（_auto_extract_subdirs），则不再解压，避免重复解压导致嵌套
                                    auto_extract_subdirs = task.get("_auto_extract_subdirs") or []
                                    is_auto_extracted_subdir = subdir_path and subdir_path in auto_extract_subdirs
                                    
                                    auto_extract_mode = task.get("auto_extract_archive", "disabled")
                                    if auto_extract_mode != "disabled" and subdir_path and not is_auto_extracted_subdir:
                                        # 获取刚转存的文件列表
                                        fresh_files = self.ls_dir(self.savepath_fid[savepath])
                                        
                                        # 用于安全更新 need_save_list 的临时列表
                                        items_to_remove_ep = []
                                        items_to_add_ep = []
                                        
                                        # 检查是否有压缩文件需要解压
                                        for saved_item in need_save_list:
                                            if not saved_item.get("dir", False) and self.is_archive_file(saved_item["file_name"]):
                                                # 在目标目录中找到对应的压缩文件
                                                archive_file = None
                                                for f in fresh_files:
                                                    if f["file_name"] == saved_item["file_name"]:
                                                        archive_file = f
                                                        break
                                                
                                                if archive_file:
                                                    # 执行自动解压
                                                    extract_result = self.process_auto_extract(
                                                        archive_fid=archive_file["fid"],
                                                        archive_name=archive_file["file_name"],
                                                        target_pdir_fid=self.savepath_fid[savepath],
                                                        extract_mode=auto_extract_mode,
                                                        task=task
                                                    )
                                                    
                                                    if extract_result.get("success"):
                                                        # 只有在压缩文件确实被删除时，才从列表中移除它
                                                        if extract_result.get("archive_deleted", False):
                                                            items_to_remove_ep.append(saved_item)
                                                        # 添加解压后的文件到待添加列表
                                                        for moved_file in extract_result.get("moved_files", []):
                                                            # 关键修复：为解压出的文件生成正确的 save_name（根据剧集命名规则）
                                                            moved_file_name = moved_file["file_name"]
                                                            episode_num = extract_episode_number_local(moved_file_name)
                                                            if episode_num is not None:
                                                                # 根据剧集命名规则生成 save_name
                                                                file_ext = os.path.splitext(moved_file_name)[1]
                                                                if episode_pattern == "[]":
                                                                    save_name = f"{episode_num:02d}{file_ext}"
                                                                else:
                                                                    save_name = episode_pattern.replace("[]", f"{episode_num:02d}") + file_ext
                                                                # 应用字幕命名规则
                                                                save_name = apply_subtitle_naming_rule(save_name, task)
                                                            else:
                                                                # 无法提取剧集号，使用原文件名
                                                                save_name = moved_file_name
                                                            
                                                            items_to_add_ep.append({
                                                                "fid": moved_file["fid"],
                                                                "file_name": moved_file_name,
                                                                "original_name": moved_file_name,
                                                                "save_name": save_name,
                                                                "size": moved_file["size"],
                                                                "dir": False,
                                                                "_from_extraction": True,
                                                            })
                                        
                                        # 剧集模式下此处也会移除已解压的压缩包，同样保留转存记录（其它命名模式在 do_save_task 中已统一记录）
                                        for item in items_to_remove_ep:
                                            self.create_transfer_record(
                                                task=task,
                                                file_info=item,
                                                renamed_to=item.get("file_name", item.get("save_name", ""))
                                            )
                                        # 应用解压后的列表更新
                                        for item in items_to_remove_ep:
                                            if item in need_save_list:
                                                need_save_list.remove(item)
                                        need_save_list.extend(items_to_add_ep)
                                    
                                    # 保存转存记录到数据库（仅非自动解压子目录）
                                    if not is_auto_extracted_subdir:
                                        for saved_item in need_save_list:
                                            if not saved_item.get("dir", False):  # 只记录文件，不记录文件夹
                                                # 转存时先用原文件名记录，重命名后再更新
                                                self.create_transfer_record(
                                                    task=task,
                                                    file_info=saved_item,
                                                    renamed_to=saved_item["file_name"]  # 转存时使用原文件名
                                                )
                                    
                                    run_rename_block = True
                                else:
                                    err_msg = query_task_return["message"]
                                    add_notify(f"❌《{task['taskname']}》转存失败: {err_msg}\n")
                            else:
                                add_notify(f"❌《{task['taskname']}》转存失败: {save_file_return['message']}\n")
                    # 重命名逻辑：自动解压子目录或保存成功后执行
                    if run_rename_block:
                        # 刷新目录列表以获取新保存的文件
                        fresh_dir_file_list = self.ls_dir(self.savepath_fid[savepath])
                        
                        # 创建一个映射来存储原始文件名到保存项的映射
                        original_name_to_item = {}
                        for saved_item in need_save_list:
                            # 使用文件名前缀作为键，处理可能的文件名变化
                            file_prefix = saved_item["original_name"].split(".")[0]
                            original_name_to_item[file_prefix] = saved_item
                            # 同时保存完整文件名的映射
                            original_name_to_item[saved_item["original_name"]] = saved_item
                        
                        # 创建一个列表来收集所有重命名操作
                        rename_operations = []
                        
                        # 首先尝试使用剧集号进行智能匹配
                        for dir_file in fresh_dir_file_list:
                            if dir_file["dir"]:
                                continue
                            # 从文件名中提取剧集号
                            episode_num = extract_episode_number_local(dir_file["file_name"])
                            if episode_num is None:
                                continue
                            
                            # 查找对应的目标文件
                            for saved_item in need_save_list:
                                saved_episode_num = extract_episode_number_local(saved_item["original_name"])
                                if saved_episode_num == episode_num:
                                    # 关键修复：防止将视频文件错误匹配到压缩文件项
                                    if self.is_archive_file(dir_file["file_name"]) != self.is_archive_file(saved_item["original_name"]):
                                        continue
                                    target_name = saved_item["save_name"]
                                    if target_name not in [f["file_name"] for f in fresh_dir_file_list]:
                                        rename_operations.append((dir_file, target_name, episode_num))
                                        break
                                    else:
                                        name_base, ext = os.path.splitext(target_name)
                                        alt_name = f"{name_base} ({episode_num}){ext}"
                                        if alt_name not in [f["file_name"] for f in fresh_dir_file_list]:
                                            rename_operations.append((dir_file, alt_name, episode_num))
                                            break
                        
                        # 对于未能通过剧集号匹配的文件，尝试使用文件名匹配
                        for dir_file in fresh_dir_file_list:
                            if dir_file["dir"]:
                                continue
                            if any(op[0]["fid"] == dir_file["fid"] for op in rename_operations):
                                continue
                            if dir_file["file_name"] in original_name_to_item:
                                saved_item = original_name_to_item[dir_file["file_name"]]
                                if self.is_archive_file(dir_file["file_name"]) != self.is_archive_file(saved_item["original_name"]):
                                    continue
                                target_name = saved_item["save_name"]
                                episode_num = extract_episode_number_local(saved_item["original_name"]) or 9999
                                if target_name not in [f["file_name"] for f in fresh_dir_file_list]:
                                    rename_operations.append((dir_file, target_name, episode_num))
                                continue
                            dir_file_prefix = dir_file["file_name"].split(".")[0]
                            for prefix, saved_item in list(original_name_to_item.items()):
                                if prefix in dir_file_prefix or dir_file_prefix in prefix:
                                    if self.is_archive_file(dir_file["file_name"]) != self.is_archive_file(saved_item["original_name"]):
                                        continue
                                    target_name = saved_item["save_name"]
                                    episode_num = extract_episode_number_local(saved_item["original_name"]) or 9999
                                    if target_name not in [f["file_name"] for f in fresh_dir_file_list]:
                                        rename_operations.append((dir_file, target_name, episode_num))
                                        original_name_to_item.pop(prefix, None)
                                        break
                        
                        # 按剧集号排序重命名操作
                        rename_operations.sort(key=lambda x: x[2])
                        
                        # 执行排序后的重命名操作，收集到分支共用的 rename_logs
                        renamed_count = 0
                        for dir_file, target_name, _ in rename_operations:
                            rename_result = self.rename(dir_file["fid"], target_name)
                            if rename_result["code"] == 0:
                                log_message = f"重命名: {dir_file['file_name']} → {target_name}"
                                rename_logs.append(log_message)
                                renamed_count += 1
                                for df in fresh_dir_file_list:
                                    if df["fid"] == dir_file["fid"]:
                                        df["file_name"] = target_name
                                        break
                            else:
                                error_log = f"重命名: {dir_file['file_name']} → {target_name} 失败，{rename_result['message']}"
                                rename_logs.append(error_log)
                        is_rename_count += renamed_count
                        
                        # 更新dir_file_list以包含新转存的文件
                        dir_file_list = self.ls_dir(self.savepath_fid[savepath])
                    else:
                        # print("没有需要保存的新文件")
                        pass
                except Exception as e:
                    add_notify(f"❌《{task['taskname']}》处理分享链接时发生错误: {str(e)}\n")

            # 对本地已有文件进行重命名（即使没有分享链接或处理失败也执行）
            renamed_files = {}
            
            # 应用过滤词过滤（修复bug：为本地文件重命名添加过滤规则）
            if task.get("filterwords"):
                # 记录过滤前的文件总数
                original_total_count = len(dir_file_list)
                
                # 使用高级过滤函数处理保留词和过滤词
                dir_file_list = advanced_filter_files(dir_file_list, task["filterwords"])
            
            # 使用一个列表收集所有需要重命名的操作（rename_logs 已在分支开头初始化，此处只追加）
            rename_operations = []
            
            # 筛选出需要重命名的文件
            for dir_file in dir_file_list:
                if dir_file["dir"]:
                    continue

                # 检查是否需要重命名
                episode_num = extract_episode_number_local(dir_file["file_name"])
                if episode_num is not None:
                    # 根据剧集命名模式生成目标文件名
                    file_ext = os.path.splitext(dir_file["file_name"])[1]
                    if episode_pattern == "[]":
                        # 使用完整的剧集号识别逻辑，而不是简单的纯数字判断
                        # 生成新文件名
                        new_name = f"{episode_num:02d}{file_ext}"
                        # 应用字幕命名规则
                        new_name = apply_subtitle_naming_rule(new_name, task)
                        # 只有当当前文件名与目标文件名不同时才重命名
                        if dir_file["file_name"] != new_name:
                            rename_operations.append((dir_file, new_name, episode_num))
                    else:
                        # 生成目标文件名
                        new_name = episode_pattern.replace("[]", f"{episode_num:02d}") + file_ext
                        # 应用字幕命名规则
                        new_name = apply_subtitle_naming_rule(new_name, task)
                        # 检查文件名是否已经符合目标格式
                        if dir_file["file_name"] != new_name:
                            rename_operations.append((dir_file, new_name, episode_num))
            
            # 按剧集号排序
            rename_operations.sort(key=lambda x: x[2])

            # 执行重命名操作，但不立即打印日志
            for dir_file, new_name, _ in rename_operations:
                # 防止重名，支持忽略后缀选项
                name_conflict = False
                if task.get("ignore_extension", False):
                    # 忽略后缀模式：只比较文件名部分，不比较扩展名
                    new_name_base = os.path.splitext(new_name)[0]
                    name_conflict = any(
                        os.path.splitext(f["file_name"])[0] == new_name_base for f in dir_file_list
                    )
                else:
                    # 不忽略后缀模式：完整文件名必须匹配
                    name_conflict = new_name in [f["file_name"] for f in dir_file_list]

                if not name_conflict:
                    try:
                        rename_return = self.rename(dir_file["fid"], new_name)
                        if rename_return["code"] == 0:
                            # 收集日志但不打印
                            rename_logs.append(f"重命名: {dir_file['file_name']} → {new_name}")
                            is_rename_count += 1
                            # 更新dir_file_list中的文件名，防止后续重名判断出错
                            for df in dir_file_list:
                                if df["fid"] == dir_file["fid"]:
                                    df["file_name"] = new_name
                                    break
                            # 记录已重命名的文件
                            already_renamed_files.add(new_name)
                            
                            # 不在这里直接调用update_transfer_record，而是在do_save中统一处理
                            # self.update_transfer_record(
                            #     task=task,
                            #     file_info=dir_file,
                            #     renamed_to=new_name
                            # )
                        else:
                            # 收集错误日志但不打印
                            error_msg = rename_return.get("message", "未知错误")
                            
                            # 刷新目录列表，检查文件是否实际已重命名成功
                            fresh_dir_file_list = self.ls_dir(self.savepath_fid[savepath])
                            target_exists = any(df["file_name"] == new_name for df in fresh_dir_file_list)
                            
                            # 如果目标文件已存在，说明重命名已经成功或有同名文件
                            if target_exists:
                                # 对于已经成功的情况，我们仍然记录成功
                                rename_logs.append(f"重命名: {dir_file['file_name']} → {new_name}")
                                is_rename_count += 1
                                # 更新dir_file_list中的文件名
                                for df in dir_file_list:
                                    if df["fid"] == dir_file["fid"]:
                                        df["file_name"] = new_name
                                        break
                                # 记录已重命名的文件
                                already_renamed_files.add(new_name)
                            else:
                                # 真正的错误情况
                                # 注释掉错误消息记录
                                # rename_logs.append(f"重命名: {dir_file['file_name']} → {new_name} 失败，{error_msg}")
                                pass
                    except Exception as e:
                        # 收集错误日志但不打印
                        # 注释掉异常信息记录
                        # rename_logs.append(f"重命名出错: {dir_file['file_name']} → {new_name}，错误：{str(e)}")
                        pass
                else:
                    # 检查目标文件是否已经存在且是我们想要重命名的结果
                    # 这可能是因为之前的操作已经成功，但API返回了错误
                    fresh_dir_file_list = self.ls_dir(self.savepath_fid[savepath])
                    if any(df["file_name"] == new_name and df["fid"] != dir_file["fid"] for df in fresh_dir_file_list):
                        # 真正存在同名文件
                        # 注释掉同名文件警告信息记录
                        # rename_logs.append(f"重命名: {dir_file['file_name']} → {new_name} 失败，目标文件名已存在")
                        pass
                    else:
                        # 目标文件可能是之前操作的结果，不显示错误
                        pass
            
            # 返回重命名日志和成功标志
            return (is_rename_count > 0), rename_logs

        # 正则模式或无特殊命名模式，直接返回空结果
        else:
            # 检查是否有正则模式的配置
            pattern = task.get("pattern", "")
            replace = task.get("replace", "")
            
            # 如果没有设置正则匹配模式，直接返回空结果
            if not pattern:
                return False, []
            
            # 获取魔法正则处理后的真实规则
            pattern, replace = self.magic_regex_func(pattern, replace, task["taskname"])
            
            # 获取目录文件列表
            savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}{subdir_path}")
            if not self.savepath_fid.get(savepath):
                # 路径不存在，创建或获取fid
                savepath_fids = self.get_fids([savepath])
                if not savepath_fids:
                    # print(f"保存路径不存在，准备新建：{savepath}")
                    mkdir_result = self.mkdir(savepath)
                    if mkdir_result["code"] == 0:
                        self.savepath_fid[savepath] = mkdir_result["data"]["fid"]
                        # print(f"保存路径新建成功：{savepath}")
                    else:
                        # print(f"保存路径新建失败：{mkdir_result['message']}")
                        return False, []
                else:
                    self.savepath_fid[savepath] = savepath_fids[0]["fid"]
            
            # 获取目录中的文件列表
            dir_file_list = self.ls_dir(self.savepath_fid[savepath])
            
            # 应用过滤词过滤（修复bug：为本地文件重命名添加过滤规则）
            if task.get("filterwords"):
                # 记录过滤前的文件总数
                original_total_count = len(dir_file_list)
                
                # 使用高级过滤函数处理保留词和过滤词
                dir_file_list = advanced_filter_files(dir_file_list, task["filterwords"])
            
            # 使用一个列表收集所有需要重命名的操作
            rename_operations = []
            rename_logs = []  # 收集重命名日志
            is_rename_count = 0
            
            # 遍历目录中的文件，找出符合正则条件的
            for dir_file in dir_file_list:
                if dir_file["dir"]:
                    continue  # 跳过文件夹
                
                # 应用正则表达式
                try:
                    # 在应用正则表达式前，先检查文件名是否已经是符合目标格式的
                    orig_name = dir_file["file_name"]
                    
                    # 应用正则表达式获取目标文件名
                    new_name = re.sub(pattern, replace, orig_name)
                    
                    # 应用字幕命名规则
                    new_name = apply_subtitle_naming_rule(new_name, task)
                    
                    # 如果替换后的文件名没有变化，跳过
                    if new_name == orig_name:
                        continue
                    
                    # 如果替换后的文件名是任务名的重复嵌套，跳过
                    # 例如：对于"你好，星期六 - 2025-04-05.mp4" -> "你好，星期六 - 你好，星期六 - 2025-04-05.mp4"
                    if " - " in new_name:
                        parts = new_name.split(" - ")
                        # 检查是否有重复的部分
                        if len(parts) >= 2 and parts[0] == parts[1]:
                            continue
                        
                        # 另一种情况：检查前缀是否已存在于文件名中
                        prefix = replace.split(" - ")[0] if " - " in replace else ""
                        if prefix and prefix in orig_name:
                            # 如果原始文件名已经包含了需要添加的前缀，跳过重命名
                            continue
                            
                    # 如果文件名发生变化，需要重命名
                    if new_name != orig_name:
                        rename_operations.append((dir_file, new_name))
                except Exception as e:
                    print(f"正则替换出错: {dir_file['file_name']}，错误: {str(e)}")
            
            # 按原始文件名字母顺序排序，使重命名操作有序进行
            # rename_operations.sort(key=lambda x: x[0]["file_name"])
            
            # 修改为按日期或数字排序（复用与文件树相同的排序逻辑）
            def extract_sort_value(file_name):
                # 使用全局排序函数
                return sort_file_by_name(file_name)
            
            # 按目标文件名中的日期或数字进行排序，与顺序命名和剧集命名模式保持一致
            rename_operations.sort(key=lambda x: extract_sort_value(x[1]))
            
            # 执行重命名操作，并收集日志
            already_renamed_files = set()  # 用于防止重复重命名
            for dir_file, new_name in rename_operations:
                # 检查是否会导致重名，支持忽略后缀选项
                name_conflict = False
                if task.get("ignore_extension", False):
                    # 忽略后缀模式：只比较文件名部分，不比较扩展名
                    new_name_base = os.path.splitext(new_name)[0]
                    name_conflict = any(
                        os.path.splitext(f["file_name"])[0] == new_name_base for f in dir_file_list
                    ) or any(
                        os.path.splitext(existing_name)[0] == new_name_base for existing_name in already_renamed_files
                    )
                else:
                    # 不忽略后缀模式：完整文件名必须匹配
                    name_conflict = new_name in [f["file_name"] for f in dir_file_list] or new_name in already_renamed_files

                if not name_conflict:
                    try:
                        rename_return = self.rename(dir_file["fid"], new_name)
                        if rename_return["code"] == 0:
                            # 收集日志但不打印
                            log_message = f"重命名: {dir_file['file_name']} → {new_name}"
                            rename_logs.append(log_message)
                            is_rename_count += 1
                            # 更新dir_file_list中的文件名，防止后续重名判断出错
                            for df in dir_file_list:
                                if df["fid"] == dir_file["fid"]:
                                    df["file_name"] = new_name
                                    break
                            # 记录已重命名的文件
                            already_renamed_files.add(new_name)
                        else:
                            # 收集错误日志但不打印
                            error_msg = rename_return.get("message", "未知错误")
                            error_log = f"重命名: {dir_file['file_name']} → {new_name} 失败，{error_msg}"
                            rename_logs.append(error_log)
                    except Exception as e:
                        # 收集错误日志但不打印
                        error_log = f"重命名出错: {dir_file['file_name']} → {new_name}，错误: {str(e)}"
                        rename_logs.append(error_log)
                else:
                    # 重名警告但不打印
                    warning_log = f"重命名: {dir_file['file_name']} → {new_name} 失败，目标文件名已存在"
                    rename_logs.append(warning_log)
            
            # 返回重命名日志和成功标志
            return (is_rename_count > 0), rename_logs


def verify_account(account):
    # 验证账号
    print(f"▶️ 验证第 {account.index} 个账号")
    if "__uid" not in account.cookie:
        return False
    else:
        account_info = account.init()
        if not account_info:
            add_notify(f"👤 第 {account.index} 个账号登录失败，cookie 无效 ❌")
            return False
        else:
            print(f"👤 账号昵称: {account_info['nickname']} ✅")
            return True


def format_bytes(size_bytes: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")
    i = 0
    while size_bytes >= 1024 and i < len(units) - 1:
        size_bytes /= 1024
        i += 1
    return f"{size_bytes:.2f} {units[i]}"


def do_sign(account):
    if not account.mparam:
        print("⏭️ 移动端参数未设置，跳过签到")
        print()
        return
    # 每日领空间
    growth_info = account.get_growth_info()
    if growth_info:
        growth_message = f"💾 {'88VIP' if growth_info['88VIP'] else '普通用户'}: 总空间 {format_bytes(growth_info['total_capacity'])}，签到累计获得 {format_bytes(growth_info['cap_composition'].get('sign_reward', 0))}"
        if growth_info["cap_sign"]["sign_daily"]:
            sign_message = f"📅 签到记录: 今日已签到 +{int(growth_info['cap_sign']['sign_daily_reward']/1024/1024)}MB，连签进度（{growth_info['cap_sign']['sign_progress']}/{growth_info['cap_sign']['sign_target']}）✅"
            message = f"{sign_message}\n{growth_message}"
            print(message)
        else:
            sign, sign_return = account.get_growth_sign()
            if sign:
                sign_message = f"📅 执行签到: 今日签到 +{int(sign_return/1024/1024)}MB，连签进度（{growth_info['cap_sign']['sign_progress']+1}/{growth_info['cap_sign']['sign_target']}）✅"
                message = f"{sign_message}\n{growth_message}"
                if (
                    str(
                        CONFIG_DATA.get("push_config", {}).get("QUARK_SIGN_NOTIFY")
                    ).lower()
                    == "false"
                    or os.environ.get("QUARK_SIGN_NOTIFY") == "false"
                ):
                    print(message)
                else:
                    if account.nickname:
                        message = message.replace("今日", f"{account.nickname} 今日")
                    add_notify(message)
            else:
                print(f"📅 签到异常: {sign_return}")
    print()


def do_save(account, tasklist=[], ignore_execution_rules=False):
    print(f"🧩 载入插件")
    plugins, CONFIG_DATA["plugins"], task_plugins_config = Config.load_plugins(
        CONFIG_DATA.get("plugins", {})
    )
    print(f"转存账号: {account.nickname}")
    # 获取全部保存目录fid
    account.update_savepath_fid(tasklist)
    
    # 创建已发送通知跟踪集合，避免重复显示通知
    sent_notices = set()

    def is_time(task):
        # 若为手动单任务运行并明确要求忽略执行周期/进度限制，则始终执行
        if ignore_execution_rules:
            return True
        # 获取任务的执行周期模式，优先使用任务自身的execution_mode，否则使用系统配置的execution_mode
        execution_mode = task.get("execution_mode") or CONFIG_DATA.get("execution_mode", "manual")
        
        # 按任务进度执行（自动）
        if execution_mode == "auto":
            try:
                # 从task_metrics表获取任务进度
                cal_db = CalendarDB()
                task_name = task.get("taskname") or task.get("task_name") or ""
                if task_name:
                    metrics = cal_db.get_task_metrics(task_name)
                    if metrics and metrics.get("progress_pct") is not None:
                        progress_pct = int(metrics.get("progress_pct", 0))
                        # 如果任务进度100%，则跳过
                        if progress_pct >= 100:
                            return False
                        # 如果任务进度不是100%，则需要运行
                        return True
                    else:
                        # 没有任务进度数据，退回按自选周期执行的逻辑
                        execution_mode = "manual"
            except Exception as e:
                # 获取任务进度失败，退回按自选周期执行的逻辑
                execution_mode = "manual"
        
        # 按自选周期执行（自选）- 原有逻辑
        if execution_mode == "manual":
            return (
                not task.get("enddate")
                or (
                    datetime.now().date()
                    <= datetime.strptime(task["enddate"], "%Y-%m-%d").date()
                )
            ) and (
                "runweek" not in task
                # 星期一为0，星期日为6
                or (datetime.today().weekday() + 1 in task.get("runweek"))
            )
        
        # 默认返回True（兼容未知模式）
        return True

    # 执行任务
    for index, task in enumerate(tasklist):
        # 检查环境变量获取真实的任务索引（用于显示）
        if len(tasklist) == 1 and os.environ.get("ORIGINAL_TASK_INDEX"):
            try:
                display_index = int(os.environ.get("ORIGINAL_TASK_INDEX"))
            except (ValueError, TypeError):
                display_index = index + 1
        else:
            display_index = index + 1
            
        print()
        print(f"#{str(display_index).zfill(2)}------------------")
        print(f"任务名称: {task['taskname']}")
        print(f"分享链接: {task['shareurl']}")
        print(f"保存路径: {task['savepath']}")
        # 根据命名模式显示不同信息
        if task.get("use_sequence_naming") and task.get("sequence_naming"):
            print(f"顺序命名: {task['sequence_naming']}")
        elif task.get("use_episode_naming") and task.get("episode_naming"):
            print(f"剧集命名: {task['episode_naming']}")
        else:
            # 正则命名模式
            if task.get("pattern") is not None:  # 修改为判断是否为None，而非是否为真值
                print(f"正则匹配: {task['pattern']}")
            if task.get("replace") is not None:  # 显示替换规则，即使为空字符串
                print(f"正则替换: {task['replace']}")
        if task.get("update_subdir"):
            print(f"更新目录: {task['update_subdir']}")
        # 获取任务的执行周期模式，优先使用任务自身的execution_mode，否则使用系统配置的execution_mode
        execution_mode = task.get("execution_mode") or CONFIG_DATA.get("execution_mode", "manual")
        
        # 根据执行周期模式显示日志
        if execution_mode == "auto":
            # 按任务进度执行（自动）
            try:
                # 检查是否有任务进度数据
                cal_db = CalendarDB()
                task_name = task.get("taskname") or task.get("task_name") or ""
                if task_name:
                    metrics = cal_db.get_task_metrics(task_name)
                    if metrics and metrics.get("progress_pct") is not None:
                        # 有任务进度数据，显示自动模式
                        print(f"执行周期: 自动")
                    else:
                        # 没有任务进度数据，退回按自选周期执行，显示自选周期信息
                        if task.get("runweek") or task.get("enddate"):
                            print(
                                f"执行周期: WK{task.get('runweek',[])} ~ {task.get('enddate','forever')} (自动模式，无进度数据，退回自选周期)"
                            )
                        else:
                            print(f"执行周期: 自动 (无进度数据，退回自选周期)")
                else:
                    # 没有任务名称，退回按自选周期执行
                    if task.get("runweek") or task.get("enddate"):
                        print(
                            f"执行周期: WK{task.get('runweek',[])} ~ {task.get('enddate','forever')} (自动模式，无任务名称，退回自选周期)"
                        )
                    else:
                        print(f"执行周期: 自动 (无任务名称，退回自选周期)")
            except Exception:
                # 获取任务进度失败，退回按自选周期执行
                if task.get("runweek") or task.get("enddate"):
                    print(
                        f"执行周期: WK{task.get('runweek',[])} ~ {task.get('enddate','forever')} (自动模式，获取进度失败，退回自选周期)"
                    )
                else:
                    print(f"执行周期: 自动 (获取进度失败，退回自选周期)")
        else:
            # 按自选周期执行（自选）- 原有逻辑
            if task.get("runweek") or task.get("enddate"):
                print(
                    f"执行周期: WK{task.get('runweek',[])} ~ {task.get('enddate','forever')}"
                )
        
        print()
        # 判断任务周期
        if not is_time(task):
            if execution_mode == "auto":
                # 按任务进度执行时的跳过提示
                try:
                    cal_db = CalendarDB()
                    task_name = task.get("taskname") or task.get("task_name") or ""
                    if task_name:
                        metrics = cal_db.get_task_metrics(task_name)
                        if metrics and metrics.get("progress_pct") is not None:
                            progress_pct = int(metrics.get("progress_pct", 0))
                            print(f"任务进度已达 {progress_pct}%，跳过")
                        else:
                            print(f"任务不在执行周期内，跳过")
                    else:
                        print(f"任务不在执行周期内，跳过")
                except Exception:
                    print(f"任务不在执行周期内，跳过")
            else:
                # 按自选周期执行时的跳过提示（原有逻辑）
                print(f"任务不在执行周期内，跳过")
        else:
            # 保存之前的通知信息
            global NOTIFYS
            notifys_before = NOTIFYS.copy()
            
            # 将全局的自动解压设置添加到任务中
            task_settings = CONFIG_DATA.get("task_settings", {})
            if "auto_extract_archive" not in task:
                task["auto_extract_archive"] = task_settings.get("auto_extract_archive", "disabled")
            
            # 执行保存任务
            is_new_tree = account.do_save_task(task)
            
            # 检查是否需要清除重复的通知
            # 由于在修改顺序，现在不需要特殊处理通知
            is_special_sequence = (task.get("use_sequence_naming") and task.get("sequence_naming")) or (task.get("use_episode_naming") and task.get("episode_naming"))
            # 对于正则命名模式，也将其视为特殊序列
            is_regex_mode = not is_special_sequence and task.get("pattern") is not None
            
            # 执行重命名任务，但收集日志而不是立即打印
            is_rename, rename_logs = account.do_rename_task(task)

            
            # 处理子目录重命名 - 如果配置了更新目录且使用正则命名模式
            if task.get("update_subdir") and task.get("pattern") is not None:
                # 获取保存路径
                savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}")
                if account.savepath_fid.get(savepath):
                    # 递归处理所有符合条件的子目录
                    def process_subdirs(current_path, relative_path=""):
                        # 获取当前目录的文件列表
                        current_fid = account.savepath_fid.get(current_path)
                        if not current_fid:
                            # 尝试获取目录的fid
                            path_fids = account.get_fids([current_path])
                            if path_fids:
                                current_fid = path_fids[0]["fid"]
                                account.savepath_fid[current_path] = current_fid
                            else:
                                print(f"无法获取目录fid: {current_path}")
                                return
                        
                        current_dir_files = account.ls_dir(current_fid)
                        
                        # 处理当前目录
                        if relative_path:
                            # 对当前目录执行重命名操作
                            subtask = task.copy()
                            if not subtask.get("pattern"):
                                subtask["pattern"] = ".*"
                                subtask["replace"] = ""
                            
                            subdir_is_rename, subdir_rename_logs = account.do_rename_task(subtask, relative_path)
                            
                            # 合并日志
                            if subdir_is_rename and subdir_rename_logs:
                                clean_logs = []
                                for log in subdir_rename_logs:
                                    if "失败" not in log:
                                        clean_logs.append(log)
                                
                                rename_logs.extend(clean_logs)
                                nonlocal is_rename
                                is_rename = is_rename or subdir_is_rename
                        
                        # 找出符合更新目录规则的子目录
                        subdirs = []
                        for file in current_dir_files:
                            if file["dir"]:
                                # 如果是根目录，检查是否符合更新目录的规则
                                if not relative_path and re.search(task["update_subdir"], file["file_name"]):
                                    subdirs.append(file)
                                # 如果已经在某个更新目录内，则所有子目录都需要处理
                                elif relative_path:
                                    subdirs.append(file)
                        
                        # 对每个子目录递归调用
                        for subdir in subdirs:
                            # 构建子目录完整路径
                            subdir_full_path = f"{current_path}/{subdir['file_name']}"
                            subdir_relative_path = f"{relative_path}/{subdir['file_name']}"
                            
                            # 递归处理子目录
                            process_subdirs(subdir_full_path, subdir_relative_path)
                    
                    # 从根目录开始递归处理
                    process_subdirs(savepath)
            
            # 处理云解压后“保持原目录结构”的子目录重命名
            # 与顶层目录使用相同的重命名规则
            auto_extract_subdirs = task.get("_auto_extract_subdirs") or []
            for subdir_path in auto_extract_subdirs:
                try:
                    subtask = task.copy()
                    subdir_is_rename, subdir_rename_logs = account.do_rename_task(subtask, subdir_path)
                    if subdir_is_rename and subdir_rename_logs:
                        # 过滤掉失败日志，只保留成功的
                        clean_logs = []
                        for log in subdir_rename_logs:
                            if "失败" not in log:
                                clean_logs.append(log)
                        rename_logs.extend(clean_logs)
                        is_rename = is_rename or subdir_is_rename
                except Exception as e:
                    # 防御性处理，避免子目录异常影响主流程
                    print(f"⚠️ 自动解压子目录重命名出错（{subdir_path}）: {e}")
            
            # 简化日志处理 - 只保留成功的重命名消息
            if rename_logs:
                success_logs = []
                for log in rename_logs:
                    if "失败" not in log:
                        success_logs.append(log)
                # 完全替换日志，只显示成功部分
                rename_logs = success_logs
                
            # 对于顺序命名模式，需要重新创建文件树以显示实际的文件名
            # 这确保显示的是实际存在的文件名，而不是预期的文件名
            # 检查是否是保持原目录结构模式
            auto_extract_mode_check = task.get("auto_extract_archive", "disabled")
            keep_structure_check = "keep_structure" in auto_extract_mode_check if auto_extract_mode_check != "disabled" else False
            auto_extract_subdirs_check = task.get("_auto_extract_subdirs") or []
            
            # 如果是保持原目录结构模式且有自动解压的子目录，跳过重新创建文件树
            # 因为文件已经在子目录中，文件树会通过 _auto_extract_subdirs 的逻辑来显示
            if keep_structure_check and auto_extract_subdirs_check:
                # 跳过重新创建文件树，使用原来的文件树
                pass
            elif task.get("shareurl") and rename_logs and is_rename and (task.get("use_sequence_naming") or task.get("use_episode_naming")):
                # 获取当前目录
                savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}")
                if account.savepath_fid.get(savepath):
                    # 创建新的Tree对象
                    new_tree = Tree()
                    # 创建根节点
                    new_tree.create_node(
                        savepath,
                        "root",
                        data={
                            "is_dir": True,
                        },
                    )
                    
                    # 获取实际的文件名映射
                    actual_file_names = account.get_actual_file_names_from_directory(task, rename_logs)

                    # 从重命名日志中提取预期的文件名映射
                    expected_renamed_files = {}
                    for log in rename_logs:
                        # 格式：重命名: 旧名 → 新名
                        match = re.search(r'重命名: (.*?) → (.+?)($|\s|，|失败)', log)
                        if match:
                            old_name = match.group(1).strip()
                            expected_new_name = match.group(2).strip()
                            expected_renamed_files[old_name] = expected_new_name

                    # 获取文件列表，只添加重命名的文件
                    fresh_dir_file_list = account.ls_dir(account.savepath_fid[savepath])

                    # 检查是否是保持原目录结构模式
                    auto_extract_mode = task.get("auto_extract_archive", "disabled")
                    keep_structure = "keep_structure" in auto_extract_mode if auto_extract_mode != "disabled" else False
                    # 获取自动解压的子目录列表，用于过滤根目录中的文件
                    auto_extract_subdirs = task.get("_auto_extract_subdirs") or []
                    auto_extract_subdir_names = set()
                    for subdir_path in auto_extract_subdirs:
                        subdir_name = subdir_path.lstrip("/")
                        auto_extract_subdir_names.add(subdir_name)
                    
                    # 如果是保持原目录结构模式，获取子目录下的所有文件fid和文件名，用于过滤
                    subdir_file_fids = set()
                    subdir_file_names = set()  # 同时记录文件名，用于双重检查
                    # 递归获取所有子目录中的文件（包括嵌套子目录）
                    def get_all_subdir_files(subdir_fid, subdir_name):
                        """递归获取子目录及其所有子目录中的文件"""
                        all_files = []
                        try:
                            subdir_files = account.ls_dir(subdir_fid)
                            for subdir_file in subdir_files:
                                if subdir_file.get("dir", False):
                                    # 递归处理子目录
                                    sub_subdir_path = f"{subdir_name}/{subdir_file['file_name']}"
                                    sub_subdir_full_path = f"{savepath}/{sub_subdir_path}"
                                    if sub_subdir_full_path in account.savepath_fid:
                                        sub_subdir_fid = account.savepath_fid[sub_subdir_full_path]
                                        all_files.extend(get_all_subdir_files(sub_subdir_fid, sub_subdir_path))
                                else:
                                    all_files.append(subdir_file)
                        except Exception:
                            pass
                        return all_files
                    
                    if keep_structure and auto_extract_subdir_names:
                        for subdir_name in auto_extract_subdir_names:
                            subdir_full_path = f"{savepath}/{subdir_name}"
                            if subdir_full_path in account.savepath_fid:
                                subdir_fid = account.savepath_fid[subdir_full_path]
                                try:
                                    all_subdir_files = get_all_subdir_files(subdir_fid, subdir_name)
                                    for subdir_file in all_subdir_files:
                                        subdir_file_fids.add(subdir_file["fid"])
                                        subdir_file_names.add(subdir_file["file_name"])
                                except Exception:
                                    pass
                    
                    # 扁平化模式下，需要识别所有转存的文件（包括未重命名的）
                    flat_mode = not keep_structure and auto_extract_mode != "disabled"
                    current_time = int(time.time())
                    time_threshold = 600  # 10分钟 = 600秒

                    # 添加实际存在的文件到树中
                    for file in fresh_dir_file_list:
                        if not file["dir"]:  # 只处理文件
                            # 如果是保持原目录结构模式，检查文件是否在子目录中
                            # 使用双重检查：fid匹配和文件名匹配
                            if keep_structure:
                                file_in_subdir = False
                                if file.get("fid") in subdir_file_fids:
                                    file_in_subdir = True
                                elif file["file_name"] in subdir_file_names:
                                    # 即使fid不匹配，如果文件名在子目录中，也可能是同一个文件（重命名后）
                                    file_in_subdir = True
                                
                                if file_in_subdir:
                                    # 文件在子目录中，不应该添加到根目录
                                    continue
                            
                            # 检查这个文件是否是当次转存的文件
                            is_transferred_file = False
                            check_method = None

                            # 方法1：检查文件名是否在实际重命名结果中
                            if file["file_name"] in actual_file_names.values():
                                is_transferred_file = True
                                check_method = "method1_actual_names"

                            # 方法2：检查文件名是否在预期重命名结果中（用于重命名失败的情况）
                            elif file["file_name"] in expected_renamed_files.values():
                                is_transferred_file = True
                                check_method = "method2_expected_values"

                            # 方法3：检查文件名是否是原始文件名（重命名失败保持原名）
                            elif file["file_name"] in expected_renamed_files.keys():
                                is_transferred_file = True
                                check_method = "method3_expected_keys"
                            
                            # 方法4：扁平化模式下，检查文件是否是最近创建的（可能是转存的文件）
                            elif flat_mode:
                                file_updated_at = file.get("updated_at", 0)
                                # 处理毫秒级时间戳
                                if file_updated_at > 4102444800:  # 2100年的时间戳
                                    file_updated_at = file_updated_at / 1000
                                if file_updated_at > 0:
                                    time_diff = current_time - int(file_updated_at)
                                    if time_diff < time_threshold:
                                        is_transferred_file = True
                                        check_method = "method4_recent_file"

                            if is_transferred_file:
                                new_tree.create_node(
                                    file["file_name"],  # 使用实际存在的文件名
                                    file["fid"],
                                    parent="root",
                                    data={
                                        "is_dir": False,
                                        "path": f"{savepath}/{file['file_name']}",
                                    },
                                )
                    
                    # 如果树的大小大于1（有文件），则设置为新的Tree对象
                    if new_tree.size() > 1:
                        is_new_tree = new_tree
            
            # 添加生成文件树的功能（无论是否是顺序命名模式）
            # 如果is_new_tree返回了Tree对象，则打印文件树
            if is_new_tree and isinstance(is_new_tree, Tree) and is_new_tree.size() > 1:
                # 获取所有文件（非目录）节点
                file_nodes = [node for node in is_new_tree.all_nodes_itr() if node.data.get("is_dir") == False]
                
                # 如果是保持原目录结构模式，在开始处理文件节点之前，先过滤掉子目录中的文件
                auto_extract_mode_prefilter = task.get("auto_extract_archive", "disabled")
                keep_structure_prefilter = "keep_structure" in auto_extract_mode_prefilter if auto_extract_mode_prefilter != "disabled" else False
                auto_extract_subdirs_prefilter = task.get("_auto_extract_subdirs") or []
                
                if keep_structure_prefilter and auto_extract_subdirs_prefilter:
                    # 获取所有子目录中的文件名（递归）
                    savepath_prefilter = re.sub(r"/{2,}", "/", f"/{task['savepath']}")
                    subdir_file_names_prefilter = set()
                    
                    def get_all_subdir_files_prefilter(subdir_fid, subdir_name):
                        """递归获取子目录及其所有子目录中的文件"""
                        all_files = []
                        try:
                            subdir_files = account.ls_dir(subdir_fid)
                            for subdir_file in subdir_files:
                                if subdir_file.get("dir", False):
                                    sub_subdir_path = f"{subdir_name}/{subdir_file['file_name']}"
                                    sub_subdir_full_path = f"{savepath_prefilter}/{sub_subdir_path}"
                                    if sub_subdir_full_path in account.savepath_fid:
                                        sub_subdir_fid = account.savepath_fid[sub_subdir_full_path]
                                        all_files.extend(get_all_subdir_files_prefilter(sub_subdir_fid, sub_subdir_path))
                                else:
                                    all_files.append(subdir_file)
                        except Exception:
                            pass
                        return all_files
                    
                    for subdir_path in auto_extract_subdirs_prefilter:
                        subdir_name = subdir_path.lstrip("/")
                        subdir_full_path = f"{savepath_prefilter}/{subdir_name}"
                        if subdir_full_path in account.savepath_fid:
                            subdir_fid = account.savepath_fid[subdir_full_path]
                            try:
                                all_subdir_files = get_all_subdir_files_prefilter(subdir_fid, subdir_name)
                                for subdir_file in all_subdir_files:
                                    subdir_file_names_prefilter.add(subdir_file["file_name"])
                            except Exception:
                                pass
                    
                    # 过滤掉子目录中的文件
                    filtered_file_nodes = []
                    
                    for node in file_nodes:
                        file_name = remove_file_icons(node.tag)
                        if file_name not in subdir_file_names_prefilter:
                            filtered_file_nodes.append(node)
                    
                    file_nodes = filtered_file_nodes
                
                # 创建一个映射列表，包含需要显示的文件名
                display_files = []
                
                # 检查是否有更新目录，并获取目录下的文件
                update_subdir = task.get("update_subdir")
                
                # 如果有设置更新目录，查找对应目录下的文件
                if update_subdir:
                    # 检查文件树中是否有更新目录的节点
                    update_dir_nodes = []
                    update_subdir_files = []
                    
                    # 遍历所有节点，查找对应的目录节点
                    for node in is_new_tree.all_nodes_itr():
                        if node.data.get("is_dir", False) and node.tag.lstrip("📁") == update_subdir:
                            update_dir_nodes.append(node)
                    
                    # 如果找到更新目录节点，收集其子节点
                    if update_dir_nodes:
                        for dir_node in update_dir_nodes:
                            # 获取目录节点下的所有文件子节点
                            for node in is_new_tree.all_nodes_itr():
                                if (not node.data.get("is_dir", False) and 
                                    node.predecessor == dir_node.identifier):
                                    update_subdir_files.append(node)
                
                # 按文件名排序
                if is_special_sequence:
                    # 对于顺序命名模式，直接使用文件树中的实际文件名
                    if rename_logs:
                        # 直接显示文件树中的实际文件名（已经是重命名后的结果）
                        # 按照文件名中的序号进行排序
                        def extract_sequence_number(node):
                            filename = remove_file_icons(node.tag)
                            # 尝试从文件名中提取序号，如 "乘风2025 - S06E01.flac" -> 1
                            import re
                            match = re.search(r'E(\d+)', filename)
                            if match:
                                return int(match.group(1))
                            # 如果没有找到E序号，尝试其他模式
                            match = re.search(r'(\d+)', filename)
                            if match:
                                return int(match.group(1))
                            return 0

                        # 按序号排序
                        sorted_nodes = sorted(file_nodes, key=extract_sequence_number)

                        for node in sorted_nodes:
                            # 获取实际文件名（去除已有图标）
                            actual_filename = remove_file_icons(node.tag)
                            # 获取适当的图标
                            icon = get_file_icon(actual_filename, is_dir=node.data.get("is_dir", False))
                            # 添加到显示列表
                            display_files.append((f"{icon} {actual_filename}", node))
                    else:
                        # 如果没有重命名日志，使用原来的顺序命名逻辑
                        if task.get("use_sequence_naming") and task.get("sequence_naming"):
                            # 顺序命名模式预览
                            sequence_pattern = task["sequence_naming"]
                            
                            # 对于每个文件，生成其重命名后的名称
                            for i, node in enumerate(file_nodes):
                                # 提取序号（从1开始）
                                file_num = i + 1
                                # 获取原始文件的扩展名
                                orig_filename = remove_file_icons(node.tag)
                                file_ext = os.path.splitext(orig_filename)[1]
                                # 生成新的文件名（使用顺序命名模式）
                                if sequence_pattern == "{}":
                                    # 对于单独的{}，直接使用数字序号作为文件名
                                    new_filename = f"{file_num:02d}{file_ext}"
                                else:
                                    new_filename = sequence_pattern.replace("{}", f"{file_num:02d}") + file_ext
                                # 获取适当的图标
                                icon = get_file_icon(orig_filename, is_dir=node.data.get("is_dir", False))
                                # 添加到显示列表
                                display_files.append((f"{icon} {new_filename}", node))
                            
                            # 按数字排序
                            display_files.sort(key=lambda x: int(os.path.splitext(remove_file_icons(x[0]))[0]) if os.path.splitext(remove_file_icons(x[0]))[0].isdigit() else float('inf'))
                        # 对于剧集命名模式
                        elif task.get("use_episode_naming") and task.get("episode_naming"):
                            # 从重命名日志提取新旧文件名 (备用)
                            renamed_files = {}
                            for log in rename_logs:
                                # 格式：重命名: 旧名 → 新名
                                match = re.search(r'重命名: (.*?) → (.+?)($|\s|，|失败)', log)
                                if match:
                                    old_name = match.group(1).strip()
                                    new_name = match.group(2).strip()
                                    renamed_files[old_name] = new_name
                            
                            # 使用已知的剧集命名模式来生成新文件名
                            episode_pattern = task["episode_naming"]
                            
                            # 创建剧集号提取函数
                            def extract_episode_number_local(filename):
                                # 使用全局的统一提取函数
                                return extract_episode_number(filename, episode_patterns=account.episode_patterns)
                            
                            # 只显示重命名的文件
                            for node in file_nodes:
                                # 获取原始文件名（去除已有图标）
                                orig_filename = remove_file_icons(node.tag)
                                # 检查此文件是否在重命名日志中
                                if orig_filename in renamed_files:
                                    # 使用重命名后的文件名
                                    new_filename = renamed_files[orig_filename]
                                    # 获取适当的图标
                                    icon = get_file_icon(new_filename, is_dir=node.data.get("is_dir", False))
                                    # 添加到显示列表
                                    display_files.append((f"{icon} {new_filename}", node))
                    
                    # 如果没有找到任何文件要显示，使用原始文件名
                    if not display_files:
                        for node in sorted(file_nodes, key=lambda node: node.tag):
                            # 获取原始文件名（去除已有图标）
                            orig_filename = remove_file_icons(node.tag)
                            # 优先使用存储在data中的图标，否则重新计算
                            icon = node.data.get("icon") if node.data else get_file_icon(orig_filename, is_dir=node.data.get("is_dir", False) if node.data else False)
                            display_files.append((f"{icon} {orig_filename}", node))
                else:
                    # 其他模式：显示原始文件名
                    display_files = []
                    # 获取所有节点（包括目录节点）
                    all_nodes = [node for node in is_new_tree.all_nodes_itr()]
                    
                    # 获取保存路径的最后一部分目录名（如"/测试/魔法"取"魔法"）
                    save_path_basename = os.path.basename(task.get("savepath", "").rstrip("/"))
                    
                    # 创建一个保存目录结构的字典
                    dir_structure = {"root": []}
                    
                    # 首先处理所有目录节点，构建目录结构
                    dir_nodes = [node for node in all_nodes if node.data and node.data.get("is_dir", False) and node.identifier != "root"]
                    
                    # 如果有设置更新目录但在树中没找到，尝试查找目录
                    update_dir_node = None
                    if update_subdir:
                        for node in dir_nodes:
                            name = node.tag.lstrip("📁")
                            if name == update_subdir:
                                update_dir_node = node
                                break
                        
                        # 如果树中没有找到更新目录节点，但设置了更新目录，尝试单独处理
                        if not update_dir_node:
                            savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}")
                            update_subdir_path = f"{savepath}/{update_subdir}"
                            
                            # 检查更新目录是否存在于账户的目录缓存中
                            if update_subdir_path in account.savepath_fid:
                                # 获取此目录下的文件，单独处理显示
                                try:
                                    update_subdir_fid = account.savepath_fid[update_subdir_path]
                                    update_subdir_files = account.ls_dir(update_subdir_fid)
                                    
                                    # 如果目录存在但没有在文件树中，这可能是首次执行的情况
                                    # 添加一个虚拟目录节点到树中
                                    class VirtualDirNode:
                                        def __init__(self, name, dir_id):
                                            self.tag = f"📁{name}"
                                            self.identifier = dir_id
                                            self.data = {"is_dir": True}
                                    
                                    # 创建虚拟目录节点
                                    update_dir_node = VirtualDirNode(update_subdir, f"virtual_{update_subdir}")
                                    dir_nodes.append(update_dir_node)
                                    
                                    # 为每个文件创建虚拟节点
                                    virtual_file_nodes = []
                                    for file in update_subdir_files:
                                        if not file.get("dir", False):
                                            class VirtualFileNode:
                                                def __init__(self, name, file_id, dir_id):
                                                    self.tag = name
                                                    self.identifier = file_id
                                                    self.predecessor = dir_id
                                                    self.data = {"is_dir": False}
                                            
                                            # 创建虚拟文件节点，关联到虚拟目录
                                            virtual_node = VirtualFileNode(
                                                file["file_name"], 
                                                f"virtual_file_{file['fid']}", 
                                                update_dir_node.identifier
                                            )
                                            virtual_file_nodes.append(virtual_node)
                                    
                                    # 将虚拟文件节点添加到all_nodes
                                    if virtual_file_nodes:
                                        all_nodes.extend(virtual_file_nodes)
                                except Exception as e:
                                    print(f"获取更新目录信息时出错: {str(e)}")
                    
                    # 恢复目录结构构建
                    for node in sorted(dir_nodes, key=lambda node: node.tag):
                        # 获取原始文件名（去除已有图标）
                        orig_filename = node.tag.lstrip("📁")
                        
                        # 确保只显示目录名，而不是完整路径
                        if "/" in orig_filename:
                            # 只取路径的最后一部分作为显示名
                            orig_filename = orig_filename.split("/")[-1]
                        
                        # 跳过与保存路径目录名相同的目录
                        if orig_filename == save_path_basename:
                            continue
                        
                        # 获取父节点ID
                        parent_id = node.predecessor if hasattr(node, 'predecessor') and node.predecessor != "root" else "root"
                        
                        # 确保父节点键存在于字典中
                        if parent_id not in dir_structure:
                            dir_structure[parent_id] = []
                        
                        # 添加目录节点到结构中
                        dir_structure[parent_id].append({
                            "id": node.identifier,
                            "name": f"📁{orig_filename}",
                            "is_dir": True
                        })
                        
                        # 为该目录创建一个空列表，用于存放其子项
                        dir_structure[node.identifier] = []
                    
                    # 然后处理所有文件节点和虚拟文件节点
                    all_file_nodes = [node for node in all_nodes if hasattr(node, 'data') and not node.data.get("is_dir", False)]
                    
                    for node in sorted(all_file_nodes, key=lambda node: node.tag):
                        # 获取原始文件名（去除已有图标）
                        orig_filename = remove_file_icons(node.tag)
                        # 添加适当的图标
                        icon = get_file_icon(orig_filename, is_dir=False)
                        
                        # 获取父节点ID
                        parent_id = node.predecessor if hasattr(node, 'predecessor') and node.predecessor != "root" else "root"
                        
                        # 确保父节点键存在于字典中
                        if parent_id not in dir_structure:
                            dir_structure[parent_id] = []
                        
                        # 添加文件节点到结构中
                        dir_structure[parent_id].append({
                            "id": node.identifier,
                            "name": f"{icon} {orig_filename}",
                            "is_dir": False
                        })
                
                # 添加成功通知，带文件数量图标
                # 这个通知会在下面的新逻辑中添加，这里注释掉
                # add_notify(f"✅《{task['taskname']}》新增文件:")
                # add_notify(f"/{task['savepath']}")
                
                # 移除调试信息
                # 获取更新目录名称
                update_subdir = task.get("update_subdir")
                
                # 创建一个列表来存储所有匹配的更新目录节点及其文件
                update_dir_nodes = []
                files_by_dir = {}
                # 默认根目录
                files_by_dir["root"] = []
                
                # 查找所有匹配的更新目录节点
                dir_nodes = [node for node in is_new_tree.all_nodes_itr() if node.data.get("is_dir") == True and node.identifier != "root"]
                if update_subdir:
                    for node in dir_nodes:
                        dir_name = node.tag.lstrip("📁")
                        # 使用正则表达式匹配目录名
                        if re.search(update_subdir, dir_name):
                            update_dir_nodes.append(node)
                            # 为每个匹配的目录创建空文件列表
                            files_by_dir[node.identifier] = []
                
                # 如果是保持原目录结构模式，获取子目录文件列表用于过滤（递归获取所有子目录）
                auto_extract_mode_display = task.get("auto_extract_archive", "disabled")
                keep_structure_display = "keep_structure" in auto_extract_mode_display if auto_extract_mode_display != "disabled" else False
                auto_extract_subdirs_display = task.get("_auto_extract_subdirs") or []
                subdir_file_names_display = set()
                # 递归获取所有子目录中的文件（包括嵌套子目录）
                def get_all_subdir_files_display(subdir_fid, subdir_name):
                    """递归获取子目录及其所有子目录中的文件"""
                    all_files = []
                    try:
                        subdir_files = account.ls_dir(subdir_fid)
                        for subdir_file in subdir_files:
                            if subdir_file.get("dir", False):
                                # 递归处理子目录
                                sub_subdir_path = f"{subdir_name}/{subdir_file['file_name']}"
                                sub_subdir_full_path = f"{savepath_display}/{sub_subdir_path}"
                                if sub_subdir_full_path in account.savepath_fid:
                                    sub_subdir_fid = account.savepath_fid[sub_subdir_full_path]
                                    all_files.extend(get_all_subdir_files_display(sub_subdir_fid, sub_subdir_path))
                            else:
                                all_files.append(subdir_file)
                    except Exception:
                        pass
                    return all_files
                
                if keep_structure_display and auto_extract_subdirs_display:
                    savepath_display = re.sub(r"/{2,}", "/", f"/{task['savepath']}")
                    for subdir_path in auto_extract_subdirs_display:
                        subdir_name = subdir_path.lstrip("/")
                        subdir_full_path = f"{savepath_display}/{subdir_name}"
                        if subdir_full_path in account.savepath_fid:
                            subdir_fid = account.savepath_fid[subdir_full_path]
                            try:
                                all_subdir_files = get_all_subdir_files_display(subdir_fid, subdir_name)
                                for subdir_file in all_subdir_files:
                                    subdir_file_names_display.add(subdir_file["file_name"])
                            except Exception:
                                pass
                
                # 从文件的路径信息中提取父目录
                for node in file_nodes:
                    # 如果是保持原目录结构模式，检查文件是否在子目录中
                    if keep_structure_display:
                        file_name = remove_file_icons(node.tag)
                        if file_name in subdir_file_names_display:
                            # 文件在子目录中，不应该添加到根目录，跳过
                            continue
                    
                    if hasattr(node, 'data') and node.data and 'path' in node.data:
                        path = node.data['path']
                        path_parts = path.strip('/').split('/')
                        
                        # 确定文件应该属于哪个目录
                        if len(path_parts) > 1 and update_subdir:
                            # 获取保存路径
                            save_path = task.get("savepath", "").rstrip("/")
                            save_path_parts = save_path.split("/")
                            save_path_basename = save_path_parts[-1] if save_path_parts else ""
                            
                            # 去除路径中的保存路径部分，只保留相对路径
                            # 首先查找保存路径在完整路径中的位置
                            relative_path_start = 0
                            for i, part in enumerate(path_parts):
                                if i < len(path_parts) - 1 and part == save_path_basename:
                                    relative_path_start = i + 1
                                    break
                            
                            # 提取相对路径部分，不含文件名
                            relative_path_parts = path_parts[relative_path_start:-1]
                            
                            # 如果没有相对路径部分（直接在根目录下的文件）
                            if not relative_path_parts:
                                # 如果是保持原目录结构模式，检查文件是否在子目录中
                                if keep_structure_display:
                                    file_name_check = remove_file_icons(node.tag)
                                    if file_name_check in subdir_file_names_display:
                                        # 文件在子目录中，不应该添加到根目录，跳过
                                        continue
                                files_by_dir["root"].append(node)
                                continue
                                
                            # 检查第一级子目录是否是更新目录之一
                            first_level_dir = relative_path_parts[0]
                            parent_found = False
                            
                            for dir_node in update_dir_nodes:
                                dir_name = dir_node.tag.lstrip("📁")
                                # 只看第一级目录是否匹配更新目录规则
                                if dir_name == first_level_dir:
                                    # 文件属于此更新目录，直接添加到对应目录下
                                    files_by_dir[dir_node.identifier].append(node)
                                    parent_found = True
                                    
                                    # 为了在树显示中定位该文件，设置完整路径信息
                                    if len(relative_path_parts) > 1:
                                        # 如果是多级嵌套目录，设置嵌套路径标记
                                        node.nested_path = "/".join(relative_path_parts[1:])
                                    break
                            
                            if not parent_found:
                                # 尝试检查文件的直接父目录
                                parent_dir_name = path_parts[-2]
                                for dir_node in update_dir_nodes:
                                    dir_name = dir_node.tag.lstrip("📁")
                                    if parent_dir_name == dir_name:
                                        files_by_dir[dir_node.identifier].append(node)
                                        parent_found = True
                                        break
                                
                                if not parent_found:
                                    # 如果没有找到匹配的父目录，将文件添加到根目录
                                    # 如果是保持原目录结构模式，检查文件是否在子目录中
                                    if keep_structure_display:
                                        file_name_check = remove_file_icons(node.tag)
                                        if file_name_check in subdir_file_names_display:
                                            # 文件在子目录中，不应该添加到根目录，跳过
                                            continue
                                    files_by_dir["root"].append(node)
                        else:
                            # 文件属于根目录
                            # 如果是保持原目录结构模式，检查文件是否在子目录中
                            if keep_structure_display:
                                file_name_check = remove_file_icons(node.tag)
                                if file_name_check in subdir_file_names_display:
                                    # 文件在子目录中，不应该添加到根目录，跳过
                                    continue
                            files_by_dir["root"].append(node)
                    else:
                        # 没有路径信息，默认添加到根目录
                        # 如果是保持原目录结构模式，检查文件是否在子目录中
                        if keep_structure_display:
                            file_name_check = remove_file_icons(node.tag)
                            if file_name_check in subdir_file_names_display:
                                # 文件在子目录中，不应该添加到根目录，跳过
                                continue
                        files_by_dir["root"].append(node)
                
                # 处理自动解压的文件夹（保持原目录结构模式）
                # 检查是否有自动解压的文件夹需要添加到文件树中
                auto_extract_subdirs = task.get("_auto_extract_subdirs") or []
                if auto_extract_subdirs:
                    savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}")
                    if savepath in account.savepath_fid:
                        savepath_fid = account.savepath_fid[savepath]
                        
                        # 递归获取文件夹内容的辅助函数
                        def get_extracted_folder_contents(folder_fid, folder_name, relative_path=""):
                            """递归获取解压文件夹内的所有文件和子目录"""
                            try:
                                folder_contents = account.ls_dir(folder_fid)
                                files = []
                                dirs = []
                                
                                for item in folder_contents:
                                    item_name = item["file_name"]
                                    item_fid = item["fid"]
                                    item_is_dir = item.get("dir", False)
                                    
                                    # 构建相对路径
                                    if relative_path:
                                        item_relative_path = f"{relative_path}/{item_name}"
                                    else:
                                        item_relative_path = item_name
                                    
                                    if item_is_dir:
                                        # 递归获取子目录内容
                                        sub_contents = get_extracted_folder_contents(item_fid, item_name, item_relative_path)
                                        dirs.append({
                                            "name": item_name,
                                            "fid": item_fid,
                                            "relative_path": item_relative_path,
                                            "contents": sub_contents
                                        })
                                    else:
                                        # 添加文件
                                        files.append({
                                            "name": item_name,
                                            "fid": item_fid,
                                            "relative_path": item_relative_path,
                                            "size": item.get("size", 0),
                                            "obj_category": item.get("obj_category", "default")
                                        })
                                
                                return {"files": files, "dirs": dirs}
                            except Exception as e:
                                print(f"⚠️ 获取解压文件夹内容失败（{folder_name}）: {e}")
                                return {"files": [], "dirs": []}
                        
                        # 为每个解压文件夹创建虚拟节点并添加到文件树
                        for subdir_path in auto_extract_subdirs:
                            try:
                                # subdir_path 格式为 "/文件夹名"
                                subdir_name = subdir_path.lstrip("/")
                                subdir_full_path = f"{savepath}/{subdir_name}"
                                
                                # 获取文件夹的 fid
                                if subdir_full_path in account.savepath_fid:
                                    subdir_fid = account.savepath_fid[subdir_full_path]
                                    
                                    # 创建虚拟目录节点
                                    class VirtualExtractedDirNode:
                                        def __init__(self, name, dir_id, relative_path=""):
                                            self.tag = f"📁{name}"
                                            self.identifier = f"extracted_dir_{dir_id}"
                                            self.data = {
                                                "is_dir": True,
                                                "fid": dir_id,
                                                "relative_path": relative_path
                                            }
                                    
                                    # 创建解压文件夹的虚拟节点
                                    extracted_dir_node = VirtualExtractedDirNode(subdir_name, subdir_fid, subdir_name)
                                    
                                    # 如果设置了 update_subdir，检查是否匹配
                                    if update_subdir:
                                        if re.search(update_subdir, subdir_name):
                                            # 匹配更新目录规则，添加到更新目录列表
                                            if extracted_dir_node.identifier not in [n.identifier for n in update_dir_nodes]:
                                                update_dir_nodes.append(extracted_dir_node)
                                                files_by_dir[extracted_dir_node.identifier] = []
                                    else:
                                        # 没有设置 update_subdir，添加到更新目录列表
                                        if extracted_dir_node.identifier not in [n.identifier for n in update_dir_nodes]:
                                            update_dir_nodes.append(extracted_dir_node)
                                            files_by_dir[extracted_dir_node.identifier] = []
                                    
                                    # 递归获取文件夹内容
                                    folder_contents = get_extracted_folder_contents(subdir_fid, subdir_name, subdir_name)
                                    
                                    # 创建虚拟文件节点的辅助函数
                                    def create_virtual_file_node(file_info, parent_dir_id, relative_path=""):
                                        """创建虚拟文件节点"""
                                        class VirtualExtractedFileNode:
                                            def __init__(self, name, file_id, parent_dir_id_param, relative_path=""):
                                                file_icon = get_file_icon(name, is_dir=False)
                                                self.tag = f"{file_icon} {name}"
                                                self.identifier = f"extracted_file_{file_id}"
                                                self.predecessor = parent_dir_id_param
                                                # 构建完整路径
                                                if relative_path:
                                                    full_path = f"{savepath}/{relative_path}"
                                                else:
                                                    full_path = f"{savepath}/{name}"
                                                self.data = {
                                                    "is_dir": False,
                                                    "fid": file_id,
                                                    "path": full_path,
                                                    "relative_path": relative_path
                                                }
                                                # 如果相对路径包含多级目录，设置嵌套路径
                                                if relative_path and "/" in relative_path:
                                                    path_parts = relative_path.split("/")
                                                    if len(path_parts) > 1:
                                                        # 嵌套路径是除了最后一层（文件名）之外的所有路径
                                                        self.nested_path = "/".join(path_parts[:-1])
                                        
                                        return VirtualExtractedFileNode(
                                            file_info["name"],
                                            file_info["fid"],
                                            parent_dir_id,  # 使用外层函数的参数
                                            relative_path
                                        )
                                    
                                    # 递归添加文件和子目录到文件树
                                    def add_extracted_contents_to_tree(contents, parent_dir_id, current_relative_path=""):
                                        """递归添加解压内容到文件树"""
                                        # 添加文件
                                        for file_info in contents["files"]:
                                            # 构建文件的相对路径
                                            if current_relative_path:
                                                file_relative_path = f"{current_relative_path}/{file_info['name']}"
                                            else:
                                                file_relative_path = file_info["name"]
                                            
                                            file_node = create_virtual_file_node(
                                                file_info,
                                                parent_dir_id,
                                                file_relative_path
                                            )
                                            files_by_dir[parent_dir_id].append(file_node)
                                        
                                        # 递归处理子目录
                                        for dir_info in contents["dirs"]:
                                            # 创建子目录的虚拟节点
                                            subdir_node = VirtualExtractedDirNode(
                                                dir_info["name"],
                                                dir_info["fid"],
                                                dir_info["relative_path"]
                                            )
                                            
                                            # 必须先初始化 files_by_dir，否则递归内 append 会 KeyError
                                            if subdir_node.identifier not in files_by_dir:
                                                files_by_dir[subdir_node.identifier] = []
                                            
                                            # 如果设置了 update_subdir，检查子目录是否匹配
                                            if update_subdir:
                                                if re.search(update_subdir, dir_info["name"]):
                                                    # 匹配更新目录规则，添加到更新目录列表
                                                    if subdir_node.identifier not in [n.identifier for n in update_dir_nodes]:
                                                        update_dir_nodes.append(subdir_node)
                                            
                                            # 递归添加子目录内容
                                            add_extracted_contents_to_tree(
                                                dir_info["contents"],
                                                subdir_node.identifier,
                                                dir_info["relative_path"]
                                            )
                                    
                                    # 添加解压文件夹的内容到文件树
                                    add_extracted_contents_to_tree(folder_contents, extracted_dir_node.identifier)
                                    
                            except Exception as e:
                                print(f"⚠️ 处理解压文件夹失败（{subdir_path}）: {e}")
                
                # 排序函数，使用文件节点作为输入
                def sort_nodes(nodes):
                    return sorted(nodes, key=lambda node: sort_file_by_name(remove_file_icons(node.tag)))
                
                # 初始化最终显示文件的字典
                final_display_files = {
                    "root": [],
                    "subdirs": {}
                }
                
                # 创建集合来跟踪当次新增的文件和文件夹
                current_added_files = set()
                current_added_dirs = set()
                
                # 跟踪子目录和根目录是否有新增文件
                has_update_in_root = False
                has_update_in_subdir = False
                
                # 记录是否是首次执行
                is_first_run = True
                if task.get("last_run_time"):
                    is_first_run = False
                
                # 先检查是否有新增的目录
                for dir_node in dir_nodes:
                    dir_name = dir_node.tag.lstrip("📁")
                    
                    # 检查是否是解压文件夹的虚拟目录节点（以 extracted_dir_ 开头）
                    if dir_node.identifier.startswith("extracted_dir_"):
                        current_added_dirs.add(dir_name)
                        has_update_in_subdir = True
                    # 检查是否是指定的更新目录
                    elif update_subdir and re.search(update_subdir, dir_name):
                        current_added_dirs.add(dir_name)
                        has_update_in_subdir = True
                
                # 检查根目录是否有新增文件
                root_new_files = []
                for node in files_by_dir["root"]:
                    file_name = remove_file_icons(node.tag)
                    # 判断是否为新增文件
                    is_new_file = False

                    # 如果是保持原目录结构模式且该文件来自解压，则不视为“根目录新增文件”
                    auto_extract_mode_root = task.get("auto_extract_archive", "disabled")
                    keep_structure_root = "keep_structure" in auto_extract_mode_root if auto_extract_mode_root != "disabled" else False
                    if keep_structure_root and getattr(node, "data", None) and node.data.get("from_extraction"):
                        # 这类文件已经通过解压目录结构展示，不应再次作为根目录文件显示
                        continue
                    
                    # 1. 检查是否是解压文件夹的虚拟节点（以 extracted_ 开头）
                    if node.identifier.startswith("extracted_"):
                        is_new_file = True
                    
                    # 2. 检查是否在当前转存的文件列表中
                    elif hasattr(node, 'data') and 'created_at' in node.data:
                        current_time = int(time.time())
                        time_threshold = 600  # 10分钟 = 600秒
                        if current_time - node.data['created_at'] < time_threshold:
                            is_new_file = True
                    
                    # 3. 节点本身是从当次转存的文件树中获取的
                    elif hasattr(is_new_tree, 'nodes') and node.identifier in is_new_tree.nodes:
                        is_new_file = True
                    
                    # 4. 首次运行任务时，视为所有文件都是新增的
                    elif is_first_run:
                        is_new_file = True
                    
                    if is_new_file:
                        # 如果是保持原目录结构模式，再次检查文件是否在子目录中
                        auto_extract_mode_final = task.get("auto_extract_archive", "disabled")
                        keep_structure_final = "keep_structure" in auto_extract_mode_final if auto_extract_mode_final != "disabled" else False
                        auto_extract_subdirs_final = task.get("_auto_extract_subdirs") or []
                        
                        if keep_structure_final and auto_extract_subdirs_final:
                            # 获取所有子目录中的文件名（递归）
                            savepath_final = re.sub(r"/{2,}", "/", f"/{task['savepath']}")
                            subdir_file_names_final = set()
                            
                            def get_all_subdir_files_final(subdir_fid, subdir_name):
                                """递归获取子目录及其所有子目录中的文件"""
                                all_files = []
                                try:
                                    subdir_files = account.ls_dir(subdir_fid)
                                    for subdir_file in subdir_files:
                                        if subdir_file.get("dir", False):
                                            sub_subdir_path = f"{subdir_name}/{subdir_file['file_name']}"
                                            sub_subdir_full_path = f"{savepath_final}/{sub_subdir_path}"
                                            if sub_subdir_full_path in account.savepath_fid:
                                                sub_subdir_fid = account.savepath_fid[sub_subdir_full_path]
                                                all_files.extend(get_all_subdir_files_final(sub_subdir_fid, sub_subdir_path))
                                        else:
                                            all_files.append(subdir_file)
                                except Exception:
                                    pass
                                return all_files
                            
                            for subdir_path in auto_extract_subdirs_final:
                                subdir_name = subdir_path.lstrip("/")
                                subdir_full_path = f"{savepath_final}/{subdir_name}"
                                if subdir_full_path in account.savepath_fid:
                                    subdir_fid = account.savepath_fid[subdir_full_path]
                                    try:
                                        all_subdir_files = get_all_subdir_files_final(subdir_fid, subdir_name)
                                        for subdir_file in all_subdir_files:
                                            subdir_file_names_final.add(subdir_file["file_name"])
                                    except Exception:
                                        pass
                            
                            # 如果文件在子目录中，跳过添加到根目录
                            if file_name in subdir_file_names_final:
                                continue
                        
                        root_new_files.append(node)
                        current_added_files.add(file_name)
                        has_update_in_root = True
                        
                        # 记录到最终显示的文件列表中
                        final_display_files["root"].append(node)
                
                # 创建多目录下的更新文件字典
                subdir_new_files = {}
                
                # 首次运行时，记录哪些子目录是新添加的
                new_added_dirs = set()
                
                # 对每个已识别的更新目录，检查是否有新文件
                for dir_node in update_dir_nodes:
                    dir_name = dir_node.tag.lstrip("📁")
                    dir_id = dir_node.identifier
                    dir_files = files_by_dir.get(dir_id, [])
                    
                    # 初始化该目录的新文件列表
                    subdir_new_files[dir_id] = []
                    
                    # 首次运行时，记录所有符合更新目录规则的目录
                    if is_first_run:
                        new_added_dirs.add(dir_name)
                        has_update_in_subdir = True
                    
                    # 检查该目录下的文件是否是新文件
                    for file_node in dir_files:
                        file_name = remove_file_icons(file_node.tag)
                        # 判断是否为新增文件
                        is_new_file = False
                        
                        # 1. 检查是否是解压文件夹的虚拟节点（以 extracted_ 开头）
                        if file_node.identifier.startswith("extracted_"):
                            is_new_file = True
                        
                        # 2. 检查是否在当前转存的文件列表中
                        elif hasattr(file_node, 'data') and 'created_at' in file_node.data:
                            current_time = int(time.time())
                            time_threshold = 600  # 10分钟 = 600秒
                            if current_time - file_node.data['created_at'] < time_threshold:
                                is_new_file = True
                        
                        # 3. 节点本身是从当次转存的文件树中获取的
                        elif hasattr(is_new_tree, 'nodes') and file_node.identifier in is_new_tree.nodes:
                            is_new_file = True
                        
                        # 4. 首次运行任务时，视为所有文件都是新增的
                        elif is_first_run:
                            is_new_file = True
                        
                        if is_new_file:
                            subdir_new_files[dir_id].append(file_node)
                            current_added_files.add(file_name)
                            has_update_in_subdir = True
                            
                            # 记录到最终显示的文件列表中
                            if dir_id not in final_display_files["subdirs"]:
                                final_display_files["subdirs"][dir_id] = []
                            final_display_files["subdirs"][dir_id].append(file_node)
                
                # 如果已识别的目录中没有新增文件，尝试进一步查找符合update_subdir规则的目录
                if update_subdir and not has_update_in_subdir:
                    savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}")
                    
                    # 获取保存路径下的所有目录
                    try:
                        all_dirs = account.ls_dir(account.savepath_fid[savepath])
                        
                        # 筛选出符合更新规则的目录
                        for dir_item in all_dirs:
                            if dir_item.get("dir", False):
                                dir_name = dir_item["file_name"]
                                if re.search(update_subdir, dir_name):
                                    # 检查该目录是否已经处理过
                                    if not any(node.tag.lstrip("📁") == dir_name for node in update_dir_nodes):
                                        # 获取目录的文件列表
                                        update_subdir_path = f"{savepath}/{dir_name}"
                                        if update_subdir_path in account.savepath_fid:
                                            try:
                                                update_subdir_fid = account.savepath_fid[update_subdir_path]
                                                update_subdir_files = account.ls_dir(update_subdir_fid)
                                                
                                                # 创建虚拟目录节点
                                                class VirtualDirNode:
                                                    def __init__(self, name, dir_id):
                                                        self.tag = f"📁{name}"
                                                        self.identifier = dir_id
                                                        self.data = {"is_dir": True}
                                                
                                                # 创建虚拟目录节点
                                                virtual_dir_node = VirtualDirNode(dir_name, f"virtual_{dir_name}")
                                                update_dir_nodes.append(virtual_dir_node)
                                                
                                                # 初始化新文件列表
                                                subdir_new_files[virtual_dir_node.identifier] = []
                                                
                                                # 为每个文件创建虚拟节点
                                                for file in update_subdir_files:
                                                    if not file.get("dir", False):
                                                        class VirtualFileNode:
                                                            def __init__(self, name, file_id, dir_id):
                                                                self.tag = name
                                                                self.identifier = file_id
                                                                self.predecessor = dir_id
                                                                self.data = {"is_dir": False}
                                                        
                                                        # 创建虚拟文件节点，关联到虚拟目录
                                                        virtual_node = VirtualFileNode(
                                                            file["file_name"], 
                                                            f"virtual_file_{file['fid']}", 
                                                            virtual_dir_node.identifier
                                                        )
                                                        
                                                        # 首次运行时，将所有文件视为新文件
                                                        if is_first_run:
                                                            subdir_new_files[virtual_dir_node.identifier].append(virtual_node)
                                                            has_update_in_subdir = True
                                            except Exception as e:
                                                print(f"获取目录 {dir_name} 文件列表时出错: {str(e)}")
                    except Exception as e:
                        print(f"获取目录列表时出错: {str(e)}")
                
                # 对所有目录中的文件进行排序
                for dir_id in subdir_new_files:
                    if subdir_new_files[dir_id]:
                        subdir_new_files[dir_id] = sort_nodes(subdir_new_files[dir_id])
                
                # 记录本次执行时间
                task["last_run_time"] = int(time.time())
                
                # 显示文件树规则:
                # 1. 只有根目录有更新：只显示根目录
                # 2. 只有子目录有更新：只显示子目录
                # 3. 根目录和子目录都有更新：显示两者
                # 4. 都没有更新：不显示任何内容
                
                # 如果没有任何更新，不显示任何内容
                if not has_update_in_root and not has_update_in_subdir:
                    # 不添加任何通知
                    pass
                else:
                    # 添加基本通知
                    add_notify(f"✅《{task['taskname']}》新增文件:")
                    savepath = task['savepath']
                    add_notify(f"{re.sub(r'/{2,}', '/', f'/{savepath}')}")
                    
                    # 修正首次运行时对子目录的处理 - 只有在首次运行且有新增的子目录时才显示子目录内容
                    if has_update_in_root and has_update_in_subdir and is_first_run and len(new_added_dirs) == 0:
                        # 虽然标记为首次运行，但没有新增子目录，不应展示子目录内容
                        has_update_in_subdir = False
                    
                    # 构建重命名映射（用于更新文件树中的文件名）
                    rename_mapping = {}
                    if rename_logs:
                        for log in rename_logs:
                            # 格式：重命名: 旧名 → 新名
                            match = re.search(r'重命名: (.*?) → (.+?)($|\s|，|失败)', log)
                            if match:
                                old_name = match.group(1).strip()
                                new_name = match.group(2).strip()
                                rename_mapping[old_name] = new_name
                    
                    # 构建完整的目录树结构（支持多层级嵌套）
                    def build_directory_tree():
                        # 创建目录树结构
                        dir_tree = {"root": {"dirs": {}, "files": []}}
                        
                        # 获取保存路径的最后一部分目录名（用于过滤）
                        save_path = task.get("savepath", "").rstrip("/")
                        save_path_parts = save_path.split("/")
                        save_path_basename = save_path_parts[-1] if save_path_parts else ""
                        
                        # 处理所有目录节点
                        for dir_node in update_dir_nodes:
                            dir_id = dir_node.identifier
                            dir_name = dir_node.tag.lstrip("📁")
                            
                            # 跳过与保存路径相同的目录
                            if dir_name == save_path_basename or dir_name == save_path:
                                continue
                                
                            # 如果目录名包含完整路径，检查是否与保存路径相关
                            if "/" in dir_name:
                                # 检查是否以路径开头
                                if dir_name.startswith("/"):
                                    continue
                                
                                # 检查是否是保存路径或其子路径
                                path_parts = dir_name.split("/")
                                # 跳过包含保存路径的完整路径
                                if any(part == save_path_basename for part in path_parts):
                                    continue
                            
                            # 分割路径，处理可能的多级目录
                            path_parts = dir_name.split("/")
                            
                            # 从根节点开始构建路径
                            current = dir_tree["root"]
                            
                            for i, part in enumerate(path_parts):
                                # 跳过空部分和保存路径部分
                                if not part or part == save_path_basename:
                                    continue
                                    
                                if part not in current["dirs"]:
                                    # 创建新的目录节点
                                    current["dirs"][part] = {
                                        "dirs": {},
                                        "files": [],
                                        "dir_id": dir_id if i == len(path_parts) - 1 else None
                                    }
                                current = current["dirs"][part]
                            
                            # 添加该目录的文件
                            if dir_id in subdir_new_files:
                                # 处理嵌套目录下的文件
                                non_nested_files = []
                                
                                for file_node in subdir_new_files[dir_id]:
                                    file_name = remove_file_icons(file_node.tag)
                                    if hasattr(file_node, 'nested_path') and file_node.nested_path:
                                        # 处理嵌套文件
                                        nested_path_parts = file_node.nested_path.split('/')
                                        
                                        # 创建或获取子目录结构
                                        sub_current = current
                                        for i, part in enumerate(nested_path_parts):
                                            if part not in sub_current["dirs"]:
                                                # 创建新的子目录结构
                                                sub_current["dirs"][part] = {
                                                    "dirs": {},
                                                    "files": [],
                                                    "dir_id": None  # 虚拟目录暂时没有ID
                                                }
                                            sub_current = sub_current["dirs"][part]
                                        
                                        # 添加文件到嵌套目录
                                        sub_current["files"].append(file_node)
                                    else:
                                        # 不是嵌套的文件，直接添加到当前目录
                                        non_nested_files.append(file_node)
                                
                                # 设置当前目录的非嵌套文件
                                current["files"] = non_nested_files
                        
                        # 添加根目录文件
                        if has_update_in_root and final_display_files["root"]:
                            dir_tree["root"]["files"] = final_display_files["root"]
                        
                        return dir_tree
                    
                    # 递归显示目录树
                    def display_tree(node, prefix="", depth=0):
                        # 获取目录和文件列表
                        dirs = sorted(node["dirs"].items())
                        files = node.get("files", [])
                        
                        # 根目录文件特殊处理（如果路径以"/"开头，检查是否和当前保存路径相关）
                        save_path = task.get("savepath", "").rstrip("/")
                        save_path_parts = save_path.split("/")
                        save_path_basename = save_path_parts[-1] if save_path else ""
                        
                        # 过滤目录 - 如果只有根目录有更新，且不是首次运行或没有新增目录，则不显示子目录
                        if depth == 0 and has_update_in_root and (not has_update_in_subdir or (is_first_run and len(new_added_dirs) == 0)):
                            # 在根目录级别，如果只有根目录有更新，则过滤掉所有子目录
                            dirs = []
                        
                        # 先过滤掉空的目录，避免影响后续计算
                        valid_dirs = []
                        for dir_name, dir_data in dirs:
                            dir_files = dir_data.get("files", [])
                            dir_subdirs = dir_data.get("dirs", {})
                            # 如果目录既没有文件也没有子目录，跳过
                            if len(dir_files) == 0 and len(dir_subdirs) == 0:
                                continue
                            # 检查目录是否在新增目录列表中或有子文件更新
                            if is_first_run and dir_name not in new_added_dirs and len(dir_files) == 0:
                                continue
                            valid_dirs.append((dir_name, dir_data))
                        
                        # 计算总项数（有效目录+文件）
                        total_items = len(valid_dirs) + len(files)
                        current_item = 0
                        
                        # 处理目录
                        for i, (dir_name, dir_data) in enumerate(valid_dirs):
                            current_item += 1
                            is_dir_last = current_item == total_items
                            
                            # 过滤条件：
                            # 1. 目录名不为空
                            # 2. 不是保存路径本身
                            # 3. 不是以"/"开头的完整路径
                            # 4. 不是保存路径的基本名称
                            if (dir_name and 
                                dir_name != save_path and
                                not dir_name.startswith("/") and
                                dir_name != save_path_basename):
                                
                                dir_prefix = prefix + ("└── " if is_dir_last else "├── ")
                                add_notify(format_file_display(dir_prefix, "📁", dir_name))
                                
                                # 计算子项的前缀，保持树形结构清晰
                                # 第一个缩进标记使用点号，后续使用空格
                                if prefix == "":
                                    # 第一层缩进使用点号开头
                                    new_prefix = "·   " if is_dir_last else "│   "
                                else:
                                    # 后续层级开头保持前缀不变，尾部添加新的缩进标记
                                    if is_dir_last:
                                        new_prefix = prefix + "    "  # 最后一项，空格缩进
                                    else:
                                        new_prefix = prefix + "│   "  # 非最后一项，使用竖线
                                
                                # 递归显示子目录
                                display_tree(dir_data, new_prefix, depth + 1)
                        
                        # 处理文件
                        sorted_files = sort_nodes(files) if files else []

                        for j, file_node in enumerate(sorted_files):
                            current_item += 1
                            is_file_last = current_item == total_items
                            
                            # 显示文件
                            file_prefix = prefix + ("└── " if is_file_last else "├── ")
                            file_name = remove_file_icons(file_node.tag)
                            # 关键修复：如果文件被重命名了，使用重命名后的文件名
                            if file_name in rename_mapping:
                                file_name = rename_mapping[file_name]
                            icon = get_file_icon(file_name, is_dir=False)
                            notify_text = format_file_display(file_prefix, icon, file_name)
                            add_notify(notify_text)
                    
                    # 构建并显示目录树
                    if has_update_in_root or has_update_in_subdir:
                        directory_tree = build_directory_tree()
                        display_tree(directory_tree["root"])
                        add_notify("")
                        
                    # 处理重命名日志，避免显示不必要的路径信息
                    # 注意：重命名日志将在文件树显示后统一处理和打印
                
                add_notify("")
            
            
            
            # 如果是剧集命名模式并且成功进行了重命名，单独显示排序好的文件列表
            elif is_rename and task.get("use_episode_naming") and task.get("episode_naming"):
                # 重新获取文件列表
                savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}")
                dir_file_list = account.ls_dir(account.savepath_fid[savepath])

                # 过滤出非目录的文件
                file_nodes = [f for f in dir_file_list if not f["dir"]]

                # 获取实际的文件名映射
                actual_file_names = account.get_actual_file_names_from_directory(task, rename_logs)

                # 从重命名日志提取预期的新旧文件名映射
                expected_renamed_files = {}
                for log in rename_logs:
                    # 格式：重命名: 旧名 → 新名
                    if "重命名:" in log and " → " in log:
                        # 先分割出"重命名:"后面的部分
                        parts = log.split("重命名:", 1)[1].strip()
                        # 再按箭头分割
                        if " → " in parts:
                            old_name, expected_new_name = parts.split(" → ", 1)
                            # 只处理失败信息，不截断正常文件名
                            if " 失败，" in expected_new_name:
                                expected_new_name = expected_new_name.split(" 失败，")[0]
                            # 去除首尾空格
                            old_name = old_name.strip()
                            expected_new_name = expected_new_name.strip()
                            expected_renamed_files[old_name] = expected_new_name
                    
                # 确保至少显示实际存在的文件
                display_files = []

                # 添加所有实际存在的转存文件
                for old_name in expected_renamed_files.keys():
                    # 获取实际的文件名
                    actual_name = actual_file_names.get(old_name, expected_renamed_files[old_name])

                    # 检查文件是否实际存在于目录中
                    if any(f["file_name"] == actual_name for f in file_nodes):
                        if actual_name not in display_files:
                            display_files.append(actual_name)
                
                # 此外，检查是否有新的文件节点（比较节点时间）
                if not display_files and is_new_tree and hasattr(is_new_tree, 'nodes'):
                    # 如果有转存文件树，从中提取文件
                    tree_nodes = is_new_tree.nodes.values()
                    for node in tree_nodes:
                        if hasattr(node, 'data') and not node.data.get('is_dir', False):
                            file_path = node.data.get('path', '')
                            if file_path:
                                file_name = os.path.basename(file_path)
                                if file_name not in display_files:
                                    display_files.append(file_name)
                
                # 还需要检查是否有打印到控制台的转存文件信息（情况：转存后立即重命名）
                # 无论display_files是否为空都执行此代码，确保能提取到重命名的文件
                for log in rename_logs:
                    if "重命名:" in log and " → " in log:
                        parts = log.split(" → ", 1)
                        if len(parts) > 1:
                            new_name = parts[1].strip()
                            # 过滤掉可能的结束标记，但要确保完整保留文件名
                            if "\n" in new_name:
                                new_name = new_name.split("\n")[0].strip()
                            
                            # 只有当文件名包含明确的分隔符时才进行分割
                            # 例如"黑镜 - S07E02.mkv"不应该被分割
                            if "，" in new_name:
                                new_name = new_name.split("，")[0].strip()
                                
                            # 确保不要错误地只提取文件名的一部分（如"黑镜"）
                            if " " in new_name and "." in new_name:  # 如果包含空格和扩展名
                                # 检查这是不是一个完整文件名
                                if re.search(r'\.\w+$', new_name):  # 检查是否以扩展名结尾
                                    # 这是一个完整的文件名，不做进一步分割
                                    pass
                                else:
                                    # 不是以扩展名结尾，可能需要进一步处理
                                    new_name = new_name.split(" ")[0].strip()
                            
                            if new_name and new_name not in display_files:
                                # 额外检查，确保提取的是完整文件名
                                if "." in new_name:  # 通常文件名应包含扩展名
                                    display_files.append(new_name)
                
                # 如果通过重命名和文件树都没找到文件，使用最新时间排序的文件
                if not display_files and file_nodes:
                    # 查找目录中修改时间最新的文件（可能是刚刚转存的）
                    today = datetime.now().strftime('%Y-%m-%d')
                    recent_files = []  # 定义并初始化recent_files变量
                    
                    # 首先尝试通过修改日期过滤当天的文件
                    for file in file_nodes:
                        # 如果有时间戳，转换为日期字符串
                        if 'updated_at' in file and file['updated_at']:
                            try:
                                # 检查时间戳是否在合理范围内 (1970-2100年)
                                timestamp = file['updated_at']
                                if timestamp > 4102444800:  # 2100年的时间戳
                                    # 可能是毫秒级时间戳，尝试转换为秒级
                                    timestamp = timestamp / 1000
                                
                                # 再次检查时间戳是否在合理范围内
                                if 0 < timestamp < 4102444800:
                                    update_time = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d')
                                    if update_time == today:
                                        recent_files.append(file)
                                else:
                                    print(f"警告: 文件 {file.get('file_name', '未知')} 的时间戳 {file['updated_at']} 超出范围")
                            except (ValueError, OSError, OverflowError) as e:
                                print(f"警告: 处理文件 {file.get('file_name', '未知')} 的时间戳时出错: {e}")
                    
                    # 如果没有找到当天的文件，至少显示一个最新的文件
                    if not recent_files and file_nodes:
                        # 定义安全的排序键函数
                        def safe_timestamp_key(x):
                            try:
                                timestamp = x.get('updated_at', 0)
                                # 如果时间戳太大，可能是毫秒级时间戳
                                if timestamp > 4102444800:  # 2100年的时间戳
                                    timestamp = timestamp / 1000
                                # 再次检查范围
                                if timestamp < 0 or timestamp > 4102444800:
                                    return 0  # 无效时间戳返回0
                                return timestamp
                            except (ValueError, TypeError):
                                return 0  # 无效返回0
                        
                        try:
                            # 按修改时间排序，使用安全的排序函数
                            recent_files = sorted(file_nodes, key=safe_timestamp_key, reverse=True)
                        except Exception as e:
                            print(f"警告: 文件排序时出错: {e}")
                            # 如果排序出错，直接使用原始列表
                            recent_files = file_nodes
                    
                    # 只取第一个作为显示
                    if recent_files:
                        try:
                            display_files.append(recent_files[0]['file_name'])
                        except (IndexError, KeyError) as e:
                            print(f"警告: 获取文件名时出错: {e}")
                            # 如果出错，尝试添加第一个文件（如果有）
                            if file_nodes:
                                try:
                                    display_files.append(file_nodes[0]['file_name'])
                                except (KeyError, IndexError):
                                    print("警告: 无法获取有效的文件名")
                
                # 添加成功通知 - 修复问题：确保在有文件时添加通知
                if display_files:
                    add_notify(f"✅《{task['taskname']}》新增文件:")
                    savepath = task['savepath']
                    add_notify(f"{re.sub(r'/{2,}', '/', f'/{savepath}')}")
                
                
                # 创建episode_pattern函数用于排序
                def extract_episode_number_local(filename):
                    # 使用全局的统一提取函数，但优先尝试从episode_naming模式中提取
                    episode_pattern = task["episode_naming"]
                    
                    # 优先尝试全局函数提取
                    ep_num = extract_episode_number(filename)
                    if ep_num is not None:
                        return ep_num
                    
                    # 如果全局函数无法提取，尝试从episode_naming模式中提取
                    if "[]" in episode_pattern:
                        pattern_parts = episode_pattern.split("[]")
                        if len(pattern_parts) == 2:
                            prefix, suffix = pattern_parts
                            if prefix and filename.startswith(prefix):
                                number_part = filename[len(prefix):].split(suffix)[0] if suffix else filename[len(prefix):]
                                if number_part.isdigit():
                                    return int(number_part)
                                # 尝试转换中文数字
                                else:
                                    arabic_num = chinese_to_arabic(number_part)
                                    if arabic_num is not None:
                                        return arabic_num
                    
                    # 如果所有方法都失败，返回float('inf')
                    return float('inf')
                
                # 按剧集号排序
                display_files.sort(key=extract_episode_number_local)
                
                # 打印文件列表
                for idx, file_name in enumerate(display_files):
                    prefix = "├── " if idx < len(display_files) - 1 else "└── "
                    # 查找文件信息用于获取图标
                    file_info = next((f for f in file_nodes if f["file_name"] == file_name or 
                                    (f["file_name"] in renamed_files and renamed_files[f["file_name"]] == file_name)), None)
                    if file_info is None:
                        # 如果找不到对应信息，可能是已重命名文件，使用默认图标
                        icon = get_file_icon(file_name, is_dir=False)
                    else:
                        icon = get_file_icon(file_name, is_dir=file_info.get("dir", False))
                    add_notify(format_file_display(prefix, icon, file_name))
                
                # 确保只有在有文件时才添加空行
                if display_files:
                    add_notify("")
            # 添加正则命名模式的文件树显示逻辑
            elif is_rename and not is_special_sequence and task.get("pattern") is not None:
                # 重新获取文件列表
                savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}")
                dir_file_list = account.ls_dir(account.savepath_fid[savepath])

                # 过滤出非目录的文件
                file_nodes = [f for f in dir_file_list if not f["dir"]]

                # 获取实际的文件名映射
                actual_file_names = account.get_actual_file_names_from_directory(task, rename_logs)

                # 从重命名日志提取预期的新旧文件名映射
                expected_renamed_files = {}
                for log in rename_logs:
                    # 格式：重命名: 旧名 → 新名
                    match = re.search(r'重命名: (.*?) → (.+?)($|\s|，|失败)', log)
                    if match:
                        old_name = match.group(1).strip()
                        expected_new_name = match.group(2).strip()
                        expected_renamed_files[old_name] = expected_new_name

                # 只显示实际存在的转存文件
                display_files = []
                for old_name in expected_renamed_files.keys():
                    # 获取实际的文件名
                    actual_name = actual_file_names.get(old_name, expected_renamed_files[old_name])

                    # 检查文件是否实际存在于目录中
                    if any(f["file_name"] == actual_name for f in file_nodes):
                        if actual_name not in display_files:
                            display_files.append(actual_name)
                
                # 如果没有找到任何文件要显示，使用原始逻辑
                if not display_files:
                    # 创建一个映射列表，包含所有文件
                    display_files = [file["file_name"] for file in file_nodes]
                
                # 添加成功通知
                add_notify(f"✅《{task['taskname']}》新增文件:")
                savepath = task['savepath']
                add_notify(f"{re.sub(r'/{2,}', '/', f'/{savepath}')}")
                
                # 打印文件列表
                for idx, file_name in enumerate(display_files):
                    prefix = "├── " if idx < len(display_files) - 1 else "└── "
                    file_info = file_nodes[next((i for i, f in enumerate(file_nodes) if f["file_name"] == file_name), 0)]
                    icon = get_file_icon(file_name, is_dir=file_info.get("dir", False))
                    add_notify(format_file_display(prefix, icon, file_name))
                add_notify("")
            
            # 打印重命名日志（文件树之后）
            if rename_logs:
                # 处理重命名日志，更新数据库记录
                account.process_rename_logs(task, rename_logs)
                
                # 控制台视觉分隔：若前面未通过 add_notify("") 打印过空行，则补打一行空行
                try:
                    if not (NOTIFYS and NOTIFYS[-1] == ""):
                        print()
                except Exception:
                    # 兜底：若 NOTIFYS 异常，仍保证有一行空行
                    print()
                
                # 对剧集命名模式和其他模式统一处理重命名日志
                # 按sort_file_by_name函数的多级排序逻辑排序重命名日志
                sorted_rename_logs = []
                
                for log in rename_logs:
                    # 提取新文件名（格式：重命名: 旧名 → 新名）
                    # 使用更精确的字符串分割方法，确保能捕获完整的文件名
                    if "重命名:" in log and " → " in log:
                        parts = log.split("重命名:", 1)[1].strip()
                        if " → " in parts:
                            _, new_name = parts.split(" → ", 1)
                            
                            # 只处理失败信息，不截断正常文件名
                            if " 失败，" in new_name:
                                new_name = new_name.split(" 失败，")[0]
                            
                            # 去除首尾空格
                            new_name = new_name.strip()
                            
                            # 使用sort_file_by_name函数获取排序值
                            sort_tuple = sort_file_by_name(new_name)
                            sorted_rename_logs.append((sort_tuple, log))
                    else:
                        # 没找到箭头或格式不符的日志放在最后
                        sorted_rename_logs.append(((float('inf'), float('inf'), float('inf'), 0), log))
                
                # 按sort_file_by_name返回的排序元组排序
                sorted_rename_logs.sort(key=lambda x: x[0])
                
                # 打印排序后的日志
                for _, log in sorted_rename_logs:
                    print(log)
            
            # 补充任务的插件配置
            def merge_dicts(a, b):
                result = a.copy()
                for key, value in b.items():
                    if (
                        key in result
                        and isinstance(result[key], dict)
                        and isinstance(value, dict)
                    ):
                        result[key] = merge_dicts(result[key], value)
                    elif key not in result:
                        result[key] = value
                return result

            task["addition"] = merge_dicts(
                task.get("addition", {}), task_plugins_config
            )
            
            # 为任务添加剧集模式配置
            if task.get("use_episode_naming") and task.get("episode_naming"):
                task["config_data"] = {
                    "episode_patterns": CONFIG_DATA.get("episode_patterns", [])
                }
            # 调用插件
            if is_new_tree or is_rename:
                print()
                print(f"🧩 调用插件")
                for plugin_name, plugin in plugins.items():
                    if plugin.is_active and (is_new_tree or is_rename):
                        task = (
                            plugin.run(task, account=account, tree=is_new_tree, rename_logs=rename_logs) or task
                        )
            elif is_new_tree is False:  # 明确没有新文件
                print(f"任务完成: 没有新的文件需要转存")
    print()


def main():
    global CONFIG_DATA
    start_time = datetime.now()
    print(f"===============程序开始===============")
    print(f"⏰ 执行时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    # 读取启动参数
    config_path = sys.argv[1] if len(sys.argv) > 1 else "quark_config.json"
    # 从环境变量中获取 TASKLIST
    tasklist_from_env = []
    if tasklist_json := os.environ.get("TASKLIST"):
        try:
            tasklist_from_env = json.loads(tasklist_json)
        except Exception as e:
            print(f"从环境变量解析任务列表失败 {e}")
    # 检查本地文件是否存在，如果不存在就下载
    if not os.path.exists(config_path):
        if os.environ.get("QUARK_COOKIE"):
            print(
                f"⚙️ 读取到 QUARK_COOKIE 环境变量，仅签到领空间。如需执行转存，请删除该环境变量后配置 {config_path} 文件"
            )
            cookie_val = os.environ.get("QUARK_COOKIE")
            cookie_form_file = False
        else:
            print(f"⚙️ 配置文件 {config_path} 不存在❌，正远程从下载配置模版")
            config_url = f"{GH_PROXY}https://raw.githubusercontent.com/Cp0204/quark_auto_save/main/quark_config.json"
            if Config.download_file(config_url, config_path):
                print("⚙️ 配置模版下载成功✅，请到程序目录中手动配置")
            return
    else:
        print(f"⚙️ 正从 {config_path} 文件中读取配置")
        CONFIG_DATA = Config.read_json(config_path)
        Config.breaking_change_update(CONFIG_DATA)
        cookie_val = CONFIG_DATA.get("cookie")
        if not CONFIG_DATA.get("magic_regex"):
            CONFIG_DATA["magic_regex"] = MAGIC_REGEX
        cookie_form_file = True
    # 获取cookie
    cookies = Config.get_cookies(cookie_val)
    if not cookies:
        print("❌ cookie 未配置")
        return
    accounts = [Quark(cookie, index) for index, cookie in enumerate(cookies)]
    # 签到
    print()
    print(f"===============签到任务===============")
    if tasklist_from_env:
        verify_account(accounts[0])
    else:
        for account in accounts:
            verify_account(account)
            do_sign(account)
    print()
    # 转存
    if accounts[0].is_active and cookie_form_file:
        print(f"===============转存任务===============")
        # 任务列表
        if tasklist_from_env:
            # 若通过环境变量传入任务列表，视为手动运行，可由外层控制是否忽略执行周期/进度限制
            ignore_execution_rules = os.environ.get("IGNORE_EXECUTION_RULES", "").lower() in ["1", "true", "yes"]
            do_save(accounts[0], tasklist_from_env, ignore_execution_rules=ignore_execution_rules)
        else:
            # 定时任务或命令行全量运行，始终遵循执行周期/进度规则
            do_save(accounts[0], CONFIG_DATA.get("tasklist", []), ignore_execution_rules=False)
        print()
    # 通知
    if NOTIFYS:
        # 为推送通知做一次轻量清洗，避免文件树中出现多余空行
        def _clean_notify_lines(lines):
            """
            轻量清洗通知行，避免推送正文中的文件树出现多余空行。
            
            说明：
            - 终端日志中的空行保留不变，仍然由 add_notify 打印；
            - 这里只在推送前对 NOTIFYS 做一次只读清洗。
            """
            cleaned = []
            total = len(lines)
            for i, line in enumerate(lines):
                # 跳过仅用于分隔的空行（例如出现在树形文件项之间）
                if not line.strip():
                    prev_line = lines[i - 1] if i > 0 else ""
                    next_line = lines[i + 1] if i + 1 < total else ""
                    
                    # 判断一行是否为“树形文件行”（支持前缀有 │ / · 等符号）
                    def is_tree_line(text: str) -> bool:
                        stripped = text.strip()
                        # 直接以树形前缀开头
                        if stripped.startswith(("├──", "└──")):
                            return True
                        # 或者中间包含树形前缀（例如 "│   ├──", "·   └──"）
                        return "├──" in stripped or "└──" in stripped
                    
                    # 如果前后都是树形文件行，则认为这是多余的空行，只在终端显示，不进入推送正文
                    if is_tree_line(prev_line) and is_tree_line(next_line):
                        continue
                cleaned.append(line)
            return cleaned

        notify_lines = _clean_notify_lines(NOTIFYS)
        notify_body = "\n".join(notify_lines)
        print(f"===============推送通知===============")
        send_ql_notify("【夸克自动转存】", notify_body)
        print()
    else:
        # 如果没有通知内容，显示统一提示
        print(f"===============推送通知===============")
        print("📭 本次运行没有新的转存，未推送通知")
        print()
    if cookie_form_file:
        # 更新配置
        Config.write_json(config_path, CONFIG_DATA)

    print(f"===============程序结束===============")
    duration = datetime.now() - start_time
    print(f"😃 运行时长: {round(duration.total_seconds(), 2)}s")
    print()


if __name__ == "__main__":
    main()
