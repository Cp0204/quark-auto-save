# routes.py — 豆瓣追踪模块的 Flask 路由
import threading
from flask import Blueprint, jsonify, request, Response, stream_with_context

from .api import CATEGORIES, get_douban_list
from .tracker import (
    build_transfer_tasks,
    run_transfer,
    transfer_one,
    is_transfer_running,
    transfer_status,
    transfer_lock,
    log_progress,
    clear_progress,
    check_expired_tasks,
    fix_expired_tasks,
    _search_resource,
    _do_transfer,
)
from .history import (
    load_history,
    save_history,
    load_exec_history,
    add_exec_record,
    clear_exec_history,
    invalidate_history_cache,
)

douban_bp = Blueprint("douban", __name__, url_prefix="/api/douban")


def _get_config_data():
    """从 Flask app 上下文获取全局 config_data"""
    from flask import current_app
    return current_app.config.get("config_data", {})


# ===== 分类 =====

@douban_bp.route("/categories")
def get_categories():
    return jsonify({"success": True, "data": CATEGORIES})


# ===== 榜单列表 =====

@douban_bp.route("/list")
def get_list():
    path = request.args.get("path", "movie/hot")
    sub_type = request.args.get("type", "全部")
    limit = int(request.args.get("limit", 20))
    min_rating = float(request.args.get("min_rating", 0))
    sort_by = request.args.get("sort_by", "rating")
    year_from = int(request.args.get("year_from", 0))
    year_to = int(request.args.get("year_to", 0))
    items = get_douban_list(path, sub_type, limit, min_rating, sort_by, year_from, year_to)
    return jsonify({"success": True, "data": items})


# ===== 批量转存 =====

@douban_bp.route("/transfer", methods=["POST"])
def start_transfer():
    if is_transfer_running():
        return jsonify({"success": False, "message": "转存正在进行中"})

    body = request.json or {}
    tasks = body.get("tasks", [])
    limit = body.get("limit", 10)
    filters = body.get("filters", {})

    if not tasks:
        return jsonify({"success": False, "message": "请选择至少一个榜单"})

    config_data = _get_config_data()
    task_list = build_transfer_tasks(tasks, filters)

    if not task_list:
        return jsonify({"success": False, "message": "未获取到任何影片"})

    t = threading.Thread(target=run_transfer, args=(task_list, limit, config_data), daemon=True)
    t.start()
    return jsonify({"success": True, "message": f"已开始转存 ({len(task_list)} 条)"})


# ===== 转存状态 =====

@douban_bp.route("/transfer/status")
def transfer_status_api():
    with transfer_lock:
        stats = dict(transfer_status.get("stats", {}))
        running = is_transfer_running()
        summary = transfer_status.get("summary")
        start_time = transfer_status.get("start_time")
    progress = list(log_progress)
    return jsonify({
        "success": True,
        "running": running,
        "start_time": start_time,
        "stats": stats,
        "summary": summary,
        "progress": progress,
    })


# ===== 停止转存 =====

@douban_bp.route("/transfer/stop", methods=["POST"])
def stop_transfer():
    with transfer_lock:
        transfer_status["stop"] = True
    return jsonify({"success": True, "message": "正在停止..."})


# ===== 单条转存 =====

@douban_bp.route("/transfer_one", methods=["POST"])
def transfer_one_api():
    if is_transfer_running():
        return jsonify({"success": False, "message": "busy", "conflict": True})

    body = request.json or {}
    title = body.get("title", "").strip()
    savepath = body.get("savepath", "").strip()
    category = body.get("category", "movie")
    shareurl = body.get("shareurl", "")
    pattern = body.get("pattern", "")
    replace = body.get("replace", "")

    if not title or not savepath:
        return jsonify({"success": False, "message": "缺少必要参数"}), 400

    config_data = _get_config_data()

    if shareurl:
        t = threading.Thread(
            target=transfer_one,
            args=(title, sharepath, savepath, pattern, replace, category),
            kwargs={"config_data": config_data},
            daemon=True,
        )
        t.start()
        return jsonify({"success": True, "message": "已提交转存"})
    else:
        task = {"title": title, "savepath": savepath, "category": category}
        t = threading.Thread(target=run_transfer, args=([task], 1, config_data), daemon=True)
        t.start()
        return jsonify({"success": True, "message": "已提交搜索转存"})


# ===== 搜索 =====

@douban_bp.route("/search")
def search():
    keyword = request.args.get("q", "") or request.args.get("keyword", "")
    if not keyword:
        return jsonify({"success": False, "message": "请输入搜索关键词"})

    config_data = _get_config_data()
    results = _search_resource(keyword, config_data)
    return jsonify({"success": True, "data": results})


# ===== 历史记录 =====

@douban_bp.route("/history")
def get_history():
    history = load_history()
    return jsonify({"success": True, "total": len(history), "items": history})


@douban_bp.route("/history/manage", methods=["POST"])
def manage_history():
    body = request.json or {}
    action = body.get("action", "")

    if action == "delete":
        titles = body.get("titles", [])
        history = load_history()
        for t in titles:
            history.pop(t, None)
        save_history(history)
        return jsonify({"success": True, "message": f"已删除 {len(titles)} 条"})
    elif action == "clear":
        save_history({})
        return jsonify({"success": True, "message": "已清空历史"})
    elif action == "add":
        title = body.get("title", "")
        shareurl = body.get("shareurl", "")
        category = body.get("category", "movie")
        if not title:
            return jsonify({"success": False, "message": "缺少 title"}), 400
        history = load_history()
        history[title] = {
            "date": "",
            "status": "manual",
            "category": category,
            "shareurl": shareurl,
        }
        save_history(history)
        return jsonify({"success": True, "message": "已添加"})
    elif action == "update":
        title = body.get("title", "")
        shareurl = body.get("shareurl", "")
        history = load_history()
        if title in history:
            history[title]["shareurl"] = shareurl
            save_history(history)
            return jsonify({"success": True, "message": "已更新"})
        return jsonify({"success": False, "message": "记录不存在"}), 404
    else:
        return jsonify({"success": False, "message": "未知操作"}), 400


# ===== 执行历史 =====

@douban_bp.route("/exec_history")
def get_exec_history():
    items = load_exec_history()
    return jsonify({"success": True, "total": len(items), "items": items})


@douban_bp.route("/exec_history/clear", methods=["POST"])
def clear_exec():
    clear_exec_history()
    return jsonify({"success": True, "message": "已清空执行历史"})


# ===== 失效检测 =====

@douban_bp.route("/check_expired")
def check_expired():
    limit = int(request.args.get("limit", 50))
    config_data = _get_config_data()
    expired = check_expired_tasks(config_data, limit)
    return jsonify({"success": True, "expired": expired})


@douban_bp.route("/fix_expired", methods=["POST"])
def fix_expired():
    if is_transfer_running():
        return jsonify({"success": False, "message": "有任务正在执行"})
    config_data = _get_config_data()
    t = threading.Thread(target=fix_expired_tasks, args=(config_data,), daemon=True)
    t.start()
    return jsonify({"success": True, "message": "已启动失效链接修复"})


# ===== 豆瓣定时调度 =====

@douban_bp.route("/schedule")
def get_schedule():
    config_data = _get_config_data()
    douban_config = config_data.get("douban", {})
    return jsonify({"success": True, "data": douban_config})


@douban_bp.route("/schedule", methods=["POST"])
def save_schedule():
    from flask import current_app
    body = request.json or {}
    action = body.get("action", "save")

    config_data = _get_config_data()
    douban_config = config_data.setdefault("douban", {})

    if action == "save":
        if "transfer" in body:
            douban_config["transfer"] = body["transfer"]
        if "expired_check" in body:
            douban_config["expired_check"] = body["expired_check"]
        from quark_auto_save import Config
        Config.write_json(current_app.config["CONFIG_PATH"], config_data)
        # 重新注册调度
        _reload_douban_scheduler()
        return jsonify({"success": True, "message": "已保存"})
    elif action == "toggle":
        section = body.get("section", "")
        enabled = body.get("enabled", False)
        if section in douban_config:
            douban_config[section]["enabled"] = enabled
        from quark_auto_save import Config
        Config.write_json(current_app.config["CONFIG_PATH"], config_data)
        _reload_douban_scheduler()
        return jsonify({"success": True, "message": "已切换"})
    elif action == "run_now":
        section = body.get("section", "transfer")
        if section == "transfer":
            if is_transfer_running():
                return jsonify({"success": False, "message": "转存正在进行中"})
            transfer_cfg = douban_config.get("transfer", {})
            tasks = transfer_cfg.get("tasks", [])
            limit = transfer_cfg.get("limit", 10)
            if not tasks:
                return jsonify({"success": False, "message": "未配置转存任务"})
            task_list = build_transfer_tasks(tasks)
            if not task_list:
                return jsonify({"success": False, "message": "未获取到影片"})
            t = threading.Thread(target=run_transfer, args=(task_list, limit, config_data), daemon=True)
            t.start()
            return jsonify({"success": True, "message": "已开始转存"})
        elif section == "expired_check":
            t = threading.Thread(target=fix_expired_tasks, args=(config_data,), daemon=True)
            t.start()
            return jsonify({"success": True, "message": "已启动失效检测"})
        return jsonify({"success": False, "message": "未知 section"})
    else:
        return jsonify({"success": False, "message": "未知操作"}), 400


def _reload_douban_scheduler():
    """重新加载豆瓣模块的调度任务"""
    from flask import current_app
    scheduler = current_app.config.get("scheduler")
    if not scheduler:
        return

    config_data = _get_config_data()
    douban_config = config_data.get("douban", {})

    # 移除旧的豆瓣任务
    for job_id in ["douban_transfer", "douban_expired_check"]:
        try:
            scheduler.remove_job(job_id)
        except Exception:
            pass

    # 转存定时
    transfer_cfg = douban_config.get("transfer", {})
    if transfer_cfg.get("enabled") and transfer_cfg.get("crontab"):
        from apscheduler.triggers.cron import CronTrigger
        scheduler.add_job(
            _scheduled_transfer,
            trigger=CronTrigger.from_crontab(transfer_cfg["crontab"]),
            id="douban_transfer",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
            replace_existing=True,
        )

    # 失效检测定时
    expired_cfg = douban_config.get("expired_check", {})
    if expired_cfg.get("enabled") and expired_cfg.get("crontab"):
        from apscheduler.triggers.cron import CronTrigger
        scheduler.add_job(
            _scheduled_expired_check,
            trigger=CronTrigger.from_crontab(expired_cfg["crontab"]),
            id="douban_expired_check",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
            replace_existing=True,
        )


def _scheduled_transfer():
    """定时转存任务"""
    from flask import current_app
    config_data = current_app.config.get("config_data", {})
    douban_config = config_data.get("douban", {})
    transfer_cfg = douban_config.get("transfer", {})
    tasks = transfer_cfg.get("tasks", [])
    limit = transfer_cfg.get("limit", 10)
    if not tasks:
        return
    task_list = build_transfer_tasks(tasks)
    if task_list:
        run_transfer(task_list, limit, config_data)


def _scheduled_expired_check():
    """定时失效检测"""
    from flask import current_app
    config_data = current_app.config.get("config_data", {})
    fix_expired_tasks(config_data)
