#!/usr/bin/env python3
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
import io
import threading

parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, parent_dir)
from quark_auto_save import Quark, Config, MagicRename

# 用于存储当前运行的日志
current_log = io.StringIO()
log_lock = threading.Lock()
# 添加一个标志来跟踪任务是否正在运行
task_running = False
# 存储当前运行的进程
current_process = None
process_lock = threading.Lock()


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
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = os.environ.get("PORT", 5005)

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

    # 清空当前日志
    with log_lock:
        current_log.seek(0)
        current_log.truncate(0)
        current_log.write("任务开始执行...\n")

    # 设置任务运行状态
    global task_running, current_process
    task_running = True

    # 确保之前的进程已经终止
    with process_lock:
        if current_process is not None:
            try:
                if current_process.poll() is None:
                    current_process.terminate()
                    current_process.wait(timeout=1)
            except Exception as e:
                logging.error(f"终止旧进程失败: {str(e)}")
            current_process = None

    def generate_output():
        # 设置环境变量
        process_env = os.environ.copy()
        process_env["PYTHONIOENCODING"] = "utf-8"
        if request.json.get("quark_test"):
            process_env["QUARK_TEST"] = "true"
            process_env["COOKIE"] = json.dumps(
                request.json.get("cookie", []), ensure_ascii=False
            )
            process_env["PUSH_CONFIG"] = json.dumps(
                request.json.get("push_config", {}), ensure_ascii=False
            )
        if tasklist:
            process_env["TASKLIST"] = json.dumps(tasklist, ensure_ascii=False)

        # 创建进程并保存引用
        global current_process
        with process_lock:
            current_process = subprocess.Popen(
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
            for line in iter(current_process.stdout.readline, ""):
                logging.info(line.strip())
                # 将日志存储到全局变量中
                with log_lock:
                    current_log.write(line)
                yield f"data: {line}\n\n"

            # 检查进程退出状态
            exit_code = current_process.wait()
            if exit_code != 0:
                error_msg = f"任务异常结束，退出码: {exit_code}\n"
                logging.error(error_msg)
                with log_lock:
                    current_log.write(error_msg)
                yield f"data: {error_msg}\n\n"

            yield "data: [DONE]\n\n"
        finally:
            if current_process:
                if current_process.poll() is None:
                    current_process.stdout.close()
                    current_process.wait()
            # 检查进程状态
            check_process_status()

    return Response(
        stream_with_context(generate_output()),
        content_type="text/event-stream;charset=utf-8",
    )


# 添加一个函数来检查进程状态
def check_process_status():
    global task_running, current_process
    with process_lock:
        if current_process is not None:
            # 检查进程是否仍在运行
            poll_result = current_process.poll()
            if poll_result is None:
                # 进程仍在运行
                logging.debug(">>> 检查进程状态：进程仍在运行")
                task_running = True
            else:
                # 进程已结束，记录退出码
                if poll_result != 0:
                    error_msg = f">>> 检查进程状态：进程异常结束，退出码 {poll_result}"
                    logging.error(error_msg)
                    # 将错误信息添加到日志中
                    with log_lock:
                        if not current_log.getvalue().endswith(
                            f"任务异常结束，退出码: {poll_result}\n"
                        ):
                            current_log.write(f"任务异常结束，退出码: {poll_result}\n")
                else:
                    logging.info(f">>> 检查进程状态：进程正常结束，退出码 {poll_result}")
                task_running = False
                current_process = None
        else:
            if task_running:
                logging.info(">>> 检查进程状态：无进程引用但状态为运行中，重置状态")
                task_running = False
            else:
                logging.debug(">>> 检查进程状态：无进程运行")


# 获取当前运行的日志
@app.route("/get_current_log")
def get_current_log():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})

    # 检查进程状态
    check_process_status()

    with log_lock:
        log_content = current_log.getvalue()
    return jsonify({"success": True, "log": log_content, "running": task_running})


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
    account = Quark()
    pwd_id, passcode, pdir_fid, paths = account.extract_url(shareurl)
    if not stoken:
        get_stoken = account.get_stoken(pwd_id, passcode)
        if get_stoken.get("status") == 200:
            stoken = get_stoken["data"]["stoken"]
        else:
            return jsonify(
                {"success": False, "data": {"error": get_stoken.get("message")}}
            )
    share_detail = account.get_detail(pwd_id, stoken, pdir_fid, _fetch_share=1)

    if share_detail.get("code") != 0:
        return jsonify(
            {"success": False, "data": {"error": share_detail.get("message")}}
        )

    data = share_detail["data"]
    data["paths"] = paths
    data["stoken"] = stoken

    # 正则处理预览
    def preview_regex(data):
        task = request.json.get("task", {})
        magic_regex = request.json.get("magic_regex", {})
        global_regex = request.json.get("global_regex", {})
        mr = MagicRename(magic_regex)
        mr.set_taskname(task.get("taskname", ""))
        account = Quark(config_data["cookie"][0])
        get_fids = account.get_fids([task.get("savepath", "")])
        if get_fids:
            dir_file_list = account.ls_dir(get_fids[0]["fid"])["data"]["list"]
            dir_filename_list = [dir_file["file_name"] for dir_file in dir_file_list]
        else:
            dir_file_list = []
            dir_filename_list = []

        # 获取全局正则和任务正则
        global_regex_enabled = global_regex.get("enabled", False)
        global_pattern = global_regex.get("pattern", "")
        global_replace = global_regex.get("replace", "")

        task_pattern, task_replace = mr.magic_regex_conv(
            task.get("pattern", ""), task.get("replace", "")
        )

        for share_file in data["list"]:
            if share_file["dir"]:
                search_pattern = task.get("update_subdir", "")
                if re.search(search_pattern, share_file["file_name"]):
                    # 目录不进行重命名
                    share_file["file_name_re"] = share_file["file_name"]
                continue

            # 初始化重命名后的文件名为原始文件名
            renamed_name = share_file["file_name"]
            applied_global_regex = False
            applied_task_regex = False

            # 应用全局正则（如果启用）
            if global_regex_enabled and global_pattern:
                if re.search(global_pattern, renamed_name):
                    global_renamed_name = re.sub(
                        global_pattern, global_replace, renamed_name
                    )
                    renamed_name = global_renamed_name
                    applied_global_regex = True

            # 应用任务正则（如果存在）
            if task_pattern:
                if re.search(task_pattern, renamed_name):
                    task_renamed_name = mr.sub(task_pattern, task_replace, renamed_name)
                    renamed_name = task_renamed_name
                    applied_task_regex = True

            # 检查是否应用了任何正则
            if applied_global_regex or applied_task_regex:
                if file_name_saved := mr.is_exists(
                    renamed_name,
                    dir_filename_list,
                    (task.get("ignore_extension") and not share_file["dir"]),
                ):
                    share_file["file_name_saved"] = file_name_saved
                else:
                    share_file["file_name_re"] = renamed_name

        # 文件列表排序
        replace_for_i = ""
        if task.get("replace"):
            replace_for_i = task_replace
        elif global_regex_enabled and global_replace:
            replace_for_i = global_replace

        if replace_for_i and re.search(r"\{I+\}", replace_for_i):
            mr.set_dir_file_list(dir_file_list, replace_for_i)
            mr.sort_file_list(data["list"])

    if request.json.get("task"):
        preview_regex(data)

    return jsonify({"success": True, "data": data})


@app.route("/get_savepath_detail")
def get_savepath_detail():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})
    account = Quark(config_data["cookie"][0])
    paths = []
    if path := request.args.get("path"):
        path = re.sub(r"/+", "/", path)
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
    account = Quark(config_data["cookie"][0])
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
    if not request_data.get("addition"):
        request_data["addition"] = task_plugins_config_default
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

    # --- 开始进行配置项向后兼容更新 ---

    # 1. 确保 magic_regex 存在
    if not config_data.get("magic_regex"):
        config_data["magic_regex"] = MagicRename().magic_regex
        logging.info("配置升级：已补全 'magic_regex' 默认配置。")

    # 2. 确保 global_regex 存在
    if not config_data.get("global_regex"):
        config_data["global_regex"] = {"enabled": False, "pattern": "", "replace": ""}
        logging.info("配置升级：已补全 'global_regex' 默认配置。")
        
    # 3. 确保 file_blacklist 存在
    if "file_blacklist" not in config_data:
        config_data["file_blacklist"] = []
        logging.info("配置升级：已补全 'file_blacklist' 默认配置。")

    # 4. 确保 source 存在
    if "source" not in config_data:
        config_data["source"] = {
            "cloudsaver": {"server": "", "username": "", "password": "", "token": ""}
        }
        logging.info("配置升级：已补全 'source' 默认配置。")

    # 5. 确保 shortcuts 及其所有子项存在
    if "shortcuts" not in config_data:
        config_data["shortcuts"] = {
            "saveEnabled": True,
            "runEnabled": False,
            "autoSaveEnabled": True,
        }
        logging.info("配置升级：已补全 'shortcuts' 默认配置。")
    else:
        if "saveEnabled" not in config_data["shortcuts"]:
            config_data["shortcuts"]["saveEnabled"] = True
        if "runEnabled" not in config_data["shortcuts"]:
            config_data["shortcuts"]["runEnabled"] = False
        if "autoSaveEnabled" not in config_data["shortcuts"]:
            config_data["shortcuts"]["autoSaveEnabled"] = True
            logging.info("配置升级：为 'shortcuts' 已补全 'autoSaveEnabled' 子项。")
            
    # 6. 确保 tasklist 中每个任务都包含新参数 (例如 ignore_extension)
    if config_data.get("tasklist") and isinstance(config_data["tasklist"], list):
        for task in config_data["tasklist"]:
            if "ignore_extension" not in task:
                task["ignore_extension"] = False
            if "addition" not in task:
                task["addition"] = {}

    # --- 配置项向后兼容更新结束 ---

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

    # 更新配置，将所有补全的默认值写回文件
    Config.write_json(CONFIG_PATH, config_data)


# 添加一个重置任务状态的路由
@app.route("/reset_task_status", methods=["POST"])
def reset_task_status():
    if not is_login():
        return jsonify({"success": False, "message": "未登录"})

    global task_running, current_process

    # 如果有正在运行的进程，尝试终止它
    with process_lock:
        if current_process is not None:
            try:
                if current_process.poll() is None:  # 进程仍在运行
                    current_process.terminate()
                    current_process.wait(timeout=5)  # 等待最多5秒
                current_process = None
            except Exception as e:
                logging.error(f"终止进程失败: {str(e)}")

    # 重置任务状态
    task_running = False

    # 清空日志
    with log_lock:
        current_log.seek(0)
        current_log.truncate(0)
        current_log.write("任务状态已重置\n")

    logging.info(">>> 任务状态已手动重置")
    return jsonify({"success": True, "message": "任务状态已重置"})


if __name__ == "__main__":
    init()
    reload_tasks()
    app.run(
        debug=DEBUG,
        host=HOST,
        port=PORT,
    )
