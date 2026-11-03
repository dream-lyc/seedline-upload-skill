# 配置与运行

仅供 Agent 内部执行；对教师的回复遵循 [教师用语](communication.md)，不复制命令、字段或原始报错。

首次使用、迁移工作机或工具版本不兼容时读取。面向用户解释动作即可，签名、JSON 转换和命令编排由 Agent 负责。

## 每个会话先检查连接

先读 [会话开始与授权引导](onboarding.md)，每个新会话先只读查看连接状态；有正式配置才执行 doctor，没有配置直接发起网站连接。成功才进入考试选择，之后再邀请资料；不能因文件存在或上个会话曾成功而跳过验证。

默认使用网站一键连接：`connect start --open` 给出本次专用网页及核对码，管理员在网站同意后 `connect continue` 自动保存确认及核对身份。`connect status` 只返回本机公开进度；`connect cancel` 取消尚未完成的连接。原连接恢复优先，不重新创建凭据。

所有带 `--profile` 的调用都使用同一配置。恢复旧批次须传原 `--batch` 供身份核对；连接状态和候选保存在工具管理的私密目录，不能复制到会话产物、预览或题目账本。网站确认后仍需实际核对完整权限；身份检查故障保留已确认阶段及旧配置。

只有旧部署或用户明确选择时使用手动配置：给同站点登录和 API 接入链接，在真实交互终端以 `configure` 隐藏输入并以 0600 保存。不要把命令当作教师待办，不在聊天索取 SK，不虚构安全窗口。需要六项权限 `contracts:read`、`content:read`、`content:publish`、`media:read`、`media:write`、`submissions:read`，网站连接固定使用该预设。

手动创建响应丢失无法找回原 SK；用户可在页面刷新列表、吊销后重建。网站连接的短窗口同份交付恢复只由工具处理，不适用于历史凭据找回。

## 安装与版本

Skill 1.5.0 提供 Windows x64、Intel Mac 和 Apple 芯片 Mac 独立工具，首次运行自动下载对应版本并核对摘要，不需要安装 Python、Node.js、pip、虚拟环境或 SDK。在 Skill 目录执行：

```sh
./scripts/run.sh --help
./scripts/run.sh connect status
./scripts/run.sh connect start --open
./scripts/run.sh connect continue
./scripts/run.sh doctor
```

已有 `~/.acebro-import/profile.json` 且站点为生产域名时先实际验证，通过后复用配置、跳过新连接；其他站点按 onboarding 指引另行连接，不改旧凭据的地址；默认生产题库连接 `https://edu.learningiland.com`。`run.sh` 检查平台和文件摘要后启动工具。以下 `acebro-import` 均指 `/实际Skill目录/scripts/run.sh`，无需改全局 PATH。Mac 请求使用 curl、OpenSSL、jq 和 Bash；Windows 使用系统 PowerShell/.NET 和 curl.exe，凭据以 Windows 当前用户独占 ACL 保护。音视频探测需要 ffprobe。缺少这些基础工具时说明具体缺项；不要临时安装 SDK 或绕过校验。

支持 Windows x64、macOS x64 和 macOS arm64；按主 Skill 的平台入口运行。其他平台停止并说明不支持。分发不含凭据；工具版本 1.5.0 携带契约校验器和静态预览 1.0.0。契约历史字段 `minimum_sdk_version` 表示兼容能力基线，不表示需要安装 SDK。未知规则、版本不兼容或摘要失败时保留批次并停止。

## Agent 使用的命令

`--profile`、`--cache` 是全局选项，放在子命令前。以下路径是示例，执行时使用本次工作目录的实际路径。

```text
acebro-import --profile /安全目录/profile.json doctor
acebro-import contract ielts
acebro-import contract toefl --version 1.0.0
acebro-import ids --count 3
acebro-import prepare /工作目录/candidate.json --batch /工作目录/batch-001
acebro-import validate /工作目录/batch-001
acebro-import preview /工作目录/batch-001 --output /当前会话目录/试题预览.html
acebro-import status /工作目录/batch-001
acebro-import query /工作目录/batch-001
```

发布前必须先提供校验结果和本轮最新预览，等待教师明确确认。在用户答复前不要调用 `confirm`，也不要把以下两步和整理命令放在同一次自动执行中。确认仅针对本轮展示的内容：

```text
acebro-import confirm /工作目录/batch-001 --preview /本轮实际preview_open_file.html
acebro-import run /工作目录/batch-001
```

工具核对预览和当前批次并保存本机确认；普通run在缺确认或版本已变时返回awaiting_confirmation，媒体和内容均不提交。校验成功不是确认。修改内容后重新校验、导出并请用户确认；旧快照、另一批次或损坏快照不能确认当前内容。确认后同一版本恢复保留记录，实际媒体回填不要求重复确认。

已持有同一版本发布确认，且用户已选择重试时才使用：

```text
acebro-import run /工作目录/batch-001 --retry-operation content
acebro-import run /工作目录/batch-001 --retry-operation media:原媒体占位UUID
```

`--retry-operation` 可重复，值来自原账本，不重新生成。`query` 只读，不启动恢复执行。

候选修改通过 `revise 原批次目录 修正候选.json`，仅允许内容尚未冻结的批次，并保留原身份。内容已冻结时遵循恢复说明，不直接编辑账本绕过不可变事实。尚未发送的同一媒体可在 `revise` 时修正源文件；已有操作键的媒体事实不能原地改写。

用户提供修正后的失败媒体时，使用：

```text
acebro-import replace-media /工作目录/batch-001 原媒体占位UUID /工作目录/新媒体.json
```

新媒体文件为单个媒体项，字段与候选 `media` 项一致。此命令建立新的媒体身份和操作，保留旧操作历史；内容已冻结后只允许替换后置视频。结果未知的上传先查询，已成功媒体不能静默替换。替换材料后重新校验、导出最新预览并取得对新版本的明确确认，再运行原批次；只执行尚未成功的所需步骤，不重新发布已有题目。

读取既有内容使用 `content KIND --id UUID`；列表可用 `--q`、`--status` 和 `--page`。四个 KIND 为 `ielts_question`、`ielts_paper`、`toefl_item`、`toefl_form`。

## 候选文件与媒体

候选文件由 Agent 整理，普通用户无需手写。外层结构如下，完整 `data` 从固定契约的 schema 与样例生成，不能照下面的空对象直接提交：

```json
{
  "exam": "ielts",
  "objects": [
    {"ref": "passage-one", "kind": "ielts_question", "data": {}}
  ],
  "source_manifest": [
    {"object_id": "@object:passage-one", "namespace": "用户资料", "source_id": "第1篇", "locator": "第2至4页"}
  ],
  "media": []
}
```

`prepare` 可为省略 `id` 的对象分配 UUIDv7，并将 `@object:标签` 替换为真实 ID。恢复后不要从原始候选文件重新 `prepare` 覆盖原批次。

媒体项包含 `ref`（预分配的规范 UUID）、`path`、`purpose`、`content_type` 和适用 `context`。题目媒体 `file` 先引用该 `ref`；它只是本地占位，本地工具 上传后会回填系统返回的真实媒体 ID。`path` 可相对候选文件所在目录；本地工具 保存源文件快照和摘要。后置解析视频标记 `after_content: true`，目标指向本批真实对象及适用小题。

预览使用 `preview 批次 --output 当前会话目录/试题预览.html` 导出保存文件；JS/CSS/字体内嵌，材料在同目录 preview-media。命令结束即退出，关闭或重启 Agent 后仍可直接打开，无需临时端口。返回的 `preview_file` 是持续更新的固定文件，`preview_open_file` 是本轮可直接打开的完整快照；内容变化时后者路径随之变化，内容不变则复用。面向教师的链接使用本轮 `preview_open_file`，避免桌面应用复用仍显示旧内容的标签。快照仅反映导出时内容，修改题目、答案、解析、译文或媒体后必须先保存当前批次，再用同一个 `--output` 路径重新导出，核对本次改动。已打开的固定页面可由Agent刷新，不能把文件覆盖等同于窗口已刷新。旧快照保留用于核对，不删掉其材料。保留 HTML 和材料文件夹，不复制凭据、账本或短效地址。提供本轮预览后等待用户明确确认发布；页面本身保持只读，不加入发布按钮。

音频预览只引用 `preview-media` 中的完整静态文件，使用普通 audio controls。预览不提供句段播放或切片；查看解析及原文依据不改变音频播放位置。该简化不改变题目 JSON 或正式录入的时间轴校验。
