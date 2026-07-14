# disk-cleanup-skills

[English README](README.en.md)

面向 Windows 的磁盘扫描与安全清理 Skill。对外仅提供 `scan` 和 `clean`：前者只读分析，后者只根据同一任务的候选 ID 生成不可变计划，并在下一轮明确审批后把文件或受控目录移入 Windows 回收站。

## 安全边界

- 审查与删除严格分离，审查阶段不会修改文件系统。
- 删除入口只接受 run_id、candidate_id、plan_hash 和确认短语，不接受任意路径或命令。
- 删除前重新验证扫描根、保护目录、reparse point、文件 ID 和修改时间。
- 执行器支持文件和经过重解析点复核的受控目录。
- 回收站操作失败时不会降级为永久删除。
- Web 页面只用于审查；真实删除只能通过本项目 CLI 执行。
- Web 页面可生成不可变清理计划，但不提供删除 API 或“确认删除”按钮。
- 不执行 BleachBit cleaner，不使用 Remove-Item、rd 或 Shell COM 作为回退。

即使具备上述保护，也应先使用测试目录验证。不要首次运行就选择重要个人文件、项目目录或应用数据。

## 环境要求

- Windows 10/11
- Python 3.11 或更高版本
- WizTree 64 位版本（整盘扫描必需）
- PowerShell 5.1 或更高版本

项目不包含 WizTree，也不会自动下载、安装或提权。扫描盘符根目录时必须提供 WizTree 64 位程序或其 CSV 导出；普通子目录才允许使用较慢的只读流式遍历。WizTree 按 `--wiztree`、`DISK_CLEAN_WIZTREE`、`config.local.toml` 和默认安装路径的顺序检测。

```mermaid
flowchart LR
  A[WizTree 扫描或子目录流式扫描] --> B[本地 HTML 报告]
  B --> C[勾选候选并生成不可变计划]
  C --> D[新一轮对话: 执行删除勾选内容]
  D --> E[Windows 回收站]
  E --> F[报告页与本地端口自动关闭]
```

## 快速开始

克隆仓库后进入本目录。无需创建包含本机路径的配置文件；可以通过参数或环境变量指定 WizTree：

~~~powershell
$env:DISK_CLEAN_WIZTREE = "C:\Tools\WizTree\WizTree64.exe"
~~~

### 第一阶段：扫描

扫描本地磁盘并创建有效期为24小时的任务：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode scan -Target "C:"
~~~

便携版或非标准安装位置应显式传入路径：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode scan -Target "C:" -WizTreePath "C:\Tools\WizTree\WizTree64.exe"
~~~

默认 ExportMaxDepth 为 0，即不限制导出深度。对全盘扫描可能需要数分钟；脚本会等待 WizTree 完成 CSV 导出，然后自动输出目录、文件、扩展名和清理候选分析。若只出现 WizTree 界面而没有继续，请确认使用的是包含 PowerShell 5.1 兼容修复的当前版本。

也可以导入已有 WizTree CSV：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode scan -Target "C:" -CsvPath "C:\path\to\wiztree-export.csv"
~~~

命令返回 run_id。运行数据保存在 LOCALAPPDATA 下的 DiskCleanupSkill/runs 目录，过期后自动清理。

候选项会同时出现在命令输出和本地 HTML 报告中。报告展示最大目录与文件类型柱状图；勾选后点击“生成清理计划”，核对网页显示的精确路径和计划哈希，再在新一轮对话中说“执行删除勾选内容”。网页不会直接删除文件。

### 第二阶段：计划与清理

只选择审查结果中的候选 ID：

~~~powershell
.\scripts\invoke-once.ps1 -Mode clean -RunId "<run_id>" -CandidateId "C0123456789AB","CABCDEF012345"
~~~

核对输出中的每条精确路径、风险和 plan_hash 后，使用命令提示的确认短语执行：

~~~powershell
.\scripts\invoke-once.ps1 -Mode clean -RunId "<run_id>" -PlanHash "<plan_hash>" -ApprovalCode "RECYCLE <code>"
~~~

结果状态：

- RECYCLED：原路径已不存在，回收站操作完成。
- BLOCKED：安全校验拒绝执行。
- FAILED：Windows 回收站调用失败。
- UNKNOWN：无法可靠确认结果，不应当作成功处理。

任务数据在 24 小时后自动过期。审批码有效期为 10 分钟且只能使用一次。

## 本地配置与隐私

config.local.toml 仅用于本机覆盖，并已被 .gitignore 排除。不要提交以下内容：

- 真实 WizTree CSV、SQLite 索引、扫描报告和清理计划。
- config.local.toml、用户名、绝对安装路径或个人目录。
- API 密钥、令牌、截图、浏览器记录和终端日志。
- pytest 缓存、Python 缓存和构建产物。

提交前运行：

~~~powershell
git status --ignored --short
git grep -n -I -E "Users\\|[A-Z]:\\|api[_-]?key|password|secret|token"
~~~

示例与测试必须使用虚构用户名、占位路径和合成数据。

## 开发验证

~~~powershell
$env:PYTHONPATH = "src"
python -m pytest tests --basetemp .pytest_tmp
python -m compileall -q src tests
~~~

项目结构：

- SKILL.md：Skill 触发信息和不可绕过规则。
- agents/：Codex UI 元数据。
- references/：审查与清理工作流。
- scripts/：PowerShell 调用入口。
- src/disk_cleanup/：索引、分析、任务、Web 和安全执行代码。
- rules/：候选识别和保护规则。
- schemas/：公开 JSON 数据结构。
- tests/：合成数据与自动化测试。

## 开源许可

本项目采用 MIT License，详见 LICENSE。第三方工具及其商标归各自权利人所有。
