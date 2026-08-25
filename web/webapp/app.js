(function () {
  "use strict";

  const MODELS = [
    { key: "nano_banana2", label: "Nano Banana 2", hint: "Баланс цены и качества" },
    { key: "flux-schnell", label: "Flux 2 Pro", hint: "Премиум и скорость" },
    { key: "gpt_image2", label: "GPT Image 2", hint: "OpenAI через OpenRouter" },
  ];

  const RATIOS = [
    { value: "1:1", label: "Квадрат", icon: "⬜" },
    { value: "3:4", label: "Пост", icon: "📱" },
    { value: "4:5", label: "Instagram", icon: "📷" },
    { value: "9:16", label: "Stories", icon: "📲" },
    { value: "16:9", label: "Широкий", icon: "🖥️" },
  ];

  const state = {
    platform: "unknown",
    userId: null,
    firstName: null,
    authHeader: null,
    modelKey: MODELS[0].key,
    aspectRatio: "1:1",
    ready: false,
  };

  function $(id) {
    return document.getElementById(id);
  }

  function resolveApiBase() {
    const params = new URLSearchParams(window.location.search);
    const fromQuery = params.get("api_base");
    if (fromQuery) return fromQuery.replace(/\/$/, "");
    return window.location.origin;
  }

  function setGreeting() {
    const name = state.firstName ? ", " + state.firstName : "";
    $("greetingTitle").textContent = "Привет" + name + "!";
  }

  function setStatus(text, kind) {
    const el = $("statusLine");
    el.textContent = text || "";
    el.className = "status" + (kind ? " " + kind : "");
  }

  function renderModels() {
    const grid = $("modelGrid");
    grid.innerHTML = "";
    MODELS.forEach(function (m) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "model-card" + (state.modelKey === m.key ? " selected" : "");
      btn.setAttribute("role", "radio");
      btn.setAttribute("aria-checked", state.modelKey === m.key ? "true" : "false");
      btn.innerHTML = "<strong>" + m.label + "</strong><span>" + m.hint + "</span>";
      btn.addEventListener("click", function () {
        state.modelKey = m.key;
        renderModels();
      });
      grid.appendChild(btn);
    });
  }

  function renderRatios() {
    const grid = $("ratioGrid");
    grid.innerHTML = "";
    RATIOS.forEach(function (r) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "ratio-card" + (state.aspectRatio === r.value ? " selected" : "");
      btn.setAttribute("role", "radio");
      btn.setAttribute("aria-checked", state.aspectRatio === r.value ? "true" : "false");
      btn.innerHTML =
        '<div class="ratio-icon">' +
        r.icon +
        "</div><strong>" +
        r.label +
        "</strong><span>" +
        r.value +
        "</span>";
      btn.addEventListener("click", function () {
        state.aspectRatio = r.value;
        renderRatios();
      });
      grid.appendChild(btn);
    });
  }

  function detectTelegram() {
    const tg = window.Telegram && window.Telegram.WebApp;
    if (!tg || !tg.initData) return false;
    tg.ready();
    tg.expand();
    const user = tg.initDataUnsafe && tg.initDataUnsafe.user ? tg.initDataUnsafe.user : null;
    state.platform = "telegram";
    state.userId = user ? user.id : null;
    state.firstName = user && user.first_name ? user.first_name : null;
    state.authHeader = "tma " + tg.initData;
    $("platformHint").textContent = "Telegram · результат придёт в чат с ботом";
    setGreeting();
    return Boolean(state.userId && state.authHeader);
  }

  async function detectVk() {
    const bridge = window.vkBridge;
    if (!bridge) return false;
    try {
      await bridge.send("VKWebAppInit");
      const launch = await bridge.send("VKWebAppGetLaunchParams");
      const params = launch && typeof launch === "object" ? launch : {};
      const vkUserId = Number(params.vk_user_id || params.vkUserId || 0);
      if (!vkUserId) return false;

      const search = new URLSearchParams(window.location.search);
      const signParams = new URLSearchParams();
      Object.keys(params).forEach(function (key) {
        if (params[key] !== undefined && params[key] !== null) {
          signParams.set(key, String(params[key]));
        }
      });
      if (!signParams.has("sign") && search.has("sign")) {
        search.forEach(function (v, k) {
          signParams.set(k, v);
        });
      }

      state.platform = "vk";
      state.userId = vkUserId;
      state.firstName = null;
      state.authHeader = "vk " + signParams.toString();
      $("platformHint").textContent = "VK · результат придёт в диалог с ботом";
      setGreeting();
      return true;
    } catch (err) {
      console.warn("VK bridge init failed", err);
      return false;
    }
  }

  async function bootstrapPlatform() {
    if (detectTelegram()) {
      state.ready = true;
      return;
    }
    if (await detectVk()) {
      state.ready = true;
      return;
    }
    setStatus("Откройте студию из Telegram или VK", "error");
  }

  async function submitGenerate() {
    if (!state.ready) {
      setStatus("Не удалось проверить запуск Mini App", "error");
      return;
    }
    const prompt = ($("promptInput").value || "").trim();
    if (!prompt) {
      setStatus("Введите описание изображения", "error");
      return;
    }

    const btn = $("generateBtn");
    btn.disabled = true;
    setStatus("Отправляем задачу…", "");

    try {
      const resp = await fetch(resolveApiBase() + "/api/webapp/generate", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: state.authHeader,
        },
        body: JSON.stringify({
          user_id: state.userId,
          platform: state.platform,
          model_key: state.modelKey,
          aspect_ratio: state.aspectRatio,
          prompt: prompt,
        }),
      });
      const data = await resp.json().catch(function () {
        return {};
      });
      if (!resp.ok) {
        const detail = data.detail;
        const message =
          typeof detail === "string"
            ? detail
            : Array.isArray(detail)
              ? detail.map(function (x) { return x.msg || x; }).join("; ")
              : data.message || "Ошибка " + resp.status;
        throw new Error(message);
      }
      if (data.status === "ok") {
        setStatus("Задача принята! Результат скоро придёт в чат.", "ok");
      } else {
        setStatus("Задача отправлена.", "ok");
      }
      if (state.platform === "telegram" && window.Telegram && window.Telegram.WebApp) {
        window.Telegram.WebApp.close();
      }
    } catch (err) {
      setStatus(err.message || "Не удалось отправить запрос", "error");
    } finally {
      btn.disabled = false;
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    renderModels();
    renderRatios();
    $("generateBtn").addEventListener("click", submitGenerate);
    bootstrapPlatform();
  });
})();
