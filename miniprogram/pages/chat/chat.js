const app = getApp();

const STORAGE_KEY = "sjtu_miniprogram_chat_history";
const CHECKLIST_STORAGE_KEY = "sjtu_miniprogram_checklist_state";
const ROUTE_WORDS = ["怎么去", "怎么到", "怎么走", "路线", "导航", "开车", "接送", "去", "到"];
const FRIENDLY_SERVICE_ERROR = "暂时连接不上服务，请稍后再试。";

function routeLike(text) {
  return ROUTE_WORDS.some((word) => text.includes(word));
}

function safeText(value, fallback = "") {
  return value === undefined || value === null || value === "" ? fallback : String(value);
}

function loadChecklistState() {
  return wx.getStorageSync(CHECKLIST_STORAGE_KEY) || {};
}

function saveChecklistState(state) {
  wx.setStorageSync(CHECKLIST_STORAGE_KEY, state);
}

function hasCoordinate(place) {
  return place && Number.isFinite(Number(place.lng)) && Number.isFinite(Number(place.lat));
}

function toMapPoint(point) {
  if (!hasCoordinate(point)) {
    return null;
  }

  return {
    longitude: Number(point.lng),
    latitude: Number(point.lat)
  };
}

function parseAmapPolyline(polylineText) {
  if (!polylineText) {
    return [];
  }

  return String(polylineText)
    .split(";")
    .map((item) => {
      const [longitude, latitude] = item.split(",").map(Number);

      if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) {
        return null;
      }

      return { longitude, latitude };
    })
    .filter(Boolean);
}

function buildAmapMarkerUrl(place) {
  if (!hasCoordinate(place)) {
    return "";
  }

  return `https://uri.amap.com/marker?position=${place.lng},${place.lat}&name=${encodeURIComponent(place.name || "目的地")}&src=sjtu-freshman-agent&coordinate=gaode&callnative=0`;
}

function distanceMeters(left, right) {
  if (!hasCoordinate(left) || !hasCoordinate(right)) {
    return null;
  }

  const radius = 6371000;
  const toRad = (value) => (Number(value) * Math.PI) / 180;
  const dLat = toRad(right.lat - left.lat);
  const dLng = toRad(right.lng - left.lng);
  const lat1 = toRad(left.lat);
  const lat2 = toRad(right.lat);
  const a =
    Math.sin(dLat / 2) * Math.sin(dLat / 2) +
    Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) * Math.sin(dLng / 2);

  return Math.round(radius * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a)));
}

function buildRouteMapData(card) {
  const data = card.data || {};
  const route = data.route || {};
  const fromPoint = toMapPoint(data.from);
  const toPoint = toMapPoint(data.to);
  const points = [];

  (route.steps || []).forEach((step) => {
    parseAmapPolyline(step.polyline).forEach((point) => points.push(point));
  });

  if (points.length === 0 && fromPoint && toPoint) {
    points.push(fromPoint, toPoint);
  }

  if (points.length === 0) {
    return {
      can_draw: false,
      message: "暂时没有可绘制的路线。可以先点击定位，再重新发送路线问题。"
    };
  }

  const markers = [];
  const startPoint = fromPoint || points[0];
  const endPoint = toPoint || points[points.length - 1];

  if (startPoint) {
    markers.push({
      id: 1,
      ...startPoint,
      title: "起点",
      width: 26,
      height: 26
    });
  }

  if (endPoint) {
    markers.push({
      id: 2,
      ...endPoint,
      title: "终点",
      width: 26,
      height: 26
    });
  }

  return {
    can_draw: true,
    latitude: points[0].latitude,
    longitude: points[0].longitude,
    markers,
    polyline: [
      {
        points,
        color: "#1f6feb",
        width: 6,
        dottedLine: false,
        arrowLine: true
      }
    ]
  };
}

function buildTourMapData(card) {
  const stops = ((card.data || {}).stops || []).filter(hasCoordinate);
  const points = stops.map(toMapPoint).filter(Boolean);

  if (points.length === 0) {
    return {
      can_draw: false,
      message: "这条参观路线暂时没有可绘制的坐标。"
    };
  }

  return {
    can_draw: true,
    latitude: points[0].latitude,
    longitude: points[0].longitude,
    markers: points.map((point, index) => ({
      id: index + 1,
      ...point,
      title: stops[index].title || stops[index].place_name || `第 ${index + 1} 站`,
      width: 26,
      height: 26
    })),
    polyline:
      points.length >= 2
        ? [
            {
              points,
              color: "#1f6feb",
              width: 5,
              dottedLine: false,
              arrowLine: true
            }
          ]
        : []
  };
}

Page({
  currentRequestTask: null,
  cancelRequested: false,

  data: {
    apiBaseUrl: app.globalData.defaultApiBaseUrl,
    inputText: "",
    sending: false,
    locating: false,
    error: "",
    currentLocation: null,
    roleOptions: ["新生", "家长"],
    roleIndex: 0,
    modelOptions: [
      { label: "DeepSeek V4 Flash（常规）", value: "deepseek-chat" },
      { label: "DeepSeek V4 Flash（思考）", value: "deepseek-reasoner" },
      { label: "MiniMax-M2.7", value: "minimax-m2.7" },
      { label: "Qwen3.6-27B", value: "qwen3.6-27b" }
    ],
    modelLabels: ["DeepSeek V4 Flash（常规）", "DeepSeek V4 Flash（思考）", "MiniMax-M2.7", "Qwen3.6-27B"],
    modelIndex: 0,
    profile: {
      campus: "",
      college: "",
      major: "",
      dorm_area: ""
    },
    messages: []
  },

  onLoad() {
    const saved = wx.getStorageSync(STORAGE_KEY);
    if (Array.isArray(saved)) {
      this.setData({
        messages: saved.map((message) => ({
          ...message,
          cards: this.normalizeCards(message.cards)
        }))
      });
    }
  },

  onApiInput(event) {
    this.setData({ apiBaseUrl: event.detail.value.trim() });
  },

  onRoleChange(event) {
    this.setData({ roleIndex: Number(event.detail.value) });
  },

  onModelChange(event) {
    this.setData({ modelIndex: Number(event.detail.value) });
  },

  onProfileInput(event) {
    const key = event.currentTarget.dataset.key;
    this.setData({
      [`profile.${key}`]: event.detail.value
    });
  },

  onInput(event) {
    this.setData({ inputText: event.detail.value });
  },

  clearChat() {
    this.cancelResponse();
    wx.removeStorageSync(STORAGE_KEY);
    this.setData({ messages: [], error: "" });
  },

  buildProfile() {
    const profile = this.data.profile;
    return {
      role: this.data.roleIndex === 1 ? "parent" : "student",
      campus: profile.campus.trim() || null,
      college: profile.college.trim() || null,
      major: profile.major.trim() || null,
      dorm_area: profile.dorm_area.trim() || null,
      international_student: false
    };
  },

  buildHistory(messages) {
    return messages
      .filter((message) => !message.thinking && (message.role === "user" || message.role === "assistant"))
      .map((message) => ({
        role: message.role,
        content: message.content
      }))
      .slice(-12);
  },

  requestLocation() {
    return new Promise((resolve) => {
      wx.getLocation({
        type: "gcj02",
        success: (res) => {
          const location = {
            lng: res.longitude,
            lat: res.latitude,
            accuracy: res.accuracy || null
          };
          this.setData({ currentLocation: location, error: "" });
          resolve(location);
        },
        fail: (error) => {
          console.error("location failed", error);
          resolve(null);
        }
      });
    });
  },

  async locateMe() {
    if (this.data.locating) {
      return;
    }

    this.setData({ locating: true, error: "" });
    const location = await this.requestLocation();
    this.setData({
      locating: false,
      error: location ? "" : "定位失败，请在微信权限设置里允许定位后再试。",
      messages: location
        ? this.data.messages.map((message) => ({
            ...message,
            cards: this.normalizeCards(message.cards)
          }))
        : this.data.messages
    });

    if (!location) {
      wx.showToast({
        title: "请在微信权限中允许定位",
        icon: "none"
      });
    }
  },

  async getLocationIfNeeded(text) {
    if (!routeLike(text)) {
      return null;
    }

    if (this.data.currentLocation) {
      return this.data.currentLocation;
    }

    return this.requestLocation();
  },

  requestChat(payload) {
    const baseUrl = this.data.apiBaseUrl.replace(/\/$/, "");

    return new Promise((resolve, reject) => {
      const task = wx.request({
        url: `${baseUrl}/api/chat`,
        method: "POST",
        timeout: 30000,
        header: {
          "Content-Type": "application/json"
        },
        data: payload,
        success: (res) => {
          if (this.currentRequestTask === task) {
            this.currentRequestTask = null;
          }
          if (res.statusCode < 200 || res.statusCode >= 300) {
            console.error("chat http error", res.statusCode, res.data);
            reject(new Error(`后端返回 ${res.statusCode}`));
            return;
          }
          if (!res.data || typeof res.data.answer !== "string" || !Array.isArray(res.data.cards)) {
            console.error("invalid chat response", res.data);
            reject(new Error("返回格式异常"));
            return;
          }
          resolve(res.data);
        },
        fail: (error) => {
          if (this.currentRequestTask === task) {
            this.currentRequestTask = null;
          }
          console.error("chat request failed", error);
          reject(new Error(error.errMsg || "请求失败"));
        }
      });

      this.currentRequestTask = task;
    });
  },

  normalizeDiningRecommendations(recommendations) {
    const current = this.data.currentLocation;

    return (recommendations || []).map((recommendation) => {
      const canteen = recommendation.canteen || {};
      const distance = distanceMeters(current, canteen);
      const features = Array.isArray(canteen.features) ? canteen.features.slice(0, 3).join("、") : "";

      return {
        ...recommendation,
        canteen: {
          ...canteen,
          map_url: buildAmapMarkerUrl(canteen),
          distance_text: distance === null ? "" : distance >= 1000 ? `${(distance / 1000).toFixed(1)} km` : `${distance} m`
        },
        display_reason:
          recommendation.reason ||
          [
            recommendation.crowd ? `拥挤度：${recommendation.crowd.crowd_text}` : "实时拥挤度暂未获取",
            features ? `特色：${features}` : "",
            recommendation.preference_count ? `历史偏好：${recommendation.preference_count} 次` : ""
          ]
            .filter(Boolean)
            .join("；")
      };
    });
  },

  normalizeCards(cards) {
    const checklistState = loadChecklistState();

    return (cards || []).map((card) => {
      const next = {
        ...card,
        data: card.data || {}
      };

      if (next.type === "checklist" || next.type === "parent_checklist") {
        next.data.groups = (next.data.groups || []).map((group) => ({
          ...group,
          items: (group.items || []).map((item) => ({
            ...item,
            checked: Boolean(checklistState[item.id])
          }))
        }));
      }

      if (next.type === "calendar") {
        next.data.display_url = next.data.calendar_url || next.data.pdf_url || "";
      }

      if (next.type === "route" && next.data.route) {
        next.data.duration_minutes = Math.round(Number(next.data.route.duration || 0) / 60);
      }

      if (next.type === "route") {
        next.data.route_map = buildRouteMapData(next);
      }

      if (next.type === "campus_tour") {
        next.data.tour_map = buildTourMapData(next);
      }

      if (next.type === "dining" || next.type === "food_recommendation") {
        next.data.recommendations = this.normalizeDiningRecommendations(next.data.recommendations);
      }

      return next;
    });
  },

  persistMessages(messages) {
    wx.setStorageSync(STORAGE_KEY, messages.filter((message) => !message.thinking));
  },

  async sendMessage() {
    if (this.data.sending) {
      this.cancelResponse();
      return;
    }

    const text = this.data.inputText.trim();
    if (!text) {
      return;
    }

    await this.sendChatText(text, { addUserMessage: true });
  },

  async sendChatText(text, options = {}) {
    this.cancelRequested = false;
    const addUserMessage = options.addUserMessage !== false;
    const userMessage = {
      id: `u_${Date.now()}`,
      role: "user",
      content: text,
      cards: []
    };
    const thinkingMessage = {
      id: `t_${Date.now()}`,
      role: "assistant",
      content: "助手正在思考...",
      thinking: true,
      cards: []
    };
    const messages = addUserMessage
      ? [...this.data.messages, userMessage, thinkingMessage]
      : [...this.data.messages, thinkingMessage];

    this.setData({
      messages,
      inputText: "",
      sending: true,
      error: ""
    });

    try {
      const location = options.location || (await this.getLocationIfNeeded(text));
      if (this.cancelRequested) {
        return;
      }

      const data = await this.requestChat({
        message: text,
        history: this.buildHistory(this.data.messages),
        profile: this.buildProfile(),
        location,
        dining_preferences: [],
        model: this.data.modelOptions[this.data.modelIndex].value
      });

      console.log("chat response", data);
      console.log("cards", data.cards);

      const nextMessages = this.data.messages.filter((message) => !message.thinking);
      nextMessages.push({
        id: `a_${Date.now()}`,
        role: "assistant",
        content: data.answer || "",
        used_llm: Boolean(data.used_llm),
        cards: this.normalizeCards(data.cards)
      });

      this.setData({
        messages: nextMessages,
        sending: false
      });
      this.persistMessages(nextMessages);
    } catch (error) {
      console.error("send chat failed", error);
      const nextMessages = this.data.messages.filter((message) => !message.thinking);
      if (this.cancelRequested) {
        this.setData({
          messages: nextMessages,
          sending: false,
          error: "已中止回答。"
        });
        this.persistMessages(nextMessages);
        return;
      }

      const failedMessages = [
        ...nextMessages,
        {
          id: `a_${Date.now()}`,
          role: "assistant",
          content: FRIENDLY_SERVICE_ERROR,
          used_llm: false,
          cards: []
        }
      ];

      this.setData({
        messages: failedMessages,
        sending: false,
        error: FRIENDLY_SERVICE_ERROR
      });
      wx.showToast({
        title: FRIENDLY_SERVICE_ERROR,
        icon: "none"
      });
      this.persistMessages(failedMessages);
    }
  },

  cancelResponse() {
    if (!this.data.sending && !this.currentRequestTask) {
      return;
    }

    this.cancelRequested = true;

    if (this.currentRequestTask && typeof this.currentRequestTask.abort === "function") {
      this.currentRequestTask.abort();
      this.currentRequestTask = null;
    }

    const nextMessages = this.data.messages.filter((message) => !message.thinking);
    this.setData({
      messages: nextMessages,
      sending: false,
      error: "已中止回答。"
    });
    this.persistMessages(nextMessages);
  },

  async navigateToDining(event) {
    const name = safeText(event.currentTarget.dataset.name);
    if (!name || this.data.sending) {
      return;
    }

    if (!this.data.currentLocation) {
      this.setData({ error: "请先点击定位，或在问题里说明起点。" });
      wx.showToast({
        title: "请先点击定位，或说明起点",
        icon: "none"
      });
      return;
    }

    await this.sendChatText(`从当前位置到${name}怎么走`, {
      addUserMessage: true,
      location: this.data.currentLocation
    });
  },

  toggleChecklistItem(event) {
    const id = event.currentTarget.dataset.id;
    if (!id) {
      return;
    }

    const checklistState = loadChecklistState();
    checklistState[id] = !checklistState[id];
    saveChecklistState(checklistState);

    this.setData({
      messages: this.data.messages.map((message) => ({
        ...message,
        cards: this.normalizeCards(message.cards)
      }))
    });
  },

  copyText(event) {
    const text = safeText(event.currentTarget.dataset.text);
    if (!text) {
      return;
    }

    wx.setClipboardData({
      data: text
    });
  }
});
