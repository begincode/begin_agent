# 第24步：备份恢复演练与对象引用核对

日期：2026-11-06。用户授权连续完善第二十至二十五步，范围见[计划](../specs/feature-steps-20-25.md)。

## 工具与边界

新增 `python -m scripts.backup_restore backup <新目录> --writers-stopped`，备份项目 public schema、私有本地对象及既有凭据主密钥，写入表数据摘要和逐文件 SHA256 清单。目录 0700、文件 0600，包含密钥的备份不得提交或公开。支持外部主密钥；清单校验用于发现损坏，不代替可信备份来源和存储访问控制。备份前需停 API/Worker 写入，前后数据库摘要变化则拒绝标记有效。全库核对要求受控 BYPASSRLS/迁移角色，不能用租户 API 角色。存在 S3 引用时拒绝冒称完整本地备份。

恢复命令需显式 `--database begin_agent_restore_<名字> --data-directory <新目录>`，仅允许本机、全新限定前缀数据库及新目录，拒绝覆盖。pg_restore 在这个新库内单事务恢复，再逐表比较摘要、复制对象与主密钥。生产回切、角色授权和云桶备份仍须单独制定流程。

新增只读 `python -m scripts.audit_objects --verify-content --output <报告>`，核对文件引用、缺失、损坏、链接路径和未引用对象；不删除或移动任何业务文件。达到扫描上限或存在未验证远程引用明确标记不完整，不能据此自动清理。

## 实际验收

完整后端 273 项、前端 133 项、UI 6 项、类型和生产构建通过；脚本改动后的八项备份单测复验通过。修复严格恢复时 public schema 已存在的问题，清理只作用于刚创建的受限新库；已有恢复库仍拒绝覆盖。

对包含账号、模型配置、版本、任务附件、生成文件和回执的隔离库停写备份，恢复至 begin_agent_restore_step24_verified，28 张 public 表摘要一致，主密钥相同且恢复后的模型凭据可解密。只读核对发现刻意添加的一个未引用测试文件，并保持文件不变、删除数零。

独立 Chromium 登录恢复后的 18031 服务，查看原任务和生成文件，下载字节与恢复前一致；六视口尺寸和截图通过。实际 18029 API/Worker 重新编译启动，未覆盖原库。证据 output/playwright/implementation-step-24：完整日志、restore-rehearsal、object-audit、browser-restore、geometry、截图及下载样本。私有完整备份位于 `.data/acceptance/step24-backup`，不在 Git 或网页目录。

这是本机 PostgreSQL 17 的隔离恢复演练，不等于云桶灾备、生产回切或跨版本升级验收。

独立文章：[有备份文件，还需要证明系统能恢复](articles/24-backup-restore-audit.md)。
