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
from datetime import timedelta
import subprocess
import requests
import hashlib
import logging
import base64
import sys
import os
import re

parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, parent_dir)
from quark_auto_save import Quark
from quark_auto_save import Config


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

config_data = {}
task_plugins_config_default = {}

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
        # 验证用户名和密码
        if (username == request.form.get("username")) and (
            password == request.form.get("password")
        ):
            logging.info(f">>> 用户 {username} 登录成功")
            session.permanent = True
            session["token"] = get_login_token()
            return redirect(url_for("index"))
        else:
            logging.info(f">>> 用户 {username} 登录失败")
            return render_template("login.html", message="登录失败")

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
    del data["webui"]
    data["api_token"] = get_login_token()
    data["task_plugins_config_default"] = task_plugins_config_default
    return jsonify({"success": True, "data": data})


# 更新数据
@app.route("/update", methods=["POST"])
def update():
    global config_data
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    dont_save_keys = ["task_plugins_config_default", "api_token"]
    for key, value in request.json.items():
        if key not in dont_save_keys:
            config_data.update({key: value})
    Config.write_json(CONFIG_PATH, config_data)
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


@app.route("/task_suggestions")
def get_task_suggestions():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    query = request.args.get("q", "").lower()
    deep = request.args.get("d", "").lower()
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
            search = cs.auto_login_search(query)
            if search.get("success"):
                if search.get("new_token"):
                    cs_data["token"] = search.get("new_token")
                    Config.write_json(CONFIG_PATH, config_data)
                search_results = cs.clean_search_results(search.get("data"))
                return jsonify(
                    {"success": True, "source": "CloudSaver", "data": search_results}
                )
            else:
                return jsonify({"success": True, "message": search.get("message")})
        else:
            base_url = base64.b64decode("aHR0cHM6Ly9zLjkxNzc4OC54eXo=").decode()
            url = f"{base_url}/task_suggestions?q={query}&d={deep}"
            response = requests.get(url)
            return jsonify(
                {"success": True, "source": "网络公开", "data": response.json()}
            )
    except Exception as e:
        return jsonify({"success": True, "message": f"error: {str(e)}"})


@app.route("/get_share_detail", methods=["POST"])
def get_share_detail():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    shareurl = request.json.get("shareurl", "")
    stoken = request.json.get("stoken", "")
    account = Quark("", 0)
    pwd_id, passcode, pdir_fid, paths = account.extract_url(shareurl)
    if not stoken:
        is_sharing, stoken = account.get_stoken(pwd_id, passcode)
        if not is_sharing:
            return jsonify({"success": False, "data": {"error": stoken}})
    share_detail = account.get_detail(pwd_id, stoken, pdir_fid, _fetch_share=1)
    share_detail["paths"] = paths
    share_detail["stoken"] = stoken

    # 正则命名预览
    def preview_regex(share_detail):
        regex = request.json.get("regex")
        # 检查是否为顺序命名模式
        if regex.get("use_sequence_naming") and regex.get("sequence_naming"):
            # 顺序命名模式预览
            sequence_pattern = regex.get("sequence_naming")
            current_sequence = 1
            
            # 构建顺序命名的正则表达式
            regex_pattern = re.escape(sequence_pattern).replace('\\{\\}', '(\\d+)')
            
            # 实现高级排序算法
            def extract_sorting_value(file):
                if file["dir"]:  # 跳过文件夹
                    return float('inf')
                
                filename = file["file_name"]
                
                # 提取文件名，不含扩展名
                file_name_without_ext = os.path.splitext(filename)[0]
                
                # 1. "第X期/集/话" 格式
                match_chinese = re.search(r'第(\d+)[期集话]', filename)
                episode_num = int(match_chinese.group(1)) if match_chinese else 0
                
                # 5. 文件名含"上中下"（优先处理，因为可能与其他格式同时存在）
                if match_chinese:
                    # 如果同时存在集数和上中下，则按照集数*10+位置排序
                    if '上' in filename:
                        return episode_num * 10 + 1
                    elif '中' in filename:
                        return episode_num * 10 + 2
                    elif '下' in filename:
                        return episode_num * 10 + 3
                elif '上' in filename:
                    return 1
                elif '中' in filename:
                    return 2
                elif '下' in filename:
                    return 3
                
                # 如果已经匹配到"第X期/集/话"格式，直接返回
                if episode_num > 0:
                    return episode_num * 10
                
                # 2.1 S01E01 格式，提取季数和集数
                match_s_e = re.search(r'[Ss](\d+)[Ee](\d+)', filename)
                if match_s_e:
                    season = int(match_s_e.group(1))
                    episode = int(match_s_e.group(2))
                    return season * 1000 + episode
                
                # 2.2 E01 格式，仅提取集数
                match_e = re.search(r'[Ee][Pp]?(\d+)', filename)
                if match_e:
                    return int(match_e.group(1))
                
                # 2.3 1x01 格式，提取季数和集数
                match_x = re.search(r'(\d+)[Xx](\d+)', filename)
                if match_x:
                    season = int(match_x.group(1))
                    episode = int(match_x.group(2))
                    return season * 1000 + episode
                
                # 3. 日期格式识别（支持多种格式）
                
                # 3.1 完整的YYYYMMDD格式
                match_date_compact = re.search(r'(20\d{2})(\d{2})(\d{2})', filename)
                if match_date_compact:
                    year = int(match_date_compact.group(1))
                    month = int(match_date_compact.group(2))
                    day = int(match_date_compact.group(3))
                    return year * 10000 + month * 100 + day
                
                # 3.2 YYYY-MM-DD 或 YYYY.MM.DD 或 YYYY/MM/DD 格式
                match_date_full = re.search(r'(20\d{2})[-./](\d{1,2})[-./](\d{1,2})', filename)
                if match_date_full:
                    year = int(match_date_full.group(1))
                    month = int(match_date_full.group(2))
                    day = int(match_date_full.group(3))
                    return year * 10000 + month * 100 + day
                
                # 3.3 MM/DD/YYYY 或 DD/MM/YYYY 格式
                match_date_alt = re.search(r'(\d{1,2})[-./](\d{1,2})[-./](20\d{2})', filename)
                if match_date_alt:
                    # 假设第一个是月，第二个是日（美式日期）
                    month = int(match_date_alt.group(1))
                    day = int(match_date_alt.group(2))
                    year = int(match_date_alt.group(3))
                    # 检查月份值，如果大于12可能是欧式日期格式（DD/MM/YYYY）
                    if month > 12:
                        month, day = day, month
                    return year * 10000 + month * 100 + day
                
                # 3.4 MM/DD 格式（无年份），假设为当前年
                match_date_short = re.search(r'(\d{1,2})[-./](\d{1,2})', filename)
                if match_date_short:
                    # 假设第一个是月，第二个是日
                    month = int(match_date_short.group(1))
                    day = int(match_date_short.group(2))
                    # 检查月份值，如果大于12可能是欧式日期格式（DD/MM）
                    if month > 12:
                        month, day = day, month
                    # 由于没有年份，使用一个较低的基数，确保任何有年份的日期都排在后面
                    return month * 100 + day
                
                # 3.5 年期格式，如"2025年14期"
                match_year_issue = re.search(r'(20\d{2})[年].*?(\d+)[期]', filename)
                if match_year_issue:
                    year = int(match_year_issue.group(1))
                    issue = int(match_year_issue.group(2))
                    return year * 1000 + issue
                
                # 4. 纯数字格式（文件名开头是纯数字）
                match_num = re.match(r'^(\d+)', file_name_without_ext)
                if match_num:
                    return int(match_num.group(1))
                
                # 6. 默认使用更新时间
                try:
                    return file.get("last_update_at", 0)
                except:
                    return 0
            
            # 过滤出非目录文件，并且排除已经符合命名规则的文件
            files_to_process = [
                f for f in share_detail["list"] 
                if not f["dir"] and not re.match(regex_pattern, f["file_name"])
            ]
            
            # 根据提取的排序值进行排序
            sorted_files = sorted(files_to_process, key=extract_sorting_value)
            
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
                    file["file_name_re"] = sequence_pattern.replace("{}", f"{current_sequence:02d}") + file_ext
                    current_sequence += 1
                    
            return share_detail
        elif regex.get("use_episode_naming") and regex.get("episode_naming"):
            # 剧集命名模式预览
            episode_pattern = regex.get("episode_naming")
            episode_patterns = regex.get("episode_patterns", [])
            
            # 实现序号提取函数
            def extract_episode_number(filename):
                # 优先匹配SxxExx格式
                match_s_e = re.search(r'[Ss](\d+)[Ee](\d+)', filename)
                if match_s_e:
                    # 直接返回E后面的集数
                    return int(match_s_e.group(2))
                
                # 尝试使用每个配置的正则表达式匹配文件名
                for pattern in episode_patterns:
                    try:
                        pattern_regex = pattern.get("regex", "(\\d+)")
                        match = re.search(pattern_regex, filename)
                        if match:
                            return int(match.group(1))
                    except Exception as e:
                        print(f"Error matching pattern {pattern}: {str(e)}")
                        continue
                return None
            
            # 构建剧集命名的正则表达式 (主要用于检测已命名文件)
            if "[]" in episode_pattern:
                regex_pattern = re.escape(episode_pattern).replace('\\[\\]', '(\\d+)')
            else:
                # 如果输入模式不包含[]，则使用简单匹配模式，避免正则表达式错误
                print(f"⚠️ 剧集命名模式中没有找到 [] 占位符，将使用简单匹配")
                regex_pattern = "^" + re.escape(episode_pattern) + "(\\d+)$"
            
            # 实现高级排序算法
            def extract_sorting_value(file):
                if file["dir"]:  # 跳过文件夹
                    return float('inf')
                
                filename = file["file_name"]
                
                # 尝试获取剧集序号
                episode_num = extract_episode_number(filename)
                if episode_num is not None:
                    return episode_num
                
                # 如果无法提取序号，则使用更新时间
                try:
                    return file.get("last_update_at", 0)
                except:
                    return 0
            
            # 过滤出非目录文件，并且排除已经符合命名规则的文件
            files_to_process = [
                f for f in share_detail["list"] 
                if not f["dir"] and not re.match(regex_pattern, f["file_name"])
            ]
            
            # 根据提取的排序值进行排序
            sorted_files = sorted(files_to_process, key=extract_sorting_value)
            
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
            
            # 为每个文件生成新文件名
            for file in sorted_files:
                if not file.get("filtered"):
                    # 获取文件扩展名
                    file_ext = os.path.splitext(file["file_name"])[1]
                    # 尝试提取剧集号
                    episode_num = extract_episode_number(file["file_name"])
                    if episode_num is not None:
                        # 生成预览文件名
                        file["file_name_re"] = episode_pattern.replace("[]", f"{episode_num:02d}") + file_ext
                    else:
                        # 无法提取剧集号，标记为无法处理
                        file["file_name_re"] = "❌ 无法识别剧集号"
                    
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
    account = Quark(config_data["cookie"][0], 0)
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
    account = Quark(config_data["cookie"][0], 0)
    if fid := request.json.get("fid"):
        response = account.delete([fid])
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
        or config_data.get("webui", {}).get("password", "admin123"),
    }

    # 默认定时规则
    if not config_data.get("crontab"):
        config_data["crontab"] = "0 8,18,20 * * *"

    # 初始化插件配置
    _, plugins_config_default, task_plugins_config_default = Config.load_plugins()
    plugins_config_default.update(config_data.get("plugins", {}))
    config_data["plugins"] = plugins_config_default

    # 更新配置
    Config.write_json(CONFIG_PATH, config_data)


if __name__ == "__main__":
    init()
    reload_tasks()
    app.run(debug=DEBUG, host="0.0.0.0", port=5005)
