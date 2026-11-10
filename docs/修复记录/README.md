# 修复记录

本目录由 git `post-commit` 钩子自动维护：**每提交一次 commit，就在此生成一份独立的修复记录，并自动追加一笔 `docs: 修复记录 <sha>` 提交跟随其后。**

## 命名
`YYYYMMDD-HHMMSS-<短提交号>.md`，一笔业务提交对应一份，互不覆盖。

## 每份记录包含
- 提交号 / 提交时间 / 作者 / 标题
- 说明（取自 commit message 正文）
- 变更文件（name-status）
- 变更统计（diffstat）

## 机制来源与开关
- 钩子脚本（纳入版本控制）：`scripts/git-hooks/post-commit`
- 新克隆 / 换机器后启用：
  ```
  powershell -ExecutionPolicy Bypass -File scripts/install-hooks.ps1
  ```
- 临时关闭：删除 `.git/hooks/post-commit`（不影响正常提交）。
- 递归保护：自动生成的记录提交标题以 `docs: 修复记录 ` 开头，钩子识别后跳过，不会无限生成。

> 注意：`.git/hooks/` 不被 git 跟踪，所以钩子本体放在 `scripts/git-hooks/` 下随仓库分发，靠安装脚本落到 `.git/hooks/`。
