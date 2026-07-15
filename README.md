# SJTU Freshman Agent Learning

这是一个面向上海交通大学新生的校园助手项目。当前项目由 FastAPI 后端和原生 HTML/CSS/JS 前端组成，支持新生问答、校园地点导航、食堂推荐、校历查询、入学 checklist 和本地知识库检索。

## 主要功能

- 新生问答：检索 `data/knowledge/` 下的 Markdown 知识库，回答入学准备、校园生活等问题。
- LLM 驱动工具使用：后端先让模型判断是否需要调用地图、食堂等工具，再把工具结果交给模型组织回答。
- 校园地图与路线：支持“包图怎么走”“从宿舍到图书馆怎么去”等问题，前端会在需要时把浏览器定位传给后端。
- 食堂推荐：结合本地食堂知识库、campuslife 实时拥挤度接口和浏览器保存的用户偏好。
- 离线官方资料：校历、checklist、新生手册等低频更新资料由维护者定期更新到 `data/`，用户查询时直接读取本地数据。
- 前端卡片：路线、地点、食堂、校历和 checklist 都会以结构化卡片展示。

## 项目结构

```text
.
├── app/                    # FastAPI 后端
│   ├── main.py             # 应用入口，托管 frontend 静态页面
│   ├── routes.py           # /health、/api/chat、/api/search
│   ├── agent.py            # Agent 编排层
│   ├── llm.py              # LLM 调用与工具规划
│   ├── knowledge_base.py   # 本地 Markdown 知识库检索
│   ├── schemas.py          # 请求/响应模型
│   └── tools/              # 工具层
│       ├── calendar.py     # 离线校历资料读取
│       ├── checklist.py    # 离线新生 checklist 读取
│       ├── places.py       # 校园地点识别与路线补全
│       ├── amap.py         # 高德地图 Web Service 调用
│       ├── dining.py       # 食堂推荐与实时拥挤度
│       └── official.py     # 其他官方信息类工具
├── data/
│   ├── raw/                # 原始 PDF 或官方下载文件
│   ├── knowledge/          # PDF 转换后的 Markdown，用于 RAG
│   ├── official/           # 校历等官方结构化配置
│   ├── checklists/         # checklist JSON
│   ├── places/             # 校园地点本地库
│   └── dining/             # 食堂本地知识库
├── frontend/
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   └── config.js           # 本地前端配置，可参考 config_example.js
├── scripts/
│   ├── import_pdf_handbook.py
│   └── update_calendar_config.py
└── requirement.txt
```

## 运行方式

安装依赖：

```powershell
pip install -r requirement.txt
```

配置 `.env`：

```env
OPENAI_API_KEY=你的 API Key
OPENAI_BASE_URL=你的模型服务地址
OPENAI_MODEL=deepseek-chat
AMAP_WEB_SERVICE_KEY=你的高德 Web Service Key
```

启动后端：

```powershell
uvicorn app.main:app --reload
```

推荐直接访问后端托管的前端页面：

```text
http://127.0.0.1:8000/
```

## 官方资料离线更新流程

低频更新资料不要在用户查询时实时抓取官网。维护者应定期更新 `data/` 目录，更新后重启后端，或按项目实际部署方式触发知识库重新加载。

新生手册：

```powershell
# 1. 把 PDF 放入 data/raw/
# 2. 批量转换为 Markdown
py scripts/import_pdf_handbook.py --all --overwrite
```

生成的 Markdown 会写入 `data/knowledge/`，用于后端 RAG 检索。

校历：

```powershell
py scripts/update_calendar_config.py --pdf-url "https://www.sjtu.edu.cn/resource/assets/img/2026_2027qiuji.pdf" --school-year "2026-2027" --semester "秋季学期" --source-url "https://www.sjtu.edu.cn/" --updated-at "2026-07-15" --overwrite
```

也可以让维护脚本自动去教务处历年校历页匹配对应学年，再写入本地配置：

```powershell
py scripts/update_calendar_config.py --auto --school-year "2026-2027" --overwrite
```

脚本会写入 `data/official/calendar.json`。自动模式只在维护者运行脚本时访问官网；用户问“校历、放假、寒假、暑假、开学、考试周、节假日、假期”等问题时，后端仍然只读取本地配置并返回 calendar card。

Checklist：

维护者直接编辑：

```text
data/checklists/freshman_checklist.json
```

用户问“清单、准备什么、要带什么、入学准备、报到准备、报到当天、开学前、手续”等问题时，后端读取本地 checklist 并返回 checklist card。前端会用 localStorage 保存每个浏览器的勾选状态。

## 数据来源

- `data/knowledge/`：本地 Markdown 知识库，主要来自新生手册和维护者整理资料。
- `data/official/calendar.json`：服务器端维护的官方校历副本或链接。
- `data/checklists/freshman_checklist.json`：服务器端维护的新生入学 checklist。
- `data/places/campus_places.json`：校园地点本地库，用于地点识别、别名匹配和路线补全。
- `data/dining/canteens.json`：食堂静态知识库。
- campuslife 接口：运行时获取食堂实时拥挤度。
- 高德地图 API：运行时获取 POI 和步行路线。

静态官方资料使用本地数据；实时信息如食堂拥挤度和路线规划可以继续调用外部 API。

## 家长模式

前端侧栏可以选择“新生”或“家长”身份。选择家长后，后端会在 prompt 中加入家长视角说明，回答会更关注报到陪同、交通接送、住宿生活、安全医疗、缴费防诈骗和孩子适应大学等内容。

家长陪同报到清单来自 `data/checklists/parent_checklist.json`，会以前端 `parent_checklist` 卡片展示并支持勾选。家长常见问题知识库来自 `data/knowledge/parent_faq.md`。涉及具体政策、时间、费用、电话或现场安排时，仍应以学校和学院最新官方通知为准。
