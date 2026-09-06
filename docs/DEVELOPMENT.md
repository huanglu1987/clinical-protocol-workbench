# 开发与合成验证

普通使用者只需安装 Skill；以下实验源码不随 Skill 自动安装。所有命令均在本仓库根目录执行。不要将真实临床资料放入验证目录或提交到 Git。

## 可重复的本地回归

要求 Python 3.12。创建独立环境；依赖安装会访问所配置的软件包源，不调用模型。

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python scripts/build_s3_native_review_fixture.py
PYTHONPATH=worker .venv/bin/python -m unittest discover -s worker/tests -v
```

Windows PowerShell 相应使用 `.venv\Scripts\python.exe`，并设置 `$env:PYTHONPATH = 'worker'`；此说明不代表 Windows 实机已测。

合成构建会在 `docs/verification/` 下生成虚构 DOCX、冻结计划和清单（均被忽略，不进入发行包）。源/输出存在且字节不同时会拒绝覆盖。80 项测试包含原有 67 项计划过期/篡改、批注锚点、既有审阅结构保护、ZIP/XML 风险与策略依赖漂移测试，以及 13 项组合安装测试；测试通过不等于生产使用验收。

preview.5 另新增 7 项必读规范测试，总计 87 项。必读规范测试仅使用虚构三行文件，不将内部规范正文提交到测试仓库；真实原文件校验另在本机执行。

组合安装测试可单独用 `python3 -m unittest discover -s worker/tests -p test_bundle_install.py -v` 执行。核心文件发生获准变更后，维护人员用 `python3 scripts/build_bundle_lock.py` 更新核心文件指纹，随后重跑测试；该脚本保留既有外部依赖锁。首次建立外部锁的 `--sources` 只能指向已从脚本固定提交取得、核对完整的目录，不得对用户漂移目录重新取哈希来消除冲突。writing-standard.lock.json 固定的是用户指定规范，不能随核心代码更新而重算它来接受内容变化。

独立重算合成审阅结构：

```sh
.venv/bin/python scripts/verify_s3_native_review.py \
  --source docs/verification/fixtures/s3/synthetic-native-review-source-v0.1.docx \
  --output docs/verification/artifacts/s3/synthetic-native-review-redline-v0.1.docx \
  --plan docs/verification/evidence/2026-09-05-s3-macos/frozen-change-plan.json \
  --report docs/verification/evidence/s3-structure-recheck.json
```

报告路径应是本轮新路径；此开发脚本会写报告，不用于覆盖受控证据。其范围仅为合成的简单修订和经典批注，不是通用 Word 审阅验收器。

## Word 实机合成验证

macOS 装有 Microsoft Word 且允许所需自动化时，在全新的输出路径运行：

```sh
sh scripts/run_s3_word_macos.sh \
  "$PWD/docs/verification/artifacts/s3/synthetic-native-review-redline-v0.1.docx" \
  "$PWD/docs/verification/artifacts/s3/word-markup.pdf" \
  "$PWD/docs/verification/evidence/s3-word-record.txt"
```

此入口锁定合成样例哈希，不接受任意真实方案。它实际打开 Word、枚举对象、导出含标记 PDF，但成功状态仍为 `pending_manual_page_review`；必须查看 PDF 每一页后另记视觉结论。输出或记录已存在会拒绝覆盖。若样例哈希漂移，先调查原因，不把锁值改成新值后冒充复验。

Windows 脚本见 `scripts/word_windows_s3_validate.ps1`，参数按脚本的 `param` 声明传入；尚未实机验证，不把静态源码检查当作跨平台通过。

## 实验 worker 的边界

`worker/clinical_qc` 在内存中处理受限 DOCX 包，生成函数不提供模型调用、网络或落盘入口。支持普通正文/简单表格中单一纯文本 run 的精确定位，每段最多一处；不是任意全文修订器。

文字替换仅允许实现中的窄规则：中文逗号的两项机械修复与预定义的精确术语替换。任何术语变更仍需实际项目的逐项批准；已有测试规则不构成其他项目的医学用语授权。字段、超链接、书签、旧修订内部和复杂结构不自动写入。

模块保留支持范围内的旧插入/删除和经典批注；现代评论、移动/属性修订等拒绝处理。规则名称、严重度或模型填入 `user_confirmed=true` 不能充当人工授权。生产级身份认证、可信审批入口、IPC、原子写入与全量文档支持不在本版内。

生成函数只报告 `spike_subset_passed` 和 `word_visual_status=not_performed`；不能因为生成返回成功而声称 Word/视觉通过。

## 发行边界

发行仓库是显式白名单导出的干净源码，不保留原开发 Git 历史。只包含 Skill、通用实验模块、合成测试源和说明；不包含真实项目脚本或输入/输出。Release ZIP 从冻结提交生成，校验清单提供文件与压缩包 SHA-256。
