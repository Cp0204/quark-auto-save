# !/usr/bin/env python3
# -*- coding: utf-8 -*-
# Modify: 2024-02-29
# Repo: https://github.com/Cp0204/quark_auto_save
# ConfigFile: quark_config.json
"""
new Env('夸克自动追更');
0 8,18,20 * * * quark_auto_save.py
"""
import os
import re
import sys
import json
import random
import requests
from datetime import datetime

config_data = {}
notifys = []
first_account = {}

magic_regex = {
    "$TV": {
        "pattern": ".*?(S\\d{1,2}E)?P?(\\d{1,3}).*?\\.(mp4|mkv)",
        "replace": "\\1\\2.\\3",
    },
}


# 魔法正则匹配
def magic_regex_func(pattern, replace):
    keyword = pattern
    if keyword in magic_regex:
        pattern = magic_regex[keyword]["pattern"]
        if replace == "":
            replace = magic_regex[keyword]["replace"]
    return pattern, replace


# 发送通知消息
def send_ql_notify(title, body):
    try:
        # 导入通知模块
        import sendNotify

        # 如未配置 push_config 则使用青龙环境通知设置
        if config_data.get("push_config"):
            config_data["push_config"]["CONSOLE"] = True
            sendNotify.push_config = config_data["push_config"]
        sendNotify.send(title, body)
    except Exception as e:
        if e:
            print("发送通知消息失败！")


# 添加消息
def add_notify(text):
    global notifys
    notifys.append(text)
    print("📢", text)
    return text


def common_headers():
    return {
        "cookie": first_account["cookie"],
        "content-type": "application/json",
    }


def get_growth_info(cookie):
    url = "https://drive-m.quark.cn/1/clouddrive/capacity/growth/info"
    querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
    headers = {
        "cookie": cookie,
        "content-type": "application/json",
    }
    response = requests.request("GET", url, headers=headers, params=querystring).json()
    if response.get("data"):
        return response["data"]
    else:
        return False


def get_growth_sign(cookie):
    url = "https://drive-m.quark.cn/1/clouddrive/capacity/growth/sign"
    querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
    payload = {
        "sign_cyclic": True,
    }
    headers = {
        "cookie": cookie,
        "content-type": "application/json",
    }
    response = requests.request(
        "POST", url, json=payload, headers=headers, params=querystring
    ).json()
    if response.get("data"):
        return True, response["data"]["sign_daily_reward"]
    else:
        return False, response["message"]


def get_id_from_url(url):
    pattern = r"/s/(\w+)(#/list/share.*/(\w+))?"
    match = re.search(pattern, url)
    if match:
        pwd_id = match.group(1)
        if match.group(2):
            pdir_fid = match.group(3)
        else:
            pdir_fid = 0
        return pwd_id, pdir_fid
    else:
        return None


def get_account_info(cookie):
    url = "https://pan.quark.cn/account/info"
    querystring = {"fr": "pc", "platform": "pc"}
    headers = {
        "cookie": cookie,
        "content-type": "application/json",
    }
    response = requests.request("GET", url, headers=headers, params=querystring).json()
    if response.get("data"):
        return response["data"]
    else:
        return False


# 可验证资源是否失效
def get_stoken(pwd_id):
    url = "https://pan.quark.cn/1/clouddrive/share/sharepage/token"
    querystring = {"pr": "ucpro", "fr": "h5"}
    payload = {"pwd_id": pwd_id, "passcode": ""}
    headers = common_headers()
    response = requests.request(
        "POST", url, json=payload, headers=headers, params=querystring
    ).json()
    if response.get("data"):
        return True, response["data"]["stoken"]
    else:
        return False, response["message"]


def get_detail(pwd_id, stoken, pdir_fid):
    file_list = []
    page = 1
    while True:
        url = "https://pan.quark.cn/1/clouddrive/share/sharepage/detail"
        querystring = {
            "pr": "ucpro",
            "fr": "pc",
            "pwd_id": pwd_id,
            "stoken": stoken,
            "pdir_fid": pdir_fid,
            "force": "0",
            "_page": page,
            "_size": "50",
            "_fetch_banner": "0",
            "_fetch_share": "0",
            "_fetch_total": "1",
            "_sort": "file_type:asc,updated_at:desc",
        }
        headers = common_headers()
        response = requests.request(
            "GET", url, headers=headers, params=querystring
        ).json()
        if response["data"]["list"]:
            file_list += response["data"]["list"]
            page += 1
        else:
            break
        if len(file_list) >= response["metadata"]["_total"]:
            break
    # 仅有一个文件夹
    if len(file_list) == 1 and file_list[0]["file_type"] == 0:
        print("🧠 该分享是一个文件夹，读取文件夹内列表")
        file_list = get_detail(pwd_id, stoken, file_list[0]["fid"])
    return file_list


def get_fids(file_paths):
    url = "https://drive.quark.cn/1/clouddrive/file/info/path_list"
    querystring = {"pr": "ucpro", "fr": "pc"}
    payload = {"file_path": file_paths, "namespace": "0"}
    headers = common_headers()
    response = requests.request(
        "POST", url, json=payload, headers=headers, params=querystring
    ).json()
    # print(response)
    return response["data"]


def ls_dir(pdir_fid):
    file_list = []
    page = 1
    while True:
        url = "https://drive.quark.cn/1/clouddrive/file/sort"
        querystring = {
            "pr": "ucpro",
            "fr": "pc",
            "uc_param_str": "",
            "pdir_fid": pdir_fid,
            "_page": page,
            "_size": "50",
            "_fetch_total": "1",
            "_fetch_sub_dirs": "0",
            "_sort": "file_type:asc,updated_at:desc",
        }
        headers = common_headers()
        response = requests.request(
            "GET", url, headers=headers, params=querystring
        ).json()
        if response["data"]["list"]:
            file_list += response["data"]["list"]
            page += 1
        else:
            break
        if len(file_list) >= response["metadata"]["_total"]:
            break
    return file_list


def save_file(fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken):
    url = "https://drive.quark.cn/1/clouddrive/share/sharepage/save"
    querystring = {
        "pr": "ucpro",
        "fr": "pc",
        "uc_param_str": "",
        "__dt": int(random.uniform(1, 5) * 60 * 1000),
        "__t": datetime.now().timestamp(),
    }
    payload = {
        "fid_list": fid_list,
        "fid_token_list": fid_token_list,
        "to_pdir_fid": to_pdir_fid,
        "pwd_id": pwd_id,
        "stoken": stoken,
        "pdir_fid": "0",
        "scene": "link",
    }
    headers = common_headers()
    response = requests.request(
        "POST", url, json=payload, headers=headers, params=querystring
    ).json()
    return response


def mkdir(dir_path):
    url = "https://drive-pc.quark.cn/1/clouddrive/file"
    querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
    payload = {
        "pdir_fid": "0",
        "file_name": "",
        "dir_path": dir_path,
        "dir_init_lock": False,
    }
    headers = common_headers()
    response = requests.request(
        "POST", url, json=payload, headers=headers, params=querystring
    ).json()
    return response


def rename(fid, file_name):
    url = "https://drive-pc.quark.cn/1/clouddrive/file/rename"
    querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
    payload = {"fid": fid, "file_name": file_name}
    headers = common_headers()
    response = requests.request(
        "POST", url, json=payload, headers=headers, params=querystring
    ).json()
    return response


def update_savepath_fid(tasklist):
    dir_paths = [
        item["savepath"]
        for item in tasklist
        if not item.get("enddate")
        or (
            datetime.now().date()
            <= datetime.strptime(item["enddate"], "%Y-%m-%d").date()
        )
    ]
    if not dir_paths:
        return False
    dir_paths_exist_arr = get_fids(dir_paths)
    dir_paths_exist = [item["file_path"] for item in dir_paths_exist_arr]
    # 比较创建不存在的
    dir_paths_unexist = list(set(dir_paths) - set(dir_paths_exist))
    for dir_path in dir_paths_unexist:
        mkdir_return = mkdir(dir_path)
        if mkdir_return["code"] == 0:
            new_dir = mkdir_return["data"]
            dir_paths_exist_arr.append({"file_path": dir_path, "fid": new_dir["fid"]})
            print(f"创建文件夹: {dir_path}")
        else:
            print(f"创建文件夹: {dir_path} 失败, {mkdir_return['message']}")
    # 更新到配置
    for task in tasklist:
        for dir_path in dir_paths_exist_arr:
            if task["savepath"] == dir_path["file_path"]:
                task["savepath_fid"] = dir_path["fid"]
    # print(dir_paths_exist_arr)


def do_save_task(task):
    # 判断资源失效记录
    if task.get("shareurl_ban"):
        print(f"《{task['taskname']}》：{task['shareurl_ban']}")
        return

    # 链接转换所需参数
    pwd_id, pdir_fid = get_id_from_url(task["shareurl"])
    # print("match: ", pwd_id, pdir_fid)

    # 获取stoken，同时可验证资源是否失效
    is_sharing, stoken = get_stoken(pwd_id)
    if not is_sharing:
        add_notify(f"《{task['taskname']}》：{stoken}")
        task["shareurl_ban"] = stoken
        return
    # print("stoken: ", stoken)

    # 获取分享文件列表
    share_file_list = get_detail(pwd_id, stoken, pdir_fid)
    if not share_file_list:
        add_notify(f"《{task['taskname']}》：分享目录为空")
        return
    # print("share_file_list: ", share_file_list)

    # 获取目标目录文件列表
    task["savepath_fid"] = (
        task.get("savepath_fid")
        if task.get("savepath_fid")
        else get_fids([task["savepath"]])[0]["fid"]
    )
    to_pdir_fid = task["savepath_fid"]
    dir_file_list = ls_dir(to_pdir_fid)
    # print("dir_file_list: ", dir_file_list)

    # 需保存的文件清单
    need_save_list = []
    # 添加符合的
    for share_file in share_file_list:
        # 正则文件名匹配
        pattern, replace = magic_regex_func(task["pattern"], task["replace"])
        if re.search(pattern, share_file["file_name"]):
            # 替换后的文件名
            save_name = (
                re.sub(pattern, replace, share_file["file_name"])
                if replace != ""
                else share_file["file_name"]
            )
            # 判断目标目录文件是否存在，可选忽略后缀
            if task.get("ignore_extension"):
                compare_func = lambda a, b1, b2: (
                    os.path.splitext(a)[0] == os.path.splitext(b1)[0]
                    or os.path.splitext(a)[0] == os.path.splitext(b2)[0]
                )
            else:
                compare_func = lambda a, b1, b2: (a == b1 or a == b2)
            file_exists = any(
                compare_func(dir_file["file_name"], share_file["file_name"], save_name)
                for dir_file in dir_file_list
            )
            if not file_exists:
                share_file["save_name"] = save_name
                need_save_list.append(share_file)

    fid_list = [item["fid"] for item in need_save_list]
    fid_token_list = [item["share_fid_token"] for item in need_save_list]
    save_name_list = [item["save_name"] for item in need_save_list]
    if fid_list:
        save_file_return = save_file(
            fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken
        )
        if save_file_return["code"] == 0:
            task_id = save_file_return["data"]["task_id"]
            query_task_return = query_task(task_id)
            if query_task_return["code"] == 0:
                save_name_list.sort()
                add_notify(
                    f"《{task['taskname']}》添加追更：{', '.join(save_name_list)}"
                )
                return True
            else:
                err_msg = query_task_return["message"]
        else:
            err_msg = save_file_return["message"]
        add_notify(f"《{task['taskname']}》转存失败：{err_msg}")
        return False
    else:
        print("任务结束：没有新的转存任务")
        return False


def query_task(task_id):
    url = "https://drive-pc.quark.cn/1/clouddrive/task"
    querystring = {
        "pr": "ucpro",
        "fr": "pc",
        "uc_param_str": "",
        "task_id": task_id,
        "retry_index": "1",
        "__dt": int(random.uniform(1, 5) * 60 * 1000),
        "__t": datetime.now().timestamp(),
    }
    headers = common_headers()
    response = requests.request("GET", url, headers=headers, params=querystring).json()
    if response["code"] == 32003:
        response["message"] = "容量限制"
    return response


def do_rename_task(task):
    dir_file_list = ls_dir(task["savepath_fid"])
    is_rename = False
    for dir_file in dir_file_list:
        pattern, replace = magic_regex_func(task["pattern"], task["replace"])
        if re.search(pattern, dir_file["file_name"]):
            save_name = (
                re.sub(pattern, replace, dir_file["file_name"])
                if replace != ""
                else dir_file["file_name"]
            )
            if save_name != dir_file["file_name"]:
                rename_return = rename(dir_file["fid"], save_name)
                if rename_return["code"] == 0:
                    print(f"重命名：{dir_file['file_name']} → {save_name}")
                    is_rename = True
                else:
                    print(
                        f"重命名：{dir_file['file_name']} → {save_name} 失败，{rename_return['message']}"
                    )
    return is_rename


def emby_refresh(emby_id):
    emby_url = config_data.get("emby").get("url")
    emby_apikey = config_data.get("emby").get("apikey")
    if emby_url and emby_apikey and emby_id:
        url = f"{emby_url}/emby/Items/{emby_id}/Refresh"
        headers = {"X-Emby-Token": emby_apikey}
        querystring = {
            "Recursive": "true",
            "MetadataRefreshMode": "FullRefresh",
            "ImageRefreshMode": "FullRefresh",
            "ReplaceAllMetadata": "false",
            "ReplaceAllImages": "false",
        }
        response = requests.request("POST", url, headers=headers, params=querystring)
        if response.text == "":
            print(f"🎞 刷新Emby媒体库：成功✅")
            return True
        else:
            print(f"🎞 刷新Emby媒体库：{response.text}❌")
            return False


def emby_search(media_name):
    emby_url = config_data.get("emby").get("url")
    emby_apikey = config_data.get("emby").get("apikey")
    if emby_url and emby_apikey and media_name:
        url = f"{emby_url}/emby/Items"
        headers = {"X-Emby-Token": emby_apikey}
        querystring = {
            "IncludeItemTypes": "Series",
            "StartIndex": 0,
            "SortBy": "SortName",
            "SortOrder": "Ascending",
            "ImageTypeLimit": 0,
            "Recursive": "true",
            "SearchTerm": media_name,
            "Limit": 10,
            "IncludeSearchTypes": "false",
        }
        response = requests.request("GET", url, headers=headers, params=querystring)
        if "application/json" in response.headers["Content-Type"]:
            response = response.json()
            if response.get("Items"):
                for item in response["Items"]:
                    if item["IsFolder"]:
                        print(f"🎞 《{item['Name']}》匹配到Emby媒体库ID：{item['Id']}")
                        return item["Id"]
        else:
            print(f"🎞 搜索Emby媒体库：{response.text}❌")
    return False


def download_file(url, save_path):
    response = requests.get(url)
    if response.status_code == 200:
        with open(save_path, "wb") as file:
            file.write(response.content)
        return True
    else:
        return False


def get_cookies(cookie_val):
    if isinstance(cookie_val, list):
        return cookie_val
    elif cookie_val:
        if "\n" in cookie_val:
            return cookie_val.split("\n")
        else:
            return [cookie_val]
    else:
        return False


def do_sign(cookies):
    first_account = {}
    print(f"===============签到任务===============")
    for index, cookie in enumerate(cookies):
        # 验证账号
        account_info = get_account_info(cookie)
        print(f"▶️ 验证第{index+1}个账号")
        if not account_info:
            add_notify(f"👤 第{index+1}个账号登录失败，cookie无效❌")
        else:
            if index == 0:
                first_account = account_info
                first_account["cookie"] = cookie
            print(f"👤 账号昵称: {account_info['nickname']}✅")
            # 每日领空间
            growth_info = get_growth_info(cookie)
            if growth_info:
                if growth_info["cap_sign"]["sign_daily"]:
                    print(
                        f"📅 执行签到: 今日已签到+{int(growth_info['cap_sign']['sign_daily_reward']/1024/1024)}MB，连签进度({growth_info['cap_sign']['sign_progress']}/{growth_info['cap_sign']['sign_target']})✅"
                    )
                else:
                    sign, sign_return = get_growth_sign(cookie)
                    if sign:
                        message = f"📅 执行签到: 今日签到+{int(sign_return/1024/1024)}MB，连签进度({growth_info['cap_sign']['sign_progress']+1}/{growth_info['cap_sign']['sign_target']})✅"
                        if (
                            config_data.get("push_config").get("QUARK_SIGN_NOTIFY")
                            == False
                            or os.environ.get("QUARK_SIGN_NOTIFY") == False
                        ):
                            print(message)
                        else:
                            message = message.replace(
                                "今日", f"[{account_info['nickname']}]今日"
                            )
                            add_notify(message)
                    else:
                        print(f"📅 执行签到: {sign_return}")
        print(f"")
    print(f"")
    return first_account


def do_save():
    print(f"===============转存任务===============")
    print(f"转存账号: {first_account['nickname']}")
    # 任务列表
    tasklist = config_data.get("tasklist", [])
    # 获取全部保存目录fid
    update_savepath_fid(tasklist)

    def check_date(task):
        return (
            not task.get("enddate")
            or (
                datetime.now().date()
                <= datetime.strptime(task["enddate"], "%Y-%m-%d").date()
            )
        ) and (
            not task.get("runweek")
            # 星期一为0，星期日为6
            or (datetime.today().weekday() + 1 in task.get("runweek"))
        )

    # 执行任务
    for index, task in enumerate(tasklist):
        # 判断任务期限
        if check_date(task):
            print(f"")
            print(f"#{index+1}------------------")
            print(f"任务名称: {task['taskname']}")
            print(f"分享链接: {task['shareurl']}")
            print(f"目标目录: {task['savepath']}")
            print(f"正则匹配: {task['pattern']}")
            print(f"正则替换: {task['replace']}")
            if task.get("enddate"):
                print(f"任务截止: {task['enddate']}")
            if task.get("emby_id"):
                print(f"刷媒体库: {task['emby_id']}")
            if task.get("ignore_extension"):
                print(f"忽略后缀: {task['ignore_extension']}")
            print()
            is_new = do_save_task(task)
            is_rename = do_rename_task(task)
            if (is_new or is_rename) and task.get("emby_id") != "0":
                if task.get("emby_id"):
                    emby_refresh(task["emby_id"])
                else:
                    match_emby_id = emby_search(task["taskname"])
                    if match_emby_id:
                        task["emby_id"] = match_emby_id
                        emby_refresh(match_emby_id)
    print(f"")


def main():
    global config_data, first_account
    formatted_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"===============程序开始===============")
    print(f"⏰ 执行时间: {formatted_time}")
    print(f"")
    # 启动参数
    arguments = sys.argv
    if len(arguments) > 1:
        config_path = arguments[1]
    else:
        config_path = "quark_config.json"
    # 检查本地文件是否存在，如果不存在就下载
    if not os.path.exists(config_path):
        if os.environ.get("QUARK_COOKIE"):
            print(
                f"⚙️ 读取到 QUARK_COOKIE 环境变量，仅签到领空间。如需执行转存，请删除该环境变量后配置 {config_path} 文件"
            )
            cookie_val = os.environ.get("QUARK_COOKIE")
            cookie_form_file = False
        else:
            print(f"⚙️ 配置文件 {config_path} 不存在❌，正远程从下载配置模版")
            config_url = "https://mirror.ghproxy.com/https://raw.githubusercontent.com/Cp0204/quark_auto_save/main/quark_config.json"
            if download_file(config_url, config_path):
                print("⚙️ 配置模版下载成功✅，请到程序目录中手动配置")
            return
    else:
        print(f"⚙️ 正从 {config_path} 文件中读取配置")
        with open(config_path, "r", encoding="utf-8") as file:
            config_data = json.load(file)
        cookie_val = config_data.get("cookie")
        cookie_form_file = True
    # 获取cookie
    cookies = get_cookies(cookie_val)
    if not cookies:
        print("❌ cookie 未配置")
        return
    # 签到
    first_account = do_sign(cookies)
    # 转存
    if first_account and cookie_form_file:
        do_save()
    # 通知
    if notifys:
        notify_body = "\n".join(notifys)
        print(f"===============推送通知===============")
        send_ql_notify("【夸克自动追更】", notify_body)
        print(f"")
    if cookie_form_file:
        # 更新配置
        with open(config_path, "w", encoding="utf-8") as file:
            json.dump(config_data, file, ensure_ascii=False, indent=2)
    print(f"======================================")


if __name__ == "__main__":
    main()
