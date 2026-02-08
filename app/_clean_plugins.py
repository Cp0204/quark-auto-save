# 清理非当前系统和架构的模块

import platform
import os

PLUGINS_DIR = "plugins"


def clean_plugins():
    arch = platform.machine()
    system = platform.system()
    sys_ext = "pyd" if system == "Windows" else "so"

    for f in os.listdir(PLUGINS_DIR):
        if f.endswith(f".{sys_ext}"):
            if f".{arch}" in f:
                new_f = f.replace(f".{arch}", "")
                print(f"Renaming: {f} -> {new_f}")
                # 重命名为 xx.so
                os.rename(
                    os.path.join(PLUGINS_DIR, f),
                    os.path.join(PLUGINS_DIR, new_f),
                )
            else:
                print(f"Removing: {f}")
                os.remove(os.path.join(PLUGINS_DIR, f))

        elif f.endswith(".py") or f.endswith(".json"):
            print(f"Keeping: {f}")

        else:
            print(f"Removing: {f}")
            os.remove(os.path.join(PLUGINS_DIR, f))


if __name__ == "__main__":
    clean_plugins()
