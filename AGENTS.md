# 杀五码项目专属规则

本项目继承上级 `..\AGENTS.md`。本文件只补充“杀五码”项目独有的数据契约、路径、入口、站点解析和已确认特例，不重复上级统一规则。

## 项目根目录与正式文件

源项目生产根目录：

`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版`

正式文件：

- 配置：`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\targets.json`
- 核心入口：`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\crawler.py`
- 普通交互入口：`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\run_crawler_prompt.py`
- 多期入口：`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\run_multi_issue_prompt.py`
- 正式缓存：`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\recent_10_cache.json`
- 缓存锁：`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\.recent_10_cache.json.lock`
- 普通 BAT：`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\爬虫-每天杀五码.bat`
- 多期 BAT：`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\爬虫-多期验证杀五码.bat`
- 失败调试页：`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\debug_pages\`
- 现有安全回归测试：`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\test_safety_regressions.py`

正式成功、失败输出目录由 `crawler.py` 固定为：

`C:\Users\Administrator\Desktop\每天工具\数据系列\大围杀号生肖数据统一归纳\`

单期输出名称：

- `{期数}期-杀五码-成功.txt`
- `{期数}期-杀五码-失败.txt`

报告按当前实现写入运行工作目录；正式 BAT 会先切换到项目根目录，因此通常为：

`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\{期数}期报告.txt`

不得虚构独立正式判重 BAT。当前目录不存在独立判重入口或判重 BAT。

入口参数契约：

- `crawler.py` 使用 `--issues`、`--workers`、`--retry-passes`；默认期数为 `128`，默认线程数为 8，默认网络补抓 2 轮。
- `run_crawler_prompt.py` 会强制传入 `--workers 16`，覆盖 `crawler.py` 的默认 8 线程；未输入期数时沿用核心默认期数 `128`。
- `run_multi_issue_prompt.py` 使用 `--issues` 和 `--workers`；多期默认线程数为 16。
- `staging_crawler.py` 必须提供 `--issues`，并在 `--names` 与 `--candidate-json` 中二选一；`--workers` 默认 8，`--retry-passes` 默认 2。
- `--candidate-json` 只能加载尚未写入正式配置的隔离候选，不能借此修改正式 `targets.json`、正式缓存或正式输出。

## 五号码数据契约

- 普通站点每期必须返回完整、唯一的一组五个号码。
- `count` 必须为 `5`。
- 号码必须是 `01` 至 `49` 的合法值，保持页面原始顺序，禁止排序。
- 同一组号码不得重复。
- 禁止跨期、跨栏目、跨文档或跨候选组拼接。
- 成功文件每行只能是：

  `号码1,号码2,号码3,号码4,号码5 目录名`

- 成功行不得附加期数、生肖、说明或其他字段。
- 只有唯一例外“拔树寻根”允许六个号码，其他任何 `count` 非 `5` 的配置必须拒绝。

## 正式配置约束

`targets.json` 是站点配置唯一来源。

每条启用配置必须明确：

- `name`
- `url`
- `keywords`
- `count`
- `region`
- 已确认的正文锚点、停止锚点或其他专属边界字段

带有 `disabled` 的站点不参与普通抓取。未经明确授权，不得删除、停用、恢复或改名。

关键词必须来自目标页面真实出现的栏目文字。不得根据号码格式猜测栏目名称。

项目现有专属配置字段包括 `anchor`、`stop_anchor`、`first_issue_chain`、`issue_position_window`、`decoded_anchor_only`、`decoded_anchor_chunks`、`decoded_stop_anchor`、`decoded_anchor_to_end`、`rendered_fallback_selectors`、`encoding`、`insecure_tls`、`list_detail`、`list_title_keywords`、`keyword_before_issue` 和 `keyword_before_issue_window`。新增或修复只能沿用真实页面已经证明需要的字段，不能为凑成功随意开启。

- `/article/admin/`、`/article/manager/`、`/article/lottery/` 必须按 URL 文章记录 ID 走对应专属接口及同 ID 校验。
- `#/users/<id>` 用户页必须按 URL 用户 ID 绑定该用户的论坛记录，不能借用其他作者或帖子。
- `list_detail: true` 必须先按 `list_title_keywords` 在列表页锁定目标详情，再在同一详情文档内解析；列表与详情不能拼接号码。
- `decoded_*` 只允许在已配置的解码正文锚点范围内取数；`rendered_fallback_selectors` 只允许在配置明确授权且静态来源符合现有兜底条件时使用。

## top/bottom 方向

项目当前方向窗口默认使用 `crawler.py` 中的 `CANDIDATE_REGION_WINDOW = 5`：

- `top`、`upper`、`head`、`first`、`上`、`顶部` 统一归一化为 `top`：只允许目标权威文档、目标栏目和目标区块内靠前五个高可信有效候选。
- `bottom`、`lower`、`tail`、`last`、`下`、`尾部`、`底部` 统一归一化为 `bottom`：只允许同一范围内靠后五个高可信有效候选。
- 无效号码组、数量错误组、重复号码组不占用方向窗口。
- `issue_position_window` 配置存在时，优先使用该站点明确配置的窗口。
- `first_issue_chain`、`anchor`、`stop_anchor` 只能缩小解析范围，不能绕过方向窗口。
- 同一站点的 top 与 bottom 候选不得混合判断最新期。
- 自适应结构恢复默认关闭；不得由自适应结果决定期数、方向、栏目或号码。

## 已确认的项目特例

### 拔树寻根

仅以下配置允许六号码：

- `name`：拔树寻根
- `url`：`https://zcphjs.ce83x-ms2rz-orwude.work:12277/#/users/116164`
- 已确认合法 `keywords`：`期杀6码`
- `count`：`6`
- `region`：`top`

当前 `targets.json` 还保留历史别名 `期杀六码`；它只能作为待核实的现状记录，不能复制到新增配置或据此扩大六号码例外。其余站点一律五号码。

### 黄金宝坛

必须使用正式配置中的专属范围：

- `name`：黄金宝坛
- `url`：`https://kk.676626a.com:1888/`
- `keywords`：`杀五码`、`杀5码`
- `count`：`5`
- `region`：`top`
- `anchor`：`今日头版资料杀五码`
- `stop_anchor`：`高手专研定制心水五不中`
- `first_issue_chain`：`true`

项目根目录中的黄金宝坛截图只能作为定位证据，不能作为号码来源、缓存来源或判重依据，不能覆盖 `targets.json` 的正式边界。

### 六合皇

- 固定栏目锚点：`『绝杀系列』`。
- 固定停止锚点：`必杀三尾`。
- `first_issue_chain: true`，只能在该专属链内按 `top` 方向解析 `绝杀五码`/`绝杀5码`。

### 倦鸟归林

- URL 固定为 `https://nlafoq9v.dh5565656.xyz/bbs/topic.php?id=918`。
- 强制 `encoding: gb18030`，栏目锚点为 `倦鸟归林`，方向为 `bottom`，并使用 `first_issue_chain: true`。
- 它是当前唯一允许 `insecure_tls: true` 的“名称 + URL”绑定例外；不得把关闭 TLS 校验扩展到任何其他站点或第三方脚本。

### 随风流浪

- URL 固定为 `https://kqfmnjht.ip6et-0zvu7-gpqkrf.work:16677/topic/175682.html`，栏目锚点为 `作者:随风流浪`，方向为 `bottom`。
- 解码正文必须使用 `decoded_anchor_to_end: true`，以覆盖同一作者正文中的最新历史行；仍严格按 bottom 最近 3 组校验，禁止使用窗口外的同期候选。

## 单期抓取

普通日常抓取必须使用：

`爬虫-每天杀五码.bat`

该入口最终调用：

`run_crawler_prompt.py -> crawler.py`

日常单期流程必须：

1. 只解析用户指定期数。
2. 对每个站点执行其配置的关键词、数量、锚点、停止锚点和方向校验。
3. 任一站点失败都写入分类、站点、网址和具体原因。
4. 单期实时结果定稿后，成功目录占本次启用目录严格超过 85% 时，允许同步正式 `recent_10_cache.json`；失败目录写入缓存顶层 `failures` 数组并标记 `status: failed`，不得写入伪号码。
5. 单期成功目录占比小于或等于 85% 时，正式缓存保持原样，不创建、不覆盖、不滚动；多期模式仍永远不更新正式缓存。

缓存职责补充：单期实时抓取、解析和统一校验不读取或使用
`recent_10_cache.json` 补数、判期、判方向、解冲突或决定成功/失败；缓存的业务读取只允许由
`kill5/onboarding.py` 用于新增站点正式重复检测。单期成功/失败 TXT 和实时结果先定稿，之后才滚动写入近10期缓存。
缓存写入失败必须报告“缓存更新未完成”，但不得撤销或改写本轮已定稿的 TXT；该失败只影响缓存更新和后续新增站点判重。

网络临时失败可按现有程序的重试次数补抓，但补抓必须使用完全相同的解析规则。

## 多期、历史回抓和独立验证

多期抓取必须使用：

`爬虫-多期验证杀五码.bat`

该入口调用：

`run_multi_issue_prompt.py`

多期模式：

- 每个期数独立抓取。
- 每个期数独立生成成功、失败和报告文件。
- 任意指定期成功即可在多期汇总中通过。
- 只有全部指定期数失败的目录进入多期汇总失败报告。
- 多期汇总写入正式成功/失败输出目录 `大围杀号生肖数据统一归纳`，单期命名为 `{期数}期-杀五码-多期全部失败报告.txt`，多期命名为 `{首期}-{末期}期-杀五码-多期全部失败报告.txt`。
- 永远不得更新正式 `recent_10_cache.json`。
- 历史回抓、审计、影子测试和独立验证只能使用 `staging\` 或其他隔离目录，不得修改正式配置、正式缓存或正式输出。

隔离验证入口：

`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\staging_crawler.py`

隔离目录：

- 隔离缓存：`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\staging\recent_10_cache.json`
- 隔离输出：`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\staging\results\`
- 隔离调试页：`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\staging\debug_pages\`

`staging_crawler.py` 不得被视为正式生产入口，也不得将隔离缓存直接当作正式缓存。

## 正式缓存

正式缓存固定为：

`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\recent_10_cache.json`

缓存结构保持：

- `version`
- `generated_at`
- `recent_count`
- `records`
- `failures`（达到单期缓存更新阈值且存在失败目录时写入；有效号码判重只读取 `records`）

每条记录至少包含：

- `name`
- `url`
- `issue`
- `numbers`

每个站点独立保留最近十期；缓存中的号码保持抓取时的原始顺序。正式缓存写入必须经过锁、格式校验、冲突校验和原子替换。

新增站点重复检测中的实时候选与有效缓存同期期结果不同，必须报告缓存冲突，禁止自动覆盖；单期实时抓取不使用缓存裁决结果。

缓存身份必须绑定目录名、规范化 URL/topic/文章 ID、栏目、字段、方向和配置指纹；任一身份不一致时正式判重未完成。当前源实现只用 URL 作为站点键，尚未完成这项身份绑定。

## 新增站点

新增站点必须先经过上级规则规定的同名、URL/topic、字段、真实页面、近十期有效数据、方向对齐、专属解析和正式判重流程。

本项目正式配置为：

`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\targets.json`

当前实现只提供：

- `targets.json` 同名和重复 URL 校验；
- `staging_crawler.py` 的候选配置隔离加载；
- 正式缓存读写和结果安全校验。

当前实现没有独立的正式新增站点判重器，也没有实现“连续 1–2 期、3–5 期、6 期以上”的跨站历史判重流程。因此：

- 不能把 `dedupe_results()` 当作正式判重器；它只去除完全相同的单次结果元组。
- 不能把缓存更新函数当作重复检测器。
- 不能仅凭 `targets.json` 不同名或不同 URL 宣称“不重复”。
- 在正式判重器补齐前，新增站点判重状态只能报告为“判重未完成”。

判重阈值必须遵守：

- 连续 1–2 期相同：不重复。
- 连续 3–5 期相同：疑似重复，暂停并人工审核。
- 连续 6 期及以上相同：拒收。
- 缺期、空数据、无共同期号或有效数据不足：判重未完成，不得按“不重复”通过。

## 失败分类

失败报告至少保留：

- 项目、站点和 URL
- 指定期数
- 配置方向
- 失败阶段
- 具体失败原因

当前实现支持的主要分类：

- `网络临时失败`
- `本地网络权限`
- `页面无当期`
- `位置不匹配`
- `解析歧义`
- `锚点失效`
- `其他失败`

失败时应尽量将原始调试内容保存到：

`C:\Users\Administrator\Desktop\每天工具\爬虫合集\杀五码_解耦版\debug_pages\`

失败 TXT 每个失败站点固定占一行，格式为：

`失败 {站点名} {URL} 方向: {方向} 期数: {期数} 阶段: {阶段} 原因: {原因}`

失败 TXT 只调整展示格式，不改变抓取判断、失败分类、报告明细或缓存逻辑。

不得使用其他期、其他栏目、其他号码组或旧缓存补成成功。

## 源实现与上级规则的已知冲突

- `crawler.py` 和 `run_crawler_prompt.py` 允许普通入口接收多个期数；当前服务层只对单期执行 85% 阈值缓存策略，多期仍不更新正式缓存。多期必须使用 `run_multi_issue_prompt.py`，直接调用 `crawler.py` 多期参数不得视为合规生产流程。
- `crawler.py` 的成功、失败和报告文件写入不是统一的原子替换流程；正式输出必须按上级原子写入要求处理，不能将现实现状视为满足要求。
- `backup_existing_outputs()` 仅定义于代码中，当前没有实际调用；不得宣称正式运行会自动生成 `.bak` 备份。
- 当前没有正式跨站历史判重器，也没有实现根规则要求的连续重复阈值。新增站点不得宣称已完成正式判重。
- 当前缓存同步只检查旧缓存内部冲突和本次候选内部冲突；当本次结果与旧缓存具有相同 URL、相同期数但号码不同，它会直接用新记录替换旧记录。该行为不满足缓存冲突规则，出现这种情况必须停止正式同步并报告“缓存冲突/判重未完成”。
- 当前缓存只按 URL 隔离，没有绑定目录名、栏目、字段、方向和配置指纹；完整身份未验证时不得完成正式判重。
- `staging_crawler.py` 会写入隔离缓存，但隔离缓存不等于正式缓存；独立验证、审计和历史回抓仍不得触碰正式缓存。
- 源项目中的报告文件路径与成功、失败文件路径不一致；报告依赖 BAT 先切换到项目根目录，不能假设报告一定位于正式输出目录。

## GitHub 同步规则（长期）

- 当前项目绑定的 GitHub `origin`：`https://github.com/xiaobai156/wuma.git`。
- 每次在本项目完成修改，并通过必要的相关验证后，自动提交并推送到该 `origin`。
- 自动提交只允许包含本次明确完成的项目修改；必须保留用户已有未提交改动，不得擅自覆盖、回退、清理、暂存或提交这些改动。
- 推送前必须检查提交清单，禁止推送凭据、密钥、令牌、缓存、调试页、测试产物、临时文件、个人路径文件或无关项目内容。
- 如果工作区已有未提交改动，提交必须与其隔离；无法安全隔离时停止推送并明确报告。
- 推送前必须完成本次变更范围内的必要测试、Python 编译/静态检查和真实指定流程；验证未通过不得提交或推送。
- 网络、认证、远程分支或权限导致推送失败时，保留本地已验证提交并明确报告，不得重复制造提交或改动用户文件。
