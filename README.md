# Seedline 雅思／托福试题录入助手

把试题资料交给 Codex 或 WorkBuddy，按引导连接题库、整理内容、查看预览，确认后发布。

服务网站：[edu.learningiland.com](https://edu.learningiland.com)。任何人都可以下载安装；录入发布需要网站的**平台管理员授权**。安装技能本身不会获得管理员权限，也不会自动发布试题。

## 安装

### Codex

在 Codex 对话框发送：

```text
$skill-installer 请安装 https://github.com/dream-lyc/seedline-upload-skill/tree/main/skills/seedline-import-questions
```

安装后在新对话中说：**“使用 seedline-import-questions，帮我录入试题。”**

### WorkBuddy

1. 在 [最新版本](https://github.com/dream-lyc/seedline-upload-skill/releases/latest) 下载 `seedline-import-questions-skill.zip`。
2. 打开“技能 → 添加技能 → 上传技能”，选择这个 ZIP 文件。
3. 启用后新建对话，说：**“使用试题录入助手，帮我录入试题。”**

这是 GitHub 公开分发版本，尚未上架 WorkBuddy 技能市场。WorkBuddy 官方说明：自行上传的技能包应通过“上传技能”导入，不能把 GitHub 发布说成市场上架。

## 支持的电脑

| 电脑 | 版本 | 启动方式（由 Agent 执行） |
| --- | --- | --- |
| Apple 芯片 Mac | macOS 14 或更新版本 | `scripts/run.sh` |
| Intel Mac | macOS 15 或更新版本 | `scripts/run.sh` |
| Windows x64 | Windows 10 / 11，PowerShell 5.1 与 curl | `scripts/run.ps1` |

首次使用会从本仓库对应版本下载匹配的独立工具，并核对 SHA-256；以后使用本机缓存。也可从 Releases 下载完整的平台包，在无法连接 GitHub 的电脑上使用。**无需安装 Python、Node.js 或 SDK；不会向 Agent 客户端运行环境安装依赖。**

Mac 需要系统 Bash/curl/OpenSSL/shasum 和 jq；处理音视频需要 ffprobe（FFmpeg 提供）。缺少工具时会说明缺项，不会绕过媒体或内容校验。Windows 不需要 Git Bash。此版本未提供 Windows ARM 或 Linux 工具。

## 使用过程

1. 先检查连接；尚未授权时提供网站链接，引导登录并确认连接，无需在聊天里粘贴密钥。
2. 选择雅思或托福，提供文本、文档、图片、网页、音频、视频或导出资料。
3. 按系统题目契约校验结构、必填内容、答案、引用及媒体；发现缺漏时引导补充。
4. 生成可保存、可再次打开的试题与答案预览。修改内容后更新预览。
5. **校验通过并且你明确确认之后，才上传媒体和发布试题。** 内容改变须重新校验、预览和确认。

若连接时提示“机器接入服务尚未启用”，请联系题库管理员开启接入服务；重新安装 Skill 或反复填写凭据不能解决站点未启用的问题。

资料不必一次备齐。支持的题型以题库契约为准；模糊、缺失或不支持的内容不会自动编造成题目。

## 本地数据与访问范围

凭据仅保存在本机私密配置中，不随 Skill 分发、不写入试题预览。题目资料、预览及未完成任务保存在当前工作目录。生产请求使用 HTTPS；发布到哪个账号，以网站登录授权结果为准。需要撤销接入时可在网站的管理员 API 接入页面吊销凭据。

公开仓库只包含本地客户端、校验规则、预览资源和打包文件，不包含业务服务器源码、数据库连接、部署密钥或真实用户题库数据。对话中提供的资料由你使用的 Agent 客户端按其数据政策处理；确认发布的内容发送至题库服务。

## 构建与版本

维护者通过 GitHub Actions 的 **Build and publish** 工作流分别构建三个平台。依赖只安装在每个构建任务自己的虚拟环境；构建脚本会拒绝共享 Python。发布包含文件校验清单和第三方许可说明。

需要反馈问题时，请提供客户端、系统版本和问题描述；不要提交 AKSK、登录 Cookie 或个人试题资料。

安装依据：[Codex 官方说明](https://learn.chatgpt.com/docs/build-skills)、[WorkBuddy 官方说明](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)。

## 从旧版升级

技能现在统一使用 `seedline-import-questions`。安装新版后停用旧名称的技能，避免重复调用；不要删除授权配置或未完成的试题资料。新版会识别原有授权，仍需核对站点和账号。已保存的预览及未完成录入可继续使用。
