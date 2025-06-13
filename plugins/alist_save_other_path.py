###  当verify_path变量不存在时，将在target_path路径下进行比对文件列表


import os
import re
import json
from importlib.util import source_hash

import requests
import sys



class Alist_save_other_path:

    default_config = {
        "url": "",  # Alist服务器URL
        "token": "",  # Alist服务器Token
        "quark_path": "" # Alist 服务器夸克路径

    }
    is_active = False
    # 缓存参数

    default_task_config = {
        "target_path": "",  # 需要转存到的目录
        "verify_path": ""
    }
    def __init__(self, **kwargs):
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.__class__.__name__} 模块缺少必要参数: {key}")
            if self.url and self.token:
                if self.verify_server():
                    self.is_active = True


    def _send_request(self, method, url, **kwargs):
        headers = {
            "Authorization": self.token,
            'Content-Type': 'application/json'
        }
        if "headers" in kwargs:
            headers = kwargs["headers"]
            del kwargs["headers"]
        try:
            response = requests.request(method, url, headers=headers, **kwargs)
            # print(f"{response.text}")
            # response.raise_for_status()  # 检查请求是否成功，但返回非200也会抛出异常
            return response
        except Exception as e:
            print(f"_send_request error:\n{e}")
            fake_response = requests.Response()
            fake_response.status_code = 500
            fake_response._content = b'{"status": 500, "message": "request error"}'
            return fake_response




    def verify_server(self):
        url = f"{self.url}/api/me"
        querystring = ''
        headers = {
            "Authorization": self.token,
            'Content-Type': 'application/json'
        }
        #response = self._send_request("GET", url, params=querystring).json()
        #return response["data"]["username"]
        try:
            response = requests.request("GET", url, headers=headers, params=querystring)
            response.raise_for_status()
            response = response.json()
            if response.get("code") == 200:
                if response.get("data").get("username") == "guest":
                    print(
                        f"Alist登陆失败，请检查token"
                    )
                else:
                    print(
                        f"Alist登陆成功，当前用户: {response.get('data').get('username')}"
                    )
                    return True
            else:
                print(f"Alist刷新: 连接失败❌ {response.get('message')}")
        except requests.exceptions.RequestException as e:
            print(f"获取Alist信息出错: {e}")
        return False


    def run(self, task, **kwargs):
        print(f"开始进行alist转存")
        #这一块注释的是获取任务的参数，在web界面可以看
        #print(task)
        # print(task['addition'])
        # print(task['addition']['alist_save_other_path'])
        # print(task['addition']['alist_save_other_path']['target_path'])
        #如果没有填验证目录，则验证目录与保存目录一致
        if task['addition']['alist_save_other_path']['verify_path']:
            self.verify_path = task['addition']['alist_save_other_path']['verify_path']
        else:
            self.verify_path = task['addition']['alist_save_other_path']['target_path']
        #这里获取的设置中的保存目录、验证目录
        self.target_path = task['addition']['alist_save_other_path']['target_path']
        #先验证目录是否存在，如果不存在则直接复制整个目录过去
        Error_quit = False
        if not self.get_path(self.target_path):
            dir_exists = False
            print('新建保存目标目录')
            if not self.mkdir(self.target_path):
                print('新建保存目标目录失败')
                Error_quit = True
        else:
            dir_exists = True
        if not Error_quit:
            if dir_exists:
                verify_dir_list = self.get_path_list(self.verify_path)
            #初始化alist中的夸克目录
            self.source_path = f"{self.quark_path}{task['savepath']}"
            #初始化任务名
            self.taskname = f"{task['taskname']}"
            source_dir_list = self.get_path_list(self.source_path)
            print("网盘中的文件列表")
            print(f"{source_dir_list}")
            #比对需要复制的文件
            if dir_exists:
                self.get_save_file(verify_dir_list, source_dir_list)
            else:
                self.save_file_data = []
                for source_list in source_dir_list:
                    self.save_file_data.append(source_list['name'])
            if self.save_file_data :
                self.save_start(self.save_file_data)
                print("转存的文件列表：")
                for save_file in self.save_file_data:
                    print(f"└── 🎞️{save_file}")
            else:
                print("转存文件均已存在")


    def save_start(self, save_file_data):
        url = f"{self.url}/api/fs/copy"
        payload = json.dumps({
            "src_dir": self.source_path,
            "dst_dir": self.target_path,
             "names": save_file_data
        })
        response = self._send_request("POST", url, data=payload)
        if response.status_code != 200:
            print('未能进行Alist转存，请手动转存')
        else:
            print('Alist创建任务成功')
        self.copy_task = response.json()



    def get_save_file(self, target_dir_list, source_dir_list):
        self.save_file_data = []
        if target_dir_list:
            for source_list in source_dir_list:
                file_exists = False
                for target_list in target_dir_list:
                    if source_list['name'].replace('.mp4', '').replace('.mkv', '') == target_list['name'].replace('.mp4', '').replace('.mkv', ''):
                        #print(f"文件存在，名称为：{target_dir['name']}")
                        file_exists = True
                        break
                if not file_exists:
                    skip=False
                    if re.search(self.taskname+r"\.s\d{1,3}e\d{1,3}\.(mkv|mp4)", source_list['name'],re.IGNORECASE):
                        #添加一句验证，如果有MKV，MP4存在时，则只保存某一个格式
                        if re.search(self.taskname + r"\.s\d{1,3}e\d{1,3}\.mp4", source_list['name'],
                                     re.IGNORECASE):
                            for all_file in source_dir_list:
                                if source_list['name'].replace('.mp4', '.mkv') == all_file['name']:
                                    print(f"{source_list['name']}拥有相同版本的MKV文件，跳过复制")
                                    skip=True
                        if not skip:
                            self.save_file_data.append(source_list['name'])
        else:
            for source_list in source_dir_list:
                if re.search(self.taskname + r"\.s\d{1,3}e\d{1,3}\.(mkv|mp4)", source_list['name'], re.IGNORECASE):
                    self.save_file_data.append(source_list['name'])



    def mkdir(self, path):
        url = f"{self.url}/api/fs/mkdir"
        payload = json.dumps({
            "path": path,
        })
        response = self._send_request("POST", url, data=payload)
        if response.status_code != 200 or response.json()['message'] != "success" :
            return False
        else:
            return True

    def get_path(self, path):
        url = f"{self.url}/api/fs/list"
        payload = json.dumps({
            "path": path,
            "password": "",
            "force_root": False
        })
        response = self._send_request("POST", url, data=payload)
        if response.status_code != 200 or response.json()['message'] != "success" :
            return False
        else:
            return True

    def get_path_list(self, path):
        url = f"{self.url}/api/fs/list"
        payload = json.dumps({
            "path": path,
            "password": "",
            "page": 1,
            "per_page": 0,
            "refresh": True
        })
        response = self._send_request("POST", url, data=payload)
        if response.status_code != 200:
            print(f"获取Alist目录出错: {response}")
            return False
        else:
            return response.json()["data"]["content"]

