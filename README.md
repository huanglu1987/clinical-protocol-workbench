# 中文临床试验方案工作包

`clinical-protocol-workbench` 是在 Codex 中使用的中文临床方案 Skill，支持**方案撰写、独立审核和获准后的 Word 受控修订**，并衔接口服与局部起效外用小分子的皮肤科策略模块。

当前版本：**v0.1.0-preview.1，内部预览版**。它不是独立桌面软件，也不是可自动批准研究设计或保证 CDE 合规的系统。安装成功不等于临床验证、计算机化系统验证或申报放行。

## 1. 可以做什么

| 你的需求 | 入口与输出 |
| --- | --- |
| 只有产品资料，想讨论开发路径 | 选择匹配的专业策略 Skill，形成候选策略/纲要、依据和待确认问题，不直接把建议写成研究事实 |
| 已确认设计，需要完整中文方案 | 撰写模式：项目控制表、来源清单、工作初稿、TBD/冲突清单与限制说明 |
| 已有方案，需要查错与评估 | 审核模式：冻结原文、结构检查、独立审核、回文核实的问题清单 |
| 已批准具体修改，需要留痕 | 新建审阅副本；工具能力允许时生成真实 Word 修订和批注，并分别记录结构、Word 和专业复核状态 |

核心原则：**项目事实来自指定资料，研究设计来自真实确认，外部证据支持或质疑，模型不代替专业人员作出决定。**

## 2. 安装前准备

- 已安装并登录可使用 Skills 的 Codex；沿用现有账户与授权，不需要为本工作包单独提供模型 API Key。
- 私有仓库需要对应 GitHub 访问权限；使用安全登录流程，不在聊天中粘贴令牌。
- 基础工作包以 Markdown 规则为主；依赖核验脚本使用 Python 3 标准库，本次用 Python 3.12 验证。
- 撰写 DOCX 需要当前环境具备文档生成能力。Word 视觉核验另需本机 Microsoft Word，不能用 XML 检查代替。
- 没有必需的后台服务、数据库或守护进程。工作包本身不提供遥测、自动上传或自动发布。将资料交给 Codex 的数据处理仍受所使用服务和组织策略约束，不能据此认定全程离线。

## 3. 安装工作包

### 方法 A：直接让 Codex 安装（推荐）

在 Codex 中发送：

```text
请使用 skill-installer 安装以下固定版本：
仓库：huanglu1987/clinical-protocol-workbench
版本：v0.1.0-preview.1
Skill 路径：skills/clinical-protocol-workbench
只安装该工作包，不覆盖已有同名目录。
```

安装后在下一轮消息中调用 `$clinical-protocol-workbench`。若当前客户端尚未刷新技能列表，重新打开任务后再试。

### 方法 B：使用 Codex 自带安装器（macOS/Linux）

以下命令不覆盖已存在的同名 Skill；路径按实际 Codex 安装位置调整。私有仓库下载失败时，安装器可使用现有 Git 授权回退。

```sh
codex_skills_root="${CODEX_HOME:-$HOME/.codex}/skills"
python3 "$codex_skills_root/.system/skill-installer/scripts/install-skill-from-github.py" \
  --repo huanglu1987/clinical-protocol-workbench \
  --ref v0.1.0-preview.1 \
  --path skills/clinical-protocol-workbench
```

仅复制 `skills/clinical-protocol-workbench` 会安装工作流、模板和依赖核验脚本，**不会安装仓库根目录的实验 worker，也不会自动安装外部策略、clinical-doc-qc 或 Word**。

Windows 用户优先采用方法 A；本版没有 Windows 安装/Word 实机通过声明。也可下载 Release ZIP，将其中的 `skills/clinical-protocol-workbench` 完整放入实际 Codex skills 目录，不要只复制 `SKILL.md`。

## 4. 按用途准备依赖

| 组件 | 用途 | 本版处理 |
| --- | --- | --- |
| [oral-derm-clinical-strategy](https://github.com/huanglu1987/oral-derm-clinical-strategy) | 口服小分子皮肤/毛发等策略 | 按需安装，固定提交并逐文件核验 |
| [topical-clinical-strategy](https://github.com/huanglu1987/Topical-Clinical-Strategy-Skill) | 局部起效外用小分子策略 | 按需安装，固定提交并逐文件核验 |
| [clinical-doc-qc](https://github.com/huanglu1987/clinical-doc-qc) | 审核模式的方法与格式/结构检查 | 审核时需要；未打包，未纳入本版策略哈希锁，安装后另核对所用版本 |
| 文档工具及 Microsoft Word | DOCX 生成、修订及真实渲染 | 环境依赖；缺失时停在可完成的报告/修改计划，不模拟完成 |
| PaperQA2、AutoCorrect | 可选召回/机械排版线索 | 不随本版安装，也不是基础入口的启动条件 |

口服与外用 Skill 应保持独立。两仓库在本次固定版本核查时未发现 LICENSE，本包只分发链接、提交和指纹；使用或再分发上游内容前，应确认自己的授权范围。

让 Codex 安装皮肤科依赖时，可发送：

```text
请使用 skill-installer 按以下版本安装缺失的专业依赖；保留已有目录，
不要静默覆盖或更新。如已有副本，请先用 workbench 的依赖核验脚本检查。
1. huanglu1987/oral-derm-clinical-strategy
   ref: b927d7d98283d489ab2661cd9551d0d4068cda37
   path: .
   安装名称: oral-derm-clinical-strategy
2. huanglu1987/Topical-Clinical-Strategy-Skill
   ref: 7edded14ad8bbb32a17e8ce2590efa735ff73c8c
   path: topical-clinical-strategy
```

审核依赖可单独安装：仓库 `huanglu1987/clinical-doc-qc`，路径 `.`，安装名称 `clinical-doc-qc`。本次查询到的提交为 `90a1cbd52ff1b975f774d3b1280d7b4b71ab322f`；这是定位信息，不是对其全部内容或运行依赖的验收。

核验本机实际路径（下面为默认目录示例）：

```sh
codex_skills_root="${CODEX_HOME:-$HOME/.codex}/skills"
python3 "$codex_skills_root/clinical-protocol-workbench/scripts/verify_strategy_dependencies.py" \
  --module oral-derm-clinical-strategy --root "$codex_skills_root/oral-derm-clinical-strategy"
python3 "$codex_skills_root/clinical-protocol-workbench/scripts/verify_strategy_dependencies.py" \
  --module topical-clinical-strategy --root "$codex_skills_root/topical-clinical-strategy"
```

结果须为 `matched`。退出码 0 表示指纹匹配，1 表示缺失或漂移，2 表示输入/清单异常。不要通过重算锁文件来“修复”版本不匹配；先核对实际变更。专业模块不可用时，材料盘点、TBD 整理和基于已确认设计的通用工作仍可继续，但不能声称完成相应专病审核。

## 5. 第一次使用：如何准备资料

新建一个独立项目目录。下面只是建议结构，不要求改名或移动你的原件：

```text
项目目录/
  输入/          指定的方案、IB、研究报告、模板和设计确认记录
  参考/          法规、指南、全文文献及既往格式样本
  工作记录/      项目控制表、来源哈希、TBD、冲突和变更影响表
  输出/          每轮新版本，绝不覆盖输入
```

至少说明：本轮要策略、撰写还是审核；哪个文件是研究事实源；哪些决定已经确认及确认出处；模板/旧方案允许参考到什么程度；输出位置；是否授权联网检索。实际文件应通过附件或可访问的绝对路径提供。

第一次执行时 Skill 会使用[项目控制表模板](skills/clinical-protocol-workbench/assets/project-control-template.md)建立记录。常用编号：`SRC` 来源、`PROP` 候选建议、`DEC` 已确认设计、`EVD` 证据、`TBD` 未决问题、`HIST` 废弃路径。模板中的示意行必须按真实资料填写，不能当作已经存在的确认。

## 6. 可直接复制的使用示例

以下提示中的路径和文件名是占位，需替换为真实文件。不要把示例本身当作研究设计授权。

### A. 产品资料有限，先做皮肤科开发策略

```text
使用 $clinical-protocol-workbench。
本轮只做候选临床开发策略与 Synopsis，不起草完整方案。
产品：〔填写给药途径、分子类型、起效部位、剂型〕；适应症：〔填写〕；
开发地区：〔填写〕。资料见附件。
请先选择并核验匹配的口服或外用策略模块，再完整阅读本轮必要参考。
候选剂量、样本量、终点和阈值只进入 PROP/TBD，不作为已确认研究事实。
列出依据、适用条件和需专业人员确认的问题。无法核实的证据明确标注。
```

注射生物制剂、经皮系统递送贴剂等不强行套用这两个策略模块；应明确专业覆盖缺口。

### B. 已有获确认 Synopsis，直接起草完整方案

```text
使用 $clinical-protocol-workbench，进入撰写模式。
按附件《已确认Synopsis》及其确认记录起草中文 CDE 方案内部工作初稿。
《研究者手册》用于研究依据和已知风险；《公司模板》仅提供章节与样式；
《既往方案》只供语言、结构和排版参考，禁止迁移其他项目参数。
已有明确确认不重复请求批准。缺失的操作细节保留带编号 TBD，不自行补值。
先建立项目控制表和章节依赖关系，再起草、组装、交叉核对。
交付 DOCX 工作初稿、来源清单、控制表、TBD/冲突清单及实际验证限制。
所有输出新建到指定目录，不修改原件。本轮不把起草自检称为独立审核。
```

本版已有合成文本边界和局部起草测试，但**完整新方案 DOCX 的端到端撰写试点尚未完成**。首次真实项目应按章节复核，不把“已生成文件”等同于科学与执行质量验收。

### C. 资料不足，先做占位工作稿

```text
使用 $clinical-protocol-workbench，进入撰写模式。
当前资料不足，先盘点来源、形成目录和带 TBD 的占位工作稿。
只采用附件中已确认的事实与设计。未知剂量、频次、访视、终点、阈值和统计参数
不得从模板或外部先例填入。先指出当前最影响继续起草的一个待确认问题。
```

### D. 只审核，不修改方案

```text
使用 $clinical-protocol-workbench，进入审核模式。
唯一目标为附件《待审方案》；其他文件仅作证据，原件全程只读。
先冻结版本与哈希，按 clinical-doc-qc 完成实际可运行的确定性检查。
可使用两个隔离的新执行上下文独立完成全量审核，完成前不得互看结论。
无法建立独立上下文时明确标记单通道，不宣称双通道完成。
逐条回到原文核实，报告位置、摘录、证据或算式、严重度、分歧及处置建议。
本轮只交付审核报告和修改计划，不写入 Word 修订，不改变任何云端资料。
```

### E. 对已批准条目生成原生修订和批注

```text
使用 $clinical-protocol-workbench，按我已批准的《修改计划〔版本/哈希〕》处理。
批准范围仅为：〔列明条目 ID 与“文字替换/仅批注”等动作〕。
仅修改指定方案的新副本，保留原件全部既有修订、批注及关系。
不得接受旧修订、清除批注或改动未获准设计。必须使用真实 w:ins/w:del 和原生批注。
不能可靠保全的条目留在报告，不扩大授权。分别交付 OOXML 结构检查、
Microsoft Word 实际打开/更新域/含标记 PDF 与逐页检查记录、待专业复核事项。
若 Word 能力不可用，交付现有可验证产物与缺口，不声称视觉检查通过。
```

仓库的实验 worker 不是任意修订器，不提供将任意“修改计划 JSON”一键应用到真实方案的生产入口；详见[实验模块说明](docs/DEVELOPMENT.md)。

## 7. 交付时看什么

不要只看 DOCX 是否生成。确认交付中有：输入/输出版本与 SHA-256；来源角色；真实设计确认；未关闭项及影响位置；实际执行的检查；未运行部分；专业复核责任。

“结构检查通过”“Word 对象枚举成功”“逐页视觉检查通过”“专业内容批准”是不同层级。技术结果不替代后面的专业批准。本版[验证状态](docs/VALIDATION.md)分别列出已测与未测范围。

## 8. 升级、回退与卸载

- 升级前保存当前安装目录、版本/tag、项目使用记录和依赖检查结果。停止正在使用旧版的任务后，将旧目录移到 Codex skills 根目录**之外**的备份位置，再按新 tag 安装；安装器默认拒绝覆盖，不要绕过该保护。
- 新版安装后重新核对指纹与项目输入；旧项目不要无记录切换规则。策略依赖必须按新版本清单重新确认，不能自动跟随上游 main。
- 回退：将新目录移到 skills 根目录之外，恢复原备份，并在下一轮确认加载版本。保留两版备份和项目产物，不合并目录。
- 卸载：把**实际安装目录中的 `clinical-protocol-workbench` 文件夹**移到废纸篓或 skills 外的备份目录。不要删除整个 skills 根目录。两个专业策略依赖、项目输入输出、实验源码不会随之自动删除。

## 9. 常见问题

**找不到 Skill？** 核对实际 Codex skills 目录，确认下一层是 `clinical-protocol-workbench/SKILL.md`，不是重复嵌套；下一轮再调用。检查客户端是否刷新。

**依赖 mismatch？** 停用该专业路径，核对提交和文件差异；不要静默更新或改写锁文件。通用工作可以在披露缺口后继续。

**没有 Word 或不能生成真实修订？** 停在报告和获准修改计划；不得用红字、删除线或干净重写稿冒充原生审阅。

**能保证法规最新、方案可递交吗？** 不能。依赖固定指纹证明版本一致，不证明时效、科学正确性或专业批准。每个项目仍需核实适用地区、现行原始来源与执行细节。

**会自动发送资料或更新云端吗？** 本包没有自动上传/发布入口。若任务需要外部检索、云文档写入或发送消息，应分别明确范围和授权；本地包不绕过 Codex/组织的数据策略。

## 10. 仓库结构与发行物

```text
skills/clinical-protocol-workbench/  可安装 Skill、参考规则、控制表、合成示例、依赖锁
worker/                            有限范围的实验 DOCX 模块与单元测试
scripts/                           合成原生审阅样例、结构验证、Word 实验验证入口
docs/                              使用边界、验证状态及开发复现说明
```

[Release 页面](https://github.com/huanglu1987/clinical-protocol-workbench/releases/tag/v0.1.0-preview.1)提供源码 ZIP 和 SHA-256 清单。仓库与发行包不含真实临床方案、IB、真实项目审阅副本、Word 渲染证据或原开发仓库历史。

本版没有新增开源许可证授权；为内部预览分发。第三方组件及再分发边界见[第三方说明](THIRD_PARTY.md)。
