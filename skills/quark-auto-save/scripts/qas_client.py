#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quark-auto-Save API Python Wrapper

Usage:
    python3 qas_client.py get-config                         # Get all config & tasks
    python3 qas_client.py search <query> [-d]                # Search resources
    python3 qas_client.py get-share <shareurl> [-a]          # Get share detail
    python3 qas_client.py check-path <path>                  # Check savepath
    python3 qas_client.py delete-file <path>                 # Delete cloud file
    python3 qas_client.py rename-file <path> <file_name>     # Rename cloud file
    python3 qas_client.py add-task <task.json>               # Add task
    python3 qas_client.py run-task [taskname|json]           # Run task(s)
    python3 qas_client.py update-task <name> <json>          # Update task
    python3 qas_client.py delete-task <taskname>             # Delete task
    python3 qas_client.py update-config <json>               # Update config
"""

import os
import sys
import io
import json
import urllib.request
import urllib.parse
import urllib.error
import argparse

# Fix Windows console encoding (GBK can't handle emoji/CJK)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# Config
QAS_BASE_URL = os.environ.get("QAS_BASE_URL", "")
QAS_TOKEN = os.environ.get("QAS_TOKEN", "")


def get_error(result: dict) -> str:
    """Extract error message from API response"""
    return result.get("message") or result.get("data", {}).get("error") or "Unknown error"


def ok(data=None):
    """Print success: 'OK' or 'OK <json>'"""
    if data is not None:
        print(f"OK {json.dumps(data, separators=(',', ':'), ensure_ascii=False)}")
    else:
        print("OK")


def fail(msg: str):
    """Print error: 'ERROR: message'"""
    print(f"ERROR: {msg}")


def parse_json_arg(arg: str) -> dict:
    """Parse JSON from string or file path"""
    try:
        return json.loads(arg)
    except json.JSONDecodeError:
        with open(arg, "r", encoding="utf-8") as f:
            return json.load(f)


def get(endpoint: str, params: dict = {}) -> dict:
    """GET request to QAS API"""
    url = f"{QAS_BASE_URL}{endpoint}"
    params = params or {}
    params["token"] = QAS_TOKEN
    query = urllib.parse.urlencode(params)
    full_url = f"{url}?{query}" if params else url
    try:
        resp = urllib.request.urlopen(full_url, timeout=30)
        return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"success": False, "message": f"HTTP {e.code}: {e.reason}"}
    except Exception as e:
        return {"success": False, "message": str(e)}


def post(endpoint: str, data: dict = {}, raw: bool = False) -> dict:
    """POST request to QAS API - token must be in query param, not body"""
    url = f"{QAS_BASE_URL}{endpoint}"
    data = data or {}
    # Token goes in query param, NOT in body (server ignores token in body)
    query = f"token={QAS_TOKEN}"
    full_url = f"{url}?{query}"
    json_data = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        full_url, data=json_data, headers={"Content-Type": "application/json"}
    )
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        content = resp.read().decode("utf-8")
        # Some endpoints return raw text (SSE) instead of JSON
        if raw:
            return {"success": True, "raw": content}
        return json.loads(content)
    except urllib.error.HTTPError as e:
        return {"success": False, "message": f"HTTP {e.code}: {e.reason}"}
    except json.JSONDecodeError:
        # Return raw content if not JSON
        return {"success": True, "raw": content if "content" in locals() else ""}
    except Exception as e:
        return {"success": False, "message": str(e)}


def cmd_get_config():
    """Get all config and tasks"""
    result = get("/data")
    if result.get("success"):
        ok(result["data"])
    else:
        fail(get_error(result))


def cmd_search(query: str, deep: bool = False):
    """Search resources - improved filtering for better results"""
    result = get("/task_suggestions", {"q": query, "d": "1" if deep else "0"})
    if result.get("success"):
        data = result.get("data", [])
        q = query.lower()
        content = [
            i
            for i in data
            if q in i.get("taskname", "").lower() or q in i.get("content", "").lower()
        ]
        ok(content or data[:20])
    else:
        fail(get_error(result))


def cmd_get_share(shareurl: str, task: dict = {}, show_all: bool = False):
    """Get share detail - output key info (max 10 files, or all with -a)"""
    data = {"shareurl": shareurl}
    if task:
        data["task"] = task  # type: ignore
    result = post("/get_share_detail", data)
    if result.get("success"):
        result_data = result["data"]
        share_info = result_data.get("share", {})
        file_list = result_data.get("list", [])
        total_count = len(file_list)

        info = {"name": share_info.get("title"), "total": total_count, "files": []}

        files = file_list if show_all else file_list[:10]
        for f in files:
            file_info = {
                "name": f.get("file_name"),
                "fid": f.get("fid"),
                "is_dir": f.get("dir", False),
            }

            if file_info["is_dir"]:
                file_info["include_items"] = f.get("include_items", "")
            else:
                file_info["size"] = f.get("size", "")
                file_info["type"] = f.get("obj_category", "")

            info["files"].append(file_info)

        if not show_all and total_count > 10:
            info["note"] = f"...还有 {total_count - 10} 个文件"

        ok(info)
    else:
        fail(get_error(result))


def _detect_auto_unarchive(task: dict, auto_clean_zipdir: bool = False) -> dict:
    """Auto-detect auto-unarchive config from share files"""
    ARCHIVE_EXT = {".zip", ".rar", ".7z"}
    shareurl = task.get("shareurl", "")
    if not shareurl:
        return {}
    detail = post("/get_share_detail", {"shareurl": shareurl})
    if not detail.get("success"):
        return {}
    files = detail.get("data", {}).get("list", [])
    has_archive = any(
        any(f.get("file_name", "").lower().endswith(ext) for ext in ARCHIVE_EXT) for f in files
    )
    if not has_archive:
        return {}
    return {
        "auto_unarchive": {
            "enable": True,
            "auto_clean": True,
            "auto_clean_zipdir": auto_clean_zipdir,
        }
    }


def cmd_add(task_json: str):
    """Add new task from JSON string or file"""
    task = parse_json_arg(task_json)
    # Auto-detect addition if not explicitly set
    if not task.get("addition", {}).get("auto_unarchive"):
        auto_unarchive = _detect_auto_unarchive(task)
        if auto_unarchive:
            task.setdefault("addition", {}).update(auto_unarchive)
    result = post("/api/add_task", task)
    if result.get("success"):
        ok()
    else:
        fail(get_error(result))


def cmd_run(taskname: str = "", task_json: str = ""):
    """Run task manually - returns SSE stream as JSON"""
    data = {}

    # Mode 3: Direct task parameters (no save)
    if task_json:
        try:
            task = json.loads(task_json)
            # Auto-detect addition if not explicitly set
            if not task.get("addition", {}).get("auto_unarchive"):
                auto_unarchive = _detect_auto_unarchive(task, auto_clean_zipdir=True)
                if auto_unarchive:
                    task.setdefault("addition", {}).update(auto_unarchive)
            data["tasklist"] = [task] if isinstance(task, dict) else task
        except json.JSONDecodeError:
            fail("Invalid JSON for direct task")
            return

    # Mode 2: Run specific saved task
    elif taskname:
        result = get("/data")
        if not result.get("success"):
            fail(get_error(result))
            return
        task = next(
            (t for t in result["data"].get("tasklist", []) if t.get("taskname") == taskname),
            None,
        )
        if not task:
            fail(f"Task '{taskname}' not found")
            return
        data["tasklist"] = [task]

    # Mode 1: Run all tasks (empty data)

    # Use raw=True to get plain text response
    result = post("/run_script_now", data, raw=True)
    if result.get("success"):
        print("OK")
        raw = result.get("raw", "")
        # Parse SSE-like format: "data: <content>"
        for line in raw.split("\n"):
            if line.startswith("data: "):
                content = line[6:]  # Remove "data: " prefix
                if content and content != "[DONE]":
                    print(content)
    else:
        fail(get_error(result))


def cmd_check_path(path: str = "", fid: str = ""):
    """Get savepath detail - output key info only (max 10 files)"""
    params = {}
    if path:
        params["path"] = path
    elif fid:
        params["fid"] = fid
    result = get("/get_savepath_detail", params)
    if result.get("success"):
        data = result["data"]
        total_count = len(data.get("list", []))
        # Output key info only
        info = {"total": total_count, "files": []}

        # Add path info
        if data.get("paths"):
            info["path"] = "/" + "/".join(p.get("name", "") for p in data["paths"])

        # Extract key file info (max 10)
        files = data.get("list", [])[:10]
        for f in files:
            file_info = {
                "name": f.get("file_name"),
                "fid": f.get("fid"),
                "is_dir": f.get("dir", False),
            }

            if file_info["is_dir"]:
                file_info["include_items"] = f.get("include_items", "")
            else:
                file_info["size"] = f.get("size", 0)

            info["files"].append(file_info)

        # Add note if there are more files
        if total_count > 10:
            info["note"] = f"...还有 {total_count - 10} 个文件"

        ok(info)
    else:
        fail(get_error(result))


def cmd_delete_task(taskname: str):
    """Delete a task from tasklist by name"""
    result = get("/data")
    if not result.get("success"):
        fail(get_error(result))
        return
    tasklist = result["data"].get("tasklist", [])
    new_tasklist = [t for t in tasklist if t.get("taskname") != taskname]
    if len(new_tasklist) == len(tasklist):
        fail(f"Task '{taskname}' not found")
        return
    update_result = post("/update", {"tasklist": new_tasklist})
    if update_result.get("success"):
        ok({"message": f"Task '{taskname}' deleted"})
    else:
        fail(get_error(update_result))


def cmd_update_config(update_json: str):
    """Update config via JSON string or file"""
    result = get("/data")
    if not result.get("success"):
        fail(get_error(result))
        return
    data = parse_json_arg(update_json)
    result.update(data)
    update_result = post("/update", result)
    if update_result.get("success"):
        ok()
    else:
        fail(get_error(update_result))


def cmd_update_task(taskname: str, update_json: str):
    """Update a specific task by name with partial fields"""
    result = get("/data")
    if not result.get("success"):
        fail(get_error(result))
        return
    tasklist = result["data"].get("tasklist", [])
    task = next((t for t in tasklist if t.get("taskname") == taskname), None)
    if not task:
        fail(f"Task '{taskname}' not found")
        return
    updates = parse_json_arg(update_json)
    # Remove shareurl_ban
    task.pop("shareurl_ban", None)
    task.update(updates)
    new_tasklist = [task if t.get("taskname") == taskname else t for t in tasklist]
    update_result = post("/update", {"tasklist": new_tasklist})
    if update_result.get("success"):
        ok({"message": f"Task '{taskname}' updated", "task": task})
    else:
        fail(get_error(update_result))


def cmd_delete_file(path: str):
    """Delete cloud file by path"""
    result = post("/delete_file", {"path": path})
    if result.get("success"):
        ok()
    else:
        fail(get_error(result))


def cmd_rename_file(path: str, file_name: str):
    """Rename cloud file by path"""
    result = post("/rename_file", {"path": path, "file_name": file_name})
    if result.get("success"):
        ok()
    else:
        fail(get_error(result))


def main():
    parser = argparse.ArgumentParser(description="Quark Auto-Save API Client")
    parser.add_argument(
        "command",
        choices=[
            "get-config",
            "search",
            "get-share",
            "add-task",
            "run-task",
            "check-path",
            "delete-file",
            "delete-task",
            "rename-file",
            "update-config",
            "update-task",
        ],
        help="Command to execute",
    )
    parser.add_argument("args", nargs="*", help="Command arguments")
    parser.add_argument("--deep", "-d", action="store_true", help="Deep search")
    parser.add_argument("--all", "-a", action="store_true", help="Show all files (detail)")

    args = parser.parse_args()

    if not QAS_TOKEN:
        fail("QAS_TOKEN not set. Set environment variable or update TOOLS.md")
        sys.exit(1)

    if args.command == "get-config":
        cmd_get_config()
    elif args.command == "search":
        if not args.args:
            fail("Usage: search <query>")
            sys.exit(1)
        cmd_search(args.args[0], args.deep)
    elif args.command == "get-share":
        if not args.args:
            fail("Usage: get-share <shareurl>")
            sys.exit(1)
        cmd_get_share(args.args[0], show_all=args.all)
    elif args.command == "add-task":
        if not args.args:
            fail("Usage: add-task <json_string_or_file>")
            sys.exit(1)
        cmd_add(args.args[0])
    elif args.command == "run-task":
        if not args.args:
            # Run all tasks
            cmd_run()
        elif len(args.args) == 1:
            # Check if it's JSON or taskname
            arg = args.args[0]
            if arg.startswith("{") or arg.startswith("["):
                # Direct JSON task
                cmd_run(task_json=arg)
            else:
                # Saved task name
                cmd_run(taskname=arg)
        else:
            fail("Usage: run-task [taskname|json_string]")
            sys.exit(1)
    elif args.command == "check-path":
        if not args.args:
            fail("Usage: check-path <path>")
            sys.exit(1)
        cmd_check_path(args.args[0])
    elif args.command == "delete-file":
        if not args.args:
            fail("Usage: delete-file <path>")
            sys.exit(1)
        cmd_delete_file(args.args[0])
    elif args.command == "delete-task":
        if not args.args:
            fail("Usage: delete-task <taskname>")
            sys.exit(1)
        cmd_delete_task(args.args[0])
    elif args.command == "rename-file":
        if len(args.args) < 2:
            fail("Usage: rename-file <path> <file_name>")
            sys.exit(1)
        cmd_rename_file(args.args[0], args.args[1])
    elif args.command == "update-config":
        if not args.args:
            fail("Usage: update-config <json_string_or_file>")
            sys.exit(1)
        cmd_update_config(args.args[0])
    elif args.command == "update-task":
        if len(args.args) < 2:
            fail("Usage: update-task <taskname> <json_string_or_file>")
            sys.exit(1)
        cmd_update_task(args.args[0], args.args[1])


if __name__ == "__main__":
    main()
