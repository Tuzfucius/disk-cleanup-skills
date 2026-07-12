# disk-cleanup-skills

面向 Windows 的两阶段磁盘审查与安全清理 Skill。第一阶段使用 WizTree 建立磁盘索引并生成候选项；第二阶段只根据同一任务中的 candidate_id 生成不可变计划，经用户明确确认后将文件移入 Windows 回收站。

## 安全边界

- 审查与删除严格分离，审查阶段不会修改文件系统。
- 删除入口只接受 run_id、candidate_id、plan_hash 和确认短语，不接受任意路径或命令。
- 删除前重新验证扫描根、保护目录、reparse point、文件 ID 和修改时间。
- 当前执行器只回收单个文件，不递归删除目录。
- 回收站操作失败时不会降级为永久删除。
- Web 页面只用于审查；真实删除只能通过本项目 CLI 执行。
- 不执行 BleachBit cleaner，不使用 Remove-Item、rd 或 Shell COM 作为回退。

即使具备上述保护，也应先使用测试目录验证。不要首次运行就选择重要个人文件、项目目录或应用数据。

## 环境要求

- Windows 10/11
- Python 3.11 或更高版本
- WizTree 64 位版本
- PowerShell 5.1 或更高版本

项目不包含 WizTree。请从其官方渠道获取，并遵守 WizTree 自身许可。

## 快速开始

克隆仓库后进入本目录。无需创建包含本机路径的配置文件；可以通过参数或环境变量指定 WizTree：

~~~powershell
$env:DISK_CLEAN_WIZTREE = "C:\Tools\WizTree\WizTree64.exe"
~~~

验证 Skill：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode validate
~~~

### 第一阶段：审查

扫描本地磁盘并创建有效期为24小时的任务：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode audit -Target "C:"
~~~

便携版或非标准安装位置应显式传入路径：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode audit -Target "C:" -WizTreePath "C:\Tools\WizTree\WizTree64.exe"
~~~

默认 ExportMaxDepth 为 0，即不限制导出深度。对全盘扫描可能需要数分钟；脚本会等待 WizTree 完成 CSV 导出，然后自动输出目录、文件、扩展名和清理候选分析。若只出现 WizTree 界面而没有继续，请确认使用的是包含 PowerShell 5.1 兼容修复的当前版本。

也可以导入已有 WizTree CSV：

~~~powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\invoke-once.ps1 -Mode audit -Target "C:" -CsvPath "C:\path\to\wiztree-export.csv"
~~~

命令返回 run_id。运行数据保存在 LOCALAPPDATA 下的 DiskCleanupSkill/runs 目录，过期后自动清理。

查看候选项：

~~~powershell
.\scripts\invoke-once.ps1 -Mode review -RunId "<run_id>"
~~~

### 第二阶段：计划与删除

只选择审查结果中的候选 ID：

~~~powershell
.\scripts\invoke-once.ps1 -Mode plan -RunId "<run_id>" -CandidateId "C0001","C0002"
~~~

核对输出中的每条精确路径、风险和 plan_hash 后，使用命令提示的确认短语执行：

~~~powershell
.\scripts\invoke-once.ps1 -Mode execute -RunId "<run_id>" -PlanHash "<plan_hash>" -Confirmation "DELETE <short-id>"
~~~

结果状态：

- RECYCLED：原路径已不存在，回收站操作完成。
- BLOCKED：安全校验拒绝执行。
- FAILED：Windows 回收站调用失败。
- UNKNOWN：无法可靠确认结果，不应当作成功处理。

完成后销毁任务数据：

~~~powershell
.\scripts\invoke-once.ps1 -Mode finalize -RunId "<run_id>"
~~~

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
python -m disk_cleanup validate
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
