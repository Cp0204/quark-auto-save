# !/usr/bin/env python3
# -*- coding: utf-8 -*-
from flask import (
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
import subprocess
import requests
import hashlib
import logging
import base64
import json
import sys
import os

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
DEBUG = os.environ.get("DEBUG", False)

task_plugins_config = {}

app = Flask(__name__)
app.config["APP_VERSION"] = get_app_ver()
app.secret_key = "ca943f6db6dd34823d36ab08d8d6f65d"
app.config["SESSION_COOKIE_NAME"] = "QUARK_AUTO_SAVE_SESSION"
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


# 读取 JSON 文件内容
def read_json():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


# 将数据写入 JSON 文件
def write_json(data):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, sort_keys=False, indent=2)


def is_login():
    data = read_json()
    username = data["webui"]["username"]
    password = data["webui"]["password"]
    if session.get("login") == gen_md5(username + password):
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
        data = read_json()
        username = data["webui"]["username"]
        password = data["webui"]["password"]
        # 验证用户名和密码
        if (username == request.form.get("username")) and (
            password == request.form.get("password")
        ):
            logging.info(f">>> 用户 {username} 登录成功")
            session["login"] = gen_md5(username + password)
            return redirect(url_for("index"))
        else:
            logging.info(f">>> 用户 {username} 登录失败")
            return render_template("login.html", message="登录失败")

    return render_template("login.html", error=None)


# 退出登录
@app.route("/logout")
def logout():
    session.pop("login", None)
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
        return redirect(url_for("login"))
    data = read_json()
    del data["webui"]
    data["task_plugins_config"] = task_plugins_config
    return jsonify(data)


# 更新数据
@app.route("/update", methods=["POST"])
def update():
    if not is_login():
        return "未登录"
    data = request.json
    data["webui"] = read_json()["webui"]
    if "task_plugins_config" in data:
        del data["task_plugins_config"]
    write_json(data)
    # 重新加载任务
    if reload_tasks():
        logging.info(f">>> 配置更新成功")
        return "配置更新成功"
    else:
        logging.info(f">>> 配置更新失败")
        return "配置更新失败"


# 处理运行脚本请求
@app.route("/run_script_now", methods=["GET"])
def run_script_now():
    if not is_login():
        return "未登录"
    task_index = request.args.get("task_index", "")
    command = [PYTHON_PATH, "-u", SCRIPT_PATH, CONFIG_PATH, task_index]
    logging.info(
        f">>> 手动运行任务{int(task_index)+1 if task_index.isdigit() else 'all'}"
    )

    def generate_output():
        # 设置环境变量
        process_env = os.environ.copy()
        process_env["PYTHONIOENCODING"] = "utf-8"
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
        return jsonify({"error": "未登录"})
    base_url = base64.b64decode("aHR0cHM6Ly9zLjkxNzc4OC54eXo=").decode()
    query = request.args.get("q", "").lower()
    deep = request.args.get("d", "").lower()
    url = f"{base_url}/task_suggestions?q={query}&d={deep}"
    try:
        response = requests.get(url)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/get_share_detail")
def get_share_files():
    if not is_login():
        return jsonify({"error": "未登录"})
    shareurl = request.args.get("shareurl", "")
    account = Quark("", 0)
    pwd_id, passcode, pdir_fid = account.get_id_from_url(shareurl)
    is_sharing, stoken = account.get_stoken(pwd_id, passcode)
    if not is_sharing:
        return jsonify({"error": stoken})
    share_detail = account.get_detail(pwd_id, stoken, pdir_fid, 1)
    return jsonify(share_detail)


@app.route("/get_savepath")
def get_savepath():
    if not is_login():
        return jsonify({"error": "未登录"})
    data = read_json()
    account = Quark(data["cookie"][0], 0)
    if path := request.args.get("path"):
        if path == "/":
            fid = 0
        elif get_fids := account.get_fids([path]):
            fid = get_fids[0]["fid"]
        else:
            return jsonify([])
    else:
        fid = request.args.get("fid", 0)
    file_list = account.ls_dir(fid)
    return jsonify(file_list)


# 定时任务执行的函数
def run_python(args):
    logging.info(f">>> 定时运行任务")
    os.system(f"{PYTHON_PATH} {args}")


# 重新加载任务
def reload_tasks():
    # 读取数据
    data = read_json()
    # 添加新任务
    crontab = data.get("crontab")
    if crontab:
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
    global task_plugins_config
    logging.info(f">>> 初始化配置")
    # 检查配置文件是否存在
    if not os.path.exists(CONFIG_PATH):
        if not os.path.exists(os.path.dirname(CONFIG_PATH)):
            os.makedirs(os.path.dirname(CONFIG_PATH))
        with open("quark_config.json", "rb") as src, open(CONFIG_PATH, "wb") as dest:
            dest.write(src.read())
    data = read_json()
    Config.breaking_change_update(data)
    # 默认管理账号
    data["webui"] = {
        "username": os.environ.get("WEBUI_USERNAME")
        or data.get("webui", {}).get("username", "admin"),
        "password": os.environ.get("WEBUI_PASSWORD")
        or data.get("webui", {}).get("password", "admin123"),
    }
    # 默认定时规则
    if not data.get("crontab"):
        data["crontab"] = "0 8,18,20 * * *"
    # 初始化插件配置
    _, plugins_config_default, task_plugins_config = Config.load_plugins()
    plugins_config_default.update(data.get("plugins", {}))
    data["plugins"] = plugins_config_default
    write_json(data)


if __name__ == "__main__":
    init()
    reload_tasks()
    app.run(debug=DEBUG, host="0.0.0.0", port=5005)
