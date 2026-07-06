export default defineContentScript({
  matches: ["<all_urls>"],
  runAt: "document_end",
  main() {
    console.log("[Qwen TTS] Polishing UI Structure...");

    const ICONS = {
      prev: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="19 20 9 12 19 4 19 20"/><line x1="5" y1="19" x2="5" y2="5"/></svg>`,
      play: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 3 19 12 5 21 5 3"/></svg>`,
      pause: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/></svg>`,
      next: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="5 4 15 12 5 20 5 4"/><line x1="19" y1="5" x2="19" y2="19"/></svg>`,
      stop: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"/></svg>`,
      clipboard: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1" ry="1"/></svg>`,
      bookmark: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/></svg>`,
      list: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="8" y1="6" x2="21" y2="6"/><line x1="8" y1="12" x2="21" y2="12"/><line x1="8" y1="18" x2="21" y2="18"/><line x1="3" y1="6" x2="3.01" y2="6"/><line x1="3" y1="12" x2="3.01" y2="12"/><line x1="3" y1="18" x2="3.01" y2="18"/></svg>`,
      trash: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><line x1="10" y1="11" x2="10" y2="17"/><line x1="14" y1="11" x2="14" y2="17"/></svg>`,
      settings: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>`,
      mic: `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/><path d="M19 10v1a7 7 0 0 1-14 0v-1"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>`
    };

    const callApi = async (endpoint: string, data?: any, method: string = 'POST') => {
      return await browser.runtime.sendMessage({ type: "QWEN_COMMAND", endpoint, data, method });
    };

    const initUI = async () => {
      if (document.getElementById('qwen-tts-floating-root')) return;
      if (!document.body) { setTimeout(initUI, 200); return; }

      const container = document.createElement('div');
      container.id = 'qwen-tts-floating-root';
      // bottom 30→84:避开网页底部工具栏/播放条(2026-07-01 用户反馈挡住底部信息)
      container.style.cssText = `position: fixed; bottom: 84px; right: 30px; z-index: 2147483647; user-select: none; transition: transform 0.2s ease;`;
      document.body.appendChild(container);

      const { hideFloatingBar } = await browser.storage.local.get("hideFloatingBar");
      if (hideFloatingBar) container.style.display = 'none';

      browser.storage.onChanged.addListener((changes, area) => {
        if (area === 'local' && changes.hideFloatingBar !== undefined) {
          container.style.display = changes.hideFloatingBar.newValue ? 'none' : 'block';
        }
      });

      const shadow = container.attachShadow({ mode: 'open' });

      const style = document.createElement('style');
      style.textContent = `
        @keyframes fadeIn { from { opacity: 0; transform: translateY(12px); } to { opacity: 1; transform: translateY(0); } }
        @keyframes logoPulse {
          0% { opacity: 0.45; transform: scale(0.94); filter: drop-shadow(0 0 2px rgba(77, 163, 255, 0.4)); }
          50% { opacity: 1; transform: scale(1.08); filter: drop-shadow(0 0 10px rgba(77, 163, 255, 0.8)); }
          100% { opacity: 0.45; transform: scale(0.94); filter: drop-shadow(0 0 2px rgba(77, 163, 255, 0.4)); }
        }
        .bar {
          display: flex; align-items: center; background: rgba(24, 24, 27, 0.82);
          backdrop-filter: blur(24px); -webkit-backdrop-filter: blur(24px);
          border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 26px;
          padding: 4px; box-shadow: 0 12px 40px rgba(0, 0, 0, 0.3);
          transition: all 0.4s cubic-bezier(0.16, 1, 0.3, 1); overflow: hidden;
          max-width: 38px; height: 38px; 
        }
        /* 2026-07-01:播放中(.active)不再强制撑开——悬停展开、移开自动收、
           点 logo 手动钉住;.active 改为收起态的状态灯(蓝描边微光)。 */
        .bar:hover, .bar.expanded { max-width: 470px; padding: 4px 6px; }
        .bar.active { border-color: rgba(77, 163, 255, 0.55); box-shadow: 0 12px 40px rgba(0,0,0,0.3), 0 0 10px rgba(77,163,255,0.25); }
        .logo {
          width: 30px; height: 30px; border-radius: 50%; background: transparent;
          display: flex; align-items: center; justify-content: center; color: rgba(255, 255, 255, 0.7);
          font-weight: 700; font-size: 14px; flex-shrink: 0; cursor: grab; transition: 0.2s;
        }
        .logo:hover { background: rgba(255, 255, 255, 0.06); color: white; }
        .logo.loading {
          animation: logoPulse 1.2s infinite ease-in-out;
          color: #4DA3FF !important;
        }
        .content {
          display: flex; align-items: center; gap: 2px; opacity: 0;
          transition: opacity 0.3s; pointer-events: none; margin-left: 0;
        }
        .bar:hover .content, .bar.expanded .content { opacity: 1; pointer-events: auto; margin-left: 6px; }
        .btn {
          width: 32px; height: 32px; border-radius: 10px; display: flex; align-items: center;
          justify-content: center; color: rgba(255, 255, 255, 0.75); background: transparent;
          border: none; cursor: pointer; transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        }
        .btn:hover { background: rgba(255, 255, 255, 0.08); color: white; transform: scale(1.08); }
        .btn:active { transform: scale(0.94); }
        .btn svg { width: 17px; height: 17px; }
        .btn.active { color: #4DA3FF; background: rgba(77, 163, 255, 0.12); }
        
        .btn-play-main {
          color: white; background: rgba(255, 255, 255, 0.16); border: 1px solid rgba(255, 255, 255, 0.12);
          transform: scale(1.06); margin: 0 4px;
        }
        .btn-play-main:hover { transform: scale(1.12); background: rgba(255, 255, 255, 0.22); }
        .btn-play-main.active { color: #4DA3FF; background: rgba(77, 163, 255, 0.15); box-shadow: 0 0 12px rgba(77, 163, 255, 0.3); border-color: rgba(77, 163, 255, 0.4); }

        .divider { width: 1px; height: 16px; background: rgba(255, 255, 255, 0.08); margin: 0 4px; }
        
        /* Queue Popup - Narrow Card Anchored to Right */
        .queue-popup {
          position: absolute; bottom: 52px; right: 8px; width: 230px;
          background: rgba(24, 24, 27, 0.88); backdrop-filter: blur(28px);
          border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 16px;
          box-shadow: 0 15px 45px rgba(0, 0, 0, 0.35); padding: 8px;
          display: none; flex-direction: column; gap: 2px; animation: fadeIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
          z-index: 1000;
        }
        .queue-popup.show { display: flex; }
        .queue-header { font-size: 13px; font-weight: 500; color: rgba(255, 255, 255, 0.75); padding: 2px 4px 4px; border-bottom: 1px solid rgba(255, 255, 255, 0.05); }
        .queue-list { max-height: 180px; overflow-y: auto; display: flex; flex-direction: column; padding-top: 2px; }
        .queue-item {
          display: flex; align-items: center; gap: 8px; height: 38px; padding: 0 6px; border-radius: 8px;
          cursor: pointer; transition: 0.2s;
        }
        .queue-item:hover { background: rgba(255, 255, 255, 0.04); }
        .queue-item input[type="checkbox"] { 
          appearance: none; width: 14px; height: 14px; border: 1px solid rgba(255, 255, 255, 0.25); 
          border-radius: 4px; cursor: pointer; transition: 0.2s; position: relative;
        }
        .queue-item input[type="checkbox"]:checked { background: rgba(77, 163, 255, 0.85); border-color: transparent; }
        .queue-item input[type="checkbox"]:checked::after {
          content: '✓'; color: white; font-size: 10px; position: absolute; left: 1.5px; top: -1px;
        }
        .queue-item-text { flex: 1; font-size: 13px; color: rgba(255,255,255,0.9); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .queue-item-time { font-size: 11px; color: rgba(255, 255, 255, 0.3); }
        .action-buttons {
          display: flex; align-items: center; gap: 2px;
        }
        .btn-del, .btn-play-podcast, .btn-gen-podcast {
          opacity: 0; width: 22px; height: 22px; border-radius: 6px;
          display: flex; align-items: center; justify-content: center;
          color: rgba(255, 255, 255, 0.4); transition: 0.2s;
        }
        .btn-del svg, .btn-play-podcast svg, .btn-gen-podcast svg {
          width: 12px; height: 12px;
        }
        .queue-item:hover .btn-del, .queue-item:hover .btn-play-podcast, .queue-item:hover .btn-gen-podcast { opacity: 1; }
        .btn-del:hover { background: rgba(255, 77, 77, 0.12); color: #FF4D4D; }
        .btn-play-podcast:hover { background: rgba(77, 255, 136, 0.12); color: #4DFF88; }
        .btn-gen-podcast:hover { background: rgba(77, 163, 255, 0.12); color: #4DA3FF; }
        
        .queue-footer { padding-top: 6px; display: flex; justify-content: flex-end; }
        .btn-play-selected {
          background: rgba(255, 255, 255, 0.08); border: 1px solid rgba(255, 255, 255, 0.05); color: rgba(255, 255, 255, 0.85); 
          height: 28px; padding: 0 12px; border-radius: 8px; font-size: 12px; font-weight: 500; cursor: pointer; transition: 0.2s;
        }
        .btn-play-selected:hover { background: rgba(255, 255, 255, 0.15); color: white; }

        .empty-state { padding: 8px 4px 4px; text-align: left; }
        .empty-main { font-size: 13px; color: rgba(255,255,255,0.8); margin-bottom: 2px; }
        .empty-sub { font-size: 11px; color: rgba(255,255,255,0.4); }

        .toast {
          position: fixed; bottom: 154px; right: 30px; background: rgba(30, 30, 30, 0.95);
          color: white; padding: 8px 18px; border-radius: 12px; font-size: 12px;
          box-shadow: 0 10px 25px rgba(0, 0, 0, 0.25); backdrop-filter: blur(10px);
          border: 1px solid rgba(255, 255, 255, 0.08); animation: fadeIn 0.3s ease; z-index: 2147483647;
        }

        /* Settings Popup */
        .settings-popup {
          position: absolute; bottom: 52px; right: 8px; width: 170px;
          background: rgba(24, 24, 27, 0.88); backdrop-filter: blur(28px);
          border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 16px;
          box-shadow: 0 15px 45px rgba(0, 0, 0, 0.35); padding: 10px;
          display: none; flex-direction: column; gap: 8px; animation: fadeIn 0.2s cubic-bezier(0.16, 1, 0.3, 1);
          z-index: 1000;
        }
        .settings-popup.show { display: flex; }
        .settings-group { display: flex; flex-direction: column; gap: 4px; }
        .settings-label { font-size: 10px; color: rgba(255, 255, 255, 0.45); font-weight: 500; }
        .settings-select {
          background: rgba(255, 255, 255, 0.06); border: 1px solid rgba(255, 255, 255, 0.08);
          border-radius: 8px; color: white; font-size: 11px; padding: 4px 6px; outline: none; cursor: pointer;
        }
        .settings-select option { background: #18181b; color: white; }
      `;
      shadow.appendChild(style);

      const bar = document.createElement('div');
      bar.className = 'bar';
      const logo = document.createElement('div');
      logo.className = 'logo'; logo.textContent = 'Q';
      const content = document.createElement('div');
      content.className = 'content';

      const btnPrev = createBtn(ICONS.prev, '上一句');
      const btnPlayPause = createBtn(ICONS.play, '播放/继续');
      btnPlayPause.classList.add('btn-play-main');
      const btnPause = createBtn(ICONS.pause, '暂停');
      const btnNext = createBtn(ICONS.next, '下一句');
      const btnStop = createBtn(ICONS.stop, '停止');
      const d1 = document.createElement('div'); d1.className = 'divider';
      const btnClip = createBtn(ICONS.clipboard, '朗读剪切板');
      const btnSave = createBtn(ICONS.bookmark, '保存当前/选中');
      const btnQueue = createBtn(ICONS.list, '最近收藏');

      function createBtn(svg: string, title: string) {
        const b = document.createElement('button');
        b.className = 'btn'; b.innerHTML = svg; b.title = title;
        return b;
      }

      content.append(btnPrev, btnPlayPause, btnPause, btnNext, btnStop, d1, btnClip, btnSave, btnQueue);
      bar.append(logo, content);
      
      const queuePopup = document.createElement('div');
      queuePopup.className = 'queue-popup';
      shadow.appendChild(queuePopup);


      shadow.appendChild(bar);

      let isPlaying = false, isPaused = false, hideTimeout: any = null, isExpanded = false;

      logo.onclick = () => {
        // 拖动(位移超过阈值)不算点击——否则每次拖完都会误"钉住"展开态,
        // 表现为"悬浮框收不回小圆球"(2026-07-01 用户实测)。
        if (didDrag) return;
        isExpanded = !isExpanded;
        bar.classList.toggle('expanded', isExpanded);
      };

      const updateUIState = (playing: boolean, paused: boolean, statusCode?: string) => {
        isPlaying = playing;
        isPaused = paused;
        
        btnPlayPause.innerHTML = ICONS.play;
        btnPlayPause.classList.toggle('active', isPlaying && isPaused);
        
        const isLoading = (statusCode === "BUSY" && !isPlaying && !isPaused);
        logo.classList.toggle('loading', isLoading);
        
        const isActive = isPlaying || isPaused;
        bar.classList.toggle('active', isActive);
      };

      btnPlayPause.onclick = async () => {
        if (isPlaying && isPaused) {
          updateUIState(true, false);
          const res = await callApi("/resume");
          if (res?.error) {
            updateUIState(true, true);
            showToast(`❌ 恢复失败: ${res.error}`);
          }
        } else if (!isPlaying) {
          showToast("请先用剪切板、选中文本或 Popup 开始朗读");
        }
      };

      btnPause.onclick = async () => {
        if (!isPlaying || isPaused) return;
        const res = await callApi("/pause");
        if (res?.error) {
          showToast(`❌ 暂停失败: ${res.error}`);
          return;
        }
        updateUIState(true, true);
      };

      btnStop.onclick = async () => {
        updateUIState(false, false);
        const res = await callApi("/stop");
        if (res?.error) {
          updateUIState(true, false);
          showToast(`❌ 停止失败: ${res.error}`);
        }
      };

      btnPrev.onclick = () => { 
        logo.classList.add('loading');
        updateUIState(true, false);
        callApi("/seek", { direction: -1 }); 
      };

      btnNext.onclick = () => { 
        logo.classList.add('loading');
        updateUIState(true, false);
        callApi("/seek", { direction: 1 }); 
      };
      
      btnClip.onclick = async () => {
        logo.classList.add('loading');
        updateUIState(true, false);
        const res = await browser.runtime.sendMessage({ type: "READ_CLIPBOARD" });
        if (res?.error) {
          updateUIState(false, false);
          showToast("❌ 剪切板为空"); 
        } else {
          showToast("📋 正在朗读剪切板...");
        }
      };

      btnSave.onclick = async () => {
        const sel = window.getSelection()?.toString().trim();
        if (sel) {
          const res = await callApi("/save_for_later", { text: sel, source: "selection" });
          if (!res.error) showToast("已存入选中内容");
          else showToast("保存失败: " + res.error);
          return;
        }

        const url = window.location.href;
        if (url.startsWith("chrome://") || url.startsWith("about:") || url.startsWith("edge://")) {
          showToast("当前页面不能保存");
          return;
        }
        // auto 模式：后台按"内容语言 vs 设置的目标语言"自动判断——不同则翻译成目标语
        // (target_lang=zh→中文, =en→英文)，相同则存原文。送整页已渲染 HTML，带上你的
        // library 登录态正文。存好后在 saved 列表里播放即可听到目标语言版本。
        const res = await callApi("/read_url", {
          url,
          mode: "auto",
          translate: false,
          save: true,
          html: document.documentElement.outerHTML
        });
        if (!res.error && res.status !== "error") showToast("已保存当前网页（按目标语言自动翻译）");
        else showToast("保存失败: " + (res.error || res.message || "未知错误"));
      };

      btnQueue.onmouseenter = async () => {
        clearTimeout(hideTimeout);
        const items = await callApi("/saved_items", null, "GET");
        renderQueue(items || []);
        queuePopup.classList.add('show');
      };

      bar.onmouseleave = () => {
        hideTimeout = setTimeout(() => { 
          if (!queuePopup.matches(':hover')) queuePopup.classList.remove('show'); 
        }, 600);
      };
      queuePopup.onmouseleave = () => queuePopup.classList.remove('show');

      function renderQueue(items: any) {
        if (!Array.isArray(items)) items = [];
        queuePopup.innerHTML = `<div class="queue-header">Saved · ${items.length}</div><div class="queue-list"></div>`;
        const list = queuePopup.querySelector('.queue-list')!;
        if (items.length === 0) {
          list.innerHTML = `<div class="empty-state"><div class="empty-main">还没有收藏内容</div><div class="empty-sub">划词后点击 🔖 即可保存</div></div>`;
          return;
        }

        items.forEach((item: any, i: number) => {
          const row = document.createElement('div');
          row.className = 'queue-item';
          
          const playOrMicBtn = `<div class="btn-gen-podcast" title="生成播客">${ICONS.mic}</div>`;
            
          row.innerHTML = `<input type="checkbox" checked data-idx="${i}"><span class="queue-item-text" title="${item.text}">${item.title}</span><span class="queue-item-time">${Math.ceil(item.text.length / 200)}m</span><div class="action-buttons">${playOrMicBtn}<div class="btn-del" title="删除">${ICONS.trash}</div></div>`;
          
          const bindGenClick = (btn: HTMLElement) => {
            btn.onclick = async (e) => {
              e.stopPropagation();
              btn.style.opacity = '0.5';
              showToast("🎙️ 正在生成单条播客 WAV...");
              try {
                const res = await callApi("/generate_single_podcast", {
                  text: item.text,
                  source: item.source || "web",
                  voice: item.voice,
                  title: item.title
                });
                if (res && !res.error) {
                  showToast("已提交播客生成");
                  btn.style.opacity = '1';
                } else {
                  showToast("❌ 生成失败: " + (res?.error || "未知错误"));
                  btn.style.opacity = '1';
                }
              } catch {
                showToast("❌ 连接失败");
                btn.style.opacity = '1';
              }
            };
          };

          const btnGen = row.querySelector('.btn-gen-podcast') as HTMLElement;
          if (btnGen) bindGenClick(btnGen);

          const btnDel = row.querySelector('.btn-del') as HTMLElement;
          if (btnDel) {
            btnDel.onclick = (e) => {
              e.stopPropagation();
              callApi("/delete_saved", { index: i }).then(() => {
                btnQueue.onmouseenter?.(null as any);
              });
            };
          }

          row.onclick = (e) => {
            if (e.target instanceof HTMLInputElement) return;
            const cb = row.querySelector('input') as HTMLInputElement;
            cb.checked = !cb.checked;
          };

          list.appendChild(row);
        });

        const footer = document.createElement('div'); footer.className = 'queue-footer';
        const pBtn = document.createElement('button'); pBtn.className = 'btn-play-selected';
        pBtn.textContent = `▶ Play Selected`;
        pBtn.onclick = async () => {
          const idxs = Array.from(queuePopup.querySelectorAll('input:checked')).map(cb => parseInt((cb as HTMLElement).getAttribute('data-idx')!));
          if (idxs.length) { 
            logo.classList.add('loading');
            updateUIState(true, false);
            await callApi("/play_saved", { indices: idxs }); 
            queuePopup.classList.remove('show'); 
            showToast("🎙️ 开始朗读队列..."); 
          }
        };
        footer.appendChild(pBtn); queuePopup.appendChild(footer);
      }

      function showToast(msg: string) {
        const t = document.createElement('div'); t.className = 'toast'; t.textContent = msg;
        shadow.appendChild(t); setTimeout(() => t.remove(), 3000);
      }

      browser.runtime.onMessage.addListener((msg: any) => {
        if (msg.type === "QWEN_STATUS_UPDATE") {
          updateUIState(msg.data.is_playing, msg.data.is_paused, msg.data.status_code);
        }
      });
      
      let isDragging = false, didDrag = false, sx: number, sy: number, ix: number, iy: number;
      logo.onmousedown = (e) => { isDragging = true; didDrag = false; sx = e.clientX; sy = e.clientY; const r = container.getBoundingClientRect(); ix = r.left; iy = r.top; container.style.cursor = 'grabbing'; };
      window.onmousemove = (e) => {
        if (!isDragging) return;
        // 位移超过 4px 判定为拖动(而非点击抖动)
        if (Math.abs(e.clientX - sx) + Math.abs(e.clientY - sy) > 4) didDrag = true;
        container.style.left = (ix + e.clientX - sx) + 'px'; container.style.top = (iy + e.clientY - sy) + 'px'; container.style.bottom = 'auto'; container.style.right = 'auto';
      };
      window.onmouseup = () => {
        if (isDragging && didDrag) {
          // 拖动期间是 left 锚定;松手换算回 right 锚定——收起时小圆球贴右、
          // 展开时向左绽开(2026-07-01 用户要求:球要靠右)。
          const r = container.getBoundingClientRect();
          container.style.right = Math.max(8, window.innerWidth - r.right) + 'px';
          container.style.left = 'auto';
        }
        isDragging = false; container.style.cursor = 'default';
      };
    };

    initUI();
  },
});
