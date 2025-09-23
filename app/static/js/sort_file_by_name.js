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

    // 1. 日期提取
    let match;
    // YYYY-MM-DD
    match = filename.match(/((?:19|20)\d{2})[-./\s](\d{1,2})[-./\s](\d{1,2})/);
    if (match) {
        date_value = parseInt(match[1]) * 10000 + parseInt(match[2]) * 100 + parseInt(match[3]);
    }
    // YY-MM-DD
    if (date_value === Infinity) {
        match = filename.match(/((?:19|20)?\d{2})[-./\s](\d{1,2})[-./\s](\d{1,2})/);
        if (match && match[1].length === 2) {
            let year = parseInt('20' + match[1]);
            date_value = year * 10000 + parseInt(match[2]) * 100 + parseInt(match[3]);
        }
    }
    // YYYYMMDD
    if (date_value === Infinity) {
        match = filename.match(/((?:19|20)\d{2})(\d{2})(\d{2})/);
        if (match) {
            date_value = parseInt(match[1]) * 10000 + parseInt(match[2]) * 100 + parseInt(match[3]);
        }
    }
    // YYMMDD
    if (date_value === Infinity) {
        match = filename.match(/(?<!\d)(\d{2})(\d{2})(\d{2})(?!\d)/);
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
        match = filename.match(/(\d{1,2})[-./\s](\d{1,2})[-./\s]((?:19|20)\d{2})/);
        if (match) {
            let month = parseInt(match[1]), day = parseInt(match[2]), year = parseInt(match[3]);
            if (month > 12) [month, day] = [day, month];
            date_value = year * 10000 + month * 100 + day;
        }
    }
    // MM-DD
    if (date_value === Infinity) {
        match = filename.match(/(?<!\d)(\d{1,2})[-./](\d{1,2})(?!\d)/);
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

    // 2. 期数/集数
    // 第X期/集/话
    match = filename.match(/第(\d+)[期集话]/);
    if (match) episode_value = parseInt(match[1]);
    // 第[中文数字]期/集/话
    if (episode_value === Infinity) {
        match = filename.match(/第([一二三四五六七八九十百千万零两]+)[期集话]/);
        if (match) {
            let arabic = chineseToArabic(match[1]);
            if (arabic !== null) episode_value = arabic;
        }
    }
    // X集/期/话
    if (episode_value === Infinity) {
        match = filename.match(/(\d+)[期集话]/);
        if (match) episode_value = parseInt(match[1]);
    }
    // [中文数字]集/期/话
    if (episode_value === Infinity) {
        match = filename.match(/([一二三四五六七八九十百千万零两]+)[期集话]/);
        if (match) {
            let arabic = chineseToArabic(match[1]);
            if (arabic !== null) episode_value = arabic;
        }
    }
    // S01E01
    if (episode_value === Infinity) {
        match = filename.match(/[Ss](\d+)[Ee](\d+)/);
        if (match) episode_value = parseInt(match[2]);
    }
    // E01/EP01
    if (episode_value === Infinity) {
        match = filename.match(/[Ee][Pp]?(\d+)/);
        if (match) episode_value = parseInt(match[1]);
    }
    // 1x01
    if (episode_value === Infinity) {
        match = filename.match(/(\d+)[Xx](\d+)/);
        if (match) episode_value = parseInt(match[2]);
    }
    // [数字]或【数字】
    if (episode_value === Infinity) {
        match = filename.match(/\[(\d+)\]|【(\d+)】/);
        if (match) episode_value = parseInt(match[1] || match[2]);
    }
    // 纯数字文件名
    if (episode_value === Infinity) {
        if (/^\d+$/.test(file_name_without_ext)) {
            episode_value = parseInt(file_name_without_ext);
        } else {
            // 预处理：移除技术规格信息，避免误提取技术参数中的数字为集编号
            let filename_without_resolution = filename;
            const tech_spec_patterns = [
                // 分辨率相关
                /\b\d+[pP]\b/g,  // 匹配 720p, 1080P, 2160p 等
                /\b\d+x\d+\b/g,  // 匹配 1920x1080 等
                /(?<!\d)[248]\s*[Kk](?!\d)/g,  // 匹配 2K/4K/8K
                
                // 视频编码相关（包含数字的编码）
                /\b[Hh]\.?264\b/g,  // 匹配 h264, h.264, H264, H.264
                /\b[Hh]\.?265\b/g,  // 匹配 h265, h.265, H265, H.265
                /\b[Xx]264\b/g,     // 匹配 x264, X264
                /\b[Xx]265\b/g,     // 匹配 x265, X265
                
                // 文件大小相关
                /\b\d+\.?\d*\s*[Gg][Bb]\b/g,  // 匹配 5.2GB, 7GB, 1.5GB 等
                /\b\d+\.?\d*\s*[Mm][Bb]\b/g,  // 匹配 850MB, 1.5MB 等
                /\b\d+\.?\d*\s*[Kk][Bb]\b/g,  // 匹配 128KB, 1.5KB 等
                /\b\d+\.?\d*\s*[Tt][Bb]\b/g,  // 匹配 1.5TB, 2TB 等
                /\b\d+\.?\d*\s*[Pp][Bb]\b/g,  // 匹配 1.5PB 等
                /\b\d+\.?\d*[Gg][Bb]\b/g,     // 匹配 5.2GB, 7GB, 1.5GB 等（无空格）
                /\b\d+\.?\d*[Mm][Bb]\b/g,     // 匹配 850MB, 1.5MB 等（无空格）
                /\b\d+\.?\d*[Kk][Bb]\b/g,     // 匹配 128KB, 1.5KB 等（无空格）
                /\b\d+\.?\d*[Tt][Bb]\b/g,     // 匹配 1.5TB, 2TB 等（无空格）
                
                // 音频采样率
                /\b\d+\.?\d*\s*[Kk][Hh][Zz]\b/g,  // 匹配 44.1kHz, 48kHz, 96kHz 等
                /\b\d+\.?\d*\s*[Hh][Zz]\b/g,      // 匹配 44100Hz, 48000Hz 等
                
                // 比特率
                /\b\d+\.?\d*\s*[Kk]?[Bb][Pp][Ss]\b/g,  // 匹配 128kbps, 320kbps, 1.5Mbps 等
                /\b\d+\.?\d*\s*[Mm][Bb][Pp][Ss]\b/g,   // 匹配 1.5Mbps, 2Mbps 等
                
                // 视频相关
                /\b\d+\.?\d*\s*[Bb][Ii][Tt]\b/g,  // 匹配 10bit, 8bit, 12bit 等
                /\b\d+\.?\d*\s*[Ff][Pp][Ss]\b/g,  // 匹配 30FPS, 60fps, 24fps 等
                
                // 频率相关
                /\b\d+\.?\d*\s*[Mm][Hh][Zz]\b/g,  // 匹配 100MHz, 2.4GHz 等
                /\b\d+\.?\d*\s*[Gg][Hh][Zz]\b/g,  // 匹配 2.4GHz, 5GHz 等
                /\b\d+\.?\d*[Mm][Hh][Zz]\b/g,     // 匹配 100MHz, 2.4GHz 等（无空格）
                /\b\d+\.?\d*[Gg][Hh][Zz]\b/g,     // 匹配 2.4GHz, 5GHz 等（无空格）
                
                // 声道相关
                /\b\d+\.?\d*\s*[Cc][Hh]\b/g,      // 匹配 7.1ch, 5.1ch, 2.0ch 等
                /\b\d+\.?\d*[Cc][Hh]\b/g,         // 匹配 7.1ch, 5.1ch, 2.0ch 等（无空格）
                /\b\d+\.?\d*\s*[Cc][Hh][Aa][Nn][Nn][Ee][Ll]\b/g,  // 匹配 7.1channel 等
                
                // 位深相关
                /\b\d+\.?\d*\s*[Bb][Ii][Tt][Ss]\b/g,  // 匹配 128bits, 256bits 等
                
                // 其他技术参数
                /\b\d+\.?\d*\s*[Mm][Pp]\b/g,      // 匹配 1080MP, 4KMP 等
                /\b\d+\.?\d*\s*[Pp][Ii][Xx][Ee][Ll]\b/g,  // 匹配 1920pixel 等
                /\b\d+\.?\d*\s*[Rr][Pp][Mm]\b/g,  // 匹配 7200RPM 等
            ];

            for (const pattern of tech_spec_patterns) {
                filename_without_resolution = filename_without_resolution.replace(pattern, ' ');
            }

            match = filename_without_resolution.match(/(\d+)/);
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