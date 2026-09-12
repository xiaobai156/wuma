# 杀五码_解耦版项目规则

继承上级 `..\AGENTS.md`；本文件只记录本项目独有约束。

## 项目文件

- 单期入口：`crawler.py`
- 多期入口：`multi_issue_crawler.py`
- 新增站点隔离入口：`onboarding_crawler.py --candidate-json`
- 正式配置：`targets.json`
- 正式缓存：`recent_10_cache.json`
- 调试页：`debug_pages\`
- 正式输出目录：`C:\Users\Administrator\Desktop\每天工具\爬虫合集\大围杀号生肖数据统一归纳`
- 单期输出：`{期数}期-杀五码-成功.txt`、`{期数}期-杀五码-失败.txt`
- 普通入口：`爬虫-每天杀五码.bat`

正式输出目录由 `crawler.py` 固定；报告写入项目根目录。配置中的 `disabled` 站点不参与抓取，除非用户明确恢复。

## 数据和解析

- 普通站点每期必须返回唯一一组五个号码；号码为 `01`–`49`，保留原始顺序，不排序、不跨来源拼接。
- 唯一例外“拔树寻根”允许六个号码；其他配置的 `count` 必须为 `5`。
- 成功 TXT 每行格式固定为：`号码,号码,号码,号码,号码 目录名`，不得附加期数、生肖或说明。
- 每个启用站点必须使用真实页面已确认的栏目/正文锚点；不能用号码格式猜栏目，也不能用网站地址代替正文锚点。
- `/article/admin/`、`/article/manager/`、`/article/lottery/` 按 URL 文章 ID 及同 ID 身份校验；`#/users/<id>` 按 URL 用户 ID 绑定记录。
- `list_detail: true` 必须先在列表页按 `list_title_keywords` 锁定详情，再在同一详情文档取数。
- 可用的站点专属边界字段：`anchor`、`stop_anchor`、`first_issue_chain`、`issue_position_window`、`decoded_anchor_only`、`decoded_anchor_chunks`、`decoded_stop_anchor`、`decoded_anchor_to_end`、`rendered_fallback_selectors`、`encoding`、`insecure_tls`、`list_detail`、`list_title_keywords`、`keyword_before_issue`、`keyword_before_issue_window`。只有真实验证需要时才能配置。

## top / bottom

- 默认方向窗口是 `kill5/parser.py` 的 `CANDIDATE_REGION_WINDOW = 3`；配置了 `issue_position_window` 时优先使用配置值。
- 无效号码组、数量错误组和重复号码组不占方向窗口。
- `top` 与 `bottom` 只能在各自锁定的同一栏目/文档区块内判断，不能混用。
- `anchor`、`stop_anchor`、`first_issue_chain` 只能缩小解析范围，不能绕过方向窗口或跨文档取数。
- 自适应结构恢复默认关闭；不得用自适应结果决定期数、方向、栏目或号码。

## 输出、缓存与定向重抓

- 单期只处理指定期数；实时抓取、解析和业务裁决不能读取缓存补数。
- 单期成功目录占启用目录严格超过 85% 时才更新正式缓存；小于或等于 85% 时缓存保持原样。
- 缓存更新使用现有锁、冲突校验和原子替换；失败必须报告“缓存更新未完成”。
- 多期、审计、影子测试和独立验证永远不更新正式缓存或正式输出。
- 新增或修复站点只能运行用户点名的站点；成功追加到成功 TXT，失败只更新该站点失败记录，其他站点保持不变。
- 修复追加入口：`python crawler.py --issues 期数 --repair-names 目录名[,目录名]`。同名不同号码按冲突停止，重复执行保持幂等。
- 失败重抓入口：`python crawler.py --retry-failed [--issues 期数]`。它只读取对应失败 TXT 中的站点：成功追加结果并清除对应失败行，仍失败则保留失败行；其他站点、缓存和 TXT 不受影响。
- 失败站点正式修复成功后，按上述追加流程同步该站点当期缓存，无需再次单独下达更新缓存指令。

## 多期入口

- 每期独立生成结果；多期模式永远不更新 `recent_10_cache.json`。
- 多期全部失败报告写入正式输出目录，命名为 `{首期}-{末期}期-杀五码-多期全部失败报告.txt`；单期使用 `{期数}期-杀五码-多期全部失败报告.txt`。

## GitHub 同步

- `origin`：`https://github.com/xiaobai156/wuma.git`
- 本项目修改通过必要验证后，自动提交并推送到 `origin/main`。
- 提交前只包含本次明确修改；必须排除凭据、密钥、令牌、缓存、锁文件、调试页、报告、测试产物、`.pyc`、临时文件和无关项目。
- 已有用户未提交改动必须保留；无法安全隔离时停止推送并报告。
- 推送失败时保留已验证的本地提交，不重复制造提交，也不修改用户文件。
