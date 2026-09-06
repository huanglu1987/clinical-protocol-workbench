# 组合安装指南

`v0.1.0-preview.4` 新增随核心 Skill 分发的组合安装脚本。使用 README 的完整安装提示即可授权并执行标准组合安装。Codex 的普通单 Skill 安装不会递归安装其他 Skill，因此只打开核心链接尚不等于整套安装。

`v0.1.0-preview.5` 新增每轮撰写与审核的 [必读规范](REQUIRED_STANDARD.md)。完整原文由用户本地提供，安装报告会显示其可用性和版本校验结果。7 项 Skill 安装成功不表示该文档已提供或本轮已读完；缺失时需先补齐再起草/审核。

## 两个档位

默认 `standard` 安装以下 7 项，`core` 仅安装第一项：

| Skill | 固定来源 | 用途 |
| --- | --- | --- |
| clinical-protocol-workbench | 本发行版 | 起草/审核流程、模板、项目规则与依赖校验 |
| oral-derm-clinical-strategy | huanglu1987/oral-derm-clinical-strategy，b927d7d | 口服皮肤科策略 |
| topical-clinical-strategy | huanglu1987/Topical-Clinical-Strategy-Skill，7edded1 | 局部外用策略 |
| clinical-doc-qc | huanglu1987/clinical-doc-qc，90a1cbd | 审核与格式结构检查 |
| paper-lookup | K-Dense-AI/claude-scientific-skills，1e5eeff | 文献、PubMed/PMC 检索 |
| database-lookup | 同上，1e5eeff | ClinicalTrials.gov 等数据库检索 |
| citation-management | 同上，1e5eeff | 引文管理 |

完整提交和每个受控文件的 SHA-256 均在 [bundle.lock.json](../skills/clinical-protocol-workbench/assets/bundle.lock.json)。上游独立的 clinicaltrials-database 和 pubmed-database 已分别并入 database-lookup 和 paper-lookup，标准组合采用合并后的入口，不默认恢复旧版。clinical-trial-protocol-skill 仅供借鉴阶段组织，非当前工作流的运行依赖，仍为可选参考。

这些 Skill 按实际问题调用，不是每次起草都同时加载全部内容。第三方正文在安装时从固定上游取得，没有打包进本仓库。

## 直接运行

需要 Python 3.10+、Git 和当前 Codex 自带的 skill-installer。脚本仅使用 Python 标准库；工作台的依赖核验推荐使用已验证的 Python 3.12 环境。默认安装位置遵循 `CODEX_HOME`，否则为用户的 `.codex/skills`。

在 macOS/Linux 中，先按 README 安装核心，再执行：

```sh
codex_skills_root="${CODEX_HOME:-$HOME/.codex}/skills"
python3 "$codex_skills_root/clinical-protocol-workbench/scripts/install_bundle.py"
python3 "$codex_skills_root/clinical-protocol-workbench/scripts/install_bundle.py" --check
```

Windows 可以使用 `py -3`，将参数中的脚本路径换成实际安装路径。尚未完成 Windows 实机组合安装测试。

可用参数：

```text
--profile core       仅核心（默认是 standard）
--dry-run            离线预览已有、缺失及冲突组件，不安装
--check              离线核验；任一缺失或冲突则非零退出
--dest <目录>        显式指定目标 skills 根目录
--installer <路径>   指定已有 install-skill-from-github.py
--report <新文件>    另存 JSON 报告；不会覆盖已有报告
```

从发行源码运行时，脚本位于 `skills/clinical-protocol-workbench/scripts/install_bundle.py`，可以一次安装核心及依赖。全部下载和校验成功后才写入正式目录，安装完成逐项回读；若写入失败会清理本次新建的 Skill 目录，原来存在的组件保留。

根目录型上游先使用完整 ZIP 下载，未成功时尝试同一固定提交的完整 Git 检出；子目录型上游使用 Git 安装，避免稀疏下载遗漏根目录下的 references/scripts。两种下载路径均失败或指纹不符时报告失败，不改用最新版本。网络、证书或认证问题应通过正常环境配置解决，不关闭证书验证。脚本不执行上游安装钩子、pip、模型请求或数据库查询。

## 已安装、升级与失败恢复

- `matched`：受控文件及文件集合一致，重复运行跳过；`.git`、Python 缓存和 `.DS_Store` 不参与比较。
- `missing`：组件缺失，标准安装会补装。
- `conflict`：修改、版本不同、额外源文件或不支持的链接；安装停止并列出组件，不能把它当作有效固定版。

升级时先运行新版源码中的 `--dry-run`，审阅冲突。用户确认升级的精确组件后，把旧目录完整备份到 skills 根目录之外，再安装固定新版；如有本地修改，应先比较和保留。脚本不自动备份覆盖所有依赖，也不删除旧版。不要改写锁文件以让已有目录“通过”。断电导致锁目录遗留时，先确认没有安装进程，再处理该唯一锁目录；不要删除整个 skills 根目录。

卸载核心不会删除外部依赖。其他项目可能仍在使用这些 Skill，依赖卸载应逐项决定。

## 安装成功后还有哪些环境检查

报告会列出当前 Python、Git、AutoCorrect、常用 Python 库的存在状态；macOS 下检测 Word 应用位置。`installed_and_verified` 只表示 7 项 Skill 文件安装并回读成功。

`clinical-doc-qc` 的脚本需要其文档列出的文档读取库，引文脚本可能需要 requests、bibtexparser 等；不同 Codex 环境也可能提供现成的文档能力。应在实际任务环境选择运行工具并检查，不把“Skill 存在”写成“审核工具运行成功”。PaperQA2、AutoCorrect、系统 Python、Microsoft Word 不在组合安装范围，数据库密钥/服务权限亦不由安装脚本取得。

本版的安装验证见 [组合安装验证记录](BUNDLE_VALIDATION.md)。项目启动时是否需要 AGENTS.md、应写什么，见 [项目规则说明](PROJECT_AGENTS.md)。
