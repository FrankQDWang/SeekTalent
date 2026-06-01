from __future__ import annotations

import json


def login_frame_html(connection_id: str) -> str:
    snapshot_url = f"/api/workbench/source-connections/{connection_id}/login/snapshot"
    input_url = f"/api/workbench/source-connections/{connection_id}/login/input"
    complete_url = f"/api/workbench/source-connections/{connection_id}/login/complete"
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>猎聘登录</title>
  <style>
    :root {{
      color-scheme: light;
      --paper: #f4efe6;
      --ink: #25211b;
      --muted: #777067;
      --line: #d8d0c3;
      --accent: #2f6b4f;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      background: var(--paper);
      color: var(--ink);
      font: 13px/1.4 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      min-height: 100vh;
      border: 1px solid var(--line);
    }}
    header, footer {{
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 10px 12px;
      background: rgba(255, 255, 255, 0.36);
      border-bottom: 1px solid var(--line);
    }}
    footer {{
      border-top: 1px solid var(--line);
      border-bottom: 0;
      flex-wrap: wrap;
    }}
    strong {{ font-size: 14px; }}
    #state {{ color: var(--muted); }}
    #viewport {{
      min-height: 0;
      display: grid;
      place-items: center;
      padding: 12px;
      background: #e9e2d8;
    }}
    #view {{
      max-width: 100%;
      max-height: calc(100vh - 116px);
      border: 1px solid #cfc6b8;
      background: #fff;
      cursor: crosshair;
    }}
    input {{
      min-width: 220px;
      flex: 1 1 280px;
      border: 1px solid var(--line);
      border-radius: 4px;
      padding: 8px 9px;
      background: rgba(255, 255, 255, 0.72);
      color: var(--ink);
    }}
    button {{
      border: 1px solid #bfb5a6;
      border-radius: 4px;
      padding: 8px 10px;
      background: #fffaf1;
      color: var(--ink);
      cursor: pointer;
    }}
    button.primary {{
      background: var(--accent);
      border-color: var(--accent);
      color: #fff;
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <strong>猎聘登录</strong>
      <span id="state">初始化</span>
    </header>
    <section id="viewport">
      <img id="view" alt="猎聘登录画面" />
    </section>
    <footer>
      <input id="text" autocomplete="off" placeholder="输入文字后发送到登录页" />
      <button id="type" type="button">输入</button>
      <button id="enter" type="button">Enter</button>
      <button id="refresh" type="button">刷新画面</button>
      <button id="done" class="primary" type="button">我已完成登录</button>
    </footer>
  </main>
  <script>
    const snapshotUrl = {json.dumps(snapshot_url)};
    const inputUrl = {json.dumps(input_url)};
    const completeUrl = {json.dumps(complete_url)};
    const image = document.getElementById("view");
    const state = document.getElementById("state");
    const text = document.getElementById("text");

    function csrfHeader() {{
      const row = document.cookie.split("; ").find((item) => item.startsWith("seektalent_workbench_csrf="));
      return row ? decodeURIComponent(row.split("=")[1]) : "";
    }}

    async function postJson(url, body) {{
      const response = await fetch(url, {{
        method: "POST",
        headers: {{"Content-Type": "application/json", "X-CSRF-Token": csrfHeader()}},
        body: JSON.stringify(body),
      }});
      if (!response.ok) throw new Error("request failed");
      return response.json();
    }}

    async function refresh() {{
      state.textContent = "刷新中";
      const response = await fetch(snapshotUrl, {{credentials: "same-origin"}});
      if (!response.ok) {{
        state.textContent = "无法获取画面";
        return;
      }}
      const payload = await response.json();
      image.src = `data:${{payload.imageMimeType}};base64,${{payload.imageBase64}}`;
      state.textContent = `${{payload.status}} · ${{payload.pageOrigin}}`;
    }}

    image.addEventListener("click", async (event) => {{
      const rect = image.getBoundingClientRect();
      const x = ((event.clientX - rect.left) * image.naturalWidth) / rect.width;
      const y = ((event.clientY - rect.top) * image.naturalHeight) / rect.height;
      await postJson(inputUrl, {{action: "click", x, y}});
      await refresh();
    }});

    document.getElementById("type").addEventListener("click", async () => {{
      if (!text.value) return;
      await postJson(inputUrl, {{action: "type", text: text.value}});
      text.value = "";
      await refresh();
    }});

    document.getElementById("enter").addEventListener("click", async () => {{
      await postJson(inputUrl, {{action: "key", key: "Enter"}});
      await refresh();
    }});

    document.getElementById("refresh").addEventListener("click", refresh);
    document.getElementById("done").addEventListener("click", async () => {{
      state.textContent = "确认中";
      const response = await fetch(completeUrl, {{
        method: "POST",
        headers: {{"X-CSRF-Token": csrfHeader()}},
      }});
      state.textContent = response.ok ? "已连接，可以返回工作台" : "确认失败";
    }});

    void refresh();
  </script>
</body>
</html>"""
