# 第三方与分发范围

本版是公开预览工作包。仓库公开可见不等于自动成为开放源代码软件；本次未新增通用开源许可证。仓库所有者可另行决定对外许可。

两个皮肤科策略依赖保持在其各自仓库，不在本发行包中复制：

- https://github.com/huanglu1987/oral-derm-clinical-strategy — 固定 `b927d7d98283d489ab2661cd9551d0d4068cda37`。
- https://github.com/huanglu1987/Topical-Clinical-Strategy-Skill — 固定 `7edded14ad8bbb32a17e8ce2590efa735ff73c8c`。

上述固定树检查未发现 LICENSE；链接与哈希不构成再分发许可。依赖中的法规、指南、论文或其他资料仍须分别确认使用范围。未来如要打包上游正文，先明确许可及第三方资料边界。

`clinical-doc-qc` 是独立审核依赖，本版仅提供定位，不复制其正文或运行库。本版亦未复制 Codex 安装器、PaperQA2、AutoCorrect、Microsoft Word 或文档生成服务。

实验模块使用 lxml 6.1.1；合成 DOCX 构建另外使用 python-docx 1.2.0。仅保存依赖要求，不内嵌这些库；安装与使用须遵循其各自许可证。Microsoft Word 由用户自行合法安装并授权使用。

合成示例只用于软件行为测试，不是临床证据、真实项目或可直接套用的研究模板。真实临床输入、项目特异脚本、真实审阅文件与原开发历史均未收入此仓库。
