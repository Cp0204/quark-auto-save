# -*- coding: utf-8 -*-
"""config 目录下 data.db 与 quark_config.json 的每日自动备份。"""

import json
import logging
import os
import re
import shutil
import sqlite3
from datetime import datetime

BACKUP_STAMP_FILENAME = ".backup_done"
BACKUP_DATE_SUFFIX_PATTERN = re.compile(r"^(.+)-(\d{4}-\d{2}-\d{2})$")
DEFAULT_MAX_BACKUPS = 4


def _get_config_dir(config_path: str) -> str:
    return os.path.dirname(os.path.abspath(config_path))


def _get_db_path(config_path: str) -> str:
    return os.path.join(_get_config_dir(config_path), "data.db")


def _backup_stamp_path(config_dir: str) -> str:
    return os.path.join(config_dir, BACKUP_STAMP_FILENAME)


def read_backup_stamp(config_dir: str) -> str:
    """读取上次成功备份的日期（YYYY-MM-DD）。"""
    try:
        stamp_path = _backup_stamp_path(config_dir)
        if os.path.exists(stamp_path):
            with open(stamp_path, "r", encoding="utf-8") as f:
                return (f.read() or "").strip()
    except Exception:
        pass
    return ""


def write_backup_stamp(config_dir: str, date_str: str) -> None:
    """记录本次成功备份的日期。"""
    try:
        with open(_backup_stamp_path(config_dir), "w", encoding="utf-8") as f:
            f.write(str(date_str or ""))
    except Exception as exc:
        logging.warning(f">>> 写入备份日期标记失败: {exc}")


def _cookie_is_valid(cookie) -> bool:
    """检查 cookie 是否非空（支持字符串或字符串数组）。"""
    if cookie is None:
        return False
    if isinstance(cookie, str):
        return bool(cookie.strip())
    if isinstance(cookie, list):
        return any(isinstance(item, str) and item.strip() for item in cookie)
    return False


def validate_quark_config(config_path: str) -> bool:
    """
    备份前校验 quark_config.json：
    - 必须是合法 JSON
    - cookie 不能为空
    - webui 用户名和密码不能为空
    """
    if not os.path.exists(config_path):
        logging.warning(f">>> 配置备份校验失败：文件不存在 {config_path}")
        return False

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        logging.warning(f">>> 配置备份校验失败：无法解析 quark_config.json - {exc}")
        return False

    if not isinstance(data, dict):
        logging.warning(">>> 配置备份校验失败：quark_config.json 根节点不是对象")
        return False

    if not _cookie_is_valid(data.get("cookie")):
        logging.warning(">>> 配置备份校验失败：cookie 为空，跳过 quark_config.json 备份")
        return False

    webui = data.get("webui")
    if not isinstance(webui, dict):
        logging.warning(">>> 配置备份校验失败：webui 配置缺失，跳过 quark_config.json 备份")
        return False

    username = str(webui.get("username") or "").strip()
    password = str(webui.get("password") or "").strip()
    if not username or not password:
        logging.warning(
            ">>> 配置备份校验失败：webui 用户名或密码为空，跳过 quark_config.json 备份"
        )
        return False

    return True


def backup_sqlite_db(src_path: str, dst_path: str) -> bool:
    """使用 SQLite backup API 生成一致性快照（兼容 WAL 模式）。"""
    if not os.path.exists(src_path):
        logging.warning(f">>> 数据库备份跳过：源文件不存在 {src_path}")
        return False

    tmp_path = f"{dst_path}.tmp"
    src_conn = None
    dst_conn = None
    try:
        src_conn = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, timeout=10.0)
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        dst_conn = sqlite3.connect(tmp_path)
        src_conn.backup(dst_conn)
        dst_conn.commit()
        dst_conn.close()
        dst_conn = None
        src_conn.close()
        src_conn = None
        os.replace(tmp_path, dst_path)
        return True
    except Exception as exc:
        logging.error(f">>> 数据库备份失败：{exc}")
        return False
    finally:
        if dst_conn is not None:
            try:
                dst_conn.close()
            except Exception:
                pass
        if src_conn is not None:
            try:
                src_conn.close()
            except Exception:
                pass
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def backup_config_file(src_path: str, dst_path: str) -> bool:
    """复制配置文件到备份路径（先写临时文件再原子替换）。"""
    if not os.path.exists(src_path):
        logging.warning(f">>> 配置文件备份跳过：源文件不存在 {src_path}")
        return False

    tmp_path = f"{dst_path}.tmp"
    try:
        shutil.copy2(src_path, tmp_path)
        os.replace(tmp_path, dst_path)
        return True
    except Exception as exc:
        logging.error(f">>> 配置文件备份失败：{exc}")
        return False
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def cleanup_old_backups(config_dir: str, basename: str, max_backups: int) -> None:
    """保留最多 max_backups 个带日期后缀的历史备份，删除更早的文件。"""
    prefix = f"{basename}-"
    dated_files = []

    try:
        for name in os.listdir(config_dir):
            if not name.startswith(prefix):
                continue
            match = BACKUP_DATE_SUFFIX_PATTERN.match(name)
            if match and match.group(1) == basename:
                dated_files.append((match.group(2), name))
    except Exception as exc:
        logging.warning(f">>> 扫描旧备份失败：{exc}")
        return

    dated_files.sort(key=lambda item: item[0])
    while len(dated_files) > max_backups:
        _, filename = dated_files.pop(0)
        try:
            os.remove(os.path.join(config_dir, filename))
            logging.info(f">>> 已清理旧备份：{filename}")
        except Exception as exc:
            logging.warning(f">>> 清理旧备份失败 {filename}: {exc}")


def _parse_backup_settings(config_data) -> tuple[bool, int]:
    """从 performance 配置读取备份开关与保留数量。"""
    perf = config_data.get("performance", {}) if isinstance(config_data, dict) else {}

    enabled = perf.get("backup_enabled", True)
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() not in ("0", "false", "no", "off")

    max_backups = DEFAULT_MAX_BACKUPS
    try:
        max_backups = int(perf.get("backup_max_count", DEFAULT_MAX_BACKUPS))
    except (TypeError, ValueError):
        max_backups = DEFAULT_MAX_BACKUPS

    if max_backups < 1:
        max_backups = DEFAULT_MAX_BACKUPS
    if max_backups > 30:
        max_backups = 30

    return bool(enabled), max_backups


def ensure_daily_backup_if_due(config_path: str, config_data=None) -> None:
    """
    在每日首次定时任务执行后调用：
    - 同一天只备份一次
    - 数据库备份失败时不写入日期标记，下次任务会重试
    - 配置文件校验失败时跳过配置备份，但不影响数据库备份
    """
    enabled, max_backups = _parse_backup_settings(config_data)
    if not enabled:
        return

    config_dir = _get_config_dir(config_path)
    today = datetime.now().strftime("%Y-%m-%d")

    if read_backup_stamp(config_dir) == today:
        return

    config_basename = os.path.basename(config_path)
    db_basename = "data.db"
    db_path = _get_db_path(config_path)

    logging.info(f">>> 开始执行每日自动备份（{today}）")

    db_backup_name = f"{db_basename}-{today}"
    db_backup_path = os.path.join(config_dir, db_backup_name)
    db_ok = False

    if os.path.exists(db_backup_path):
        logging.info(f">>> 今日数据库备份已存在，跳过：{db_backup_name}")
        db_ok = True
    elif backup_sqlite_db(db_path, db_backup_path):
        logging.info(f">>> 数据库备份成功：{db_backup_name}")
        cleanup_old_backups(config_dir, db_basename, max_backups)
        db_ok = True

    config_backup_name = f"{config_basename}-{today}"
    config_backup_path = os.path.join(config_dir, config_backup_name)

    if validate_quark_config(config_path):
        if os.path.exists(config_backup_path):
            logging.info(f">>> 今日配置文件备份已存在，跳过：{config_backup_name}")
        elif backup_config_file(config_path, config_backup_path):
            logging.info(f">>> 配置文件备份成功：{config_backup_name}")
            cleanup_old_backups(config_dir, config_basename, max_backups)
    else:
        logging.warning(
            ">>> quark_config.json 校验未通过，已跳过配置文件备份（避免备份损坏文件）"
        )

    if db_ok:
        write_backup_stamp(config_dir, today)
        logging.info(">>> 每日自动备份完成")
    else:
        logging.warning(">>> 数据库备份未成功，下次定时任务将重试备份")
