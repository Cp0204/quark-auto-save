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
import hashlib
import json
import os


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
python_path = "python3" if os.path.exists("/usr/bin/python3") else "python"
script_path = os.environ.get("SCRIPT_PATH", "./quark_auto_save.py")
config_path = os.environ.get("CONFIG_PATH", "./config/quark_config.json")

app = Flask(__name__)
app.config["APP_VERSION"] = get_app_ver()
app.secret_key = "ca943f6db6dd34823d36ab08d8d6f65d"
app.json.ensure_ascii = False
app.json.sort_keys = False
app.jinja_env.variable_start_string = "[["
app.jinja_env.variable_end_string = "]]"

scheduler = BackgroundScheduler()

def gen_md5(string):
    md5 = hashlib.md5()
    md5.update(string.encode("utf-8"))
    return md5.hexdigest()


# 读取 JSON 文件内容
def read_json():
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


# 将数据写入 JSON 文件
def write_json(data):
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False, sort_keys=False)


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
            session["login"] = gen_md5(username + password)
            return redirect(url_for("index"))
        else:
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
    return render_template("index.html", version=app.config["APP_VERSION"])


# 获取配置数据
@app.route("/data")
def get_data():
    if not is_login():
        return redirect(url_for("login"))
    data = read_json()
    del data["webui"]
    return jsonify(data)


# 更新数据
@app.route("/update", methods=["POST"])
def update():
    if not is_login():
        return "未登录"
    data = read_json()
    webui = data["webui"]
    data = request.json
    data["webui"] = webui
    write_json(data)
    reload_tasks()  # 重新加载任务
    return "配置更新成功"


# 处理运行脚本请求
@app.route("/run_script_now", methods=["GET"])
def run_script_now():
    if not is_login():
        return "未登录"
    task_index = request.args.get("task_index", "")
    command = [python_path, "-u", script_path, config_path, task_index]

    def generate_output():
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
            bufsize=1,
        )
        try:
            for line in iter(process.stdout.readline, ""):
                yield f"data: {line}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            process.stdout.close()
            process.wait()

    return Response(
        stream_with_context(generate_output()),
        content_type="text/event-stream;charset=utf-8",
    )


# 定时任务执行的函数
def run_python(args):
    os.system(f"{python_path} {args}")


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
            args=[f"{script_path} {config_path}"],
            id=script_path,
        )
        if scheduler.state == 0:
            scheduler.start()
        elif scheduler.state == 2:
            scheduler.resume()
        scheduler_state_map = {0: "停止", 1: "运行", 2: "暂停"}
        print(">>> 重载调度器")
        print(f"调度状态: {scheduler_state_map[scheduler.state]}")
        print(f"定时规则: {crontab}")
        print(f"现有任务: {scheduler.get_jobs()}")
    else:
        print(">>> no crontab")


def init():
    # 检查配置文件是否存在
    if not os.path.exists(config_path):
        if not os.path.exists(os.path.dirname(config_path)):
            os.makedirs(os.path.dirname(config_path))
        with open("quark_config.json", "rb") as src, open(config_path, "wb") as dest:
            dest.write(src.read())
    data = read_json()
    # 默认管理账号
    if not data.get("webui"):
        data["webui"] = {
            "username": "admin",
            "password": "admin123",
        }
    elif os.environ.get("WEBUI_USERNAME") and os.environ.get("WEBUI_PASSWORD"):
        data["webui"] = {
            "username": os.environ.get("WEBUI_USERNAME"),
            "password": os.environ.get("WEBUI_PASSWORD"),
        }
    # 默认定时规则
    if not data.get("crontab"):
        data["crontab"] = "0 8,18,20 * * *"
    write_json(data)


if __name__ == "__main__":
    init()
    reload_tasks()
    app.run(debug=os.environ.get("DEBUG", False), host="0.0.0.0", port=5005)
