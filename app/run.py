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
from sdk.cloudsaver import CloudSaver
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
app.config["SESSION_COOKIE_NAME"] = "QUARK_AUTO_SAVE_SESSION"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=31)
app.json.ensure_ascii = False
app.json.sort_keys = False
app.jinja_env.variable_start_string = "[["
app.jinja_env.variable_end_string = "]]"

scheduler = BackgroundScheduler()
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="[%(asctime)s][%(levelname)s] %(message)s",
    datefmt="%m-%d %H:%M:%S",
)
# 过滤werkzeug日志输出
if not DEBUG:
    logging.getLogger("werkzeug").setLevel(logging.ERROR)


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


# 设置icon
@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


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


# 获取配置数据
@app.route("/data")
def get_data():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    data = Config.read_json(CONFIG_PATH)

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
    """
    global config_data, task_plugins_config_default
    
    # 如果没有任务列表，直接返回
    if not config_data.get("tasklist"):
        return
        
    # 获取禁用的插件列表
    disabled_plugins = set()
    if PLUGIN_FLAGS:
        disabled_plugins = {name.lstrip('-') for name in PLUGIN_FLAGS.split(',')}
        
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
                
            # 如果任务中没有该插件的配置，添加默认配置
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
            else:
                config_data.update({key: value})
    
    # 同步更新任务的插件配置
    sync_task_plugins_config()
    
    Config.write_json(CONFIG_PATH, config_data)
    # 更新session token，确保当前会话在用户名密码更改后仍然有效
    session["token"] = get_login_token()
    # 重新加载任务
    if reload_tasks():
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
        cs_data = config_data.get("source", {}).get("cloudsaver", {})
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
            # 使用处理后的搜索关键词
            search = cs.auto_login_search(search_query)
            if search.get("success"):
                if search.get("new_token"):
                    cs_data["token"] = search.get("new_token")
                    Config.write_json(CONFIG_PATH, config_data)
                search_results = cs.clean_search_results(search.get("data"))
                # 在返回结果中添加实际使用的搜索关键词
                return jsonify(
                    {
                        "success": True, 
                        "source": "CloudSaver", 
                        "data": search_results
                    }
                )
            else:
                return jsonify({"success": True, "message": search.get("message")})
        else:
            base_url = base64.b64decode("aHR0cHM6Ly9zLjkxNzc4OC54eXo=").decode()
            # 使用处理后的搜索关键词
            url = f"{base_url}/task_suggestions?q={search_query}&d={deep}"
            response = requests.get(url)
            return jsonify(
                {
                    "success": True, 
                    "source": "网络公开", 
                    "data": response.json()
                }
            )
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
    share_detail["paths"] = paths
    share_detail["stoken"] = stoken

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
            
            # 应用过滤词过滤
            filterwords = regex.get("filterwords", "")
            if filterwords:
                # 同时支持中英文逗号分隔
                filterwords = filterwords.replace("，", ",")
                filterwords_list = [word.strip() for word in filterwords.split(',')]
                for item in sorted_files:
                    # 被过滤的文件不会有file_name_re，与不匹配正则的文件显示一致
                    if any(word in item['file_name'] for word in filterwords_list):
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
            
            # 应用过滤词过滤
            filterwords = regex.get("filterwords", "")
            if filterwords:
                # 同时支持中英文逗号分隔
                filterwords = filterwords.replace("，", ",")
                filterwords_list = [word.strip() for word in filterwords.split(',')]
                for item in share_detail["list"]:
                    # 被过滤的文件显示一个 ×
                    if any(word in item['file_name'] for word in filterwords_list):
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
            
            # 应用过滤词过滤
            filterwords = regex.get("filterwords", "")
            if filterwords:
                # 同时支持中英文逗号分隔
                filterwords = filterwords.replace("，", ",")
                filterwords_list = [word.strip() for word in filterwords.split(',')]
                for item in share_detail["list"]:
                    # 被过滤的文件不会有file_name_re，与不匹配正则的文件显示一致
                    if any(word in item['file_name'] for word in filterwords_list):
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
    file_list = {
        "list": account.ls_dir(fid),
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
                
            except Exception as e:
                logging.error(f">>> 删除记录时出错: {str(e)}")
                # 不影响主流程，即使删除记录失败也返回文件删除成功
    else:
        response = {"success": False, "message": "缺失必要字段: fid"}
    return jsonify(response)


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
    
    return jsonify({
        "success": True, 
        "message": f"成功删除 {deleted_count} 条记录",
        "deleted_count": deleted_count
    })


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
            user_info_list.append({
                "index": idx,
                "nickname": account_info["nickname"],
                "is_active": account.is_active
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

        # 计算总数
        total = len(files)

        # 优化排序：使用更高效的排序方法
        def get_sort_key(file_item):
            if sort_by == "file_name":
                return file_item["file_name"].lower()
            elif sort_by == "file_size":
                return file_item["size"] if not file_item["dir"] else 0
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
        
        # 过滤要排除的文件
        # 替换中文逗号为英文逗号
        filterwords = filterwords.replace("，", ",") 
        filter_list = [keyword.strip() for keyword in filterwords.split(",") if keyword.strip()]
        filtered_files = []
        for file in files:
            # 如果不包含文件夹且当前项是文件夹，跳过
            if not include_folders and file["dir"]:
                continue
                
            # 检查是否包含过滤关键词
            should_filter = False
            for keyword in filter_list:
                if keyword and keyword in file["file_name"]:
                    should_filter = True
                    break
                    
            if not should_filter:
                filtered_files.append(file)
        
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


if __name__ == "__main__":
    init()
    reload_tasks()
    # 初始化全局db对象，确保所有接口可用
    record_db = RecordDB()
    app.run(debug=DEBUG, host="0.0.0.0", port=PORT)
