import os
import re
import time
import traceback


class Auto_unarchive:
    plugin_name = "auto_unarchive"

    default_config = {
        "is_active": True,
        "auto_delete": True,
        "retry_count": 3,
        "max_concurrent": 3,  # 限制同时解压的任务数
    }

    default_task_config = {
        "unarchive": True,
    }

    def __init__(self, **kwargs):
        self.is_active = False
        self.config = self.default_config.copy()
        if kwargs:
            self.config.update(kwargs)
        if self.config.get("is_active"):
            self.is_active = True
            self.auto_delete = self.config.get("auto_delete")
            self.retry_count = self.config.get("retry_count")
            self.max_concurrent = self.config.get("max_concurrent", 3)

    def run(self, task, **kwargs):
        account = kwargs.get("account")
        tree = kwargs.get("tree")

        task_config = task.get("addition", {}).get(
            self.plugin_name, self.default_task_config
        )
        if not self.is_active or not task_config.get("unarchive"):
            return task

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
                    if self.auto_delete:
                        account.delete(all_cleanup_fids)
                        print(f"🧹 批量清理完成")

        except Exception as e:
            print(f"❌ 运行异常: {e}")
            traceback.print_exc()
        return task

    def _process_files(self, account, p_task, q_res, target_fid, move_list, clean_list):
        """处理文件重命名逻辑"""
        un_list = q_res.get("data", {}).get("unarchive_result", {}).get("list", [])
        sub_dir_fid = next(
            (i["fid"] for i in un_list if p_task["main_name"] in i["file_name"]), None
        )
        if not sub_dir_fid:
            return

        clean_list.append(p_task["zip_fid"])
        clean_list.append(sub_dir_fid)

        items = []
        for _ in range(self.retry_count + 1):
            ls_res = account.ls_dir(sub_dir_fid)
            items = ls_res.get("data", {}).get("list", [])
            if items:
                break
            time.sleep(2)

        for item in items:
            ext = os.path.splitext(item["file_name"])[1]
            new_name = f"{p_task['main_name']}{ext}"
            account.rename(item["fid"], new_name)
            move_list.append(item["fid"])
            print(f"    └─ 重命名: {new_name}")
