// ==UserScript==
// @name         QAS一键推送助手
// @namespace    https://github.com/Cp0204/quark-auto-save
// @license      AGPL
// @version      0.1
// @description  在夸克网盘分享页面添加推送到 QAS 的按钮
// @icon         https://pan.quark.cn/favicon.ico
// @author       Cp0204
// @match        https://pan.quark.cn/s/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_xmlhttpRequest
// @require      https://cdn.jsdelivr.net/npm/sweetalert2@11
// @downloadURL  https://update.greasyfork.org/scripts/533201/QAS%E4%B8%80%E9%94%AE%E6%8E%A8%E9%80%81%E5%8A%A9%E6%89%8B.user.js
// @updateURL    https://update.greasyfork.org/scripts/533201/QAS%E4%B8%80%E9%94%AE%E6%8E%A8%E9%80%81%E5%8A%A9%E6%89%8B.meta.js
// ==/UserScript==

(function() {
    'use strict';

    // 从 GM_getValue 中读取 qas_base 和 qas_token，如果不存在则提示用户设置
    let qas_base = GM_getValue('qas_base', '');
    let qas_token = GM_getValue('qas_token', '');

    if (!qas_base || !qas_token) {
        Swal.fire({
            title: '请设置 QAS 地址和 Token',
            html: `
                <label for="qas_base">QAS 服务器</label>
                <input id="qas_base" class="swal2-input" placeholder="例如: 192.168.1.8:5005" value="${qas_base}">
                <label for="qas_token">QAS Token</label>
                <input id="qas_token" class="swal2-input" placeholder="v0.5+ 系统配置中查找" value="${qas_token}">
            `,
            focusConfirm: false,
            preConfirm: () => {
                qas_base = document.getElementById('qas_base').value;
                qas_token = document.getElementById('qas_token').value;
                if (!qas_base || !qas_token) {
                    Swal.showValidationMessage('请填写 QAS 服务器和 Token');
                }
                return { qas_base: qas_base, qas_token: qas_token }
            }
        }).then((result) => {
            if (result.isConfirmed) {
                GM_setValue('qas_base', result.value.qas_base);
                GM_setValue('qas_token', result.value.qas_token);
                qas_base = result.value.qas_base;
                qas_token = result.value.qas_token;
                // 重新执行主逻辑
                addQASButton();
            }
        });
    } else {
        // 如果配置存在，直接执行主逻辑
        addQASButton();
    }


    function addQASButton() {
        // 等待按钮加载完成
        function waitForElement(selector, callback) {
            const element = document.querySelector(selector);
            if (element) {
                callback(element);
            } else {
                setTimeout(() => waitForElement(selector, callback), 500);
            }
        }

        waitForElement('.ant-btn.share-save', (saveButton) => {
            const qasButton = document.createElement('button');
            qasButton.type = 'button';
            qasButton.className = 'ant-btn share-save';
            qasButton.style.marginLeft = '10px';
            qasButton.innerHTML = '<span class="share-save-ico"></span><span>推送到QAS</span>';

            let taskname, shareurl, savepath; // 声明变量

            // 获取数据函数
            function getData() {
                const currentUrl = window.location.href;
                taskname = currentUrl.lastIndexOf('-')>0 ? decodeURIComponent(currentUrl.match(/.*\/[^-]+-(.+)$/)[1]) : document.querySelector('.author-name').textContent;
                shareurl = currentUrl;
                savepath = document.querySelector('.path-name').title.replace('全部文件', '').trim(); // 去掉前面的 "全部文件" 和空格
                savepath += "/" + taskname
                qasButton.title = `任务名称: ${taskname}\n分享链接: ${shareurl}\n保存路径: ${savepath}`;
            }


            // 添加鼠标悬停事件
            qasButton.addEventListener('mouseover', () => {
                getData(); // 鼠标悬停时获取数据
            });


            // 添加点击事件
            qasButton.addEventListener('click', () => {
                getData(); // 点击时重新获取数据，确保最新

                const apiUrl = `http://${qas_base}/api/add_task?token=${qas_token}`;
                const data = {
                    "taskname": taskname,
                    "shareurl": shareurl,
                    "savepath": savepath,
                };

                GM_xmlhttpRequest({
                    method: 'POST',
                    url: apiUrl,
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    data: JSON.stringify(data),
                    onload: function(response) {
                        try {
                            const jsonResponse = JSON.parse(response.responseText);
                            if (jsonResponse.success) {
                                Swal.fire({
                                    title: '任务创建成功',
                                    text: jsonResponse.message,
                                    icon: 'success'
                                });
                            } else {
                                Swal.fire({
                                    title: '任务创建失败',
                                    text: jsonResponse.message,
                                    icon: 'error'
                                });
                            }
                        } catch (e) {
                            Swal.fire({
                                title: '解析响应失败',
                                text: `无法解析 JSON 响应: ${response.responseText}`,
                                icon: 'error'
                            });
                        }
                    },
                    onerror: function(error) {
                        Swal.fire({
                            title: '任务创建失败',
                            text: error,
                            icon: 'error'
                        });
                    }
                });
            });

            saveButton.parentNode.insertBefore(qasButton, saveButton.nextSibling);
        });
    }
})();
