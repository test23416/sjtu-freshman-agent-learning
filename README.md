# 交大新生助手

面向上海交通大学新生与家长的校园智能助手。项目使用 FastAPI 构建后端，Web 端使用原生 HTML/CSS/JavaScript，小程序端使用微信小程序原生框架。系统结合本地 Markdown 知识库、LLM 综合回答和工具调用，支持入学问答、校园导航、食堂推荐、校历查询、报到清单、家长模式和校园参观路线推荐。

## 功能概览

- 新生问答：检索 `data/knowledge/` 下的本地知识库，回答报到、材料、缴费、校园生活等问题。
- LLM 驱动工具：由模型判断是否需要调用地图、食堂等工具，再结合工具结果生成自然回答。
- 校园导航：支持“包图怎么走”“从当前位置到一餐怎么走”等问题，可结合定位、上下文和地点别名补全起终点。
- 食堂推荐：结合本地食堂资料、campuslife 实时拥挤度接口和用户历史偏好进行推荐。
- 校历查询：用户索要校历文件时返回 calendar card；询问假期、考试周等内容时从本地文字版校历回答。
- 新生 checklist：读取本地 JSON，前端支持勾选状态保存。
- 家长模式：回答更关注陪同报到、交通接送、住宿生活、安全医疗、缴费防诈骗和孩子适应大学。
- 校园参观路线：按参观目的区分新生熟悉路线、游客景点打卡路线、家长陪同参观路线。
- 多端展示：Web 前端和微信小程序都支持基础聊天和主要 cards 展示。

## 技术架构

```text
.
├── app/                         # FastAPI 后端
│   ├── main.py                  # 应用入口、静态文件托管、全局异常处理
│   ├── routes.py                # /health、/api/chat、/api/search
│   ├── agent.py                 # Agent 编排：RAG、工具、LLM 回答
│   ├── llm.py                   # LLM 调用、工具规划、参观目的分类
│   ├── knowledge_base.py        # 本地 Markdown/文本知识库检索
│   ├── schemas.py               # 请求和响应模型
│   └── tools/                   # 工具层
│       ├── amap.py              # 高德 POI 与步行路线
│       ├── calendar.py          # 离线校历配置读取
│       ├── checklist.py         # 新生 checklist
│       ├── dining.py            # 食堂推荐与拥挤度
│       ├── parent.py            # 家长 checklist
│       ├── places.py            # 地点识别、别名、路线补全
│       └── tours.py             # 校园参观路线
├── data/
│   ├── raw/                     # 原始 PDF 或官方下载文件
│   ├── knowledge/               # Markdown/文本知识库，用于 RAG
│   ├── official/                # 校历等结构化官方配置
│   ├── checklists/              # checklist JSON
│   ├── places/                  # 地点坐标、别名、校区
│   ├── dining/                  # 食堂静态资料
│   └── tours/                   # 校园参观路线配置
├── frontend/                    # Web 前端
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   └── config.js
├── miniprogram/                 # 微信小程序前端
├── scripts/
│   ├── import_pdf_handbook.py   # PDF 新生手册转 Markdown
│   ├── update_calendar_config.py# 离线更新校历配置/下载校历
│   └── sync_miniprogram_appid.py
├── requirements.txt
└── requirement.txt              # 兼容旧命令，内容与 requirements.txt 保持一致
```

## 环境要求

- Python 3.10+
- 一个 OpenAI 兼容格式的大模型 API
- 高德 Web Service Key，可选；未配置时路线和 POI 能力会降级
- campuslife 食堂拥挤度接口，可选；不可用时使用本地食堂资料降级推荐

安装依赖：

```powershell
pip install -r requirements.txt
```

如果仍使用旧命令，也可以：

```powershell
pip install -r requirement.txt
```

## 配置

在项目根目录创建 `.env`：

```env
OPENAI_API_KEY=你的模型 API Key
OPENAI_BASE_URL=你的模型服务地址，例如 https://example.com/api/v1
OPENAI_MODEL=deepseek-chat
AMAP_WEB_SERVICE_KEY=你的高德 Web Service Key
CAMPUSLIFE_DINING_URL=campuslife 食堂实时拥挤度接口地址
```

当前前端支持用户在聊天框旁选择模型，后端仍会用 `.env` 中的 `OPENAI_MODEL` 作为默认模型。

## 启动

启动后端：

```powershell
uvicorn app.main:app --reload
```

访问 Web 前端：

```text
http://127.0.0.1:8000/
```

后端会托管 `frontend/`，建议优先通过后端地址访问，避免直接打开 HTML 时出现“后端未连接”。

## Web 前端

Web 端首页包含项目介绍、功能入口、快捷问题和聊天区。聊天区支持：

- 身份选择：新生 / 家长
- 模型选择
- 校区、学院、专业、宿舍等 profile 信息
- 浏览器定位
- 中止回答
- 清空对话
- cards 渲染：route、place、food、calendar、checklist、parent_checklist、campus_tour

快捷问题会复用原有 `sendMessage()` 流程，不单独绕开 `/api/chat`。

## 微信小程序

小程序目录位于 `miniprogram/`，通过 `wx.request` 调用现有 FastAPI `/api/chat`。当前支持：

- 基础聊天
- 定位并传给后端
- 模型选择
- route card 的小程序 `<map>` 展示
- 食堂推荐和“导航过去”
- calendar、checklist、parent_checklist、campus_tour 基础展示

本地开发时请在微信开发者工具中打开 `miniprogram/`，并把小程序端 API 地址指向正在运行的 FastAPI 服务。

## 本地知识库与数据维护

低频官方资料采用离线维护机制：维护者定期更新 `data/`，用户查询时直接读取服务器本地资料，不在每次请求时实时抓学校官网。

### 新生手册

把 PDF 放入 `data/raw/` 后运行：

```powershell
py scripts/import_pdf_handbook.py --all --overwrite
```

脚本会把 PDF 转成 `data/knowledge/*.md`，供 RAG 检索。PDF 转换依赖 `pypdf`。

### 校历

建议维护两份资料：

```text
data/raw/年份_calendar.pdf
data/knowledge/年份_calendar_text
```

用户明确索要校历文件，例如“给我校历”“校历在哪里”，系统优先返回本地 PDF card。用户询问校历内容，例如“国庆放几天”“寒假什么时候”，系统从文字版校历中检索回答。

可手动维护 `data/official/calendar_2025_2026.json` 等年度结构化配置，也可使用脚本自动从教务处校历页匹配下载：

```powershell
py scripts/update_calendar_config.py --auto --download --calendar-year 2026 --school-year "2026-2027" --overwrite
```

如果官网资源是图片，脚本会尝试用 Pillow 转为 PDF。

### Checklist

新生清单：

```text
data/checklists/freshman_checklist.json
```

家长清单：

```text
data/checklists/parent_checklist.json
```

前端会在本地保存勾选状态，后端只负责返回稳定的清单数据。

### 地点、食堂和参观路线

- `data/places/places.json`：地点坐标、别名、校区、分类。
- `data/dining/canteens.json`：食堂静态知识库。
- `data/official/canteens.json`：整理后的食堂结构化资料。
- `data/tours/campus_tours.json`：校园参观路线配置。
- `data/knowledge/campus_routes.md`：参观路线分类知识库。

## 降级策略

项目对外部服务失败做了友好降级：

- LLM 调用失败：回退到本地 fallback 回答，`used_llm=false`。
- 知识库为空或文件损坏：跳过异常文件，不影响工具 card。
- 高德失败：如果已识别目的地，返回地点或路线 fallback card。
- 食堂实时拥挤度失败：使用本地食堂知识库推荐。
- 校历/checklist 缺失：返回友好提示，不暴露 traceback。
- Web/小程序请求失败：显示“暂时连接不上服务，请稍后再试。”

## 测试建议

启动后端后可以尝试：

```text
包图怎么走
从当前位置到一餐怎么走
推荐几个食堂，导航到那里去
给我一份新生报到清单
校历在哪里
国庆放几天
送孩子报到要准备什么
第一次来上海交大，想了解一下校园，怎么参观？
交大有哪些适合拍照打卡的地方？
我是新生家长，送孩子报到想提前看看学校
```

## 注意事项

- 不要把真实 API Key、微信 AppID 等敏感信息提交到仓库。
- 具体政策、日期、费用、电话和现场安排应以学校或学院最新官方通知为准。
- 静态官方资料通过 `data/` 离线维护；实时信息如路线规划、食堂拥挤度可以继续调用外部 API。
