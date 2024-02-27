# !/usr/bin/env python3
# -*- coding: utf-8 -*-
# Modify: 2024-01-31
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
    global config_data
    return {
        "cookie": config_data["cookie"],
        "content-type": "application/json",
    }


def get_growth_info():
    url = "https://drive-m.quark.cn/1/clouddrive/capacity/growth/info"
    querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
    headers = common_headers()
    response = requests.request("GET", url, headers=headers, params=querystring).json()
    if response.get("data"):
        return response["data"]
    else:
        return False


def get_growth_sign():
    url = "https://drive-m.quark.cn/1/clouddrive/capacity/growth/sign"
    querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
    payload = {
        "sign_cyclic": True,
    }
    headers = common_headers()
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


def get_account_info():
    url = "https://pan.quark.cn/account/info"
    querystring = {"fr": "pc", "platform": "pc"}
    headers = common_headers()
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
    return response["data"]


def rename(fid, file_name):
    url = "https://drive-pc.quark.cn/1/clouddrive/file/rename"
    querystring = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
    payload = {"fid": fid, "file_name": file_name}
    headers = common_headers()
    response = requests.request(
        "POST", url, json=payload, headers=headers, params=querystring
    ).json()
    return response["data"]


def update_savepath_fid(tasklist):
    dir_paths = [item["savepath"] for item in tasklist]
    dir_paths_exist_arr = get_fids(dir_paths)
    dir_paths_exist = [item["file_path"] for item in dir_paths_exist_arr]
    # 比较创建不存在的
    dir_paths_unexist = list(set(dir_paths) - set(dir_paths_exist))
    for dir_path in dir_paths_unexist:
        new_dir = mkdir(dir_path)
        dir_paths_exist_arr.append({"file_path": dir_path, "fid": new_dir["fid"]})
        print("创建文件夹: ", dir_path)
    # 更新到配置
    for task in tasklist:
        for dir_path in dir_paths_exist_arr:
            if task["savepath"] == dir_path["file_path"]:
                task["savepath_fid"] = dir_path["fid"]
    # print(dir_paths_exist_arr)


def save_task(task):
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
        save_name_list.sort()
        add_notify(f"《{task['taskname']}》添加追更：{', '.join(save_name_list)}")
        task = save_file(fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken)
        return True
    else:
        print("运行结果：没有新的转存任务")
        return False


def rename_task(task):
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
                rename(dir_file["fid"], save_name)
                print("重命名：", dir_file["file_name"], "→", save_name)
                is_rename = True
    return is_rename


def emby_refresh(emby_id):
    global config_data
    emby_url = config_data.get("emby").get("url")
    emby_apikey = config_data.get("emby").get("apikey")
    if emby_url and emby_apikey and emby_id:
        url = f"{emby_url}/emby/Items/{emby_id}/Refresh"
        querystring = {
            "Recursive": "true",
            "MetadataRefreshMode": "FullRefresh",
            "ImageRefreshMode": "FullRefresh",
            "ReplaceAllMetadata": "false",
            "ReplaceAllImages": "false",
            "api_key": emby_apikey,
        }
        response = requests.request("POST", url, headers=None, params=querystring)
        if response.text == "":
            print(f"🎞 刷新Emby媒体库：成功✅")
            return True
        else:
            print(f"🎞 刷新Emby媒体库：{response.text}❌")
            return False


def download_file(url, save_path):
    response = requests.get(url)
    if response.status_code == 200:
        with open(save_path, "wb") as file:
            file.write(response.content)
        return True
    else:
        return False


def main():
    global config_data
    formatted_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"============================")
    print(f"⏰ 执行时间: {formatted_time}")
    # 启动参数
    arguments = sys.argv
    if len(arguments) > 1:
        config_path = arguments[1]
    else:
        config_path = "quark_config.json"
    # 检查本地文件是否存在，如果不存在就下载
    if not os.path.exists(config_path):
        print(f"❌ 配置文件 {config_path} 不存在，正远程从下载配置模版")
        config_url = "https://mirror.ghproxy.com/https://raw.githubusercontent.com/Cp0204/quark_auto_save/main/quark_config.json"
        if download_file(config_url, config_path):
            print("✅ 配置模版下载成功，请到程序目录中手动配置")
        return
    else:
        with open(config_path, "r", encoding="utf-8") as file:
            config_data = json.load(file)
    # 推送测试
    # add_notify("消息测试")
    # send_ql_notify("【夸克自动追更】", "\n".join(notifys))
    # return
    # 获取cookie
    if not config_data.get("cookie"):
        print("❌ cookie未配置")
        return
    # 验证账号
    account_info = get_account_info()
    if not account_info:
        add_notify("👤 验证账号: 登录失败，cookie无效❌")
    else:
        print(f"👤 验证账号: {account_info['nickname']}✅")
        # 每日领空间
        growth_info = get_growth_info()
        if growth_info:
            if growth_info["cap_sign"]["sign_daily"]:
                print(f"📅 签到任务: 今日已签到+{growth_info['cap_sign']['sign_daily_reward']/1024/1024}MB，连签进度({growth_info['cap_sign']['sign_progress']}/{growth_info['cap_sign']['sign_target']})✅")
            else:
                sign, sign_return = get_growth_sign()
                if sign:
                    print(f"📅 签到任务: 今日签到+{sign_return/1024/1024}MB，连签进度({growth_info['cap_sign']['sign_progress']+1}/{growth_info['cap_sign']['sign_target']})✅")
                else:
                    print(f"📅 签到任务: {sign_return}")
        # 任务列表
        tasklist = config_data.get("tasklist", [])
        # 获取全部保存目录fid
        if tasklist:
            update_savepath_fid(tasklist)
        # 执行任务
        for task in tasklist:
            # 判断任务期限
            if not task.get("enddate") or (
                datetime.now().date()
                <= datetime.strptime(task["enddate"], "%Y-%m-%d").date()
            ):
                print(f"============================")
                print(f"当前任务: {task['taskname']}")
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
                is_new = save_task(task)
                is_rename = rename_task(task)
                if (is_new or is_rename) and task.get("emby_id"):
                    emby_refresh(task["emby_id"])
                print(f"============================")
    # 获取cookie
    if notifys:
        notify_body = "\n".join(notifys)
        print(f"\n\n推送通知：\n{notify_body}\n\n")
        send_ql_notify("【夸克自动追更】", notify_body)
    # 更新配置
    with open(config_path, "w", encoding="utf-8") as file:
        json.dump(config_data, file, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
