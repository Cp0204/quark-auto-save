#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
任务信息提取模块
用于从任务中提取剧名、年份、类型和进度信息
"""

import re
import os
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class TaskExtractor:
    def __init__(self):
        # 剧集编号提取模式
        self.episode_patterns = [
            r'S(\d{1,2})E(\d{1,3})',  # S01E01
            r'E(\d{1,3})',  # E01
            r'第(\d{1,3})集',  # 第1集
            r'第(\d{1,3})期',  # 第1期
            r'(\d{1,3})集',  # 1集
            r'(\d{1,3})期',  # 1期
        ]
        
        # 日期提取模式
        self.date_patterns = [
            r'(\d{4})-(\d{1,2})-(\d{1,2})',  # 2025-01-01
            r'(\d{4})/(\d{1,2})/(\d{1,2})',  # 2025/01/01
            r'(\d{4})\.(\d{1,2})\.(\d{1,2})',  # 2025.01.01
        ]
    
    def extract_show_info_from_path(self, save_path: str) -> Dict:
        """
        从保存路径中提取剧名和年份信息
        
        Args:
            save_path: 任务的保存路径
            
        Returns:
            包含剧名和年份的字典
        """
        if not save_path:
            return {'show_name': '', 'year': '', 'type': 'other'}
        
        # 分割路径
        path_parts = save_path.split('/')
        
        show_name = ''
        year = ''
        content_type = 'other'
        
        # 查找包含年份信息的路径部分
        for part in path_parts:
            # 匹配年份格式：剧名 (年份)、剧名(年份)、剧名（年份）、剧名 年份
            year_patterns = [
                r'^(.+?)\s*[\(（](\d{4})[\)）]$',  # 剧名 (2025) 或 剧名（2025）
                r'^(.+?)\s*(\d{4})$',  # 剧名 2025
                r'^(.+?)\s*\((\d{4})\)$',  # 剧名(2025)
            ]
            
            for pattern in year_patterns:
                match = re.match(pattern, part.strip())
                if match:
                    show_name = match.group(1).strip()
                    year = match.group(2)
                    break
            
            if show_name and year:
                break
        
        # 如果没有找到年份信息，尝试从任务名称中提取
        if not show_name:
            show_name = self.extract_show_name_from_taskname(path_parts[-1] if path_parts else '')
        
        # 判断内容类型
        content_type = self.determine_content_type(save_path)
        
        return {
            'show_name': show_name,
            'year': year,
            'type': content_type
        }
    
    def extract_show_name_from_taskname(self, task_name: str) -> str:
        """
        从任务名称中提取纯剧名（去除季信息等）
        
        Args:
            task_name: 任务名称
            
        Returns:
            提取的剧名
        """
        if not task_name:
            return ''
        
        # 移除季信息
        season_patterns = [
            r'^(.+?)\s*[Ss](\d{1,2})',  # 剧名 S01
            r'^(.+?)\s*第(\d{1,2})季',  # 剧名 第1季
            r'^(.+?)\s*Season\s*(\d{1,2})',  # 剧名 Season 1
            r'^(.+?)\s*第([一二三四五六七八九十]+)季',  # 剧名 第一季
        ]
        
        for pattern in season_patterns:
            match = re.match(pattern, task_name)
            if match:
                # 提取到季信息前的名称后，进一步清理尾部多余分隔符/空白
                name = match.group(1).strip()
                # 去除名称结尾处常见分隔符（空格、横杠、下划线、点、破折号、间隔点、顿号、冒号等）
                name = re.sub(r"[\s\-_.—·、:：]+$", "", name)
                return name
        
        # 如果没有季信息，直接返回任务名称
        # 未匹配到季信息时，直接清理尾部多余分隔符/空白后返回
        cleaned = task_name.strip()
        cleaned = re.sub(r"[\s\-_.—·、:：]+$", "", cleaned)
        return cleaned
    
    def determine_content_type(self, save_path: str) -> str:
        """
        根据保存路径判断内容类型
        
        Args:
            save_path: 保存路径
            
        Returns:
            内容类型：tv（剧集）、anime（动画）、variety（综艺）、documentary（纪录片）、other（其他）
        """
        if not save_path:
            return 'other'
        
        path_lower = save_path.lower()
        
        # 根据路径关键词判断类型
        if any(keyword in path_lower for keyword in ['剧集', '电视剧', '电视', '电视节目', '连续剧', '影集', 'tv', 'drama']):
            return 'tv'
        elif any(keyword in path_lower for keyword in ['动画', '动漫', '动画片', '卡通片', '卡通', 'anime', 'cartoon']):
            return 'anime'
        elif any(keyword in path_lower for keyword in ['综艺', '真人秀', '综艺节目', '娱乐节目', 'variety', 'show']):
            return 'variety'
        elif any(keyword in path_lower for keyword in ['纪录片', '记录片', 'documentary', 'doc']):
            return 'documentary'
        else:
            return 'other'
    
    def extract_progress_from_latest_file(self, latest_file: str) -> Dict:
        """
        从最近转存文件中提取进度信息
        
        Args:
            latest_file: 最近转存文件信息（如 S02E24 或 2025-08-30）
            
        Returns:
            包含进度信息的字典
        """
        if not latest_file:
            return {'episode_number': None, 'air_date': None, 'progress_type': 'unknown'}
        
        # 尝试提取集数信息
        for pattern in self.episode_patterns:
            match = re.search(pattern, latest_file)
            if match:
                if 'S' in pattern and 'E' in pattern:
                    # S01E01 格式
                    season = match.group(1)
                    episode = match.group(2)
                    return {
                        'episode_number': int(episode),
                        'season_number': int(season),
                        'progress_type': 'episode'
                    }
                else:
                    # E01 或其他格式
                    episode = match.group(1)
                    return {
                        'episode_number': int(episode),
                        'season_number': None,
                        'progress_type': 'episode'
                    }
        
        # 尝试提取日期信息
        for pattern in self.date_patterns:
            match = re.search(pattern, latest_file)
            if match:
                year, month, day = match.groups()
                date_str = f"{year}-{month.zfill(2)}-{day.zfill(2)}"
                return {
                    'episode_number': None,
                    'season_number': None,
                    'air_date': date_str,
                    'progress_type': 'date'
                }
        
        return {
            'episode_number': None,
            'season_number': None,
            'air_date': None,
            'progress_type': 'unknown'
        }
    
    def extract_all_tasks_info(self, tasks: List[Dict], task_latest_files: Dict) -> List[Dict]:
        """
        提取所有任务的信息
        
        Args:
            tasks: 任务列表
            task_latest_files: 任务最近转存文件信息
            
        Returns:
            包含所有任务信息的列表
        """
        logging.debug("TaskExtractor.extract_all_tasks_info 开始")
        logging.debug(f"tasks数量: {len(tasks)}")
        logging.debug(f"task_latest_files数量: {len(task_latest_files)}")
        
        tasks_info = []
        
        for i, task in enumerate(tasks):
            try:
                logging.debug(f"处理第{i+1}个任务: {task.get('taskname', '')}")
                
                task_name = task.get('taskname', '')
                save_path = task.get('savepath', '')
                latest_file = task_latest_files.get(task_name, '')
                
                logging.debug(f"task_name: {task_name}")
                logging.debug(f"save_path: {save_path}")
                logging.debug(f"latest_file: {latest_file}")
                
                # 提取基本信息
                show_info = self.extract_show_info_from_path(save_path)
                logging.debug(f"show_info: {show_info}")
                
                # 提取进度信息
                progress_info = self.extract_progress_from_latest_file(latest_file)
                logging.debug(f"progress_info: {progress_info}")
                
                # 优先使用任务显式类型（配置或提取出的），否则回退到路径判断
                explicit_type = None
                try:
                    explicit_type = (task.get('calendar_info') or {}).get('extracted', {}).get('content_type')
                except Exception:
                    explicit_type = None
                if not explicit_type:
                    explicit_type = task.get('content_type')
                final_type = (explicit_type or show_info['type'] or 'other')

                # 合并信息
                task_info = {
                    'task_name': task_name,
                    'save_path': save_path,
                    'show_name': show_info['show_name'],
                    'year': show_info['year'],
                    'content_type': final_type,
                    'latest_file': latest_file,
                    'episode_number': progress_info.get('episode_number'),
                    'season_number': progress_info.get('season_number'),
                    'air_date': progress_info.get('air_date'),
                    'progress_type': progress_info.get('progress_type')
                }
                
                logging.debug(f"task_info: {task_info}")
                tasks_info.append(task_info)
                
            except Exception as e:
                logging.debug(f"处理任务 {i+1} 时出错: {e}")
                import traceback
                traceback.print_exc()
                continue
        
        logging.debug(f"TaskExtractor.extract_all_tasks_info 完成，返回任务数量: {len(tasks_info)}")
        return tasks_info
    
    def get_content_type_display_name(self, content_type: str) -> str:
        """
        获取内容类型的显示名称
        
        Args:
            content_type: 内容类型代码
            
        Returns:
            显示名称
        """
        type_names = {
            'tv': '剧集',
            'anime': '动画',
            'variety': '综艺',
            'documentary': '纪录片',
            'other': '其他'
        }
        
        return type_names.get(content_type, '其他')
    
    def get_content_types_with_content(self, tasks_info: List[Dict]) -> List[str]:
        """
        获取有内容的任务类型列表
        
        Args:
            tasks_info: 任务信息列表
            
        Returns:
            有内容的类型列表
        """
        types = set()
        for task_info in tasks_info:
            if task_info['show_name']:  # 只统计有剧名的任务
                types.add(task_info['content_type'])
        
        return sorted(list(types))
