import os
import re
import time
import traceback


class Auto_unarchive:
    default_config = {
        "tips_": "自动云解压(zip|rar|7z)到保存目录，在任务插件选项中启用，该功能需SVIP支持",
        "max_concurrent": 3,  # 限制同时解压的任务数
    }

    default_task_config = {
        "enable": False,  # 是否自动解压
        "auto_clean": True,  # 是否自动删除原始文件
        "auto_clean_zipdir": False,  # 是否删除占位目录，适用于一次性运行的任务，无须防止重复转存的占位目录
    }

    is_active = True  # 默认全局激活，由任务配置中开启

    def __init__(self, **kwargs):
        self.plugin_name = self.__class__.__name__.lower()
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])

    def run(self, task, **kwargs):
        account = kwargs.get("account")
        tree = kwargs.get("tree")

        task_config = task.get("addition", {}).get(
            self.plugin_name, self.default_task_config
        )
        if not self.is_active or not task_config.get("enable"):
            return task

        # 任务配置中是否自动删除原始文件
        self.auto_clean = task_config.get("auto_clean", True)
        self.auto_clean_zipdir = task_config.get("auto_clean_zipdir", False)

        try:
            savepath = re.sub(r"/{2,}", "/", f"/{task['savepath']}")
            target_pdir_fid = account.savepath_fid.get(savepath)
            if not target_pdir_fid:
                return task

            # 获取待解压节点列表
            all_zip_nodes = [
                node
                for node in tree.all_nodes()
                if node.data
                and not node.data.get("is_dir")
                and re.search(r"\.(zip|rar|7z)$", node.tag, re.I)
            ]
            if not all_zip_nodes:
                return task

            wait_list = all_zip_nodes.copy()  # 等待提交队列
            active_tasks = []  # 正在解压队列
            all_move_fids = []
            all_cleanup_fids = []

            print(
                f"📦 [{task['taskname']}] 共有 {len(wait_list)} 个任务，控制并发数为: {self.max_concurrent}"
            )

            while wait_list or active_tasks:

                while len(active_tasks) < self.max_concurrent and wait_list:
                    node = wait_list.pop(0)
                    zip_fid = node.data["fid"]
                    zip_name = node.data["file_name_re"]
                    main_name = os.path.splitext(zip_name)[0]

                    res = account.unarchive(zip_fid, target_pdir_fid)
                    if res.get("code") == 0:
                        task_id = res["data"]["task_id"]
                        active_tasks.append(
                            {
                                "task_id": task_id,
                                "zip_fid": zip_fid,
                                "main_name": main_name,
                                "zip_name": zip_name,
                            }
                        )
                        print(f"  ▶️ 提交解压: {zip_name}")
                    else:
                        print(f"  ❌ 提交失败: {zip_name} ({res.get('message')})")
                        if "concurrent" in res.get("message", ""):
                            wait_list.insert(0, node)
                            break
                    time.sleep(1)

                for p_task in active_tasks[:]:
                    q_res = account.query_task(p_task["task_id"])

                    if q_res.get("code") == 0:
                        print(f"  ✅ 解压完成: {p_task['zip_name']}")
                        self._process_files(
                            account,
                            p_task,
                            q_res,
                            target_pdir_fid,
                            all_move_fids,
                            all_cleanup_fids,
                        )
                        active_tasks.remove(p_task)
                    elif q_res.get("code") == 1:
                        pass
                    else:
                        print(
                            f"  ⚠️ 任务异常: {p_task['zip_name']} {q_res.get('message','')}"
                        )
                        active_tasks.remove(p_task)

                if active_tasks:
                    time.sleep(5)

            if all_move_fids:
                print(
                    f"🚀 任务全部解压完成，开始批量移动 {len(all_move_fids)} 个文件..."
                )
                if account.move_files(all_move_fids, target_pdir_fid).get("code") == 0:
                    if all_cleanup_fids and account.delete(all_cleanup_fids):
                        print(f"🧹 批量清理完成")

        except Exception as e:
            print(f"❌ 运行异常: {e}")
            traceback.print_exc()
        return task

    def _process_files(self, account, p_task, q_res, target_fid, move_list, clean_list):
        """处理文件重命名逻辑"""
        # 获取解压出来压缩包同名目录的fid
        un_list = q_res.get("data", {}).get("unarchive_result", {}).get("list", [])
        sub_dir_fid = next(
            (i["fid"] for i in un_list if p_task["main_name"] == i["file_name"]), None
        )
        if not sub_dir_fid:
            return

        if self.auto_clean:
            # 压缩包加入清理队列
            clean_list.append(p_task["zip_fid"])
            if self.auto_clean_zipdir:
                # 解压目录加入清理队列
                clean_list.append(sub_dir_fid)
            else:
                # 重命名解压目录为压缩包名称，占位，避免下次重复转存
                account.rename(sub_dir_fid, p_task["zip_name"])
        else:
            # 不自动清理时，原压缩包占位，将解压目录加入清理队列
            clean_list.append(sub_dir_fid)

        # 获取解压目录下的所有文件
        ls_res = account.ls_dir(sub_dir_fid)
        items = ls_res.get("data", {}).get("list", [])
        for item in items:
            move_list.append(item["fid"])

        if len(items) == 1:
            item = items[0]
            # 重命名文件 /zip1/xx.mp4 -> /zip1/zip1.mp4
            # 当压缩包里只有一个文件时，执行按压缩包名称重命名
            ext = os.path.splitext(item["file_name"])[1]
            new_name = f"{p_task['main_name']}{ext}"
            account.rename(item["fid"], new_name)
            print(f"    └─ 重命名: {new_name}")
