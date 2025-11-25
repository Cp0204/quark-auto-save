import os
import json
import sqlite3
import time
from datetime import datetime
from functools import wraps

# 数据库操作重试装饰器
def retry_on_locked(max_retries=3, base_delay=0.1):
    """数据库操作重试装饰器，用于处理 database is locked 错误"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e).lower():
                        last_exception = e
                        if attempt < max_retries - 1:
                            # 指数退避：延迟时间递增
                            delay = base_delay * (2 ** attempt)
                            time.sleep(delay)
                            continue
                    raise
                except Exception:
                    raise
            # 如果所有重试都失败，抛出最后一个异常
            if last_exception:
                raise last_exception
        return wrapper
    return decorator

class RecordDB:
    def __init__(self, db_path="config/data.db"):
        self.db_path = db_path
        self.conn = None
        self.init_db()
    
    def init_db(self):
        # 确保目录存在
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        # 创建数据库连接，设置超时时间为5秒
        self.conn = sqlite3.connect(
            self.db_path, 
            check_same_thread=False,
            timeout=5.0
        )
        cursor = self.conn.cursor()
        
        # 启用 WAL 模式，提高并发性能
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA synchronous=NORMAL')  # 平衡性能和安全性
        cursor.execute('PRAGMA busy_timeout=5000')  # 设置忙等待超时为5秒
        
        # 创建表，如果不存在
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS transfer_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transfer_time INTEGER NOT NULL,
            task_name TEXT NOT NULL,
            original_name TEXT NOT NULL,
            renamed_to TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            duration TEXT,
            resolution TEXT,
            modify_date INTEGER NOT NULL,
            file_id TEXT,
            file_type TEXT,
            save_path TEXT
        )
        ''')
        
        # 检查save_path字段是否存在，如果不存在则添加
        cursor.execute("PRAGMA table_info(transfer_records)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'save_path' not in columns:
            cursor.execute('ALTER TABLE transfer_records ADD COLUMN save_path TEXT')
            
        self.conn.commit()
    
    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
    
    @retry_on_locked(max_retries=3, base_delay=0.1)
    def add_record(self, task_name, original_name, renamed_to, file_size, modify_date, 
                  duration="", resolution="", file_id="", file_type="", save_path="", transfer_time=None):
        """添加一条转存记录"""
        cursor = self.conn.cursor()
        now_ms = int(time.time() * 1000) if transfer_time is None else transfer_time
        cursor.execute(
            "INSERT INTO transfer_records (transfer_time, task_name, original_name, renamed_to, file_size, "
            "duration, resolution, modify_date, file_id, file_type, save_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (now_ms, task_name, original_name, renamed_to, file_size, 
             duration, resolution, modify_date, file_id, file_type, save_path)
        )
        self.conn.commit()
        return cursor.lastrowid
    
    @retry_on_locked(max_retries=3, base_delay=0.1)
    def update_renamed_to(self, file_id, original_name, renamed_to, task_name="", save_path=""):
        """更新最近一条记录的renamed_to字段
        
        Args:
            file_id: 文件ID
            original_name: 原文件名
            renamed_to: 重命名后的文件名
            task_name: 任务名称，可选项，如提供则作为附加筛选条件
            save_path: 保存路径，可选项，如提供则同时更新保存路径
            
        Returns:
            更新的记录数量
        """
        cursor = self.conn.cursor()
        
        # 构建查询条件
        conditions = []
        params = []
        
        if file_id:
            conditions.append("file_id = ?")
            params.append(file_id)
        
        if original_name:
            conditions.append("original_name = ?")
            params.append(original_name)
        
        if task_name:
            conditions.append("task_name = ?")
            params.append(task_name)
        
        # 如果没有提供有效的识别条件，则返回0表示未更新任何记录
        if not conditions:
            return 0
            
        # 构建WHERE子句
        where_clause = " AND ".join(conditions)
        
        # 查找最近添加的匹配记录
        query = f"SELECT id FROM transfer_records WHERE {where_clause} ORDER BY transfer_time DESC LIMIT 1"
        cursor.execute(query, params)
        result = cursor.fetchone()
        
        if result:
            record_id = result[0]
            # 根据是否提供save_path决定更新哪些字段
            if save_path:
                cursor.execute(
                    "UPDATE transfer_records SET renamed_to = ?, save_path = ? WHERE id = ?",
                    (renamed_to, save_path, record_id)
                )
            else:
                cursor.execute(
                    "UPDATE transfer_records SET renamed_to = ? WHERE id = ?",
                    (renamed_to, record_id)
                )
            self.conn.commit()
            return cursor.rowcount
        
        return 0
    
    @retry_on_locked(max_retries=3, base_delay=0.1)
    def get_records(self, page=1, page_size=20, sort_by="transfer_time", order="desc", 
                   task_name_filter="", keyword_filter="", exclude_task_names=None,
                   task_name_list=None):
        """获取转存记录列表，支持分页、排序和筛选
        
        Args:
            page: 当前页码
            page_size: 每页记录数
            sort_by: 排序字段
            order: 排序方向（asc/desc）
            task_name_filter: 任务名称筛选条件（精确匹配）
            keyword_filter: 关键字筛选条件（模糊匹配任务名、转存为名称）
            exclude_task_names: 需要排除的任务名称列表
            task_name_list: 任务名称集合（包含多个任务名时使用IN筛选）
        """
        cursor = self.conn.cursor()
        offset = (page - 1) * page_size
        
        # 构建SQL查询
        valid_columns = ["transfer_time", "task_name", "original_name", "renamed_to", 
                         "file_size", "duration", "resolution", "modify_date", "save_path"]
        
        if sort_by not in valid_columns:
            sort_by = "transfer_time"
        
        order_direction = "DESC" if order.lower() == "desc" else "ASC"
        
        # 构建筛选条件
        where_clauses = []
        params = []
        
        if task_name_filter:
            where_clauses.append("task_name = ?")
            params.append(task_name_filter)
        
        if keyword_filter:
            where_clauses.append("(task_name LIKE ? OR renamed_to LIKE ?)")
            params.append(f"%{keyword_filter}%")
            params.append(f"%{keyword_filter}%")
        
        if exclude_task_names:
            where_clauses.append("task_name NOT IN ({})".format(",".join(["?" for _ in exclude_task_names])))
            params.extend(exclude_task_names)
        
        if task_name_list:
            placeholders = ",".join(["?" for _ in task_name_list])
            where_clauses.append(f"task_name IN ({placeholders})")
            params.extend(task_name_list)
        
        where_clause = " AND ".join(where_clauses)
        where_sql = f"WHERE {where_clause}" if where_clause else ""
        
        # 获取筛选后的总记录数
        count_sql = f"SELECT COUNT(*) FROM transfer_records {where_sql}"
        cursor.execute(count_sql, params)
        total_records = cursor.fetchone()[0]
        
        # 获取分页数据
        if sort_by in ["task_name", "original_name", "renamed_to"]:
            # 对于需要拼音排序的字段，先获取所有数据然后在Python中进行拼音排序
            query_sql = f"SELECT * FROM transfer_records {where_sql}"
            cursor.execute(query_sql, params)
            all_records = cursor.fetchall()

            # 使用拼音排序
            from utils.pinyin_sort import get_filename_pinyin_sort_key

            # 根据排序字段选择对应的索引
            field_index_map = {
                "task_name": 2,      # task_name字段索引
                "original_name": 3,  # original_name字段索引
                "renamed_to": 4      # renamed_to字段索引
            }
            field_index = field_index_map[sort_by]

            sorted_records = sorted(all_records, key=lambda x: get_filename_pinyin_sort_key(x[field_index]), reverse=(order_direction == "DESC"))

            # 手动分页
            start_idx = offset
            end_idx = offset + page_size
            records = sorted_records[start_idx:end_idx]
        else:
            # 其他字段使用SQL排序
            query_sql = f"SELECT * FROM transfer_records {where_sql} ORDER BY {sort_by} {order_direction} LIMIT ? OFFSET ?"
            cursor.execute(query_sql, params + [page_size, offset])
            records = cursor.fetchall()
        
        # 将结果转换为字典列表
        columns = [col[0] for col in cursor.description]
        result = []
        for row in records:
            record = dict(zip(columns, row))
            result.append(record)
        
        # 计算总页数
        total_pages = (total_records + page_size - 1) // page_size if total_records > 0 else 1
        
        return {
            "records": result,
            "pagination": {
                "total_records": total_records,
                "total_pages": total_pages,
                "current_page": page,
                "page_size": page_size
            }
        }
    
    @retry_on_locked(max_retries=3, base_delay=0.1)
    def get_record_by_id(self, record_id):
        """根据ID获取特定记录"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM transfer_records WHERE id = ?", (record_id,))
        record = cursor.fetchone()
        
        if record:
            columns = [col[0] for col in cursor.description]
            return dict(zip(columns, record))
        return None
    
    @retry_on_locked(max_retries=3, base_delay=0.1)
    def delete_record(self, record_id):
        """删除特定记录"""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM transfer_records WHERE id = ?", (record_id,))
        self.conn.commit()
        return cursor.rowcount
    
    @retry_on_locked(max_retries=3, base_delay=0.1)
    def get_records_by_save_path(self, save_path, include_subpaths=False):
        """根据保存路径查询记录
        
        Args:
            save_path: 要查询的保存路径
            include_subpaths: 是否包含子路径下的文件
            
        Returns:
            匹配的记录列表
        """
        cursor = self.conn.cursor()
        
        if include_subpaths:
            # 如果包含子路径，使用LIKE查询
            query = "SELECT * FROM transfer_records WHERE save_path LIKE ? ORDER BY transfer_time DESC"
            cursor.execute(query, [f"{save_path}%"])
        else:
            # 精确匹配路径
            query = "SELECT * FROM transfer_records WHERE save_path = ? ORDER BY transfer_time DESC"
            cursor.execute(query, [save_path])
            
        records = cursor.fetchall()
        
        # 将结果转换为字典列表
        if records:
            columns = [col[0] for col in cursor.description]
            return [dict(zip(columns, row)) for row in records]
        return []


class CalendarDB:
    """追剧日历本地缓存数据库：剧、季、集信息本地化存储"""
    def __init__(self, db_path="config/data.db"):
        self.db_path = db_path
        self.conn = None
        self.init_db()

    def init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        # 创建数据库连接，设置超时时间为5秒
        self.conn = sqlite3.connect(
            self.db_path, 
            check_same_thread=False,
            timeout=5.0
        )
        cursor = self.conn.cursor()
        
        # 启用 WAL 模式，提高并发性能
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA synchronous=NORMAL')  # 平衡性能和安全性
        cursor.execute('PRAGMA busy_timeout=5000')  # 设置忙等待超时为5秒

        # shows
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS shows (
            tmdb_id INTEGER PRIMARY KEY,
            name TEXT,
            year TEXT,
            status TEXT,
            poster_local_path TEXT,
            latest_season_number INTEGER,
            last_refreshed_at INTEGER,
            bound_task_names TEXT,
            content_type TEXT
        )
        ''')
        
        # 检查 content_type 字段是否存在，如果不存在则添加
        cursor.execute("PRAGMA table_info(shows)")
        columns = [column[1] for column in cursor.fetchall()]
        if 'content_type' not in columns:
            cursor.execute('ALTER TABLE shows ADD COLUMN content_type TEXT')
        
        # 检查 is_custom_poster 字段是否存在，如果不存在则添加
        if 'is_custom_poster' not in columns:
            cursor.execute('ALTER TABLE shows ADD COLUMN is_custom_poster INTEGER DEFAULT 0')

        # seasons
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS seasons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tmdb_id INTEGER,
            season_number INTEGER,
            season_name TEXT,
            episode_count INTEGER,
            refresh_url TEXT,
            UNIQUE (tmdb_id, season_number)
        )
        ''')

        # episodes
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS episodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tmdb_id INTEGER,
            season_number INTEGER,
            episode_number INTEGER,
            name TEXT,
            overview TEXT,
            air_date TEXT,
            runtime INTEGER,
            type TEXT,
            updated_at INTEGER,
            UNIQUE (tmdb_id, season_number, episode_number)
        )
        ''')

        # season_metrics（缓存：每季的三项计数）
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS season_metrics (
            tmdb_id INTEGER,
            season_number INTEGER,
            transferred_count INTEGER,
            aired_count INTEGER,
            total_count INTEGER,
            progress_pct INTEGER,
            updated_at INTEGER,
            PRIMARY KEY (tmdb_id, season_number)
        )
        ''')
        # 迁移：如缺少 progress_pct 列则新增
        try:
            cursor.execute('PRAGMA table_info(season_metrics)')
            cols = [c[1] for c in cursor.fetchall()]
            if 'progress_pct' not in cols:
                cursor.execute('ALTER TABLE season_metrics ADD COLUMN progress_pct INTEGER')
        except Exception:
            pass

        # task_metrics（缓存：每任务的转存进度）
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS task_metrics (
            task_name TEXT PRIMARY KEY,
            tmdb_id INTEGER,
            season_number INTEGER,
            transferred_count INTEGER,
            progress_pct INTEGER,
            updated_at INTEGER
        )
        ''')
        # 迁移：如缺少 progress_pct 列则新增
        try:
            cursor.execute('PRAGMA table_info(task_metrics)')
            cols = [c[1] for c in cursor.fetchall()]
            if 'progress_pct' not in cols:
                cursor.execute('ALTER TABLE task_metrics ADD COLUMN progress_pct INTEGER')
        except Exception:
            pass

        self.conn.commit()

    def close(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass

    # shows
    @retry_on_locked(max_retries=3, base_delay=0.1)
    def upsert_show(self, tmdb_id:int, name:str, year:str, status:str, poster_local_path:str, latest_season_number:int, last_refreshed_at:int=0, bound_task_names:str="", content_type:str="", is_custom_poster:int=0):
        cursor = self.conn.cursor()
        cursor.execute('''
        INSERT INTO shows (tmdb_id, name, year, status, poster_local_path, latest_season_number, last_refreshed_at, bound_task_names, content_type, is_custom_poster)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tmdb_id) DO UPDATE SET
            name=excluded.name,
            year=excluded.year,
            status=excluded.status,
            poster_local_path=excluded.poster_local_path,
            latest_season_number=excluded.latest_season_number,
            last_refreshed_at=excluded.last_refreshed_at,
            bound_task_names=excluded.bound_task_names,
            content_type=excluded.content_type,
            is_custom_poster=excluded.is_custom_poster
        ''', (tmdb_id, name, year, status, poster_local_path, latest_season_number, last_refreshed_at, bound_task_names, content_type, is_custom_poster))
        self.conn.commit()

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def bind_task_to_show(self, tmdb_id:int, task_name:str):
        """绑定任务到节目，在 bound_task_names 字段中记录任务名"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT bound_task_names FROM shows WHERE tmdb_id=?', (tmdb_id,))
        row = cursor.fetchone()
        if not row:
            return False
        
        current_bound_tasks = row[0] or ""
        task_list = current_bound_tasks.split(',') if current_bound_tasks else []
        
        # 如果任务名不在绑定列表中，则添加
        if task_name not in task_list:
            task_list.append(task_name)
            new_bound_tasks = ','.join(task_list)
            cursor.execute('UPDATE shows SET bound_task_names=? WHERE tmdb_id=?', (new_bound_tasks, tmdb_id))
            self.conn.commit()
            return True
        return False

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def unbind_task_from_show(self, tmdb_id:int, task_name:str):
        """从节目解绑任务，更新 bound_task_names 列表"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT bound_task_names FROM shows WHERE tmdb_id=?', (tmdb_id,))
        row = cursor.fetchone()
        if not row:
            return False
        current_bound_tasks = row[0] or ""
        task_list = [t for t in (current_bound_tasks.split(',') if current_bound_tasks else []) if t]
        if task_name in task_list:
            task_list.remove(task_name)
            new_bound_tasks = ','.join(task_list)
            cursor.execute('UPDATE shows SET bound_task_names=? WHERE tmdb_id=?', (new_bound_tasks, tmdb_id))
            self.conn.commit()
            return True
        return False

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def get_bound_tasks_for_show(self, tmdb_id:int):
        """获取绑定到指定节目的任务列表"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT bound_task_names FROM shows WHERE tmdb_id=?', (tmdb_id,))
        row = cursor.fetchone()
        if not row or not row[0]:
            return []
        return row[0].split(',')

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def get_show_by_task_name(self, task_name:str):
        """根据任务名查找绑定的节目"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM shows WHERE bound_task_names LIKE ?', (f'%{task_name}%',))
        row = cursor.fetchone()
        if not row:
            return None
        columns = [c[0] for c in cursor.description]
        return dict(zip(columns, row))

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def get_show(self, tmdb_id:int):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM shows WHERE tmdb_id=?', (tmdb_id,))
        row = cursor.fetchone()
        if not row:
            return None
        columns = [c[0] for c in cursor.description]
        return dict(zip(columns, row))

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def delete_show(self, tmdb_id:int):
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM episodes WHERE tmdb_id=?', (tmdb_id,))
        cursor.execute('DELETE FROM seasons WHERE tmdb_id=?', (tmdb_id,))
        cursor.execute('DELETE FROM shows WHERE tmdb_id=?', (tmdb_id,))
        self.conn.commit()

    # seasons
    @retry_on_locked(max_retries=3, base_delay=0.1)
    def upsert_season(self, tmdb_id:int, season_number:int, episode_count:int, refresh_url:str, season_name:str=""):
        cursor = self.conn.cursor()
        # 迁移：如缺少 season_name 字段则补充
        try:
            cursor.execute("PRAGMA table_info(seasons)")
            columns = [column[1] for column in cursor.fetchall()]
            if 'season_name' not in columns:
                cursor.execute('ALTER TABLE seasons ADD COLUMN season_name TEXT')
        except Exception:
            pass
        cursor.execute('''
        INSERT INTO seasons (tmdb_id, season_number, season_name, episode_count, refresh_url)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(tmdb_id, season_number) DO UPDATE SET
            season_name=excluded.season_name,
            episode_count=excluded.episode_count,
            refresh_url=excluded.refresh_url
        ''', (tmdb_id, season_number, season_name, episode_count, refresh_url))
        self.conn.commit()

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def get_season(self, tmdb_id:int, season_number:int):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM seasons WHERE tmdb_id=? AND season_number=?', (tmdb_id, season_number))
        row = cursor.fetchone()
        if not row:
            return None
        columns = [c[0] for c in cursor.description]
        return dict(zip(columns, row))

    # episodes
    @retry_on_locked(max_retries=3, base_delay=0.1)
    def upsert_episode(self, tmdb_id:int, season_number:int, episode_number:int, name:str, overview:str, air_date:str, runtime, ep_type:str, updated_at:int):
        cursor = self.conn.cursor()
        cursor.execute('''
        INSERT INTO episodes (tmdb_id, season_number, episode_number, name, overview, air_date, runtime, type, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tmdb_id, season_number, episode_number) DO UPDATE SET
            name=excluded.name,
            overview=excluded.overview,
            air_date=excluded.air_date,
            runtime=COALESCE(episodes.runtime, excluded.runtime),
            type=COALESCE(excluded.type, episodes.type),
            updated_at=excluded.updated_at
        ''', (tmdb_id, season_number, episode_number, name, overview, air_date, runtime, ep_type, updated_at))
        self.conn.commit()

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def list_latest_season_episodes(self, tmdb_id:int, latest_season:int):
        cursor = self.conn.cursor()
        cursor.execute('''
        SELECT episode_number, name, overview, air_date, runtime, type
        FROM episodes
        WHERE tmdb_id=? AND season_number=?
        ORDER BY episode_number ASC
        ''', (tmdb_id, latest_season))
        rows = cursor.fetchall()
        return [
            {
                'episode_number': r[0],
                'name': r[1],
                'overview': r[2],
                'air_date': r[3],
                'runtime': r[4],
                'type': r[5],
            } for r in rows
        ]

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def list_all_latest_episodes(self):
        """返回所有已知剧目的最新季的所有集（扁平列表，供前端汇总显示）"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT tmdb_id, name, year, status, poster_local_path, latest_season_number FROM shows')
        shows = cursor.fetchall()
        result = []
        for tmdb_id, name, year, status, poster_local_path, latest_season in shows:
            eps = self.list_latest_season_episodes(tmdb_id, latest_season)
            for e in eps:
                item = {
                    'tmdb_id': tmdb_id,
                    'show_name': name,
                    'year': year,
                    'status': status,
                    'poster_local_path': poster_local_path,
                    'season_number': latest_season,
                    **e,
                }
                result.append(item)
        return result

    # --------- 孤儿数据清理（seasons / episodes / season_metrics / task_metrics） ---------
    @retry_on_locked(max_retries=3, base_delay=0.1)
    def cleanup_orphan_data(self, valid_task_pairs, valid_task_names):
        """清理不再与任何任务对应的数据

        参数:
            valid_task_pairs: 由当前任务映射得到的 (tmdb_id, season_number) 元组列表
            valid_task_names: 当前存在的任务名列表

        规则:
        - task_metrics: 删除 task_name 不在当前任务列表中的记录
        - seasons/episodes: 仅保留出现在 valid_task_pairs 内的季与对应所有集；其余删除
        - season_metrics: 仅保留出现在 valid_task_pairs 内的记录；其余删除
        """
        try:
            cursor = self.conn.cursor()

            # 1) 清理 task_metrics（孤立任务）
            try:
                if not valid_task_names:
                    cursor.execute('DELETE FROM task_metrics')
                else:
                    placeholders = ','.join(['?'] * len(valid_task_names))
                    cursor.execute(f"DELETE FROM task_metrics WHERE task_name NOT IN ({placeholders})", valid_task_names)
            except Exception:
                pass

            # 2) 清理 seasons/episodes 与 season_metrics（仅保留任务声明的 (tmdb_id, season)）
            # 组装参数列表
            pairs = [(int(tid), int(sn)) for (tid, sn) in (valid_task_pairs or []) if tid and sn]

            if not pairs:
                # 没有任何有效任务映射：清空 seasons/episodes/season_metrics
                try:
                    cursor.execute('DELETE FROM episodes')
                    cursor.execute('DELETE FROM seasons')
                    cursor.execute('DELETE FROM season_metrics')
                except Exception:
                    pass
            else:
                # 批量删除不在 pairs 中的记录
                # 为 (tmdb_id, season_number) 对构造占位符
                tuple_placeholders = ','.join(['(?, ?)'] * len(pairs))
                flat_params = []
                for tid, sn in pairs:
                    flat_params.extend([tid, sn])

                # episodes 先删（避免残留）
                try:
                    cursor.execute(
                        f'''DELETE FROM episodes
                            WHERE (tmdb_id, season_number) NOT IN ({tuple_placeholders})''',
                        flat_params
                    )
                except Exception:
                    pass

                # seasons 再删
                try:
                    cursor.execute(
                        f'''DELETE FROM seasons
                            WHERE (tmdb_id, season_number) NOT IN ({tuple_placeholders})''',
                        flat_params
                    )
                except Exception:
                    pass

                # season_metrics 最后删
                try:
                    cursor.execute(
                        f'''DELETE FROM season_metrics
                            WHERE (tmdb_id, season_number) NOT IN ({tuple_placeholders})''',
                        flat_params
                    )
                except Exception:
                    pass

            self.conn.commit()
            return True
        except Exception:
            # 静默失败，避免影响核心流程
            return False

    # 内容类型管理方法
    @retry_on_locked(max_retries=3, base_delay=0.1)
    def update_show_content_type(self, tmdb_id:int, content_type:str):
        """更新节目的内容类型"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE shows SET content_type=? WHERE tmdb_id=?', (content_type, tmdb_id))
        self.conn.commit()
        return cursor.rowcount > 0

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def get_show_content_type(self, tmdb_id:int):
        """获取节目的内容类型"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT content_type FROM shows WHERE tmdb_id=?', (tmdb_id,))
        row = cursor.fetchone()
        return row[0] if row else None

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def get_shows_by_content_type(self, content_type:str):
        """根据内容类型获取节目列表"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM shows WHERE content_type=? ORDER BY name', (content_type,))
        rows = cursor.fetchall()
        if not rows:
            return []
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def get_all_content_types(self):
        """获取所有已使用的内容类型"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT DISTINCT content_type FROM shows WHERE content_type IS NOT NULL AND content_type != "" ORDER BY content_type')
        rows = cursor.fetchall()
        return [row[0] for row in rows]

    def bind_task_and_content_type(self, tmdb_id:int, task_name:str, content_type:str):
        """绑定任务到节目并设置内容类型"""
        # 先绑定任务
        self.bind_task_to_show(tmdb_id, task_name)
        # 再更新内容类型
        self.update_show_content_type(tmdb_id, content_type)

    # --------- 统计缓存（season_metrics / task_metrics） ---------
    @retry_on_locked(max_retries=3, base_delay=0.1)
    def upsert_season_metrics(self, tmdb_id:int, season_number:int, transferred_count, aired_count, total_count, progress_pct, updated_at:int):
        """写入/更新季级指标缓存。允许某些字段传 None，保持原值不变。"""
        cursor = self.conn.cursor()
        cursor.execute('''
        INSERT INTO season_metrics (tmdb_id, season_number, transferred_count, aired_count, total_count, progress_pct, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tmdb_id, season_number) DO UPDATE SET
            transferred_count=COALESCE(excluded.transferred_count, transferred_count),
            aired_count=COALESCE(excluded.aired_count, aired_count),
            total_count=COALESCE(excluded.total_count, total_count),
            progress_pct=COALESCE(excluded.progress_pct, progress_pct),
            updated_at=MAX(COALESCE(excluded.updated_at, 0), COALESCE(updated_at, 0))
        ''', (tmdb_id, season_number, transferred_count, aired_count, total_count, progress_pct, updated_at))
        self.conn.commit()

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def get_season_metrics(self, tmdb_id:int, season_number:int):
        cursor = self.conn.cursor()
        cursor.execute('SELECT transferred_count, aired_count, total_count, progress_pct, updated_at FROM season_metrics WHERE tmdb_id=? AND season_number=?', (tmdb_id, season_number))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            'transferred_count': row[0],
            'aired_count': row[1],
            'total_count': row[2],
            'progress_pct': row[3],
            'updated_at': row[4],
        }

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def upsert_task_metrics(self, task_name:str, tmdb_id:int, season_number:int, transferred_count:int, progress_pct, updated_at:int):
        cursor = self.conn.cursor()
        cursor.execute('''
        INSERT INTO task_metrics (task_name, tmdb_id, season_number, transferred_count, progress_pct, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(task_name) DO UPDATE SET
            tmdb_id=excluded.tmdb_id,
            season_number=excluded.season_number,
            transferred_count=excluded.transferred_count,
            progress_pct=COALESCE(excluded.progress_pct, progress_pct),
            updated_at=excluded.updated_at
        ''', (task_name, tmdb_id, season_number, transferred_count, progress_pct, updated_at))
        self.conn.commit()

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def get_task_metrics(self, task_name:str):
        """获取任务的进度指标"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT progress_pct FROM task_metrics WHERE task_name=?', (task_name,))
        row = cursor.fetchone()
        if not row:
            return None
        return {
            'progress_pct': row[0],
        }

    # --------- 扩展：管理季与集清理/更新工具方法 ---------
    @retry_on_locked(max_retries=3, base_delay=0.1)
    def purge_other_seasons(self, tmdb_id: int, keep_season_number: int):
        """清除除指定季之外的所有季与对应集数据"""
        cursor = self.conn.cursor()
        # 删除其他季的 episodes
        cursor.execute('DELETE FROM episodes WHERE tmdb_id=? AND season_number != ?', (tmdb_id, keep_season_number))
        # 删除其他季的 seasons 行
        cursor.execute('DELETE FROM seasons WHERE tmdb_id=? AND season_number != ?', (tmdb_id, keep_season_number))
        self.conn.commit()

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def delete_season(self, tmdb_id: int, season_number: int):
        """删除指定季及其所有集数据"""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM episodes WHERE tmdb_id=? AND season_number=?', (tmdb_id, season_number))
        cursor.execute('DELETE FROM seasons WHERE tmdb_id=? AND season_number=?', (tmdb_id, season_number))
        self.conn.commit()

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def prune_season_episodes_not_in(self, tmdb_id: int, season_number: int, valid_episode_numbers):
        """删除本地该季中不在 valid_episode_numbers 列表内的所有集
        
        参数:
            tmdb_id: 节目 TMDB ID
            season_number: 季编号
            valid_episode_numbers: 允许保留的集号列表
        """
        try:
            cursor = self.conn.cursor()
            # 当 TMDB 返回空集时，表示该季应无集，直接清空该季的 episodes
            if not valid_episode_numbers:
                cursor.execute('DELETE FROM episodes WHERE tmdb_id=? AND season_number=?', (tmdb_id, season_number))
                self.conn.commit()
                return
            # 动态占位符
            placeholders = ','.join(['?'] * len(valid_episode_numbers))
            params = [tmdb_id, season_number] + [int(x) for x in valid_episode_numbers]
            cursor.execute(
                f'SELECT episode_number FROM episodes WHERE tmdb_id=? AND season_number=? AND episode_number NOT IN ({placeholders})',
                params
            )
            to_delete = [row[0] for row in (cursor.fetchall() or [])]
            if to_delete:
                placeholders_del = ','.join(['?'] * len(to_delete))
                params_del = [tmdb_id, season_number] + to_delete
                cursor.execute(
                    f'DELETE FROM episodes WHERE tmdb_id=? AND season_number=? AND episode_number IN ({placeholders_del})',
                    params_del
                )
            self.conn.commit()
        except Exception:
            # 静默失败，避免影响主流程
            pass

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def update_show_latest_season_number(self, tmdb_id: int, latest_season_number: int):
        """更新 shows.latest_season_number"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE shows SET latest_season_number=? WHERE tmdb_id=?', (latest_season_number, tmdb_id))
        self.conn.commit()

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def update_show_poster(self, tmdb_id: int, poster_local_path: str, is_custom_poster: int = 0):
        """更新节目的海报路径和自定义标记"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE shows SET poster_local_path=?, is_custom_poster=? WHERE tmdb_id=?', (poster_local_path, is_custom_poster, tmdb_id))
        self.conn.commit()

    @retry_on_locked(max_retries=3, base_delay=0.1)
    def get_all_shows(self):
        """获取所有节目"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM shows')
        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
