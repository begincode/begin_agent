# 前端规范与校验入口验证

日期：2026-10-23（Asia/Shanghai）。

- `npm --prefix web run check` 完成：8 项前端功能测试、5 项 UI 检查器测试、当前源码规则检查、Vue 类型检查及生产构建全部通过。
- 新建临时 Vue 组件，按钮使用 37px 高、5px 圆角；检查器识别新按钮类并以退出码 1 拒绝。临时文件随后移除，当前源码检查再次通过。
- `.github/workflows/frontend-checks.yml` 已添加并通过 YAML 语法检查；未提交、推送或在 GitHub 运行，不能记作云端 CI 已通过。
- 本次通过真实浏览器重新检查六种规范视口：320×800、390×844、768×1024、1280×720、844×390、390×360。模型与思考下拉框均为 40px / 8px，同排底边差 0，Logo 中心偏差 0；无页面溢出，输入区可见，复选框保持 16px。
- 本次源码中的界面改动仅将复选框样式选择器写成显式类型以配合检查器，未改变视觉设计。Markdown、复制、停止和失败处理没有修改，未重新向供应商发请求；相关功能测试通过不代表新增真实 API 验证。

几何数据：[frontend-standards-browser-verification.json](./frontend-standards-browser-verification.json)。

![本次构建的控件对齐检查](./frontend-standards-browser-verification.png)
