---
name: quark-auto-save
description: Manage quark-auto-save(QAS, 夸克自动转存, 夸克转存, 夸克订阅) tasks via API.
metadata:
  openclaw:
    emoji: "💾"
    homepage: "https://github.com/Cp0204/quark-auto-save"
    requires:
      env:
        - QAS_BASE_URL
        - QAS_TOKEN
      anyBins:
        - curl
        - python3
    primaryEnv: QAS_TOKEN
---

# quark-auto-save

Manage quark-auto-save(QAS, 夸克自动转存, 夸克转存, 夸克订阅) tasks via API.

When user send message like `https://pan.quark.cn/s/***`, get detail, add a QAS task.

**WIKI:**
- RegexRename: https://github.com/Cp0204/quark-auto-save/wiki/%E6%AD%A3%E5%88%99%E5%A4%84%E7%90%86%E6%95%99%E7%A8%8B
- MagicRegex: https://github.com/Cp0204/quark-auto-save/wiki/%E9%AD%94%E6%B3%95%E5%8C%B9%E9%85%8D%E5%92%8C%E9%AD%94%E6%B3%95%E5%8F%98%E9%87%8F

## ⚠️ Prerequisites

**Env:**
- `QAS_BASE_URL` -  User provided, e.g., http://192.168.1.x:5005)
- `QAS_TOKEN` - User provided

**Actual configuration values are recorded in TOOLS.md, Do not modify SKILL.md**

## First Configuration: Analyze User Habits

After the user sets the token, the following analysis must be performed and recorded in TOOLS.md:

1. **Get Current Configuration**:
   ```
   GET /data?token={QAS_TOKEN}
   ```

2. **Analyze Saving Habits**:
   - Extract `savepath` directory patterns from existing tasks (e.g., `/video/tv/`, `/video/anime/`, `/video/movie/`)
   - Understand naming pattern preferences from `pattern` `replace` `magic_regex`

3. **Record to TOOLS.md**:
   ```markdown
   ### quark-auto-save habits
   - TV Series Directory: /video/tv/{name}
   - Anime Directory: /video/anime/{name}
   - Movie Directory: /video/movie/{name}
   - Naming Pattern: $TV_MAGIC
   ```

## Python Client (Recommended)

If Python is available, prioritize using Python for execution
A Python wrapper script is available at `{baseDir}/scripts/qas_client.py`.

```bash
# Set environment
export QAS_BASE_URL=
export QAS_TOKEN=

# Commands
python {baseDir}/scripts/qas_client.py data                   # Get all config & tasks
python {baseDir}/scripts/qas_client.py search "query"         # Search resources
python {baseDir}/scripts/qas_client.py search "query" -d      # Deep search
python {baseDir}/scripts/qas_client.py detail "<shareurl>"    # Get share detail
python {baseDir}/scripts/qas_client.py add task.json          # Add task
python {baseDir}/scripts/qas_client.py run                    # Run all tasks
python {baseDir}/scripts/qas_client.py run "TaskName"         # Run specific task
python {baseDir}/scripts/qas_client.py savepath "/video/tv"   # Check savepath
```


## ⚠️ Important: Token Location

**Token MUST be in URL query parameter, NOT in request body!**

- ✅ Correct: `GET /data?token=xxx` or `POST /api/add_task?token=xxx`
- ❌ Wrong: `POST /api/add_task` with `{"token": "xxx"}` in body (server ignores it)

## API

All APIs require `?token=xxx` query parameter. Example: `$QAS_BASE_URL/$ENDPOINT?token=xxx`

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/data` | GET | Get all config, tasks, and API token |
| `/update` | POST | Update config (including add/delete/modify tasks via tasklist) |
| `/api/add_task` | POST | Add new task |
| `/task_suggestions` | GET | Search resources `?q=keyword&d=1` (d=1 for deep search) |
| `/get_share_detail` | POST | Get share details, file list, and subdirs |
| `/get_savepath_detail` | GET | Get savepath file list `?path=/video/tv/xxx` or `?fid=xxx` |
| `/delete_file` | POST | Delete file by fid (dangerous behavior, requires confirmation) |
| `/run_script_now` | POST | Run task manually (supports SSE streaming output) |
| `/login` | GET/POST | WebUI login |
| `/logout` | GET | Logout |

## Task Schema

```json
{
  "taskname": "Earth",
  "shareurl": "https://pan.quark.cn/s/xxx#/list/share/fid",
  "savepath": "/video/tv/Earth",
  "pattern": "$TV_MAGIC",
  "replace": "",
  "update_subdir": "",
  "ignore_extension": false,
  "runweek": [1,2,3,4,5,6,7],
  "addition": {}
}
```

## Task Fields

| Field | Required | Description |
|-------|----------|-------------|
| `taskname` | Yes | Standard media name, no season info (e.g., "Black Mirror" not "Black Mirror S03") |
| `shareurl` | Yes | Quark share URL |
| `savepath` | Yes | Save directory in Quark cloud drive |
| `pattern` | No | Regex or magic pattern for rename |
| `replace` | No | Replacement pattern |
| `update_subdir` | No | Update pattern for subdirectories |
| `ignore_extension` | No | Ignore file extension when checking duplicates |
| `runweek` | No | Week of run, []=disable task |
| `addition` | No | (Auto gen) Plugin config |
| `shareurl_ban` | No |(Auto gen) Have key mean bad shareurl, value is reason|

## savepath Rules

(Example, based on user habits)
- `/video/tv/{name}` for TV series
- `/video/anime/{name}` for anime
- `/video/movie/{name}` for movies


## shareurl Rules

Format
- `https://pan.quark.cn/s/{abc123}`
- `https://pan.quark.cn/s/{abc123}#/list/share/{fid}`

**Priority for selecting subdirectories:**
1. Select subdirectories containing video files (mp4, mkv, avi, etc.)
2. Prioritize directories with higher resolution: 4K > 1080P > 720P > Others
3. Prioritize directories with embedded/internal subtitles
4. Avoid selecting non-main content directories such as trailers, extras, etc.

**Getting subdir:**
```
POST /get_share_detail
{"shareurl": "https://pan.quark.cn/s/{abc123}#/list/share/{fid}", "task": {...}}
```
Returns `file_list` structure containing all subdir and files.

## Pattern & Rename

| pattern | replace | Example |
|---|---|---|
| `.*` | | Save all files |
| `\.(mp4\|mkv)$` | | Save all .mp4 .mkv files |
| `^【AD】NAME(\d+)\.mp4` | `\1.\2` | 【AD】NAME01.mp4 → 01.mp4 |
| `^(\d+)\.mp4` | `S02E\1.mp4` | 01.mp4 → S02E01.mp4 |
| `^(\d+)\.mp4` | `{TASKNAME}.S02E\1.mp4` | 01.mp4 → taskname.S02E01.mp4 |
| `$TV` | | Use MagicRegex (User Custom) |

### Magic Variables

Can be used in `task.replace`

| Variable | Description | Example |
|---|---|---|
| `{TASKNAME}` | `taskname` from task | `Earth` |
| `{II}` | Index number, auto incremented, padding with zeros | `01` `02` `001` `002` |
| `{EXT}` | File extension, extracted from filename | `txt` `mp4` `jpg` |
| `{DATE}` | Date, extracted from filename, formatted as YYYYMMDD | `20231026` |
| `{YEAR}` | Year, extracted from filename | `1874` `2025` |
| `{S}` | Season number, extracted from filename | `01` `02` |
| `{SXX}` | Season string with S prefix, or S01 if not found | `S01` `S02` |
| `{E}` | Episode number, extracted from filename | `1` `01` `123` |
| `{PART}` | "上/中/下" or "一/二/三/...十" part, or empty if not found | `上` `下` `一` `十` |

## Workflow: Add New Task

1. **Search** for the media:
   ```
   GET /task_suggestions?q={name}&d=1&token={QAS_TOKEN}
   ```

2. **Get share detail** to see subdirs and files:
   ```
   POST /get_share_detail?token={QAS_TOKEN}
   {"shareurl": "https://pan.quark.cn/s/xxx"}
   ```

3. **Select subdir**: Find folder with video files, prefer highest quality (4k > 1080p > 720p)

4. **Verify savepath** exists:
   ```
   GET /get_savepath_detail?path=/video/tv/{name}&token={QAS_TOKEN}
   ```

5. **Create task**:
   ```
   POST /api/add_task?token={QAS_TOKEN}
   {
     "taskname": "Black Mirror",
     "shareurl": "https://pan.quark.cn/s/xxx#/list/share/fid",
     "savepath": "/video/tv/Black Mirror",
     "pattern": "$TV_MAGIC",
     "addition": {"emby": {"try_match": true}, "alist_strm_gen": {"auto_gen": true}}
   }
   ```

## Workflow: Check for Invalid Tasks

Each time the task list is retrieved, check for the `shareurl_ban` key:

1. **Get Task List**:
   ```
   GET /data?token={QAS_TOKEN}
   ```

2. **Check for Invalid Tasks**:
   ```python
   tasks = data.get('tasklist', [])
   invalid_tasks = [t for t in tasks if 'shareurl_ban' in t]
   ```

3. **Notify User**:
   - Inform the user which tasks are invalid and why
   - Ask if they need replacement

4. **Replace Invalid Tasks**:
   - Replace the `shareurl` value of invalid tasks, keeping other values unchanged
   - Remove the `shareurl_ban` key
   - Use `/update` to update the tasks

## Workflow: Delete Task

Tasks cannot be deleted directly. Use `/update` to replace the entire tasklist:

1. **Get current tasks**:
   ```
   GET /data?token={QAS_TOKEN}
   ```

2. **Update with filtered tasklist** (remove the task you want to delete):
   ```
   POST /update?token={QAS_TOKEN}
   {"tasklist": [{"taskname": "Task to keep", ...}]}
   ```

## Workflow: Run Task Manually

Returns SSE stream (text/event-stream) with script output, not JSON.

```bash
# Run specific task
curl -X POST "$QAS_BASE_URL/run_script_now?token=$QAS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tasklist": [{"taskname": "Black Mirror", ...}]}'

# Run all tasks
curl -X POST "$QAS_BASE_URL/run_script_now?token=$QAS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Output format:
```
data: ===============程序开始===============
data: ⏰ 执行时间: 2026-03-18 14:38:34
data: ...
```

## Example Commands

```bash
# Get all config and tasks
curl "$QAS_BASE_URL/data?token=$QAS_TOKEN"

# Search for a movie
curl "$QAS_BASE_URL/task_suggestions?q=dune%20part%20two&d=1&token=$QAS_TOKEN"

# Get share detail with preview (magic rename preview)
curl -X POST "$QAS_BASE_URL/get_share_detail?token=$QAS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"shareurl": "https://pan.quark.cn/s/xxx", "task": {"taskname": "Dune", "savepath": "/video/movie/Dune", "pattern": "$TV_MAGIC", "update_subdir": "", "ignore_extension": false}}'

# Check savepath contents
curl "$QAS_BASE_URL/get_savepath_detail?path=/video/tv/Black%20Mirror&token=$QAS_TOKEN"

# Delete a file
curl -X POST "$QAS_BASE_URL/delete_file?token=$QAS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"fid": "5f9xxxxxxxxxxxxx"}'
```

## Config Fields

The `/data` endpoint returns these config sections:

| Section | Description |
|---------|-------------|
| `source.net` | Network search config |
| `source.cloudsaver` | CloudSaver config |
| `source.pansou` | PanSou search config |
| `crontab` | Cron schedule (e.g., "0 8,18,20 * * *") |
| `magic_regex` | Custom magic rename patterns |
| `plugins` | Plugin configurations |
| `tasklist` | Array of all tasks |
| `api_token` | Current API token |

## Error Handling

- `{"success": false, "message": ""}` - Token invalid or not provided
- `{"success": false, "data": {"error": "..."}}` - API returned an error
- Check response `success` field for operation status

