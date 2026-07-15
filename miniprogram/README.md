# 微信小程序前端

这是当前 FastAPI 新生助手的微信小程序 MVP 前端，后端仍使用现有 `/api/chat`。

## 本地调试

1. 启动后端：

```powershell
uvicorn app.main:app --reload
```

2. 用微信开发者工具打开 `miniprogram/`。
3. 开发者工具里勾选“不校验合法域名、web-view、TLS 版本以及 HTTPS 证书”。
4. 页面顶部 API 地址默认是：

```text
http://127.0.0.1:8000
```

如果真机预览，需要把 API 地址改成手机可访问的局域网地址或已配置 HTTPS 域名。

## AppID 配置

真实小程序 AppID 不提交到 GitHub。把它放在项目根目录 `.env`：

```env
MINIPROGRAM_APPID=你的微信小程序AppID
```

然后运行：

```powershell
python scripts/sync_miniprogram_appid.py
```

脚本会生成已被 `.gitignore` 忽略的 `miniprogram/project.private.config.json`。公开的 `project.config.json` 保持 `touristappid`，避免 GitHub 扫描报警。
