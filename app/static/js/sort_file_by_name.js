// 与后端 quark_auto_save.py 的 sort_file_by_name 完全一致的排序逻辑
// 用于前端文件列表排序

function chineseToArabic(chinese) {
    // 简单实现，支持一到一万
    const cnNums = {
        '零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9,
        '十': 10, '百': 100, '千': 1000, '万': 10000
    };
    let result = 0, unit = 1, num = 0;
    for (let i = chinese.length - 1; i >= 0; i--) {
        const char = chinese[i];
        if (cnNums[char] >= 10) {
            unit = cnNums[char];
            if (unit === 10 && (i === 0 || cnNums[chinese[i - 1]] === undefined)) {
                num = 1;
            }
        } else if (cnNums[char] !== undefined) {
            num = cnNums[char];
            result += num * unit;
        }
    }
    return result || null;
}

function sortFileByName(file) {
    // 兼容 dict 或字符串
    let filename = typeof file === 'object' ? (file.file_name || '') : file;
    let update_time = typeof file === 'object' ? (file.updated_at || 0) : 0;
    let file_name_without_ext = filename.replace(/\.[^/.]+$/, '');
    
    // 0. 预处理（前移）：移除技术规格与季号，供后续“日期与集数”提取共同使用
    // 这样可以避免 30FPS/1080p/Season 等噪音影响识别
    let cleanedName = file_name_without_ext;
    try {
        const techSpecs = [
            // 分辨率相关（限定常见p档）
            /\b(?:240|360|480|540|720|900|960|1080|1440|2160|4320)[pP]\b/g,
            // 常见分辨率 WxH（白名单）
            /\b(?:640x360|640x480|720x480|720x576|854x480|960x540|1024x576|1280x720|1280x800|1280x960|1366x768|1440x900|1600x900|1920x1080|2560x1080|2560x1440|3440x1440|3840x1600|3840x2160|4096x2160|7680x4320)\b/g,
            /(?<!\d)[248]\s*[Kk](?!\d)/g,       // 2K/4K/8K

            // 视频编码相关（包含数字的编码）
            /\b[Hh]\.?264\b/g,                  // h264, h.264, H264, H.264
            /\b[Hh]\.?265\b/g,                  // h265, h.265, H265, H.265
            /\b[Xx]264\b/g,                      // x264, X264
            /\b[Xx]265\b/g,                      // x265, X265

            // 音频采样率（限定常见采样率）
            /\b(?:44\.1|48|96)\s*[Kk][Hh][Zz]\b/g,
            /\b(?:44100|48000|96000)\s*[Hh][Zz]\b/g,

            // 常见码率（白名单）
            /\b(?:64|96|128|160|192|256|320)\s*[Kk][Bb][Pp][Ss]\b/g,
            /\b(?:1|1\.5|2|2\.5|3|4|5|6|8|10|12|15|20|25|30|35|40|50|80|100)\s*[Mm][Bb][Pp][Ss]\b/g,

            // 位深（白名单）
            /\b(?:8|10|12)\s*[Bb][Ii][Tt]s?\b/g,
            // 严格限定常见帧率，避免将 "07.30FPS" 视为帧率从而连带清除集数
            /\b(?:23\.976|29\.97|59\.94|24|25|30|50|60|90|120)\s*[Ff][Pp][Ss]\b/g,

            // 频率相关（白名单，含空格/无空格）
            /\b(?:100|144|200|240|400|800)\s*[Mm][Hh][Zz]\b/g,
            /\b(?:1|1\.4|2|2\.4|5|5\.8)\s*[Gg][Hh][Zz]\b/g,
            /\b(?:100|144|200|240|400|800)[Mm][Hh][Zz]\b/g,
            /\b(?:1|1\.4|2|2\.4|5|5\.8)[Gg][Hh][Zz]\b/g,

            // 声道相关（限定常见声道）
            /\b(?:1\.0|2\.0|5\.1|7\.1)\s*[Cc][Hh]\b/g,
            /\b(?:1\.0|2\.0|5\.1|7\.1)[Cc][Hh]\b/g,
            /\b(?:1\.0|2\.0|5\.1|7\.1)\s*[Cc][Hh][Aa][Nn][Nn][Ee][Ll]\b/g,

            // 其他技术参数（白名单）
            /\b(?:8|12|16|24|32|48|50|64|108)\s*[Mm][Pp]\b/g,
            /\b(?:720|1080|1440|1600|1920|2160|4320)\s*[Pp][Ii][Xx][Ee][Ll]\b/g,
            /\b(?:5400|7200)\s*[Rr][Pp][Mm]\b/g,
            /\b(?:720|1080|1440|1600|1920|2160|4320)[Pp][Ii][Xx][Ee][Ll]\b/g,
            /\b(?:5400|7200)[Rr][Pp][Mm]\b/g,
        ];
        const seasons = [/[Ss]\d+(?![Ee])/gi, /[Ss]\s+\d+/gi, /Season\s*\d+/gi, /第\s*\d+\s*季/gi, /第\s*[一二三四五六七八九十百千万零两]+\s*季/gi];
        for (const p of techSpecs) cleanedName = cleanedName.replace(p, ' ');
        for (const p of seasons) cleanedName = cleanedName.replace(p, ' ');
    } catch (e) {}
    let date_value = Infinity, episode_value = Infinity, segment_value = 0;

    // 生成拼音排序键（第五级排序）
    let pinyin_sort_key;
    try {
        // 尝试使用 pinyinPro 库进行拼音转换
        if (typeof pinyinPro !== 'undefined') {
            pinyin_sort_key = pinyinPro.pinyin(filename, { toneType: 'none', type: 'string' }).toLowerCase();
        } else {
            pinyin_sort_key = filename.toLowerCase();
        }
    } catch (e) {
        pinyin_sort_key = filename.toLowerCase();
    }

    // 1. 日期提取（改为基于 cleanedName，以避免技术规格噪音干扰）
    let match;
    // YYYY-MM-DD
    match = cleanedName.match(/((?:19|20)\d{2})[-./\s](\d{1,2})[-./\s](\d{1,2})/);
    if (match) {
        date_value = parseInt(match[1]) * 10000 + parseInt(match[2]) * 100 + parseInt(match[3]);
    }
    // YY-MM-DD
    if (date_value === Infinity) {
        match = cleanedName.match(/(?<![Ee][Pp]?)((?:19|20)?\d{2})[-./\s](\d{1,2})[-./\s](\d{1,2})/);
        if (match && match[1].length === 2) {
            let year = parseInt('20' + match[1]);
            date_value = year * 10000 + parseInt(match[2]) * 100 + parseInt(match[3]);
        }
    }
    // YYYYMMDD
    if (date_value === Infinity) {
    match = cleanedName.match(/((?:19|20)\d{2})(\d{2})(\d{2})/);
        if (match) {
            date_value = parseInt(match[1]) * 10000 + parseInt(match[2]) * 100 + parseInt(match[3]);
        }
    }
    // YYMMDD
    if (date_value === Infinity) {
    match = cleanedName.match(/(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)/);
        if (match) {
            let month = parseInt(match[2]), day = parseInt(match[3]);
            if (1 <= month && month <= 12 && 1 <= day && day <= 31) {
                let year = parseInt('20' + match[1]);
                date_value = year * 10000 + month * 100 + day;
            }
        }
    }
    // MM/DD/YYYY
    if (date_value === Infinity) {
    match = cleanedName.match(/(\d{1,2})[-./\s](\d{1,2})[-./\s]((?:19|20)\d{2})/);
        if (match) {
            let month = parseInt(match[1]), day = parseInt(match[2]), year = parseInt(match[3]);
            if (month > 12) [month, day] = [day, month];
            date_value = year * 10000 + month * 100 + day;
        }
    }
    // MM-DD
    if (date_value === Infinity) {
    match = cleanedName.match(/(?<![Ee][Pp]?)(?<!\d)(\d{1,2})[-./](\d{1,2})(?!\d)/);
        if (match) {
            let month = parseInt(match[1]), day = parseInt(match[2]);
            // 验证是否为有效的月日组合
            if ((month >= 1 && month <= 12 && day >= 1 && day <= 31) ||
                (day >= 1 && day <= 12 && month >= 1 && month <= 31)) {
                if (month > 12) [month, day] = [day, month];
                date_value = 20000000 + month * 100 + day;
            }
        }
    }

    // 2. 期数/集数（同样基于 cleanedName）
    // 第X期/集/话
    match = cleanedName.match(/第(\d+)[期集话]/);
    if (match) episode_value = parseInt(match[1]);
    // 第[中文数字]期/集/话
    if (episode_value === Infinity) {
        match = cleanedName.match(/第([一二三四五六七八九十百千万零两]+)[期集话]/);
        if (match) {
            let arabic = chineseToArabic(match[1]);
            if (arabic !== null) episode_value = arabic;
        }
    }
    // X集/期/话
    if (episode_value === Infinity) {
        match = cleanedName.match(/(\d+)[期集话]/);
        if (match) episode_value = parseInt(match[1]);
    }
    // [中文数字]集/期/话
    if (episode_value === Infinity) {
        match = cleanedName.match(/([一二三四五六七八九十百千万零两]+)[期集话]/);
        if (match) {
            let arabic = chineseToArabic(match[1]);
            if (arabic !== null) episode_value = arabic;
        }
    }
    // S01E01
    if (episode_value === Infinity) {
        match = cleanedName.match(/[Ss](\d+)[Ee](\d+)/);
        if (match) episode_value = parseInt(match[2]);
    }
    // E01/EP01
    if (episode_value === Infinity) {
        match = cleanedName.match(/[Ee][Pp]?(\d+)/);
        if (match) episode_value = parseInt(match[1]);
    }
    // 1x01
    if (episode_value === Infinity) {
        match = cleanedName.match(/(\d+)[Xx](\d+)/);
        if (match) episode_value = parseInt(match[2]);
    }
    // [数字]或【数字】
    if (episode_value === Infinity) {
        match = cleanedName.match(/\[(\d+)\]|【(\d+)】/);
        if (match) episode_value = parseInt(match[1] || match[2]);
    }
    // 纯数字文件名
    if (episode_value === Infinity) {
        if (/^\d+$/.test(cleanedName)) {
            episode_value = parseInt(cleanedName);
        } else {
            // 兜底：直接从已清洗的 cleanedName 中提取第一个数字
            match = cleanedName.match(/(\d+)/);
            if (match) episode_value = parseInt(match[1]);
        }
    }

    // 3. 上中下标记或其他细分 - 第三级排序键
    let segment_base = 0;  // 基础值：上=1, 中=2, 下=3
    let sequence_number = 0;  // 序号值：用于处理上中下后的数字或中文数字序号

    // 严格匹配上中下标记：只有当上中下与集期话部篇相邻时才认为是段落标记
    // 避免误匹配文件内容中偶然出现的上中下字符
    if (/上[集期话部篇]|[集期话部篇]上/.test(filename)) {
        segment_base = 1;
    } else if (/中[集期话部篇]|[集期话部篇]中/.test(filename)) {
        segment_base = 2;
    } else if (/下[集期话部篇]|[集期话部篇]下/.test(filename)) {
        segment_base = 3;
    }

    // 统一的序号提取逻辑，支持多种分隔符和格式
    // 无论是否有上中下标记，都使用相同的序号提取逻辑

    // 定义序号提取的模式，使用正向匹配组合的方式
    // 这样可以精准匹配，避免误判"星期六"等内容
    const sequence_patterns = [
        // 第+中文数字+期集话部篇+序号：第一期（一）、第五十六期-二、第 一 期 三
        { pattern: /第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇]\s*[（(]\s*([一二三四五六七八九十百千万零两]+)\s*[）)]/u, type: 'chinese' },
        { pattern: /第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇]\s*[（(]\s*(\d+)\s*[）)]/u, type: 'arabic' },
        { pattern: /第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇]\s*[-_·丨]\s*([一二三四五六七八九十百千万零两]+)/u, type: 'chinese' },
        { pattern: /第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇]\s*[-_·丨]\s*(\d+)/u, type: 'arabic' },
        { pattern: /第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇]\s+([一二三四五六七八九十百千万零两]+)(?![一二三四五六七八九十])/u, type: 'chinese' },
        { pattern: /第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇]\s+(\d+)(?!\d)/u, type: 'arabic' },
        { pattern: /第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇]([一二三四五六七八九十])(?![一二三四五六七八九十])/u, type: 'chinese' },
        { pattern: /第\s*[一二三四五六七八九十百千万零两]+\s*[期集话部篇](\d+)(?!\d)/u, type: 'arabic' },

        // 第+阿拉伯数字+期集话部篇+序号：第1期（一）、第100期-二、第 1 期 三
        { pattern: /第\s*\d+\s*[期集话部篇]\s*[（(]\s*([一二三四五六七八九十百千万零两]+)\s*[）)]/u, type: 'chinese' },
        { pattern: /第\s*\d+\s*[期集话部篇]\s*[（(]\s*(\d+)\s*[）)]/u, type: 'arabic' },
        { pattern: /第\s*\d+\s*[期集话部篇]\s*[-_·丨]\s*([一二三四五六七八九十百千万零两]+)/u, type: 'chinese' },
        { pattern: /第\s*\d+\s*[期集话部篇]\s*[-_·丨]\s*(\d+)/u, type: 'arabic' },
        { pattern: /第\s*\d+\s*[期集话部篇]\s+([一二三四五六七八九十百千万零两]+)(?![一二三四五六七八九十])/u, type: 'chinese' },
        { pattern: /第\s*\d+\s*[期集话部篇]\s+(\d+)(?!\d)/u, type: 'arabic' },
        { pattern: /第\s*\d+\s*[期集话部篇]([一二三四五六七八九十])(?![一二三四五六七八九十])/u, type: 'chinese' },
        { pattern: /第\s*\d+\s*[期集话部篇](\d+)(?!\d)/u, type: 'arabic' },

        // 上中下+集期话部篇+序号：上集（一）、中部-二、下篇 三
        { pattern: /[上中下][集期话部篇]\s*[（(]\s*([一二三四五六七八九十百千万零两]+)\s*[）)]/u, type: 'chinese' },
        { pattern: /[上中下][集期话部篇]\s*[（(]\s*(\d+)\s*[）)]/u, type: 'arabic' },
        { pattern: /[上中下][集期话部篇]\s*[-_·丨]\s*([一二三四五六七八九十百千万零两]+)/u, type: 'chinese' },
        { pattern: /[上中下][集期话部篇]\s*[-_·丨]\s*(\d+)/u, type: 'arabic' },
        { pattern: /[上中下][集期话部篇]\s+([一二三四五六七八九十百千万零两]+)(?![一二三四五六七八九十])/u, type: 'chinese' },
        { pattern: /[上中下][集期话部篇]\s+(\d+)(?!\d)/u, type: 'arabic' },
        { pattern: /[上中下][集期话部篇]([一二三四五六七八九十])(?![一二三四五六七八九十])/u, type: 'chinese' },
        { pattern: /[上中下][集期话部篇](\d+)(?!\d)/u, type: 'arabic' },

        // 集期话部篇+上中下+序号：集上（一）、部中-二、篇下 三
        { pattern: /[集期话部篇][上中下]\s*[（(]\s*([一二三四五六七八九十百千万零两]+)\s*[）)]/u, type: 'chinese' },
        { pattern: /[集期话部篇][上中下]\s*[（(]\s*(\d+)\s*[）)]/u, type: 'arabic' },
        { pattern: /[集期话部篇][上中下]\s*[-_·丨]\s*([一二三四五六七八九十百千万零两]+)/u, type: 'chinese' },
        { pattern: /[集期话部篇][上中下]\s*[-_·丨]\s*(\d+)/u, type: 'arabic' },
        { pattern: /[集期话部篇][上中下]\s+([一二三四五六七八九十百千万零两]+)(?![一二三四五六七八九十])/u, type: 'chinese' },
        { pattern: /[集期话部篇][上中下]\s+(\d+)(?!\d)/u, type: 'arabic' },
        { pattern: /[集期话部篇][上中下]([一二三四五六七八九十])(?![一二三四五六七八九十])/u, type: 'chinese' },
        { pattern: /[集期话部篇][上中下](\d+)(?!\d)/u, type: 'arabic' },
    ];

    // 尝试匹配序号
    for (const { pattern, type } of sequence_patterns) {
        const match = filename.match(pattern);
        if (match) {
            if (type === 'chinese') {
                const arabic_num = chineseToArabic(match[1]);
                if (arabic_num !== null) {
                    sequence_number = arabic_num;
                    // 如果之前没有检测到上中下标记，给一个基础值
                    if (segment_base === 0) {
                        segment_base = 1;
                    }
                    break;
                }
            } else { // arabic
                sequence_number = parseInt(match[1]);
                // 如果之前没有检测到上中下标记，给一个基础值
                if (segment_base === 0) {
                    segment_base = 1;
                }
                break;
            }
        }
    }

    // 组合segment_value：基础值*1000 + 序号值，确保排序正确
    segment_value = segment_base * 1000 + sequence_number;

    return [date_value, episode_value, segment_value, update_time, pinyin_sort_key];
}

// 用法：
// arr.sort((a, b) => {
//   const ka = sortFileByName(a), kb = sortFileByName(b);
//   for (let i = 0; i < ka.length; ++i) {
//     if (ka[i] !== kb[i]) return ka[i] > kb[i] ? 1 : -1;
//   }
//   return 0;
// }); 