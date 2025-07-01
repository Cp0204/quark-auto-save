// ==UserScript==
// @name         QAS一键推送助手
// @namespace    https://github.com/Cp0204/quark-auto-save
// @license      AGPL
// @version      0.7-custom
// @description  在夸克网盘分享页面添加推送到 QAS 的按钮。修改为直接获取文件名作为任务名，且不将任务名附加到保存路径。
// @icon         https://pan.quark.cn/favicon.ico
// @author       Cp0204 (Modified by Gemini)
// @match        https://pan.quark.cn/s/*
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_xmlhttpRequest
// @grant        GM_registerMenuCommand
// @require      https://cdn.jsdelivr.net/npm/sweetalert2@11
// @downloadURL  https://cdn.jsdelivr.net/gh/Cp0204/quark-auto-save@refs/heads/main/app/static/js/qas.addtask.user.js
// @updateURL    https://cdn.jsdelivr.net/gh/Cp0204/quark-auto-save@refs/heads/main/app/static/js/qas.addtask.user.js
// ==/UserScript==

(function () {
    'use strict';

    let qas_base = GM_getValue('qas_base', '');
    let qas_token = GM_getValue('qas_token', '');
    let default_pattern = GM_getValue('default_pattern', '');
    let default_replace = GM_getValue('default_replace', '');

    // QAS 设置弹窗函数
    function showQASSettingDialog(callback) {
        Swal.fire({
            title: 'QAS 设置',
            showCancelButton: true,
            html: `
                <label for="qas_base">QAS 地址</label>
                <input id="qas_base" class="swal2-input" placeholder="如: http://192.168.1.8:5005" value="${qas_base}">
                <label for="qas_token">QAS Token</label>
                <input id="qas_token" class="swal2-input" placeholder="v0.5+ 系统配置中查找" value="${qas_token}">
                <label for="default_pattern">默认正则</label>
                <input id="default_pattern" class="swal2-input" placeholder="如 $TV" value="${default_pattern}">
                <label for="default_replace">默认替换</label>
                <input id="default_replace" class="swal2-input" value="${default_replace}">
            `,
            focusConfirm: false,
            preConfirm: () => {
                const base = document.getElementById('qas_base').value;
                const token = document.getElementById('qas_token').value;
                const pattern = document.getElementById('default_pattern').value;
                const replace = document.getElementById('default_replace').value;
                if (!base || !token) {
                    Swal.showValidationMessage('请填写 QAS 地址和 Token');
                }
                return { qas_base: base, qas_token: token, default_pattern: pattern, default_replace: replace }
            }
        }).then((result) => {
            if (result.isConfirmed) {
                GM_setValue('qas_base', result.value.qas_base);
                GM_setValue('qas_token', result.value.qas_token);
                GM_setValue('default_pattern', result.value.default_pattern);
                GM_setValue('default_replace', result.value.default_replace);

                // Update live variables
                qas_base = result.value.qas_base;
                qas_token = result.value.qas_token;
                default_pattern = result.value.default_pattern;
                default_replace = result.value.default_replace;

                if (callback) {
                    callback(); // Execute the callback function if it exists
                }
            }
        });
    }

    // 注册油猴菜单命令
    function registerMenuCommands() {
        GM_registerMenuCommand('QAS 设置', () => {
            showQASSettingDialog();
        });
    }

    // 推送到 QAS 按钮
    function addQASButton() {
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
            qasButton.innerHTML = '<span class="share-save-ico"></span><span>创建QAS任务</span>';

            let taskname, shareurl, savepath; // Declare variables

            function getData() {
                const currentUrl = window.location.href;

                // ==================== 修改点 1: taskname 获取方式 ====================
                // 直接从页面文件列表的第一个文件名中获取任务名
                const fileNameElement = document.querySelector('.filename-text');
                taskname = fileNameElement ? fileNameElement.title : '未知任务'; // 使用 .title 以获取完整文件名

                shareurl = currentUrl;

                // ==================== 修改点 2: savepath 获取方式 ====================
                const pathElement = document.querySelector('.path-name');
                // 只获取路径，不附加任务名
                savepath = pathElement ? pathElement.title.replace('全部文件', '').trim() : "";

                // 如果 savepath 为空（即根目录），则设置为 "/"
                if (savepath === "") {
                    savepath = "/";
                }

                qasButton.title = `任务名称: ${taskname}\n分享链接: ${shareurl}\n保存路径: ${savepath}`;
            }

            qasButton.addEventListener('mouseover', getData);

            function createQASTask() {
                getData();

                let qasApiBase = qas_base;
                if (!qasApiBase.startsWith('http')) {
                    qasApiBase = 'http://' + qasApiBase;
                }
                const apiUrl = `${qasApiBase}/api/add_task?token=${qas_token}`;

                const data = {
                    "taskname": taskname,
                    "shareurl": shareurl,
                    "savepath": savepath,
                    "pattern": default_pattern,
                    "replace": default_replace,
                };

                GM_xmlhttpRequest({
                    method: 'POST',
                    url: apiUrl,
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    data: JSON.stringify(data),
                    onload: function (response) {
                        try {
                            const jsonResponse = JSON.parse(response.responseText);
                            if (jsonResponse.success) {
                                Swal.fire({
                                    title: '任务创建成功',
                                    html: `<small>
                                           <b>任务名称:</b> ${taskname}<br><br>
                                           <b>保存路径:</b> ${savepath}<br><br>
                                           <a href="${qasApiBase}" target="_blank">去 QAS 查看</a>
                                           </small>`,
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
                    onerror: function (error) {
                        console.error("QAS Connection Error Details:", error);
                        Swal.fire({
                            title: '任务创建失败',
                            html: '无法连接到您的 QAS 服务器。<br>这很可能是一个网络或配置问题。',
                            icon: 'error',
                            footer: '<div style="text-align: left; font-size: 0.9em; line-height: 1.5;"><b>请检查以下几点：</b><br>' +
                                    '1. QAS 地址 (<code>' + qas_base + '</code>) 是否填写正确？<br>' +
                                    '2. 运行 QAS 的设备（如NAS、电脑）是否已开机并在同一网络下？<br>' +
                                    '3. QAS 服务程序是否已正常启动？<br>' +
                                    '4. 您能否在浏览器新标签页中直接访问您的 QAS 地址？</div>'
                        });
                    }
                });
            }

            qasButton.addEventListener('click', () => {
                if (!qas_base || !qas_token) {
                    showQASSettingDialog(createQASTask);
                } else {
                    createQASTask();
                }
            });

            saveButton.parentNode.insertBefore(qasButton, saveButton.nextSibling);
        });
    }

    (function init() {
        registerMenuCommands();
        addQASButton();
    })();
})();
