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
            // 预处理：移除分辨率标识（如 720p, 1080P, 2160p 等）
            let filename_without_resolution = filename;
            const resolution_patterns = [
                /\b\d+[pP]\b/g,  // 匹配 720p, 1080P, 2160p 等
                /\b\d+x\d+\b/g,  // 匹配 1920x1080 等
                // 注意：不移除4K/8K，因为剧集匹配规则中有 (\d+)[-_\s]*4[Kk] 模式
            ];

            for (const pattern of resolution_patterns) {
                filename_without_resolution = filename_without_resolution.replace(pattern, ' ');
            }

            match = filename_without_resolution.match(/(\d+)/);
            if (match) episode_value = parseInt(match[1]);
        }
    }

    // 3. 上中下
    if (/[上][集期话部篇]?|[集期话部篇]上/.test(filename)) segment_value = 1;
    else if (/[中][集期话部篇]?|[集期话部篇]中/.test(filename)) segment_value = 2;
    else if (/[下][集期话部篇]?|[集期话部篇]下/.test(filename)) segment_value = 3;

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