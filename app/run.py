from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    send_from_directory,
)
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import json
import os


# 定义 JSON 文件路径
config_path = os.environ.get("CONFIG_PATH", "./config/quark_config.json")

app = Flask(__name__)
app.secret_key = "ca943f6db6dd34823d36ab08d8d6f65d"
app.config["JSON_AS_ASCII"] = False
app.config["JSON_SORT_KEYS"] = False
app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False


# 设置icon
@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "favicon.ico",
        mimetype="image/vnd.microsoft.icon",
    )


# 读取 JSON 文件内容
def read_json():
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


# 将数据写入 JSON 文件
def write_json(data):
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False, sort_keys=False)


# 登录页面
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        data = read_json()
        username = request.form.get("username")
        password = request.form.get("password")
        # 验证用户名和密码
        if (
            username == data["webui"]["username"]
            and password == data["webui"]["password"]
        ):
            session["username"] = username
            return redirect(url_for("index"))
        else:
            return render_template("login.html", message="登录失败")

    return render_template("login.html", error=None)


# 退出登录
@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect(url_for("login"))


# 管理页面
@app.route("/")
def index():
    if not session.get("username"):
        return redirect(url_for("login"))
    return render_template("index.html")


# 获取配置数据
@app.route("/data")
def get_data():
    if not session.get("username"):
        return redirect(url_for("login"))
    data = read_json()
    del data["webui"]
    return jsonify(data)


# 更新数据
@app.route("/update", methods=["POST"])
def update():
    if not session.get("username"):
        return "未登录"
    data = read_json()
    webui = data["webui"]
    data = request.json
    data["webui"] = webui
    write_json(data)
    reload_tasks()  # 重新加载任务
    return "配置更新成功"


# 定时任务执行的函数
def run_script(script_path):
    os.system(f"python {script_path}")


# 重新加载任务
def reload_tasks():
    # 读取数据
    data = read_json()
    # 添加新任务
    crontab = data.get("crontab")
    if crontab:
        print(">>> 重载调度器")
        print(f"调度状态: {scheduler.state}")
        print(f"定时规则: {crontab}")
        if scheduler.state == 1:
            scheduler.pause()  # 暂停调度器
        trigger = CronTrigger.from_crontab(crontab)
        scheduler.remove_all_jobs()
        scheduler.add_job(
            run_script, trigger=trigger, args=[f"quark_auto_save.py {config_path}"]
        )
        if scheduler.state == 2:
            scheduler.resume()  # 恢复调度器


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
    # 默认定时规则
    if not data.get("crontab"):
        data["crontab"] = "0 8,18,20 * * *"
    write_json(data)


if __name__ == "__main__":
    init()
    # 创建后台调度器
    scheduler = BackgroundScheduler()
    reload_tasks()
    scheduler.start()
    app.run(debug=os.environ.get("DEBUG", False), host="0.0.0.0", port=5005)
