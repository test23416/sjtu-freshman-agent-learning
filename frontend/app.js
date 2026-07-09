const STORAGE_KEY = "sjtu_freshman_chat_history";
const ROUTE_WORDS = [
  "怎么去",
  "怎么到",
  "怎么走",
  "路线",
  "导航",
  "走到",
  "到哪里",
  "去哪里",
  "去",
  "到",
  "从",
];
let chatHistory = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");

function saveHistory() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(chatHistory));
}

let amapReadyPromise = null;
let campusMap = null;
let currentLocationPromise = null;

function isRouteQuestion(message) {
  return ROUTE_WORDS.some((word) => message.includes(word));
}

function getCurrentLocation() {
  if (!navigator.geolocation) {
    return Promise.resolve(null);
  }

  if (currentLocationPromise) {
    return currentLocationPromise;
  }

  currentLocationPromise = new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (position) => {
        resolve({
          lng: position.coords.longitude,
          lat: position.coords.latitude,
          accuracy: position.coords.accuracy,
        });
      },
      () => resolve(null),
      {
        enableHighAccuracy: true,
        timeout: 5000,
        maximumAge: 60000,
      },
    );
  }).finally(() => {
    currentLocationPromise = null;
  });

  return currentLocationPromise;
}

function loadAmapScript() {
  if (window.AMap) {
    return Promise.resolve(window.AMap);
  }

  if (amapReadyPromise) {
    return amapReadyPromise;
  }

  const config = window.APP_CONFIG || {};

  if (!config.AMAP_JS_KEY) {
    return Promise.reject(
      new Error("缺少 AMAP_JS_KEY，请检查 frontend/config.js"),
    );
  }

  window._AMapSecurityConfig = {
    securityJsCode: config.AMAP_SECURITY_CODE,
  };

  amapReadyPromise = new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(config.AMAP_JS_KEY)}`;
    script.onload = () => resolve(window.AMap);
    script.onerror = () => reject(new Error("高德地图 JS API 加载失败"));
    document.head.appendChild(script);
  });

  return amapReadyPromise;
}

function parsePolyline(polyline) {
  if (!polyline) {
    return [];
  }

  return polyline
    .split(";")
    .map((point) => {
      const [lng, lat] = point.split(",").map(Number);
      return [lng, lat];
    })
    .filter(([lng, lat]) => Number.isFinite(lng) && Number.isFinite(lat));
}

function extractRoutePath(route) {
  if (!route || !route.steps) {
    return [];
  }

  const path = [];

  route.steps.forEach((step) => {
    const stepPath = parsePolyline(step.polyline);
    stepPath.forEach((point) => path.push(point));
  });

  return path;
}

async function showRouteOnMap(routeCardData) {
  const AMap = await loadAmapScript();

  const mapSection = document.getElementById("mapSection");
  mapSection.hidden = false;

  const route = routeCardData.route;
  const path = extractRoutePath(route);

  if (path.length === 0) {
    errorBox.textContent = "路线数据中没有可绘制的 polyline。";
    return;
  }

  if (!campusMap) {
    campusMap = new AMap.Map("campusMap", {
      zoom: 17,
      resizeEnable: true,
      center: path[0],
    });
  }

  campusMap.clearMap();

  const startMarker = new AMap.Marker({
    position: path[0],
    title: "起点",
    label: {
      content: "起点",
      direction: "top",
    },
  });

  const endMarker = new AMap.Marker({
    position: path[path.length - 1],
    title: "终点",
    label: {
      content: "终点",
      direction: "top",
    },
  });

  const polyline = new AMap.Polyline({
    path: path,
    showDir: true,
    strokeWeight: 6,
  });

  campusMap.add([startMarker, endMarker, polyline]);
  campusMap.setFitView([startMarker, endMarker, polyline]);
}

const messageInput = document.getElementById("message");
const sendButton = document.getElementById("sendButton");
const chatLog = document.getElementById("chatLog");
const errorBox = document.getElementById("error");
const clearButton = document.getElementById("clearButton");
const serverStatus = document.getElementById("serverStatus");

function buildProfile() {
  return {
    campus: document.getElementById("campus").value.trim() || null,
    college: document.getElementById("college").value.trim() || null,
    major: document.getElementById("major").value.trim() || null,
    dorm_area: document.getElementById("dormArea").value.trim() || null,
    international_student: false,
  };
}

//导航卡片
function renderCards(cards, container) {
  if (!cards || cards.length === 0) {
    return;
  }

  const hasRouteCard = cards.some((card) => card.type === "route");

  const cardsBox = document.createElement("div");
  cardsBox.className = "cards";

  cards.forEach((card) => {
    if (hasRouteCard && card.type === "place") {
      return;
    }

    if (card.type === "place") {
      const place = card.data.place;
      const mapUrl = card.data.map_url;

      const item = document.createElement("div");
      item.className = "card-item";

      const title = document.createElement("div");
      title.className = "card-title";
      title.textContent = card.title || place.name;

      const meta = document.createElement("div");
      meta.className = "card-meta";
      meta.textContent = `${place.campus || "未知校区"} | ${place.category || "地点"}`;

      const description = document.createElement("div");
      description.className = "card-description";
      description.textContent = place.description || "暂无地点说明。";

      item.appendChild(title);
      item.appendChild(meta);
      item.appendChild(description);

      if (mapUrl) {
        const link = document.createElement("a");
        link.className = "card-link";
        link.href = mapUrl;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "打开地图";
        item.appendChild(link);
      } else {
        const noMap = document.createElement("div");
        noMap.className = "card-warning";
        noMap.textContent = "暂未配置精确坐标，无法生成地图链接。";
        item.appendChild(noMap);
      }

      cardsBox.appendChild(item);
    }

    if (card.type === "route") {
      const data = card.data || {};
      const route = data.route;

      const item = document.createElement("div");
      item.className = "card-item";

      const title = document.createElement("div");
      title.className = "card-title";
      title.textContent = card.title || "路线导航";
      item.appendChild(title);

      if (data.missing_origin) {
        const description = document.createElement("div");
        description.className = "card-description";
        description.textContent =
          "已识别目的地，但没有从上下文或当前位置获得起点。";
        item.appendChild(description);
      } else if (route) {
        const distance = document.createElement("div");
        distance.className = "card-description";

        const durationMinutes = Math.round(Number(route.duration) / 60);

        distance.textContent = `步行距离约 ${route.distance} 米，预计耗时约 ${durationMinutes} 分钟。`;
        item.appendChild(distance);

        if (data.route_source === "context") {
          const source = document.createElement("div");
          source.className = "card-meta";
          source.textContent = "起终点已根据上文自动补全。";
          item.appendChild(source);
        } else if (data.route_source === "current_location") {
          const source = document.createElement("div");
          source.className = "card-meta";
          source.textContent = "起点已默认使用你的当前位置。";
          item.appendChild(source);
        }

        const mapButton = document.createElement("button");
        mapButton.type = "button";
        mapButton.className = "card-map-button";
        mapButton.textContent = "在地图上显示路线";

        mapButton.addEventListener("click", () => {
          showRouteOnMap(data);
        });

        item.appendChild(mapButton);

        if (route.steps && route.steps.length > 0) {
          const details = document.createElement("details");
          details.className = "route-details";

          const summary = document.createElement("summary");
          summary.textContent = "查看文字路线步骤";
          details.appendChild(summary);

          const stepsList = document.createElement("ol");

          route.steps.slice(0, 8).forEach((step) => {
            const li = document.createElement("li");
            li.textContent = step.instruction;
            stepsList.appendChild(li);
          });

          details.appendChild(stepsList);
          item.appendChild(details);
        }
      } else {
        const description = document.createElement("div");
        description.className = "card-description";
        description.textContent =
          "已识别路线意图，但暂时没有获取到可用路线。可能是地点坐标未配置，或高德 API 没有返回路线结果。";
        item.appendChild(description);
      }

      if (data.navigation_url) {
        const link = document.createElement("a");
        link.className = "card-link";
        link.href = data.navigation_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "打开高德导航";
        item.appendChild(link);
      }

      cardsBox.appendChild(item);
    }
  });

  container.appendChild(cardsBox);
}
//输出回答
function appendMessage(
  role,
  content,
  sources = [],
  usedLlm = null,
  cards = [],
) {
  const message = document.createElement("div");
  message.className = "message " + role;

  const roleBox = document.createElement("div");
  roleBox.className = "message-role";
  roleBox.textContent = role === "user" ? "你" : "新生助手";

  const contentBox = document.createElement("div");
  contentBox.textContent = content;

  message.appendChild(roleBox);
  message.appendChild(contentBox);

  if (role === "assistant") {
    const meta = document.createElement("div");
    meta.className = "source-meta";
    meta.textContent = usedLlm ? "已使用大模型生成回答" : "使用本地知识库回答";
    message.appendChild(meta);

    renderCards(cards, message);

    const sourcesBox = document.createElement("div");
    sourcesBox.className = "sources";

    if (sources.length > 0) {
      const title = document.createElement("div");
      title.className = "message-role";
      title.textContent = "参考来源";
      sourcesBox.appendChild(title);

      sources.forEach((source) => {
        const item = document.createElement("div");
        item.className = "source";

        const header = document.createElement("button");
        header.className = "source-toggle";
        header.type = "button";
        header.textContent = `▶ ${source.title} | ${source.source} | 相关度：${source.score}`;

        const content = document.createElement("div");
        content.className = "source-content";
        content.textContent = source.content;
        content.style.display = "none";

        header.addEventListener("click", () => {
          const isHidden = content.style.display === "none";
          content.style.display = isHidden ? "block" : "none";
          header.textContent = `${isHidden ? "▼" : "▶"} ${source.title} | ${source.source} | 相关度：${source.score}`;
        });

        item.appendChild(header);
        item.appendChild(content);
        sourcesBox.appendChild(item);
      });
    }

    message.appendChild(sourcesBox);
  }

  chatLog.appendChild(message);
  message.scrollIntoView({ behavior: "smooth", block: "end" });
}
//发送操作
async function sendMessage() {
  const message = messageInput.value.trim();

  if (!message) {
    errorBox.textContent = "请先输入问题。";
    return;
  }

  appendMessage("user", message);
  messageInput.value = "";

  sendButton.disabled = true;
  sendButton.textContent = "思考中...";
  errorBox.textContent = "";

  try {
    const location = isRouteQuestion(message)
      ? await getCurrentLocation()
      : null;

    const response = await fetch("http://127.0.0.1:8000/api/chat", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message: message,
        history: chatHistory,
        profile: buildProfile(),
        location: location,
      }),
    });

    if (!response.ok) {
      throw new Error("后端返回错误：" + response.status);
    }

    const data = await response.json();
    console.log("chat response:", data);
    appendMessage(
      "assistant",
      data.answer,
      data.sources || [],
      data.used_llm,
      data.cards || [],
    );

    chatHistory.push({
      role: "user",
      content: message,
    });

    chatHistory.push({
      role: "assistant",
      content: data.answer,
    });

    saveHistory();
  } catch (error) {
    errorBox.textContent = "请求失败：" + error.message;
  } finally {
    sendButton.disabled = false;
    sendButton.textContent = "发送";
    messageInput.focus();
  }
}
//检查后端连接
async function checkServerStatus() {
  try {
    const response = await fetch("http://127.0.0.1:8000/health");

    if (!response.ok) {
      throw new Error("后端返回错误：" + response.status);
    }

    const data = await response.json();

    serverStatus.textContent = `后端已连接：${data.status}`;
    serverStatus.className = "server-status ok";
    sendButton.disabled = false;
  } catch (error) {
    serverStatus.textContent =
      "后端未连接，请先启动 uvicorn app.main:app --reload";
    serverStatus.className = "server-status error";
    sendButton.disabled = true;
  }
}
//每次刷新保留历史回答
chatHistory.forEach((message) => {
  appendMessage(message.role, message.content);
});

sendButton.addEventListener("click", sendMessage);

//清空按钮
clearButton.addEventListener("click", () => {
  chatHistory = [];
  localStorage.removeItem(STORAGE_KEY);
  chatLog.innerHTML = "";
  errorBox.textContent = "";

  clearMapView();
});
//清空地图标签
function clearMapView() {
  const mapSection = document.getElementById("mapSection");

  if (campusMap) {
    campusMap.clearMap();
  }

  if (mapSection) {
    mapSection.hidden = true;
  }
}
//按键回车直接发送
messageInput.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
});
//检查服务器状态
checkServerStatus();
