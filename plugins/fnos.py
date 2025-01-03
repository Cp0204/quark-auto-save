import ssl
import json
import sys
import certifi
import asyncio
import websockets
import base64
import secrets
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5 as Cipher_pkcs1_v1_5
from Crypto.Cipher import AES
from Crypto.Hash import HMAC, SHA256

"""
    配合 飞牛系统的Alist 项目，转存后自动下载
"""

async def create_websocket(url):
    if 'wss' in url:
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.minimum_version = ssl.TLSVersion.TLSv1_2
        ssl_context.maximum_version = ssl.TLSVersion.TLSv1_3
        ssl_context.load_verify_locations(certifi.where())
        return await websockets.connect(url, ssl=ssl_context, ping_interval=None)
    else:
        return await websockets.connect(url, ping_interval=None)

async def wss_connect(websocket):
    response = await websocket.recv()
    return response

async def close_websocket(websocket):
    await websocket.close()

async def send_ping(websocket):
    while True:
        await asyncio.sleep(5)  # 每10秒发送一次Ping消息
        await websocket.send('{"req":"ping"}')

def rsa_encrypt(message, public_key):
    public_key = RSA.import_key(public_key)
    cipher = Cipher_pkcs1_v1_5.new(public_key)
    cipher_text = base64.b64encode(cipher.encrypt(message.encode('utf-8')))
    return cipher_text.decode('utf-8')

def encrypt(text, key, iv):
    cipher = AES.new(key, AES.MODE_CBC, iv)
    pad = lambda s: s + (16 - len(s) % 16) * chr(16 - len(s) % 16)
    encrypted = base64.b64encode(cipher.encrypt(pad(text).encode()))
    return encrypted.decode()

def unpad(data):
    pad = data[-1]
    if type(pad) is int:
        pad = chr(pad)
    return data[:-ord(pad)]

def decrypt(text, key, iv):
    # 将加密数据转换位bytes类型数据
    encodebytes = base64.decodebytes(text.encode())
    # 解密
    cipher = AES.new(key, AES.MODE_CBC, iv)
    text_decrypted = cipher.decrypt(encodebytes)
    text_decrypted = unpad(text_decrypted)
    return base64.b64encode(text_decrypted).decode()

oneMark = True
def print_progress_bar(iteration, total, prefix='', suffix='', length=35):
    global oneMark
    percent = (iteration / total) * 100
    filled_length = int(length * iteration // total)
    bar = '#' * filled_length + ' ' * (length - filled_length)
    percent_str = str(int(percent)).zfill(2)
    if percent < 100:
        percent_str = " " + percent_str
    if oneMark:
        print(f'{prefix} {bar} {percent_str}% {suffix}', end='')
        oneMark = False
    else:
        print(f'\r{prefix} {bar} {percent_str}% {suffix}', end='')
    sys.stdout.flush()

def seconds_to_hms(seconds):
    hours = seconds // 3600
    remainder = seconds % 3600
    minutes = remainder // 60
    seconds = remainder % 60
    hours_str = str(int(hours)).zfill(2)
    minutes_str = str(int(minutes)).zfill(2)
    seconds_str = str(int(seconds)).zfill(2)
    return f'{hours_str}:{minutes_str}:{seconds_str}'

def format_byte_repr(byte_num):
    KB = 1024
    MB = KB * KB
    GB = MB * KB
    TB = GB * KB
    try:
        if isinstance(byte_num, str):
            byte_num = int(byte_num)
        if byte_num > TB:
            result = '%sTB' % round(byte_num / TB, 2)
        elif byte_num > GB:
            result = '%sGB' % round(byte_num / GB, 2)
        elif byte_num > MB:
            result = '%sMB' % round(byte_num / MB, 2)
        elif byte_num > KB:
            result = '%sKB' % round(byte_num / KB, 2)
        else:
            result = '%sB' % byte_num
        return result
    except Exception as e:
        print(e.args)
        return byte_num

class Fnos:

    default_config = {
        "websocket": "",  # 飞牛的websocket地址
        "user": "",  # 飞牛的用户账号
        "password": "",  # 飞牛的用户密码
        "mount_path": "",  # Alist挂载的地址
        "download_wait": "true",  # 是否等待下载完成
    }
    default_task_config = {
        "download_path": "",  # 下载路径
    }
    is_active = True

    def __init__(self, **kwargs):
        self.plugin_name = self.__class__.__name__.lower()
        if kwargs:
            for key, _ in self.default_config.items():
                if key in kwargs:
                    setattr(self, key, kwargs[key])
                else:
                    print(f"{self.__class__.__name__} 模块缺少必要参数: {key}")
            if self.websocket and self.user and self.password and self.mount_path and self.download_wait:
                self.is_active = True

    def run(self, task, **kwargs):
        dramaList = []
        if kwargs['tree'] is not None:
            for node in kwargs['tree'].all_nodes():
                if node.data['is_dir'] is False:
                    dramaList.append(f'"{self.mount_path}{node.data['path']}"')
        if len(dramaList) < 0:
            print(f"飞牛:😄 此次转存无需下载文件!")
        else:
            task_config = task.get("addition", {}).get(self.plugin_name, self.default_task_config)
            print(f"飞牛:🎞️ 转存有需下载文件️")
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            websocket = loop.run_until_complete(create_websocket(self.websocket))
            loop.run_until_complete(websocket.send('{"reqid":"676cf70d00000000000000000001","req":"util.crypto.getRSAPub"}'))
            try:
                aesKeyByte = None
                aesIvByte = None
                num = 0
                asyncio.ensure_future(send_ping(websocket))
                while True:
                    response = loop.run_until_complete(wss_connect(websocket))
                    if "-----BEGIN PUBLIC KEY-----" in response:
                        pub = json.loads(response).get("pub")
                        si = json.loads(response).get("si")
                        userData = '{"reqid":"676cf70d00000000000000000002","user":"'+self.user+'","password":"'+self.password+'","deviceType":"Browser","deviceName":"Mac OS-Google Chrome","stay":true,"req":"user.login","si":"' + si + '"}'
                        aesKeyStr = "lUfJn1XJ9akUvmmwQplpVIy1XNC2jJ3q"
                        aesIv = secrets.token_bytes(16)
                        aesIvBase64 = base64.b64encode(aesIv).decode('utf-8')
                        iv = aesIvBase64
                        rsa = rsa_encrypt(aesKeyStr, pub)
                        aes = encrypt(userData, aesKeyStr.encode(), aesIv)
                        aesKeyByte = aesKeyStr.encode()
                        aesIvByte = aesIv
                        sendMsg = '{"rsa":"' + rsa + '","iv":"' + iv + '","aes":"' + aes + '","req":"encrypted"}'
                        loop.run_until_complete(websocket.send(sendMsg))
                    elif "676cf70d00000000000000000002" in response:
                        print(f"飞牛:👨 用户认证成功🏅")
                        secret = json.loads(response).get('secret')
                        keys = decrypt(secret, aesKeyByte, aesIvByte)
                        Secret = base64.b64decode(keys)
                        a = '{"reqid":"676cf70d00000000000000000003","files":['+','.join(dramaList)+'],"pathTo":"'+task_config.get("download_path")+'","overwrite":1,"description":"剧集自动下载","req":"file.cp"}'
                        mark = base64.b64encode(HMAC.new(Secret, a.encode(), digestmod=SHA256).digest()).decode()
                        loop.run_until_complete(websocket.send(mark + a))
                    elif "pong" in response:
                        pass
                    elif "676cf70d00000000000000000003" in response and '"sysNotify":"taskId"' in response:
                        print(f"飞牛:💼 收到资源下载任务")
                        pass
                    elif "676cf70d00000000000000000003" in response and 'percent' in response:
                        data = json.loads(response)
                        if 'true' in self.download_wait.lower():
                            if num != 0 or num < int(data.get('percent')):
                                time = seconds_to_hms(data.get('time'))
                                du = format_byte_repr(data.get('size')) + '/' + format_byte_repr(data.get('sizeTotal'))
                                speed = format_byte_repr(data.get('speed')) + '/S'
                                suffix = f'{time} {du} {speed}'
                                print_progress_bar(data.get('percent'), 100, prefix='⌛飞牛: ️', suffix=suffix)
                                num = data.get('percent')
                        else:
                            print(f"飞牛:🎞️ 下载任务后台执行")
                            break
                    elif '"taskInfo":{"reqid":"676cf70d00000000000000000003"' in response:
                        pass
                    elif "676cf70d00000000000000000003" in response and '"result":"succ"' in response:
                        print()
                        print(f"飞牛: 下载任务完成✅")
                        break
                    elif "676cf70d00000000000000000003" in response and '"result":"fail"' in response:
                        print()
                        print(f"飞牛: 下载任务异常❌,检查您配置")
                        break
                    elif "676cf70d00000000000000000003" in response and '"result":"cancel"' in response:
                        print()
                        print(f"飞牛: 下载任务被取消❌")
                        break
                    else:
                        print(f"{response}")
            except Exception as e:
                print(f"飞牛: 下载任务异常❌ {e}")
            loop.run_until_complete(close_websocket(websocket))