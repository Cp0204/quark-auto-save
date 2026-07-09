# tracker.py — 豆瓣自动追踪+转存核心逻辑
import re
import time
import logging
import traceback
from datetime import datetime, timezone, timedelta
from threading import Lock, get_ident, Thread
from concurrent.futures import ThreadPoolExecutor, as_completed

from .api import get_douban_list, CATEGORIES
from .history import load_history, save_history, add_exec_record, update_exec_record

TZ = timezone(timedelta(hours=8))

VIDEO_SUB = r".*?\.(mp4|mkv|avi|ts|rmvb|flv|mov|srt|ass|ssa|sub|idx)"
TV_REPLACE = "{TASKNAME}.{SXX}E{E}.{EXT}"

transfer_status = {
    "running": False,
    "summary": None,
    "start_time": None,
    "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": 0},
    "thread_id": None,
    "stop": False,
}
transfer_lock = Lock()

# 进度日志（内存，前端轮询读取）
log_progress = []
LOG_PROGRESS_MAX = 500


def _log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    logging.info(f"[douban] {msg}")
    log_progress.append(line)
    if len(log_progress) > LOG_PROGRESS_MAX:
        del log_progress[: len(log_progress) - LOG_PROGRESS_MAX]


def clear_progress():
    log_progress.clear()


def is_transfer_running():
    with transfer_lock:
        if not transfer_status.get("running"):
            return False
        tid = transfer_status.get("thread_id")
        if tid is None:
            return False
        import threading
        for t in threading.enumerate():
            if t.ident == tid and t.is_alive():
                return True
        transfer_status["running"] = False
        transfer_status["stop"] = False
        _log("检测到转存线程已结束，自动重置状态")
        return False


def _clean_title(title):
    return re.sub(r"[^\u4e00-\u9fff0-9a-zA-Z]", "", title).lower()


def _build_history_index(history, qas_tasklist=None):
    index = {"exact": set(history.keys()), "clean": set()}
    for k in history:
        index["clean"].add(_clean_title(k))
    index["items"] = [(k, _clean_title(k)) for k in history]
    if qas_tasklist:
        index["qas_clean"] = set()
        index["qas_items"] = []
        for t in qas_tasklist:
            name = t.get("taskname", "")
            if name:
                index["qas_clean"].add(_clean_title(name))
                index["qas_items"].append((name, _clean_title(name)))
    else:
        index["qas_clean"] = set()
        index["qas_items"] = []
    return index


def _find_in_history(title, history, index=None):
    if title in history:
        return True
    if index:
        if title in index["exact"]:
            return True
        title_clean = _clean_title(title)
        if title_clean in index["clean"]:
            return True
        for k, k_clean in index["items"]:
            if title_clean == k_clean or (len(title_clean) >= 3 and title_clean in k_clean) or (
                len(k_clean) >= 3 and k_clean in title_clean
            ):
                return True
        for name, name_clean in index["qas_items"]:
            if title_clean == name_clean or (len(title_clean) >= 3 and title_clean in name_clean) or (
                len(name_clean) >= 3 and name_clean in title_clean
            ):
                return True
        return False
    title_clean = _clean_title(title)
    for k in history:
        k_clean = _clean_title(k)
        if title_clean == k_clean or (len(title_clean) >= 3 and title_clean in k_clean) or (
            len(k_clean) >= 3 and k_clean in title_clean
        ):
            return True
    return False


def build_transfer_tasks(tasks_config, filters=None):
    """从豆瓣榜单构建转存任务列表"""
    filters = filters or {}
    all_t = []
    for tk in tasks_config:
        try:
            items = get_douban_list(
                tk["path"],
                tk["type"],
                20,
                min_rating=filters.get("min_rating", 0),
                sort_by=filters.get("sort_by", "rating"),
                year_from=filters.get("year_from", 0),
                year_to=filters.get("year_to", 0),
            )
            for i in items:
                all_t.append(
                    {"title": i["title"], "savepath": tk["savepath"], "category": tk.get("category", "movie")}
                )
        except Exception as e:
            _log(f"获取错误: {e}")
    seen = set()
    uniq = []
    for ti in all_t:
        if ti["title"] not in seen:
            seen.add(ti["title"])
            uniq.append(ti)
    _log(f"共获取 {len(uniq)} 条")
    return uniq


# ===== 三源搜索（复用 QAS 的搜索能力） =====

def _search_resource(title, config_data):
    """使用 QAS 的三源搜索查找资源（PanSou + CloudSaver + 网络）"""
    from sdk.cloudsaver import CloudSaver
    from sdk.pansou import PanSou
    import base64
    import requests as req

    source = config_data.get("source", {})
    net_data = source.get("net", {})
    cs_data = source.get("cloudsaver", {})
    ps_data = source.get("pansou", {})
    search_results = []

    def net_search():
        if str(net_data.get("enable", "true")).lower() != "false":
            try:
                base_url = base64.b64decode("aHR0cHM6Ly9zLjkxNzc4OC54eXo=").decode()
                url = f"{base_url}/task_suggestions?q={title}&d="
                response = req.get(url, timeout=15)
                return response.json()
            except Exception:
                return []
        return []

    def cs_search():
        if cs_data.get("server") and cs_data.get("username") and cs_data.get("password"):
            try:
                cs = CloudSaver(cs_data.get("server"))
                cs.set_auth(cs_data.get("username", ""), cs_data.get("password", ""), cs_data.get("token", ""))
                search = cs.auto_login_search(title)
                if search.get("success"):
                    return cs.clean_search_results(search.get("data"))
            except Exception:
                pass
        return []

    def ps_search():
        if ps_data.get("server"):
            try:
                ps = PanSou(ps_data.get("server"))
                return ps.search(title)
            except Exception:
                pass
        return []

    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(net_search), executor.submit(cs_search), executor.submit(ps_search)]
            for future in as_completed(futures):
                result = future.result()
                if isinstance(result, list):
                    search_results.extend(result)

        # 去重
        results = []
        link_array = []
        search_results.sort(key=lambda x: x.get("datetime", ""), reverse=True)
        for item in search_results:
            url = item.get("shareurl", "")
            if url and url not in link_array:
                link_array.append(url)
                results.append(item)
        return results
    except Exception as e:
        _log(f"搜索异常: {e}")
        return []


# ===== 转存执行（直接调用 Quark 类，不走 HTTP） =====

def _do_transfer(title, shareurl, savepath, pattern="", replace="", cookie=""):
    """直接调用 Quark 类执行转存"""
    from quark_auto_save import Quark

    try:
        account = Quark(cookie)
        pwd_id, passcode, pdir_fid, paths = account.extract_url(shareurl)
        get_stoken = account.get_stoken(pwd_id, passcode)
        if get_stoken.get("status") != 200:
            return {"status": "error", "msg": f"获取stoken失败: {get_stoken.get('message')}"}
        stoken = get_stoken["data"]["stoken"]

        share_detail = account.get_detail(pwd_id, stoken, pdir_fid, _fetch_share=1)
        if share_detail.get("code") != 0:
            return {"status": "error", "msg": share_detail.get("message", "获取详情失败")}

        file_list = share_detail["data"].get("list", [])
        if not file_list:
            return {"status": "error", "msg": "分享内容为空"}

        # 获取或创建保存目录
        save_fids = account.get_fids([savepath])
        if save_fids:
            to_pdir_fid = save_fids[0]["fid"]
        else:
            mkdir_result = account.mkdir(savepath)
            if mkdir_result.get("code") == 0:
                to_pdir_fid = mkdir_result["data"]["fid"]
            else:
                return {"status": "error", "msg": f"创建目录失败: {mkdir_result.get('message')}"}

        # 过滤匹配的文件
        if pattern:
            matched = [f for f in file_list if re.search(pattern, f.get("file_name", ""))]
        else:
            matched = file_list

        if not matched:
            return {"status": "error", "msg": "没有匹配的文件"}

        # 执行转存（Quark.save_file 需要 fid_list 和 fid_token_list 两个参数）
        fid_list = [f["fid"] for f in matched]
        fid_token_list = [f["share_fid_token"] for f in matched]
        save_result = account.save_file(fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken)
        if save_result.get("code") == 0:
            task_id = save_result.get("data", {}).get("task_id")
            if task_id:
                query_result = account.query_task(task_id)
                if query_result.get("code") == 0:
                    save_as_top_fids = query_result.get("data", {}).get("save_as", {}).get("save_as_top_fids", [])
                    if not save_as_top_fids:
                        return {"status": "exists", "msg": "已存在"}
                    return {"status": "ok", "msg": f"转存成功 ({len(save_as_top_fids)} 个文件)"}
                else:
                    return {"status": "error", "msg": query_result.get("message", "查询转存结果失败")}
            return {"status": "ok", "msg": "转存成功"}
        else:
            return {"status": "error", "msg": save_result.get("message", "转存失败")}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


def run_transfer(task_list, limit, config_data):
    """执行批量转存"""
    global transfer_status
    tid = get_ident()
    exec_record_id = None
    try:
        rec = add_exec_record("transfer", f"开始转存 ({len(task_list)} 条)", "running")
        exec_record_id = rec["id"]
    except Exception:
        pass

    with transfer_lock:
        transfer_status.update(
            {
                "running": True,
                "summary": None,
                "start_time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": len(task_list)},
                "thread_id": tid,
                "stop": False,
            }
        )
    clear_progress()
    cookie = config_data.get("cookie", [""])
    cookie = cookie[0] if isinstance(cookie, list) and cookie else (cookie or "")

    _log(f"开始转存，上限 {limit}")
    history = load_history()

    # 获取 QAS 现有任务列表用于去重
    qas_tasklist = config_data.get("tasklist", [])
    history_index = _build_history_index(history, qas_tasklist)

    transferred = 0
    results = []
    error_msg = None

    try:
        pending_tasks = []
        for task in task_list:
            title = task["title"]
            if _find_in_history(title, history, history_index):
                _log(f"已跳过: {title}")
                results.append({"title": title, "status": "skipped", "msg": "skip", "category": task.get("category", "")})
                with transfer_lock:
                    transfer_status["stats"]["skipped"] += 1
                continue
            pending_tasks.append(task)

        _log(f"待搜索任务: {len(pending_tasks)} 条")

        # 串行搜索+转存（避免并发请求过多）
        for task in pending_tasks:
            if transfer_status.get("stop"):
                _log("任务已被用户终止")
                break
            if transferred >= limit:
                _log(f"已达上限: {limit}")
                break

            title = task["title"]
            savepath = task["savepath"]
            category = task.get("category", "movie")

            _log(f"搜索: {title}")
            sr = _search_resource(title, config_data)

            with transfer_lock:
                transfer_status["stats"]["searched"] += 1

            if not sr:
                _log(f"未找到: {title}")
                results.append({"title": title, "status": "not_found", "msg": "not_found", "category": category})
                with transfer_lock:
                    transfer_status["stats"]["failed"] += 1
                continue

            chosen = sr[0]
            shareurl = chosen.get("shareurl", "")
            _log(f"找到: {chosen.get('taskname', title)}")

            pattern = VIDEO_SUB
            replace = TV_REPLACE if category == "tv" else ""
            res = _do_transfer(title, shareurl, f"{savepath}/{title}", pattern, replace, cookie)
            _log(f"  {res['msg']}")

            history[title] = {
                "date": datetime.now(TZ).strftime("%Y-%m-%d"),
                "status": res["status"],
                "category": category,
                "shareurl": shareurl,
            }
            results.append({"title": title, "status": res["status"], "msg": res["msg"], "category": category})

            if res["status"] in ("ok", "done"):
                transferred += 1
                with transfer_lock:
                    transfer_status["stats"]["ok"] += 1
            elif res["status"] == "exists":
                with transfer_lock:
                    transfer_status["stats"]["skipped"] += 1
            else:
                with transfer_lock:
                    transfer_status["stats"]["failed"] += 1

            time.sleep(3)
    except Exception as e:
        error_msg = str(e)
        _log(f"转存异常: {e}")
        traceback.print_exc()
        results.append({"title": "异常中断", "status": "error", "msg": error_msg})
    finally:
        save_history(history)
        with transfer_lock:
            transfer_status["running"] = False
            transfer_status["stop"] = False
            transfer_status["summary"] = {
                "transferred": transferred,
                "total": len(task_list),
                "results": results,
                "error": error_msg,
            }
        _log(f"转存完成: {transferred} 条")
        if exec_record_id:
            ok_count = sum(1 for r in results if r.get("status") in ("ok", "done"))
            fail_count = sum(1 for r in results if r.get("status") not in ("ok", "done", "skipped", "exists"))
            skip_count = sum(1 for r in results if r.get("status") in ("skipped", "exists"))
            final_status = "fail" if error_msg or fail_count > 0 else "ok"
            detail = f"转存完成 成功{ok_count} 失败{fail_count} 跳过{skip_count}"
            try:
                update_exec_record(
                    exec_record_id,
                    detail=detail,
                    status=final_status,
                    data={"results": results, "ok": ok_count, "failed": fail_count, "skipped": skip_count},
                )
            except Exception:
                pass


# ===== 失效检测 =====

def check_expired_tasks(config_data, limit=None):
    """检测失效链接"""
    from quark_auto_save import Quark

    try:
        cookie = config_data.get("cookie", [""])
        cookie = cookie[0] if isinstance(cookie, list) and cookie else (cookie or "")
        account = Quark(cookie)

        # 获取所有任务
        tasklist = config_data.get("tasklist", [])
        # 过滤夸克链接
        to_check = [t for t in tasklist if t.get("shareurl", "") and "quark.cn" in t.get("shareurl", "")]

        if limit:
            to_check = to_check[:limit]
        if not to_check:
            _log("失效检测: 无符合条件的任务")
            return []

        _log(f"检测失效链接: {len(to_check)} 个")
        expired = []
        for task in to_check:
            if transfer_status.get("stop"):
                _log("检测已被用户终止")
                break
            try:
                shareurl = task.get("shareurl", "")
                pwd_id, passcode, pdir_fid, paths = account.extract_url(shareurl)
                get_stoken = account.get_stoken(pwd_id, passcode)
                if get_stoken.get("status") != 200:
                    expired.append(task)
                    continue
                stoken = get_stoken["data"]["stoken"]
                share_detail = account.get_detail(pwd_id, stoken, pdir_fid, _fetch_share=1)
                if share_detail.get("code") != 0:
                    expired.append(task)
            except Exception as e:
                _log(f"检测失败 {task.get('taskname', '')}: {e}")
                expired.append(task)

        _log(f"检测完成: {len(expired)} 个失效")
        return expired
    except Exception as e:
        _log(f"检测失效出错: {e}")
        return []


def fix_expired_tasks(config_data):
    """修复失效链接：搜索替代资源并更新"""
    global transfer_status
    tid = get_ident()
    with transfer_lock:
        transfer_status.update(
            {"running": True, "thread_id": tid, "summary": "fix_expired", "stop": False}
        )
    try:
        expired = check_expired_tasks(config_data)
        if not expired:
            _log("没有失效链接，无需修复")
            return {"total": 0, "fixed": 0, "failed": 0, "results": []}

        _log(f"开始修复 {len(expired)} 个失效链接")
        fixed = 0
        failed = 0
        results = []

        for task in expired:
            if transfer_status.get("stop"):
                _log("修复已被用户终止")
                break
            taskname = task.get("taskname", "")
            _log(f"搜索替换: {taskname}")

            try:
                sr = _search_resource(taskname, config_data)
                if not sr:
                    _log("  未找到替代资源")
                    failed += 1
                    results.append({"taskname": taskname, "status": "not_found", "msg": "未找到替代资源"})
                    continue

                chosen = sr[0]
                new_url = chosen.get("shareurl", "")
                if not new_url:
                    _log("  资源无有效链接")
                    failed += 1
                    results.append({"taskname": taskname, "status": "no_url", "msg": "资源无有效链接"})
                    continue

                # 更新任务的 shareurl
                task["shareurl"] = new_url
                fixed += 1
                results.append({"taskname": taskname, "status": "fixed", "msg": chosen.get("taskname", "")})
                _log(f"  ✅ 替换成功: {chosen.get('taskname', '')}")
                time.sleep(2)
            except Exception as e:
                _log(f"  ❌ 异常: {e}")
                failed += 1
                results.append({"taskname": taskname, "status": "error", "msg": str(e)})

        # 保存更新后的配置
        if fixed > 0:
            from quark_auto_save import Config
            Config.write_json(
                config_data.get("_config_path", "./config/quark_config.json"),
                config_data,
            )

        _log(f"修复完成: 成功 {fixed} / 失败 {failed}")
        return {"total": len(expired), "fixed": fixed, "failed": failed, "results": results}
    finally:
        with transfer_lock:
            transfer_status["running"] = False
            transfer_status["stop"] = False
            transfer_status["thread_id"] = None


def transfer_one(title, shareurl, savepath, pattern="", replace="", category="movie", config_data=None):
    """单条转存"""
    tid = get_ident()
    with transfer_lock:
        transfer_status.update(
            {
                "running": True,
                "summary": None,
                "start_time": datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "stats": {"searched": 0, "ok": 0, "skipped": 0, "failed": 0, "total": 1},
                "thread_id": tid,
                "stop": False,
            }
        )
    clear_progress()
    cookie = config_data.get("cookie", [""])
    cookie = cookie[0] if isinstance(cookie, list) and cookie else (cookie or "")
    try:
        res = _do_transfer(title, shareurl, savepath, pattern, replace, cookie)
        with transfer_lock:
            if res["status"] in ("ok", "done"):
                transfer_status["stats"]["ok"] += 1
            elif res["status"] == "exists":
                transfer_status["stats"]["skipped"] += 1
            else:
                transfer_status["stats"]["failed"] += 1
        history = load_history()
        history[title] = {
            "date": datetime.now(TZ).strftime("%Y-%m-%d"),
            "status": res["status"],
            "category": category,
            "shareurl": shareurl,
        }
        save_history(history)
        return res
    finally:
        with transfer_lock:
            transfer_status["running"] = False
            transfer_status["stop"] = False
            transfer_status["thread_id"] = None
