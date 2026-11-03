Windows 内部说明：请求由 `scripts/request.ps1` 和 `bootstrap.ps1` 执行，用 PowerShell/.NET 按相同协议签名并调用 curl.exe；私密文件使用当前用户独占 ACL。下文 Bash/OpenSSL/0600 的命令说明针对 Mac，Windows 不执行这些 Shell 命令，统一通过 run.ps1 的对应子命令操作。

# curl 接口请求

仅供 Agent 内部执行；对教师的回复遵循 [教师用语](communication.md)，不复制命令、字段或原始报错。

所有平台请求经 curl。已有签名接口由随附 `scripts/request.sh` 发送，使用 Bash、OpenSSL、jq；没有 Python/Node.js SDK 安装步骤。该脚本固定从权限 0600 的当前用户配置读取 origin/AKSK，签名基于实际路径和正文，不能直接用裸 curl 省略鉴权。

```sh
./scripts/request.sh GET '/api/integration/v1/me' --profile "$HOME/.acebro-import/profile.json"
./scripts/request.sh GET '/api/integration/v1/contracts/ielts/latest' --profile "$HOME/.acebro-import/profile.json"
./scripts/request.sh GET '/api/integration/v1/content/ielts_question?page=1&page_size=20' --profile "$HOME/.acebro-import/profile.json"
```

脚本 stdout 保留 API 原始 JSON，`--status-file /工作目录/status.txt` 可额外保存 HTTP 状态。2xx 返回 0；HTTP 失败返回 22；网络错误保留 curl 非零退出码，000 表示未取得 HTTP 响应。不要打印包含短期访问 URL 的敏感响应。

校验通过、展示本轮预览并取得用户明确发布确认后，先执行 `scripts/run.sh confirm 批次目录 --preview 本轮preview_open_file`，成功后由 `scripts/run.sh run 批次目录` 编排写接口：工具在首次发送前写入幂等键／稳定 ID，冻结媒体正文并回填真实媒体 ID，最终校验后冻结内容 JSON，再调用同一 curl 请求脚本。不得跳过用户确认或这些步骤直接拼接上传。未确认时run不发送写请求，不得改用底层request.sh绕过。

底层接口支持：

```text
request.sh POST /api/integration/v1/submissions --profile 安全配置 --body 已冻结JSON --content-type application/json --idempotency-key 原操作键
request.sh POST /api/integration/v1/media --profile 安全配置 --body 已冻结multipart --content-type 'multipart/form-data; boundary=实际边界' --idempotency-key 原媒体操作键
```

媒体不能改为 curl `-F`，否则重新生成 boundary 会导致已签名的正文摘要不一致。请求脚本使用 `--upload-file 文件` 配合固定 HTTP 方法流式发送冻结字节，避免将大媒体全量读入内存。不要在命令参数、环境变量、日志或临时文件中传入 SK；不要启用 shell 跟踪、curl verbose、自动重试、重定向、代理或旧 Cookie。失败后保留原批次，按恢复说明先查询未知结果，再由用户选择重试。

## 网站连接专用传输

网站连接由 `connect` 命令内部调用 `scripts/bootstrap.sh`，只允许固定 origin 的初始化、领取、确认和取消四种操作；两个管理员操作由网站 Session 完成。它不使用或传递浏览器 Cookie，不给旧 request.sh 增加关闭签名的参数。

不要手工调用 bootstrap.sh 并打印领取响应：其中可能含新凭据。工具自行生成和保存证明，经私密正文传递，只向 Agent 返回安全的链接、核对码和状态。授权等待是有限时的专用轮询；curl 本身没有自动重试，题目及媒体失败仍遵守用户选择重试。
