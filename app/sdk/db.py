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
        self.conn = sqlite3.connect(self.db_path)
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
            file_type TEXT
        )
        ''')
        self.conn.commit()
    
    def close(self):
        if self.conn:
            self.conn.close()
    
    def add_record(self, task_name, original_name, renamed_to, file_size, modify_date, 
                  duration="", resolution="", file_id="", file_type=""):
        """添加一条转存记录"""
        cursor = self.conn.cursor()
        cursor.execute(
            "INSERT INTO transfer_records (transfer_time, task_name, original_name, renamed_to, file_size, "
            "duration, resolution, modify_date, file_id, file_type) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (int(time.time()), task_name, original_name, renamed_to, file_size, 
             duration, resolution, modify_date, file_id, file_type)
        )
        self.conn.commit()
        return cursor.lastrowid
    
    def update_renamed_to(self, file_id, original_name, renamed_to, task_name=""):
        """更新最近一条记录的renamed_to字段
        
        Args:
            file_id: 文件ID
            original_name: 原文件名
            renamed_to: 重命名后的文件名
            task_name: 任务名称，可选项，如提供则作为附加筛选条件
            
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
            # 更新记录
            cursor.execute(
                "UPDATE transfer_records SET renamed_to = ? WHERE id = ?",
                (renamed_to, record_id)
            )
            self.conn.commit()
            return cursor.rowcount
        
        return 0
    
    def get_records(self, page=1, page_size=20, sort_by="transfer_time", order="desc", 
                   task_name_filter="", keyword_filter=""):
        """获取转存记录列表，支持分页、排序和筛选
        
        Args:
            page: 当前页码
            page_size: 每页记录数
            sort_by: 排序字段
            order: 排序方向（asc/desc）
            task_name_filter: 任务名称筛选条件（精确匹配）
            keyword_filter: 关键字筛选条件（模糊匹配任务名）
        """
        cursor = self.conn.cursor()
        offset = (page - 1) * page_size
        
        # 构建SQL查询
        valid_columns = ["transfer_time", "task_name", "original_name", "renamed_to", 
                         "file_size", "duration", "resolution", "modify_date"]
        
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
            where_clauses.append("task_name LIKE ?")
            params.append(f"%{keyword_filter}%")
        
        where_clause = " AND ".join(where_clauses)
        where_sql = f"WHERE {where_clause}" if where_clause else ""
        
        # 获取筛选后的总记录数
        count_sql = f"SELECT COUNT(*) FROM transfer_records {where_sql}"
        cursor.execute(count_sql, params)
        total_records = cursor.fetchone()[0]
        
        # 获取分页数据
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