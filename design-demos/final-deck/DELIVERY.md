# 安言答辩 Deck 交付清单

**生成日期**：2026-11-02  
**方向**：瑞士网格方向（严格栅格 + 极端字号对比）  
**尺寸**：1920×1080（16:9）

---

## 核心产出

| 文件 | 大小 | 说明 |
|------|------|------|
| `安言答辩-完整.pptx` | 1.2 MB | 可编辑 PowerPoint（13 页，嵌入 PNG） |
| `安言答辩-完整.pdf` | 1.3 MB | PDF 版本（13 页） |
| `thumbs-all.png` | 167 KB | 缩略图对比板（4×4 网格） |
| `index.html` | 5 KB | 浏览器拼接导航页 |
| `slides/*.html` | 13 个 | 单页 HTML 源文件（可编辑） |
| `slides/*.png` | 13 个 | 高清截图（每张 ~110KB） |

---

## 13 页内容

1. **封面** — 品牌定位 + 系统定义
2. **导航** — 四大核心主题
3. **问题** — 自然语言与银行执行的三重矛盾
4. **能力** — Agent、Core、规则、Android 四层架构
5. **分段架构** — 理解 → 取证 → 核验 → 执行的职责分离
6. **证据链** — 版本化规则 + 有界取证 + 来源追溯
7. **三级权限** — GREEN 咨询 / YELLOW 确认 / RED 验证
8. **授权闭环** — 预检 → 确认 → 验证 → 执行，参数绑定 + 单次消费
9. **真实界面** — Android 截图（隔离规则模式、联系人、主题适配）
10. **测试数据** — 3147 passed、140 passed JVM、24 项设备专项
11. **边与缺口** — 演示认证强度、脱敏不是匿名、沙箱无网络隔离
12. **交付与严重度** — 五类材料 + R1–R22 清单
13. **结尾** — 让自语言查询交与规则的共生执行同时成立

---

## 设计特点

**布局**：
- 12 栏严格网格（138px 栏 + 24px gutter）
- 左 3 栏标签带 + 右 9 栏内容区
- 0.5px 细石墨线分隔，无圆角无阴影

**字号**：
- 240px 巨型数字（测试数据页）
- 160px 主标题 / 72px 次标题 / 32px 段落标题 / 24px 正文
- 10:1 极端对比

**配色**：
- 米白底 `#F6F3EB` + 石墨文字 `#30312D`（90% 对比度）
- 浅金 `#B39A68`（Logo、编号、细线，5%）
- 中灰 `#6B6B68` + 暗灰 `#4A4A47`（保证可读性）

**原则**：
- 大量留白（48–80px padding）
- 信息层级全靠字号与网格位置
- 无装饰图形，纯文字编排

---

## 使用方式

**浏览器查看**：
```bash
# 打开拼接导航页
start design-demos/final-deck/index.html
```

**编辑 PowerPoint**：
```bash
# 打开 PPTX（图片嵌入，可直接编辑）
start design-demos/final-deck/安言答辩-完整.pptx
```

**打印或分发**：
```bash
# 使用 PDF 版本
start design-demos/final-deck/安言答辩-完整.pdf
```

---

## 技术栈

- **HTML 单页**：CSS Grid + CSS 自定义属性
- **截图**：Playwright 无头浏览器（1920×1080）
- **PPTX**：python-pptx（图片嵌入 16:9 幻灯片）
- **PDF**：img2pdf（13 张 PNG 合并）
- **缩略图**：Playwright 拼接 HTML → PNG

---

## 修改建议

**调整内容**：
1. 编辑 `slides/*.html` 源文件
2. 重新截图：`.venv/Scripts/python.exe _tools/capture_slides.py`
3. 重新生成 PPTX/PDF（脚本在 `_pptx_assets/`）

**改变配色**：
- 修改 `:root` CSS 变量（每个 HTML 的开头）
- 主色：`--color-primary`
- 背景：`--color-bg`
- 文字：`--color-text`

**切换字体**：
- 当前：Inter（无衬线）
- 备选：Helvetica Neue、SF Pro Display、Roboto

---

**版权**：安言银行 Agent 参赛作品  
**GitHub**：https://github.com/Zagadka-3906/ai-banking-agent
