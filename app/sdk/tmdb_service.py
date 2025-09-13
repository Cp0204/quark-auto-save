#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TMDB服务模块
用于获取电视节目信息和播出时间表
"""

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)

class TMDBService:
    def __init__(self, api_key: str = None):
        self.api_key = api_key
        # 首选改为 api.tmdb.org，备选为 api.themoviedb.org
        self.primary_url = "https://api.tmdb.org/3"
        self.backup_url = "https://api.themoviedb.org/3"
        self.current_url = self.primary_url
        self.language = "zh-CN"  # 返回中文数据
        # 复用会话，开启重试
        self.session = requests.Session()
        retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])  # 简单退避
        adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=50)
        self.session.mount('http://', adapter)
        self.session.mount('https://', adapter)
        # 简单内存缓存，避免短时间重复请求
        self._cache = {}
        self._cache_ttl_seconds = 600
        
    def is_configured(self) -> bool:
        """检查TMDB API是否已配置"""
        return bool(self.api_key and self.api_key.strip())
    
    def reset_to_primary_url(self):
        """重置到主API地址"""
        self.current_url = self.primary_url
        logger.info("TMDB API地址已重置为主地址")
    
    def get_current_api_url(self) -> str:
        """获取当前使用的API地址"""
        return self.current_url
    
    def is_using_backup_url(self) -> bool:
        """检查是否正在使用备用地址"""
        return self.current_url == self.backup_url
    
    def _make_request(self, endpoint: str, params: Dict = None) -> Optional[Dict]:
        """发送API请求，支持自动切换备用地址"""
        if not self.is_configured():
            return None
            
        if params is None:
            params = {}
            
        params.update({
            'api_key': self.api_key,
            'language': self.language,
            'include_adult': False
        })
        
        # 简单缓存键
        try:
            from time import time as _now
            cache_key = (endpoint, tuple(sorted((params or {}).items())))
            cached = self._cache.get(cache_key)
            if cached and (_now() - cached[0]) < self._cache_ttl_seconds:
                return cached[1]
        except Exception:
            pass

        # 尝试主地址
        try:
            url = f"{self.current_url}{endpoint}"
            response = self.session.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            try:
                self._cache[cache_key] = (_now(), data)
            except Exception:
                pass
            return data
        except Exception as e:
            logger.warning(f"TMDB主地址请求失败: {e}")
            
            # 如果当前使用的是主地址，尝试切换到备用地址
            if self.current_url == self.primary_url:
                logger.info("尝试切换到TMDB备用地址...")
                self.current_url = self.backup_url
                try:
                    url = f"{self.current_url}{endpoint}"
                    response = self.session.get(url, params=params, timeout=10)
                    response.raise_for_status()
                    logger.info("TMDB备用地址连接成功")
                    data = response.json()
                    try:
                        self._cache[cache_key] = (_now(), data)
                    except Exception:
                        pass
                    return data
                except Exception as backup_e:
                    logger.error(f"TMDB备用地址请求也失败: {backup_e}")
                    # 重置回主地址，下次请求时重新尝试
                    self.current_url = self.primary_url
                    return None
            else:
                # 如果备用地址也失败，重置回主地址
                logger.error(f"TMDB备用地址请求失败: {e}")
                self.current_url = self.primary_url
                return None
    
    def search_tv_show(self, query: str, year: str = None) -> Optional[Dict]:
        """搜索电视剧"""
        params = {
            'query': query,
            'first_air_date_year': year
        }
        
        result = self._make_request('/search/tv', params)
        if result and result.get('results'):
            # 返回第一个匹配结果
            return result['results'][0]
        return None
    
    def get_tv_show_details(self, tv_id: int) -> Optional[Dict]:
        """获取电视剧详细信息"""
        return self._make_request(f'/tv/{tv_id}')
    
    def get_tv_show_alternative_titles(self, tv_id: int) -> Optional[Dict]:
        """获取电视剧的别名信息"""
        return self._make_request(f'/tv/{tv_id}/alternative_titles')
    
    def _is_chinese_text(self, text: str) -> bool:
        """检查文本是否包含中文字符"""
        if not text:
            return False
        for char in text:
            if '\u4e00' <= char <= '\u9fff':  # 中文字符范围
                return True
        return False
    
    def get_chinese_title_with_fallback(self, tv_id: int, original_title: str = "") -> str:
        """
        获取中文标题，如果中文标题为空或不是中文则从别名中获取中国地区的别名
        
        Args:
            tv_id: TMDB ID
            original_title: 原始标题，作为最后的备用方案
            
        Returns:
            中文标题或备用标题
        """
        try:
            # 首先获取节目详情，检查是否有中文标题
            details = self.get_tv_show_details(tv_id)
            if details:
                tmdb_name = details.get('name', '').strip()
                
                # 检查TMDB返回的标题是否包含中文字符
                if tmdb_name and self._is_chinese_text(tmdb_name):
                    logger.debug(f"直接获取到中文标题: {tmdb_name} (TMDB ID: {tv_id})")
                    return tmdb_name
                
                # 如果TMDB返回的标题不是中文，尝试从别名中获取中国地区的别名
                alternative_titles = self.get_tv_show_alternative_titles(tv_id)
                if alternative_titles and alternative_titles.get('results'):
                    # 查找中国地区的别名
                    for alt_title in alternative_titles['results']:
                        if alt_title.get('iso_3166_1') == 'CN':
                            chinese_alt_title = alt_title.get('title', '').strip()
                            if chinese_alt_title and self._is_chinese_text(chinese_alt_title):
                                logger.debug(f"从别名中获取到中文标题: {chinese_alt_title} (TMDB ID: {tv_id})")
                                return chinese_alt_title
                
                # 如果都没有找到中文标题，返回原始标题
                logger.debug(f"未找到中文标题，使用原始标题: {original_title} (TMDB ID: {tv_id})")
                return original_title
            else:
                # 如果无法获取详情，返回原始标题
                return original_title
                
        except Exception as e:
            logger.warning(f"获取中文标题失败: {e}, 使用原始标题: {original_title}")
            return original_title
    
    def get_tv_show_episodes(self, tv_id: int, season_number: int) -> Optional[Dict]:
        """获取指定季的剧集信息"""
        return self._make_request(f'/tv/{tv_id}/season/{season_number}')
    
    def get_tv_show_air_dates(self, tv_id: int) -> Optional[Dict]:
        """获取电视剧播出时间信息"""
        return self._make_request(f'/tv/{tv_id}/air_dates')
    
    def get_tv_show_episode_air_dates(self, tv_id: int, season_number: int) -> List[Dict]:
        """获取指定季所有剧集的播出时间"""
        episodes = self.get_tv_show_episodes(tv_id, season_number)
        if not episodes or 'episodes' not in episodes:
            return []
            
        episode_list = []
        for episode in episodes['episodes']:
            if episode.get('air_date'):
                episode_list.append({
                    'episode_number': episode.get('episode_number'),
                    'air_date': episode.get('air_date'),
                    'name': episode.get('name'),
                    'overview': episode.get('overview')
                })
        
        return episode_list

    # ===== 节目状态中文映射（不含 returning_series 场景判断；该判断在 run.py 本地完成） =====
    def map_show_status_cn(self, status: str) -> str:
        """将 TMDB 节目状态映射为中文。未知状态保持原样。"""
        try:
            if not status:
                return ''
            key = str(status).strip().lower().replace(' ', '_')
            mapping = {
                'returning_series': '播出中',
                'in_production': '制作中',
                'planned': '计划中',
                'ended': '已完结',
                'canceled': '已取消',
                'cancelled': '已取消',
                'pilot': '试播集',
                'rumored': '待确认',
            }
            return mapping.get(key, status)
        except Exception:
            return status

    # 注意：returning_series 的“播出中/本季终”判断在 run.py 使用本地 seasons/episodes 统计完成

    def arabic_to_chinese_numeral(self, number: int) -> str:
        """将阿拉伯数字转换为中文数字（用于季数），支持到万（0 < number < 100000）。
        规则与约定：
        - 基本单位：一、二、三、四、五、六、七、八、九、十、百、千、万；包含“零”与“两（2的特殊口语用法）”。
        - 10-19 省略“一十”：10=十，11=十一。
        - “两”的使用：在 百/千/万 位上优先使用“两”（如200=两百，2000=两千，20000=两万）；十位仍用“二十”。
        - 正确处理“零”的读法：如 101=一百零一，1001=一千零一，10010=一万零一十。
        - 超出范围（<=0 或 >=100000）时返回原数字字符串。
        """
        try:
            number = int(number)
        except Exception:
            return str(number)

        if number <= 0 or number >= 100000:
            return str(number)

        digits = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九"]

        def convert_0_9999(n: int) -> str:
            if n == 0:
                return "零"
            if n < 10:
                return digits[n]
            if n < 20:
                # 十到十九
                return "十" + (digits[n - 10] if n > 10 else "")
            parts = []
            thousand = n // 1000
            hundred = (n % 1000) // 100
            ten = (n % 100) // 10
            one = n % 10

            # 千位
            if thousand:
                if thousand == 2:
                    parts.append("两千")
                else:
                    parts.append(digits[thousand] + "千")

            # 百位
            if hundred:
                if thousand and hundred == 0:
                    # 不会发生：hundred 有值才进来
                    pass
                if hundred == 2:
                    parts.append("两百")
                else:
                    parts.append(digits[hundred] + "百")
            else:
                if thousand and (ten != 0 or one != 0):
                    parts.append("零")

            # 十位
            if ten:
                if ten == 1 and not thousand and not hundred:
                    # 10-19 形式（已在 n<20 处理）但也考虑 0xx 场景
                    parts.append("十")
                else:
                    parts.append(digits[ten] + "十")
            else:
                if (hundred or thousand) and one != 0:
                    parts.append("零")

            # 个位
            if one:
                parts.append(digits[one])

            # 合并并清理多余“零”
            result = ''.join(parts)
            # 去重连续零
            while "零零" in result:
                result = result.replace("零零", "零")
            # 尾部零去掉
            if result.endswith("零"):
                result = result[:-1]
            return result

        if number < 10000:
            return convert_0_9999(number)

        # 处理万级：number = a * 10000 + b, 1 <= a <= 9, 0 <= b < 10000
        wan = number // 10000
        rest = number % 10000

        # 万位上的 2 使用“两万”，其他使用常规数字 + 万
        if wan == 2:
            prefix = "两万"
        else:
            prefix = digits[wan] + "万"

        if rest == 0:
            return prefix

        # rest 存在且不足四位时，需要根据是否存在中间的 0 添加“零”
        rest_str = convert_0_9999(rest)
        # 当 rest < 1000 时，且 rest_str 不以“零”开头，需要补一个“零”
        if rest < 1000:
            if not rest_str.startswith("零"):
                return prefix + "零" + rest_str
        return prefix + rest_str

    def process_season_name(self, raw_name: str) -> str:
        """将 TMDB 返回的季名称进行本地化处理：
        - 将“第1季”“第 24 季”转换为“第一季”“第二十四季”
        - 其他名称保持原样
        """
        try:
            if not raw_name:
                return raw_name
            # 匹配“第 1 季”“第1季”“第 24 季”等
            m = re.search(r"第\s*(\d+)\s*季", raw_name)
            if m:
                n = int(m.group(1))
                cn = self.arabic_to_chinese_numeral(n)
                return re.sub(r"第\s*\d+\s*季", f"第{cn}季", raw_name)
            return raw_name
        except Exception:
            return raw_name
    
    def search_and_get_episodes(self, show_name: str, year: str = None) -> Optional[Dict]:
        """搜索电视剧并获取剧集信息"""
        # 搜索电视剧
        show = self.search_tv_show(show_name, year)
        if not show:
            return None
            
        tv_id = show.get('id')
        if not tv_id:
            return None
            
        # 获取详细信息
        details = self.get_tv_show_details(tv_id)
        if not details:
            return None
            
        # 获取所有季的剧集信息
        seasons = details.get('seasons', [])
        all_episodes = []
        
        for season in seasons:
            season_number = season.get('season_number', 0)
            if season_number > 0:  # 排除特殊季（如第0季）
                episodes = self.get_tv_show_episode_air_dates(tv_id, season_number)
                for episode in episodes:
                    episode['season_number'] = season_number
                    episode['show_name'] = show_name
                    episode['show_id'] = tv_id
                    all_episodes.append(episode)
        
        return {
            'show_info': {
                'id': tv_id,
                'name': show_name,
                'original_name': details.get('original_name'),
                'overview': details.get('overview'),
                'poster_path': details.get('poster_path'),
                'first_air_date': details.get('first_air_date'),
                'media_type': details.get('media_type', 'tv')
            },
            'episodes': all_episodes
        }
    
    def get_episodes_by_date_range(self, start_date: str, end_date: str, show_name: str = None) -> List[Dict]:
        """获取指定日期范围内的剧集播出信息"""
        if not self.is_configured():
            return []
            
        # 这里可以实现更复杂的日期范围查询
        # 目前简化实现，返回空列表
        # 实际项目中可以通过TMDB的discover API或其他方式实现
        return []
    
    def convert_to_beijing_time(self, utc_time_str: str) -> str:
        """将UTC时间转换为北京时间"""
        try:
            # 解析UTC时间
            utc_time = datetime.fromisoformat(utc_time_str.replace('Z', '+00:00'))
            # 转换为北京时间（UTC+8）
            beijing_time = utc_time + timedelta(hours=8)
            return beijing_time.strftime('%Y-%m-%d')
        except Exception as e:
            logger.error(f"时间转换失败: {e}")
            return utc_time_str
