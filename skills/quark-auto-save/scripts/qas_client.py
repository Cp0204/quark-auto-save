#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quark-auto-Save API Python Wrapper

Usage:
    python qas_client.py data                    # Get all config
    python qas_client.py search <query>          # Search resources
    python qas_client.py detail <shareurl>       # Get share detail
    python qas_client.py add <task.json>         # Add task
    python qas_client.py run [<taskname>]        # Run task(s)
    python qas_client.py tasks                   # List all tasks
    python qas_client.py savepath <path>         # Check savepath
"""

import os
import sys
import json
import urllib.request
import urllib.parse
import urllib.error
import argparse

# Config
QAS_BASE_URL = os.environ.get("QAS_BASE_URL", "")
QAS_TOKEN = os.environ.get("QAS_TOKEN", "")


def get(endpoint: str, params: dict = None) -> dict:
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


def post(endpoint: str, data: dict = None, raw: bool = False) -> dict:
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


def cmd_data():
    """Get all config and tasks"""
    result = get("/data")
    if result.get("success"):
        data = result["data"]
        print(f"=== Config ===")
        print(f"API Token: {data.get('api_token')}")
        print(f"Crontab: {data.get('crontab')}")
        print(f"Tasks: {len(data.get('tasklist', []))}")
        print(f"\n=== Task List ===")
        for i, task in enumerate(data.get("tasklist", []), 1):
            enabled = "✓" if task.get("enabled", True) else "✗"
            print(f"{i}. [{enabled}] {task.get('taskname')}")
            print(f"   URL: {task.get('shareurl')[:60]}...")
            print(f"   Save: {task.get('savepath')}")
            print(f"   Pattern: {task.get('pattern')} -> {task.get('replace')}")
    else:
        print(f"Error: {result.get('message')}")
    return result


def cmd_search(query: str, deep: bool = False):
    """Search resources"""
    result = get("/task_suggestions", {"q": query, "d": "1" if deep else "0"})
    if result.get("success"):
        data = result.get("data", [])
        print(f"=== Search Results for '{query}' ({len(data)} found) ===")
        for item in data:
            title = item.get("title", "Untitled")
            shareurl = item.get("shareurl", "")
            datetime = item.get("datetime", "")
            print(f"- {title}")
            print(f"  URL: {shareurl}")
            print(f"  Time: {datetime}")
    else:
        print(f"Error: {result.get('message')}")
    return result


def cmd_detail(shareurl: str, task: dict = None):
    """Get share detail"""
    data = {"shareurl": shareurl}
    if task:
        data["task"] = task
    result = post("/get_share_detail", data)
    if result.get("success"):
        data = result["data"]
        print(f"=== Share Detail ===")
        print(f"Name: {data.get('file_name')}")
        print(f"Type: {'Folder' if data.get('dir') else 'File'}")
        print(f"\n=== Files ({len(data.get('list', []))}) ===")
        for f in data.get("list", [])[:20]:
            icon = "📁" if f.get("dir") else "📄"
            size = f.get("size", "")
            print(f"{icon} {f.get('file_name')} ({size})")
        if len(data.get("list", [])) > 20:
            print(f"... and {len(data.get('list', [])) - 20} more")

        # Show paths (breadcrumb)
        paths = data.get("paths", [])
        if paths:
            print(f"\n=== Path ===")
            print("/" + "/".join(p.get("name", "") for p in paths))
    else:
        error = result.get("data", {}).get("error") or result.get("message")
        print(f"Error: {error}")
    return result


def cmd_add(task_file: str):
    """Add new task"""
    with open(task_file, "r", encoding="utf-8") as f:
        task = json.load(f)
    task.pop("addition", None)
    result = post("/api/add_task", task)
    if result.get("success"):
        print(f"✓ Task added: {task.get('taskname')}")
    else:
        print(f"Error: {result.get('message')}")
    return result


def cmd_run(taskname: str = None):
    """Run task manually - returns SSE stream, print directly"""
    data = {}
    if taskname:
        result = get("/data")
        if result.get("success"):
            task = next(
                (
                    t
                    for t in result["data"].get("tasklist", [])
                    if t.get("taskname") == taskname
                ),
                None,
            )
            if task:
                data["tasklist"] = [task]
            else:
                print(f"Error: Task '{taskname}' not found")
                return result
        else:
            print(f"Error: {result.get('message')}")
            return result
    print(f"Running task{' ' + taskname if taskname else 's'}...")
    print("-" * 40)

    # Use raw=True to get plain text response
    result = post("/run_script_now", data, raw=True)
    if result.get("success"):
        raw = result.get("raw", "")
        # Parse SSE-like format: "data: <content>"
        for line in raw.split("\n"):
            if line.startswith("data: "):
                content = line[6:]  # Remove "data: " prefix
                print(content)
    else:
        print(f"Error: {result.get('message')}")
    return result


def cmd_savepath(path: str = None, fid: str = None):
    """Get savepath detail"""
    params = {}
    if path:
        params["path"] = path
    elif fid:
        params["fid"] = fid
    result = get("/get_savepath_detail", params)
    if result.get("success"):
        data = result["data"]
        print(f"=== Savepath Detail ===")
        paths = data.get("paths", [])
        if paths:
            print(f"Path: /" + "/".join(p.get("name", "") for p in paths))
        print(f"\n=== Files ({len(data.get('list', []))}) ===")
        for f in data.get("list", [])[:30]:
            icon = "📁" if f.get("dir") else "📄"
            size = f.get("size", "")
            print(f"{icon} {f.get('file_name')} ({size})")
    else:
        print(f"Error: {result.get('message')}")
    return result


def cmd_delete(fid: str):
    """Delete file"""
    result = post("/delete_file", {"fid": fid})
    if result.get("success"):
        print(f"✓ File deleted")
    else:
        print(f"Error: {result.get('message')}")
    return result


def main():
    parser = argparse.ArgumentParser(description="Quark Auto-Save API Client")
    parser.add_argument(
        "command",
        choices=[
            "data",
            "search",
            "detail",
            "add",
            "run",
            "tasks",
            "savepath",
            "delete",
        ],
        help="Command to execute",
    )
    parser.add_argument("args", nargs="*", help="Command arguments")
    parser.add_argument("--deep", "-d", action="store_true", help="Deep search")

    args = parser.parse_args()

    if not QAS_TOKEN:
        print("Error: QAS_TOKEN not set. Set environment variable or update TOOLS.md")
        sys.exit(1)

    if args.command == "data":
        cmd_data()
    elif args.command == "search":
        if not args.args:
            print("Usage: search <query>")
            sys.exit(1)
        cmd_search(args.args[0], args.deep)
    elif args.command == "detail":
        if not args.args:
            print("Usage: detail <shareurl>")
            sys.exit(1)
        cmd_detail(args.args[0])
    elif args.command == "add":
        if not args.args:
            print("Usage: add <task.json>")
            sys.exit(1)
        cmd_add(args.args[0])
    elif args.command == "run":
        taskname = args.args[0] if args.args else None
        cmd_run(taskname)
    elif args.command == "tasks":
        result = cmd_data()
    elif args.command == "savepath":
        if not args.args:
            print("Usage: savepath <path>")
            sys.exit(1)
        cmd_savepath(args.args[0])
    elif args.command == "delete":
        if not args.args:
            print("Usage: delete <fid>")
            sys.exit(1)
        cmd_delete(args.args[0])


if __name__ == "__main__":
    main()
