# 使用官方 Python 镜像作为基础镜像
FROM python:3.12.2-alpine

# 设置工作目录
WORKDIR /app

# 将当前目录中的文件添加到工作目录中
ADD . /app

# 安装依赖
RUN pip install flask apscheduler requests

# 时区
ENV TZ="Asia/Shanghai"

# 端口
EXPOSE 5005

# 运行应用程序
CMD ["python", "./app/run.py"]