# 托福转换

仅供 Agent 内部执行；对教师的回复遵循 [教师用语](communication.md)，不复制命令、字段或原始报错。

在本批固定 TOEFL 契约加载成功后读取。题型仅限契约提供的当前十二种任务，不把旧版或不支持的任务强行映射成近似题型。

## 题组和完整卷

`toefl_item` 保存共享材料及全部小题；保留稳定小题 key、顺序、答案和解析映射，不能只抽一个样题覆盖原题组。当前任务包括：

- 阅读：`complete_words`、`read_daily_life`、`read_academic_passage`。
- 听力：`listen_choose_response`、`listen_conversation`、`listen_announcement`、`listen_academic_talk`。
- 写作：`build_sentence`、`write_email`、`academic_discussion`。
- 口语：`listen_repeat`、`take_interview`。

`toefl_form.modules` 使用八个含点号的平面 key：`reading.router`、`reading.lower`、`reading.upper`、`listening.router`、`listening.lower`、`listening.upper`、`writing.linear`、`speaking.linear`。不要转换成嵌套对象，也不要只保留某条实际分支。

## 不能在转录时丢失的语义

- 生活阅读六种材料布局、结构化邮件和讨论题面、图片材料及逐小题图片的“未提供”和“显式空值”区别。
- 稳定材料／段落／句子 ID、材料引用、逐句译文、适用 speaker、真实音频时间轴和 UTF-16 高亮偏移。
- `answersByKey` 与 `explanationsByKey` 对齐全部小题。每题解析保留 `analysis/evidence/distractors/paraphrases`；证据只能引用本题关联材料的真实句子。
- 人工难度、来源、自定义时限、合法的零分流阈值、计分配置和任务顺序。禁止用默认值覆盖源中的合法显式配置。

使用 本地工具 按固定契约在本地展开完整 `rules/scoring`，并校验八模块、候选分支、配额、顺序和实际路径内不重复。服务端新提交接口不会代为展开或补全。

源资料只有单科或独立题组时，就报告该范围；完整卷缺任何必需候选模块时需补齐后再提交整卷。不要重复题组或制造题目来满足配额。

素材图片／音频使用对应 TOEFL purpose 和 confirmed 媒体 ID。新协议中的后置解析视频只按本批契约明确支持的用途处理；没有用途支持时保留为未接入资料，不借用学生录音或其他考试用途。

成功后题组为 `available`，整卷为 `published`。提交内容不包含草稿或状态开关。
