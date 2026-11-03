# Seedline 1.5.1

统一使用 Seedline 品牌：技能标识为 `seedline-import-questions`，独立工具为 `seedline-local`，三平台发布包及安装说明同步更名。

- Codex 从 `skills/seedline-import-questions` 安装。
- WorkBuddy 下载 `seedline-import-questions-skill.zip`，通过“添加技能 → 上传技能”导入。
- 新授权保存在 Seedline 配置目录；旧版已保存授权和未完成连接自动识别，不复制或删除凭据。
- 题目结构、答案及媒体校验通过，展示最新预览并得到用户明确确认后，才上传发布。

支持 Windows x64、Intel Mac 和 Apple 芯片 Mac，默认连接 https://edu.learningiland.com，无需安装 Python/Node SDK。
