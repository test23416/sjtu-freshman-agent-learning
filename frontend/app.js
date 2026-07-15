const STORAGE_KEY = "sjtu_freshman_chat_history";
const DINING_PREFERENCES_KEY = "sjtu_freshman_dining_preferences";
const LOCATION_STORAGE_KEY = "sjtu_freshman_last_location";
const CHECKLIST_STORAGE_KEY = "sjtu_freshman_checklist_state";
const MODEL_STORAGE_KEY = "sjtu_freshman_selected_model";
const config = window.APP_CONFIG || {};
const API_BASE_URL = (config.API_BASE_URL || window.location.origin || "").replace(/\/$/, "");
const ROUTE_WORDS = [
  "怎么去",
  "怎么到",
  "怎么走",
  "开车",
  "接送",
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
let diningPreferences = JSON.parse(
  localStorage.getItem(DINING_PREFERENCES_KEY) || "[]",
);

function saveHistory() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(chatHistory));
}

function saveDiningPreferences() {
  localStorage.setItem(
    DINING_PREFERENCES_KEY,
    JSON.stringify(diningPreferences),
  );
}

function loadChecklistState() {
  try {
    return JSON.parse(localStorage.getItem(CHECKLIST_STORAGE_KEY) || "{}");
  } catch {
    return {};
  }
}

function saveChecklistState(state) {
  localStorage.setItem(CHECKLIST_STORAGE_KEY, JSON.stringify(state));
}

function recordDiningPreference(canteen) {
  const existing = diningPreferences.find(
    (item) =>
      (canteen.id && item.canteen_id === canteen.id) ||
      item.canteen_name === canteen.name,
  );

  if (existing) {
    existing.count = Number(existing.count || 0) + 1;
    existing.last_visited_at = new Date().toISOString();
  } else {
    diningPreferences.push({
      canteen_id: canteen.id || null,
      canteen_name: canteen.name,
      count: 1,
      last_visited_at: new Date().toISOString(),
    });
  }

  saveDiningPreferences();
}

let amapReadyPromise = null;
let campusMap = null;
let currentLocationPromise = null;
let lastLocationError = "";
let activeChatAbortController = null;

function isRouteQuestion(message) {
  return ROUTE_WORDS.some((word) => message.includes(word));
}

function getCachedLocation() {
  try {
    const cached = JSON.parse(localStorage.getItem(LOCATION_STORAGE_KEY) || "null");

    if (!cached || typeof cached.lng !== "number" || typeof cached.lat !== "number") {
      return null;
    }

    return {
      lng: cached.lng,
      lat: cached.lat,
      accuracy: cached.accuracy || null,
    };
  } catch {
    return null;
  }
}

function saveCachedLocation(location) {
  localStorage.setItem(
    LOCATION_STORAGE_KEY,
    JSON.stringify({
      ...location,
      saved_at: new Date().toISOString(),
    }),
  );
}

function describeLocationError(error) {
  if (!window.isSecureContext) {
    return "浏览器只允许在 localhost/127.0.0.1 或 HTTPS 页面获取定位，请用 http://127.0.0.1:8000/ 打开。";
  }

  if (!error) {
    return "浏览器暂时没有返回定位结果。";
  }

  if (error.code === error.PERMISSION_DENIED) {
    return "浏览器定位权限被拒绝，请在地址栏左侧的网站权限里允许定位后刷新页面。";
  }

  if (error.code === error.POSITION_UNAVAILABLE) {
    return "浏览器暂时无法确定当前位置，请确认系统定位服务已开启。";
  }

  if (error.code === error.TIMEOUT) {
    return "浏览器定位超时，已尝试使用最近一次成功定位。";
  }

  return "浏览器定位失败，已尝试使用最近一次成功定位。";
}

function getCurrentLocation() {
  // 路线问题才会触发定位；失败时兜底使用最近一次成功定位。
  lastLocationError = "";

  if (!window.isSecureContext) {
    lastLocationError = describeLocationError();
    return Promise.resolve(getCachedLocation());
  }

  if (!navigator.geolocation) {
    lastLocationError = "当前浏览器不支持定位。";
    return Promise.resolve(getCachedLocation());
  }

  if (currentLocationPromise) {
    return currentLocationPromise;
  }

  currentLocationPromise = new Promise((resolve) => {
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const location = {
          lng: position.coords.longitude,
          lat: position.coords.latitude,
          accuracy: position.coords.accuracy,
        };
        saveCachedLocation(location);
        resolve(location);
      },
      (error) => {
        lastLocationError = describeLocationError(error);
        resolve(getCachedLocation());
      },
      {
        enableHighAccuracy: true,
        timeout: 12000,
        maximumAge: 300000,
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

async function showRouteOnMap(routeCardData, mapMount) {
  const AMap = await loadAmapScript();

  const route = routeCardData.route;
  const path = extractRoutePath(route);

  if (path.length === 0) {
    errorBox.textContent = "路线数据中没有可绘制的 polyline。";
    return;
  }

  if (campusMap && typeof campusMap.destroy === "function") {
    campusMap.destroy();
  }

  mapMount.hidden = false;
  mapMount.innerHTML = "";
  mapMount.classList.remove("map-section-enter");

  const header = document.createElement("div");
  header.className = "map-header";
  header.textContent = "校园路线地图";

  const mapCanvas = document.createElement("div");
  mapCanvas.className = "campus-map";

  mapMount.appendChild(header);
  mapMount.appendChild(mapCanvas);

  requestAnimationFrame(() => {
    mapMount.classList.add("map-section-enter");
  });

  campusMap = new AMap.Map(mapCanvas, {
    zoom: 17,
    resizeEnable: true,
    center: path[0],
  });

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

function extractTourPath(stops) {
  return (stops || [])
    .filter(
      (stop) =>
        Number.isFinite(Number(stop.lng)) && Number.isFinite(Number(stop.lat)),
    )
    .map((stop) => [Number(stop.lng), Number(stop.lat)]);
}

async function showTourOnMap(tourData, mapMount) {
  const path = extractTourPath(tourData.stops);

  if (path.length === 0) {
    showInlineMapStatus(mapMount, "这条参观路线暂时没有可绘制的坐标。");
    return;
  }

  const AMap = await loadAmapScript();

  if (campusMap && typeof campusMap.destroy === "function") {
    campusMap.destroy();
  }

  mapMount.hidden = false;
  mapMount.innerHTML = "";
  mapMount.classList.remove("map-section-enter");

  const header = document.createElement("div");
  header.className = "map-header";
  header.textContent = "校园参观路线";

  const mapCanvas = document.createElement("div");
  mapCanvas.className = "campus-map";

  mapMount.appendChild(header);
  mapMount.appendChild(mapCanvas);

  requestAnimationFrame(() => {
    mapMount.classList.add("map-section-enter");
  });

  campusMap = new AMap.Map(mapCanvas, {
    zoom: 16,
    resizeEnable: true,
    center: path[0],
  });

  const markers = (tourData.stops || [])
    .filter(
      (stop) =>
        Number.isFinite(Number(stop.lng)) && Number.isFinite(Number(stop.lat)),
    )
    .map(
      (stop, index) =>
        new AMap.Marker({
          position: [Number(stop.lng), Number(stop.lat)],
          title: stop.title || stop.place_name || `第 ${index + 1} 站`,
          label: {
            content: `${index + 1}. ${stop.title || stop.place_name || "站点"}`,
            direction: "top",
          },
        }),
    );

  const overlays = [...markers];

  if (path.length >= 2) {
    overlays.push(
      new AMap.Polyline({
        path,
        showDir: true,
        strokeWeight: 5,
        strokeColor: "#1f6feb",
      }),
    );
  }

  campusMap.add(overlays);
  campusMap.setFitView(overlays);
}

const messageInput = document.getElementById("message");
const sendButton = document.getElementById("sendButton");
const chatLog = document.getElementById("chatLog");
const errorBox = document.getElementById("error");
const clearButton = document.getElementById("clearButton");
const serverStatus = document.getElementById("serverStatus");
const modelSelect = document.getElementById("modelSelect");

if (modelSelect) {
  modelSelect.value = localStorage.getItem(MODEL_STORAGE_KEY) || "deepseek-chat";
  modelSelect.addEventListener("change", () => {
    localStorage.setItem(MODEL_STORAGE_KEY, modelSelect.value);
  });
}

function getSelectedModel() {
  return modelSelect ? modelSelect.value : "deepseek-chat";
}

function buildProfile() {
  return {
    role: document.getElementById("roleSelect").value || "student",
    campus: document.getElementById("campus").value.trim() || null,
    college: document.getElementById("college").value.trim() || null,
    major: document.getElementById("major").value.trim() || null,
    dorm_area: document.getElementById("dormArea").value.trim() || null,
    international_student: false,
  };
}

//导航卡片
function appendThinkingMessage() {
  const message = document.createElement("div");
  message.className = "message assistant thinking-message";
  message.setAttribute("aria-live", "polite");

  const roleBox = document.createElement("div");
  roleBox.className = "message-role";
  roleBox.textContent = "助手正在思考";

  const dots = document.createElement("div");
  dots.className = "thinking-dots";
  dots.setAttribute("aria-label", "助手正在思考");

  for (let index = 0; index < 3; index += 1) {
    const dot = document.createElement("span");
    dots.appendChild(dot);
  }

  message.appendChild(roleBox);
  message.appendChild(dots);
  chatLog.appendChild(message);
  message.scrollIntoView({ behavior: "smooth", block: "end" });

  return message;
}

function removeThinkingMessage(message) {
  if (message && message.parentNode) {
    message.remove();
  }
}

function showInlineMapStatus(mapMount, text, className = "card-warning") {
  mapMount.hidden = false;
  mapMount.innerHTML = "";

  const status = document.createElement("div");
  status.className = className;
  status.textContent = text;
  mapMount.appendChild(status);
}

async function showDiningRoute(canteen, mapMount, button) {
  const originalText = button.textContent;
  button.disabled = true;
  button.textContent = "正在规划路线...";
  showInlineMapStatus(mapMount, "正在获取当前位置并规划路线...", "card-meta");

  try {
    const location = await getCurrentLocation();

    if (!location) {
      showInlineMapStatus(
        mapMount,
        lastLocationError || "暂时没有获取到当前位置，无法生成食堂导航。",
      );
      return;
    }

    const response = await fetch(`${API_BASE_URL}/api/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message: `怎么去${canteen.name}`,
        history: chatHistory,
        profile: buildProfile(),
        location: location,
        dining_preferences: diningPreferences,
        model: getSelectedModel(),
      }),
    });

    if (!response.ok) {
      throw new Error(`后端返回错误：${response.status}`);
    }

    const data = await response.json();
    const routeCard = (data.cards || []).find((card) => card.type === "route");

    if (!routeCard || !routeCard.data || !routeCard.data.route) {
      showInlineMapStatus(mapMount, "已识别食堂，但暂时没有获取到可绘制的路线。");
      return;
    }

    await showRouteOnMap(routeCard.data, mapMount);
  } catch (error) {
    showInlineMapStatus(mapMount, `路线规划失败：${error.message}`);
  } finally {
    button.disabled = false;
    button.textContent = originalText;
  }
}

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

        const inlineMap = document.createElement("div");
        inlineMap.className = "map-section inline-map-section";
        inlineMap.hidden = true;

        mapButton.addEventListener("click", () => {
          showRouteOnMap(data, inlineMap);
        });

        item.appendChild(mapButton);
        item.appendChild(inlineMap);

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

    if (card.type === "campus_tour") {
      const data = card.data || {};
      const stops = data.stops || [];
      const hasMapPoints = extractTourPath(stops).length > 0;

      const item = document.createElement("div");
      item.className = "card-item campus-tour-card";

      const title = document.createElement("div");
      title.className = "card-title";
      title.textContent = card.title || data.title || "校园参观路线";
      item.appendChild(title);

      const meta = document.createElement("div");
      meta.className = "card-meta";
      meta.textContent = `${data.campus || "校区"} | ${data.duration || "预计用时待确认"}`;
      item.appendChild(meta);

      if (data.description) {
        const description = document.createElement("div");
        description.className = "card-description";
        description.textContent = data.description;
        item.appendChild(description);
      }

      const stopList = document.createElement("ol");
      stopList.className = "tour-stop-list";

      stops.forEach((stop) => {
        const li = document.createElement("li");
        li.className = "tour-stop";

        const stopTitle = document.createElement("div");
        stopTitle.className = "tour-stop-title";
        stopTitle.textContent = stop.title || stop.place_name || "参观点";

        const stopPlace = document.createElement("div");
        stopPlace.className = "card-meta";
        stopPlace.textContent = stop.place_name || "";

        const stopDesc = document.createElement("div");
        stopDesc.className = "card-description";
        stopDesc.textContent = stop.description || "";

        li.appendChild(stopTitle);
        if (stop.place_name) {
          li.appendChild(stopPlace);
        }
        if (stop.description) {
          li.appendChild(stopDesc);
        }
        stopList.appendChild(li);
      });

      item.appendChild(stopList);

      const tips = data.tips || [];
      if (tips.length > 0) {
        const tipsBox = document.createElement("div");
        tipsBox.className = "tour-tips";

        const tipsTitle = document.createElement("div");
        tipsTitle.className = "tour-tips-title";
        tipsTitle.textContent = "小提醒";
        tipsBox.appendChild(tipsTitle);

        const tipsList = document.createElement("ul");
        tips.forEach((tip) => {
          const li = document.createElement("li");
          li.textContent = tip;
          tipsList.appendChild(li);
        });
        tipsBox.appendChild(tipsList);
        item.appendChild(tipsBox);
      }

      if (hasMapPoints) {
        const inlineMap = document.createElement("div");
        inlineMap.className = "map-section inline-map-section";
        inlineMap.hidden = true;
        item.appendChild(inlineMap);

        showTourOnMap(data, inlineMap).catch((error) => {
          showInlineMapStatus(inlineMap, `参观路线地图加载失败：${error.message}`);
        });
      } else {
        const warning = document.createElement("div");
        warning.className = "card-warning";
        warning.textContent = "这条参观路线暂时没有可绘制的坐标。";
        item.appendChild(warning);
      }

      cardsBox.appendChild(item);
    }

    if (card.type === "calendar") {
      const data = card.data || {};

      const calendarUrl = data.calendar_url || data.pdf_url;
      const item = document.createElement(calendarUrl ? "a" : "div");
      item.className = "card-item calendar-card";

      if (calendarUrl) {
        item.href = calendarUrl;
        item.target = "_blank";
        item.rel = "noopener noreferrer";
      }

      const title = document.createElement("div");
      title.className = "card-title";
      title.textContent = card.title || data.title || "上海交通大学校历";

      const description = document.createElement("div");
      description.className = "card-description";
      description.textContent =
        data.description || "查看上海交通大学教务处发布的校历。";

      const meta = document.createElement("div");
      meta.className = "card-meta";
      meta.textContent = `${data.academic_year || "当前学年"} | ${
        data.auto_updated ? "已从官网自动获取" : "使用本地备用链接"
      }`;

      const url = document.createElement("div");
      url.className = "calendar-url";
      url.textContent = calendarUrl || "暂无校历链接";

      item.appendChild(title);
      item.appendChild(description);
      item.appendChild(meta);
      item.appendChild(url);

      if (calendarUrl) {
        const pdfCta = document.createElement("div");
        pdfCta.className = "card-link card-action-link calendar-primary-action";
        pdfCta.textContent = "打开校历";
        item.appendChild(pdfCta);
      }

      if (data.source_url) {
        const sourceLink = document.createElement("a");
        sourceLink.className = "card-link card-action-link";
        sourceLink.href = data.source_url;
        sourceLink.target = "_blank";
        sourceLink.rel = "noopener noreferrer";
        sourceLink.textContent = "查看官网来源";
        sourceLink.addEventListener("click", (event) => {
          event.stopPropagation();
        });
        item.appendChild(sourceLink);
      }

      cardsBox.appendChild(item);
    }

    if (card.type === "checklist" || card.type === "parent_checklist") {
      const data = card.data || {};
      const checklistState = loadChecklistState();

      const item = document.createElement("div");
      item.className = `card-item checklist-card ${card.type === "parent_checklist" ? "parent-checklist-card" : ""}`;

      const title = document.createElement("div");
      title.className = "card-title";
      title.textContent = card.title || data.title || "新生入学准备清单";
      item.appendChild(title);

      if (data.description) {
        const description = document.createElement("div");
        description.className = "card-description";
        description.textContent = data.description;
        item.appendChild(description);
      }

      (data.groups || []).forEach((group) => {
        const groupBox = document.createElement("div");
        groupBox.className = "checklist-group";

        const groupTitle = document.createElement("div");
        groupTitle.className = "checklist-group-title";
        groupTitle.textContent = group.title || "未分组";
        groupBox.appendChild(groupTitle);

        (group.items || []).forEach((entry) => {
          const row = document.createElement("label");
          row.className = "checklist-item";

          const checkbox = document.createElement("input");
          checkbox.type = "checkbox";
          checkbox.checked = Boolean(checklistState[entry.id]);

          const text = document.createElement("span");
          text.className = "checklist-text";
          text.textContent = entry.text || "";

          const priority = document.createElement("span");
          priority.className = `checklist-priority priority-${entry.priority || "medium"}`;
          priority.textContent = entry.priority || "medium";

          checkbox.addEventListener("change", () => {
            const nextState = loadChecklistState();
            nextState[entry.id] = checkbox.checked;
            saveChecklistState(nextState);
            row.classList.toggle("is-checked", checkbox.checked);
          });

          row.classList.toggle("is-checked", checkbox.checked);
          row.appendChild(checkbox);
          row.appendChild(text);
          row.appendChild(priority);
          groupBox.appendChild(row);
        });

        item.appendChild(groupBox);
      });

      if (!data.groups || data.groups.length === 0) {
        const empty = document.createElement("div");
        empty.className = "card-warning";
        empty.textContent = data.error || "暂时没有可显示的 checklist。";
        item.appendChild(empty);
      }

      cardsBox.appendChild(item);
    }

    if (card.type === "dining") {
      const data = card.data || {};
      const recommendations = data.recommendations || [];

      const item = document.createElement("div");
      item.className = "card-item";

      const title = document.createElement("div");
      title.className = "card-title";
      title.textContent = card.title || "食堂推荐";
      item.appendChild(title);

      recommendations.forEach((recommendation, index) => {
        const canteen = recommendation.canteen;
        const crowd = recommendation.crowd;

        const row = document.createElement("div");
        row.className = "source";

        const rowTitle = document.createElement("div");
        rowTitle.className = "source-title";
        rowTitle.textContent = `${index + 1}. ${canteen.name}`;

        const meta = document.createElement("div");
        meta.className = "source-meta";
        meta.textContent = `${canteen.campus} | ${canteen.location_desc || canteen.area || "校内"} | 拥挤度：${
          crowd ? crowd.crowd_text : "暂未获取"
        } | 偏好：${recommendation.preference_count || 0} 次`;

        const description = document.createElement("div");
        description.className = "card-description";
        const floorCount = Array.isArray(canteen.floors)
          ? canteen.floors.length
          : 0;
        description.textContent =
          canteen.description ||
          canteen.location_desc ||
          (floorCount > 0 ? `共有 ${floorCount} 层餐饮区域。` : "");

        const button = document.createElement("button");
        button.type = "button";
        button.className = "card-map-button";
        button.textContent = "导航到这里";

        const inlineMap = document.createElement("div");
        inlineMap.className = "map-section inline-map-section";
        inlineMap.hidden = true;

        button.addEventListener("click", () => {
          showDiningRoute(canteen, inlineMap, button);
        });

        row.appendChild(rowTitle);
        row.appendChild(meta);
        row.appendChild(description);
        row.appendChild(button);
        row.appendChild(inlineMap);
        item.appendChild(row);
      });

      if (recommendations.length === 0) {
        const empty = document.createElement("div");
        empty.className = "card-warning";
        empty.textContent = "暂时没有可推荐的食堂。";
        item.appendChild(empty);
      }

      cardsBox.appendChild(item);
    }

    if (card.type === "dining_preference_record") {
      const canteen = card.data && card.data.canteen;

      if (canteen) {
        recordDiningPreference(canteen);
      }

      const item = document.createElement("div");
      item.className = "card-item";

      const title = document.createElement("div");
      title.className = "card-title";
      title.textContent = card.title || "已记录用餐偏好";

      const description = document.createElement("div");
      description.className = "card-description";
      description.textContent = canteen
        ? `已把 ${canteen.name} 记入你的历史用餐偏好。`
        : "已记录你的历史用餐偏好。";

      item.appendChild(title);
      item.appendChild(description);
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

  }

  chatLog.appendChild(message);
  message.scrollIntoView({ behavior: "smooth", block: "end" });
}
//发送操作
async function sendMessage() {
  if (activeChatAbortController) {
    activeChatAbortController.abort();
    return;
  }

  const message = messageInput.value.trim();

  if (!message) {
    errorBox.textContent = "请先输入问题。";
    return;
  }

  appendMessage("user", message);
  messageInput.value = "";

  activeChatAbortController = new AbortController();
  sendButton.disabled = false;
  sendButton.textContent = "中止回答";
  errorBox.textContent = "";
  const thinkingMessage = appendThinkingMessage();

  try {
    // Send client-side context so the agent can use history, profile, location, and dining preferences.
    const needsLocation = isRouteQuestion(message);
    const location = needsLocation ? await getCurrentLocation() : null;

    if (needsLocation && !location && lastLocationError) {
      errorBox.textContent = lastLocationError;
    } else if (needsLocation && location && lastLocationError) {
      errorBox.textContent = `${lastLocationError} 已使用最近一次成功定位生成路线。`;
    }

    const response = await fetch(`${API_BASE_URL}/api/chat`, {
      method: "POST",
      signal: activeChatAbortController.signal,
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        message: message,
        history: chatHistory,
        profile: buildProfile(),
        location: location,
        dining_preferences: diningPreferences,
        model: getSelectedModel(),
      }),
    });

    if (!response.ok) {
      throw new Error("后端返回错误：" + response.status);
    }

    const data = await response.json();
    console.log("chat response:", data);
    removeThinkingMessage(thinkingMessage);
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
    removeThinkingMessage(thinkingMessage);
    if (error.name === "AbortError") {
      errorBox.textContent = "已中止回答。";
    } else {
      errorBox.textContent = "请求失败：" + error.message;
    }
  } finally {
    activeChatAbortController = null;
    sendButton.disabled = false;
    sendButton.textContent = "发送";
    messageInput.focus();
  }
}
//检查后端连接
async function checkServerStatus() {
  try {
    const response = await fetch(`${API_BASE_URL}/health`);

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

document.querySelectorAll(".prompt-chip").forEach((button) => {
  button.addEventListener("click", () => {
    messageInput.value = button.dataset.prompt || "";
    messageInput.focus();
  });
});

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
  if (campusMap && typeof campusMap.destroy === "function") {
    campusMap.destroy();
  }

  campusMap = null;
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
