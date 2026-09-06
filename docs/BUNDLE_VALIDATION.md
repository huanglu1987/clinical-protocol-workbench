# v0.1.0-preview.4 组合安装验证

验证日期：2026-09-07。环境：macOS、Python 3.13.1、当前 Codex skill-installer、Git。仅使用公开上游、发行源码和隔离临时目录；未使用真实研究资料。

## 已完成

| 检查 | 结果 |
| --- | --- |
| 全量回归 | 80/80：原有 67 项 + 新增 13 项组合安装测试 |
| Skill 结构校验 | 通过 |
| 默认 standard 真实联网安装 | 全新目录中 7/7 安装后文件集合和 SHA-256 匹配 |
| 重复安装 | 7/7 跳过，保持同一版本 |
| 离线 --check | 7/7 matched，退出码 0 |
| core 档位 | 仅安装核心并回读匹配 |
| 策略专用核验 | 口服 34/34、外用 58/58 原有受控文件匹配 |
| 已有目录冲突 | 发现后拒绝安装，原内容保留；模拟测试覆盖用户修改和额外文件 |
| 下载失败、哈希不符 | 写入正式 Skill 目录前停止 |
| 写入失败 | 模拟故障后回滚本次新建目录 |
| 符号链接与并发 | 拒绝受检链接；并发安装锁存在时停止 |
| 项目 AGENTS 文档 | 教程、模板、README 和 Skill 入口互相可达；规则与研究事实存储分工一致 |

根目录上游的 Git 稀疏下载曾遗漏子目录，被校验拦住；修正后先走完整 ZIP，在该调用环境的证书验证失败时使用同一提交的完整 Git 检出。未关闭证书校验、未换版本。最终真实安装覆盖该回退分支，两项根目录依赖与清单全部匹配。

上游 clinicaltrials-database 和 pubmed-database 的删除/合并记录已从 GitHub 读取；标准组合采用 database-lookup、paper-lookup 的现行固定文件树。clinical-trial-protocol-skill 不作为运行依赖默认安装。

## 复现

```sh
PYTHONPATH=worker python3 -m unittest discover -s worker/tests -q
python3 skills/clinical-protocol-workbench/scripts/install_bundle.py --dest <新的测试目录>
python3 skills/clinical-protocol-workbench/scripts/install_bundle.py --dest <同一测试目录>
python3 skills/clinical-protocol-workbench/scripts/install_bundle.py --dest <同一测试目录> --check
```

第二次运行的 action 应全部为 skipped；第三次的 status 应为 verified。已有版本冲突时不应返回成功。测试目录必须与正在使用的实际 Skill 目录隔离。

## 未据此证明的事项

Windows/Linux 实机组合安装、数据库在线查询、全部 Python 运行库、Word 渲染、真实项目 AGENTS 多轮行为和新方案端到端撰写不在本次验证范围。安装成功不能替代这些功能的运行验证或临床/法规专业复核。现有电脑上的不同版本可被识别为 conflict，这不等于该版本无法使用，仅表示它不符合本发行版的固定组合。
