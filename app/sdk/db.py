import os
import json
import sqlite3
import time
from datetime import datetime

class RecordDB:
    def __init__(self, db_path="config/data.db"):
        self.db_path = db_path
        self.conn = None
        self.init_db()
    
    def init_db(self):
        # 确保目录存在
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        # 创建数据库连接
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = self.conn.cursor()
        
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
            self.conn.close()
    
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
    
    def get_records(self, page=1, page_size=20, sort_by="transfer_time", order="desc", 
                   task_name_filter="", keyword_filter="", exclude_task_names=None):
        """获取转存记录列表，支持分页、排序和筛选
        
        Args:
            page: 当前页码
            page_size: 每页记录数
            sort_by: 排序字段
            order: 排序方向（asc/desc）
            task_name_filter: 任务名称筛选条件（精确匹配）
            keyword_filter: 关键字筛选条件（模糊匹配任务名、转存为名称）
            exclude_task_names: 需要排除的任务名称列表
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
    
    def get_record_by_id(self, record_id):
        """根据ID获取特定记录"""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM transfer_records WHERE id = ?", (record_id,))
        record = cursor.fetchone()
        
        if record:
            columns = [col[0] for col in cursor.description]
            return dict(zip(columns, record))
        return None
    
    def delete_record(self, record_id):
        """删除特定记录"""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM transfer_records WHERE id = ?", (record_id,))
        self.conn.commit()
        return cursor.rowcount
        
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
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        cursor = self.conn.cursor()

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

        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()

    # shows
    def upsert_show(self, tmdb_id:int, name:str, year:str, status:str, poster_local_path:str, latest_season_number:int, last_refreshed_at:int=0, bound_task_names:str="", content_type:str=""):
        cursor = self.conn.cursor()
        cursor.execute('''
        INSERT INTO shows (tmdb_id, name, year, status, poster_local_path, latest_season_number, last_refreshed_at, bound_task_names, content_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(tmdb_id) DO UPDATE SET
            name=excluded.name,
            year=excluded.year,
            status=excluded.status,
            poster_local_path=excluded.poster_local_path,
            latest_season_number=excluded.latest_season_number,
            last_refreshed_at=excluded.last_refreshed_at,
            bound_task_names=excluded.bound_task_names,
            content_type=excluded.content_type
        ''', (tmdb_id, name, year, status, poster_local_path, latest_season_number, last_refreshed_at, bound_task_names, content_type))
        self.conn.commit()

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

    def get_bound_tasks_for_show(self, tmdb_id:int):
        """获取绑定到指定节目的任务列表"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT bound_task_names FROM shows WHERE tmdb_id=?', (tmdb_id,))
        row = cursor.fetchone()
        if not row or not row[0]:
            return []
        return row[0].split(',')

    def get_show_by_task_name(self, task_name:str):
        """根据任务名查找绑定的节目"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM shows WHERE bound_task_names LIKE ?', (f'%{task_name}%',))
        row = cursor.fetchone()
        if not row:
            return None
        columns = [c[0] for c in cursor.description]
        return dict(zip(columns, row))

    def get_show(self, tmdb_id:int):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM shows WHERE tmdb_id=?', (tmdb_id,))
        row = cursor.fetchone()
        if not row:
            return None
        columns = [c[0] for c in cursor.description]
        return dict(zip(columns, row))

    def delete_show(self, tmdb_id:int):
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM episodes WHERE tmdb_id=?', (tmdb_id,))
        cursor.execute('DELETE FROM seasons WHERE tmdb_id=?', (tmdb_id,))
        cursor.execute('DELETE FROM shows WHERE tmdb_id=?', (tmdb_id,))
        self.conn.commit()

    # seasons
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

    def get_season(self, tmdb_id:int, season_number:int):
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM seasons WHERE tmdb_id=? AND season_number=?', (tmdb_id, season_number))
        row = cursor.fetchone()
        if not row:
            return None
        columns = [c[0] for c in cursor.description]
        return dict(zip(columns, row))

    # episodes
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

    # 内容类型管理方法
    def update_show_content_type(self, tmdb_id:int, content_type:str):
        """更新节目的内容类型"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE shows SET content_type=? WHERE tmdb_id=?', (content_type, tmdb_id))
        self.conn.commit()
        return cursor.rowcount > 0

    def get_show_content_type(self, tmdb_id:int):
        """获取节目的内容类型"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT content_type FROM shows WHERE tmdb_id=?', (tmdb_id,))
        row = cursor.fetchone()
        return row[0] if row else None

    def get_shows_by_content_type(self, content_type:str):
        """根据内容类型获取节目列表"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM shows WHERE content_type=? ORDER BY name', (content_type,))
        rows = cursor.fetchall()
        if not rows:
            return []
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in rows]

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

    # --------- 扩展：管理季与集清理/更新工具方法 ---------
    def purge_other_seasons(self, tmdb_id: int, keep_season_number: int):
        """清除除指定季之外的所有季与对应集数据"""
        cursor = self.conn.cursor()
        # 删除其他季的 episodes
        cursor.execute('DELETE FROM episodes WHERE tmdb_id=? AND season_number != ?', (tmdb_id, keep_season_number))
        # 删除其他季的 seasons 行
        cursor.execute('DELETE FROM seasons WHERE tmdb_id=? AND season_number != ?', (tmdb_id, keep_season_number))
        self.conn.commit()

    def delete_season(self, tmdb_id: int, season_number: int):
        """删除指定季及其所有集数据"""
        cursor = self.conn.cursor()
        cursor.execute('DELETE FROM episodes WHERE tmdb_id=? AND season_number=?', (tmdb_id, season_number))
        cursor.execute('DELETE FROM seasons WHERE tmdb_id=? AND season_number=?', (tmdb_id, season_number))
        self.conn.commit()

    def update_show_latest_season_number(self, tmdb_id: int, latest_season_number: int):
        """更新 shows.latest_season_number"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE shows SET latest_season_number=? WHERE tmdb_id=?', (latest_season_number, tmdb_id))
        self.conn.commit()

    def update_show_poster(self, tmdb_id: int, poster_local_path: str):
        """更新节目的海报路径"""
        cursor = self.conn.cursor()
        cursor.execute('UPDATE shows SET poster_local_path=? WHERE tmdb_id=?', (poster_local_path, tmdb_id))
        self.conn.commit()

    def get_all_shows(self):
        """获取所有节目"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT * FROM shows')
        columns = [description[0] for description in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]
